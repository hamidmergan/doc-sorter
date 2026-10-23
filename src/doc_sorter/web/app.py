"""Web API for the sorting page.

Folder mode: plan a folder below `root` in the background, review, apply, undo.
Paths in requests and answers are relative to `root`.
Upload mode: upload files or ZIPs, review, download the sorted result as a ZIP; nothing on disk is moved.
"""

import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..apply import undo
from ..config import Settings
from ..core import plan as build_plan
from ..errors import SortError
from ..llm import make_client
from ..plan import LOG_DIR, SORT, Plan, inside
from ..rules import clean_part
from .uploads import MB, store, write_zip, zip_names

STATIC = Path(__file__).parent / "static"


class PlanRequest(BaseModel):
    folder: str = ""
    instruction: str = ""
    dest: str = ""
    recursive: bool = False
    copy_files: bool = False


class ApplyRequest(BaseModel):
    selected: List[int]
    # edited targets by entry index, relative to the target folder
    targets: Dict[int, str] = {}


class UndoRequest(BaseModel):
    folder: str = ""


class Job:
    def __init__(self) -> None:
        self.state = "idle"  # idle, running, done or error
        self.done = 0
        self.total = 0
        self.current = ""
        self.error = ""
        self.instruction = ""
        self.plan: Optional[Plan] = None
        self.upload = ""  # name of the upload, in upload mode
        self.uploaded = 0


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host[1:host.find("]")].lower()
    return host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()


def _edited(text: str, suffix: str) -> Optional[Path]:
    """A target typed by the user as a safe relative path that keeps the file type."""
    parts = [clean_part(p) for p in text.replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        return None
    if Path(parts[-1]).suffix.lower() != suffix.lower():
        parts[-1] += suffix
    return Path(*parts)


def create_app(settings: Settings, root: Optional[Path] = None, upload: bool = False,
               allowed_hosts: Optional[Sequence[str]] = None, client=None) -> FastAPI:
    """The sorting page and its JSON API, with one plan at a time.

    With `upload`, files come from the browser and go back as a ZIP; `root` is not used.
    `allowed_hosts` limits the Host header (against DNS rebinding); requests from another origin are refused.
    """
    tmp = tempfile.TemporaryDirectory(prefix="doc-sorter-") if upload else None
    root = Path(tmp.name if tmp else (root or Path.home())).expanduser().resolve()
    if not root.is_dir():
        raise SortError(f"Root folder not found: {root}")
    inbox = root / "inbox"
    job = Job()
    lock = threading.Lock()
    client = client or make_client(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if tmp is not None:
            tmp.cleanup()

    app = FastAPI(title="doc-sorter", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    hosts = {h.lower() for h in allowed_hosts} if allowed_hosts is not None else None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if hosts is not None and _hostname(host) not in hosts:
            return PlainTextResponse("Unknown host", status_code=400)
        origin = request.headers.get("origin")
        own = {host.lower(), request.headers.get("x-forwarded-host", "").lower()}  # also behind a reverse proxy
        if origin is not None and urlsplit(origin).netloc.lower() not in own:
            return PlainTextResponse("Cross-origin requests are not allowed", status_code=403)
        if request.url.path == "/api/upload":
            # refuse a too large upload before it is read; one MB of room for the form around the files
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return PlainTextResponse("The upload needs a Content-Length header.", status_code=411)
            if int(length) > settings.max_upload_mb * MB + MB:
                return PlainTextResponse(
                    f"The upload is larger than {settings.max_upload_mb:g} MB (max_upload_mb).", status_code=413)
        return await call_next(request)

    def folder(rel: str, must_exist: bool = True) -> Path:
        path = (root / rel.strip().lstrip("/")).resolve()
        if not inside(path, root):
            raise HTTPException(400, "The folder must be inside the root folder.")
        if must_exist and not path.is_dir():
            raise HTTPException(404, f"Folder not found: {rel or '/'}")
        return path

    def rel(path: Path) -> str:
        return PurePosixPath(path.relative_to(root)).as_posix() if path != root else ""

    def plan_dict(p: Plan) -> Dict[str, Any]:
        def short(path: Optional[Path], base: Path) -> Optional[str]:
            return PurePosixPath(path.relative_to(base)).as_posix() if path else None

        return {
            "folder": rel(p.source_root),
            "dest": rel(p.dest_root),
            "copy": p.copy,
            "rules": p.rules.to_dict(),
            "entries": [
                {"index": i, "source": short(e.source, p.source_root), "status": e.status,
                 "target": short(e.target, p.dest_root), "fields": e.fields, "reason": e.reason}
                for i, e in enumerate(p.entries)
            ],
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info():
        return {"mode": "upload" if upload else "folder", "root": "" if upload else str(root),
                "model": settings.model, "vision_model": settings.vision_model,
                "max_upload_mb": settings.max_upload_mb, "max_files": settings.max_files}

    def folder_mode_only() -> None:
        if upload:
            raise HTTPException(404, "Not available in upload mode.")

    @app.get("/api/folders")
    def folders(path: str = ""):
        folder_mode_only()
        base = folder(path)
        subs = sorted((p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.name.casefold())
        return {"path": rel(base), "parent": rel(base.parent) if base != root else None,
                "folders": [p.name for p in subs]}

    def run(source: Path, request: PlanRequest, dest: Path) -> None:
        def progress(index: int, total: int, path: Path) -> None:
            with lock:
                job.done, job.total, job.current = index - 1, total, path.name

        try:
            result = build_plan(source, request.instruction.strip() or None, dest=dest,
                                recursive=request.recursive, copy=request.copy_files, client=client,
                                progress=progress, **asdict(settings))
        except SortError as exc:
            with lock:
                job.state, job.error = "error", str(exc)
            return
        with lock:
            job.state, job.plan, job.done, job.current = "done", result, job.total, ""

    @app.post("/api/plan")
    def start_plan(request: PlanRequest):
        if upload:
            with lock:
                if not job.uploaded:
                    raise HTTPException(409, "Upload files first.")
            # uploaded files may come in subfolders of a ZIP; they are only planned here, never moved
            request.recursive, request.copy_files, request.dest = True, False, ""
            source = dest = inbox
        else:
            source = folder(request.folder)
            dest = folder(request.dest, must_exist=False) if request.dest.strip() else source
        with lock:
            if job.state == "running":
                raise HTTPException(409, "A plan is already being made.")
            job.state, job.done, job.total, job.current, job.error, job.plan = "running", 0, 0, "", "", None
            job.instruction = request.instruction
        threading.Thread(target=run, args=(source, request, dest), daemon=True).start()
        return {"state": "running"}

    @app.get("/api/job")
    def job_state():
        with lock:
            return {"state": job.state, "done": job.done, "total": job.total, "current": job.current,
                    "error": job.error, "instruction": job.instruction, "upload": job.upload,
                    "uploaded": job.uploaded, "plan": plan_dict(job.plan) if job.plan else None}

    def current_plan() -> Plan:
        with lock:
            if job.state != "done" or job.plan is None:
                raise HTTPException(409, "There is no plan to apply.")
            return job.plan

    def chosen_targets(current: Plan, request: ApplyRequest) -> Dict[int, Path]:
        """Relative target for every selected entry, with the user's edits."""
        if not request.selected:
            raise HTTPException(400, "No file selected.")
        out = {}
        for index in request.selected:
            if not 0 <= index < len(current.entries) or current.entries[index].status != SORT:
                raise HTTPException(400, f"Entry {index} cannot be applied.")
            entry = current.entries[index]
            relative = entry.target.relative_to(current.dest_root)
            if index in request.targets:
                relative = _edited(request.targets[index], entry.source.suffix)
                if relative is None:
                    raise HTTPException(400, f"Empty target for {entry.source.name}.")
            out[index] = relative
        return out

    @app.post("/api/apply")
    def apply(request: ApplyRequest):
        folder_mode_only()
        current = current_plan()
        chosen = Plan(current.source_root, current.dest_root, current.rules, copy=current.copy)
        for index, relative in chosen_targets(current, request).items():
            entry = current.entries[index]
            try:
                chosen.add(entry.source, relative, entry.fields)
            except SortError as exc:
                raise HTTPException(400, str(exc))
        if not chosen.to_sort:
            raise HTTPException(400, "No file selected.")
        try:
            log = chosen.apply()
        except SortError as exc:
            raise HTTPException(409, str(exc))
        with lock:
            job.state, job.plan = "idle", None
        return {"applied": len(chosen.to_sort), "log": rel(log), "dest": rel(chosen.dest_root)}

    @app.post("/api/undo")
    def undo_run(request: UndoRequest):
        folder_mode_only()
        target = folder(request.folder)
        if not (target / LOG_DIR).is_dir():
            raise HTTPException(404, "No run to undo in this folder.")
        try:
            result = undo(target)
        except SortError as exc:
            raise HTTPException(409, str(exc))
        return {"restored": result.restored, "removed_dirs": result.removed_dirs, "problems": result.problems}

    if upload:
        @app.post("/api/upload")
        def upload_files(request: Request, files: List[UploadFile] = File(...)):
            with lock:
                if job.state == "running":
                    raise HTTPException(409, "A plan is being made. Wait until it is done.")
                job.state, job.plan, job.uploaded, job.upload, job.error = "idle", None, 0, "", ""
            try:
                count = store([(f.filename or "", f.file) for f in files], inbox, settings)
            except SortError as exc:
                raise HTTPException(413 if "MB" in str(exc) else 400, str(exc))
            names = [Path(f.filename or "").stem for f in files]
            with lock:
                job.uploaded = count
                job.upload = clean_part(names[0] if len(names) == 1 else "documents") or "documents"
            return {"uploaded": count, "name": job.upload}

        @app.post("/api/download")
        def download(request: ApplyRequest):
            current = current_plan()
            targets = chosen_targets(current, request)
            result = root / "result.zip"
            write_zip(zip_names(current, targets, targets), result)
            return FileResponse(result, media_type="application/zip", filename=f"{job.upload}_sorted.zip")

    return app

