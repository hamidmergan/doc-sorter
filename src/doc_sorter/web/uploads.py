"""Upload mode: unpack uploaded files and ZIPs safely, and pack the sorted result as a ZIP.

The uploaded files are never changed; the result ZIP is built from them with new names.
"""

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Dict, Iterable, List, Optional, Set, Tuple

from ..config import Settings
from ..errors import LimitError, UnsupportedFileError
from ..plan import SORT, Plan, free_target
from ..rules import clean_part

CHUNK = 1024 * 1024
MB = 1024 * 1024
# junk that zip tools on macOS and Windows add
JUNK = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


class _Budget:
    """Counts bytes against a limit while they are written."""

    def __init__(self, limit_mb: float, setting: str, what: str):
        self.left = limit_mb * MB
        self.limit_mb = limit_mb
        self.setting = setting
        self.what = what

    def take(self, size: int) -> None:
        self.left -= size
        if self.left < 0:
            raise LimitError(f"{self.what} is larger than {self.limit_mb:g} MB ({self.setting}).")


def _safe_parts(name: str) -> Optional[List[str]]:
    """The path parts of an archive member, cleaned; None for junk or nothing usable."""
    raw = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not raw or any(p.lower() in JUNK or p.startswith("._") for p in raw):
        return None
    parts = [clean_part(p) for p in raw]
    return parts if all(parts) else None


def _copy(src: BinaryIO, dst: Path, budgets: List[_Budget]) -> None:
    with open(dst, "xb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            for budget in budgets:
                budget.take(len(chunk))
            out.write(chunk)


class _Inbox:
    """Writes uploaded documents below one folder, with unique names and shared limits."""

    def __init__(self, folder: Path, settings: Settings):
        self.folder = folder
        self.settings = settings
        self.count = 0
        self.taken: Set[str] = set()
        self.unpacked = _Budget(settings.max_unpacked_mb, "max_unpacked_mb", "The unpacked upload")

    def add(self, parts: List[str], stream: BinaryIO, extra: List[_Budget]) -> None:
        self.count += 1
        if self.count > self.settings.max_files:
            raise LimitError(f"More than {self.settings.max_files} files uploaded (max_files).")
        path = free_target(self.folder.joinpath(*parts), self.taken)
        self.taken.add(str(path).casefold())
        path.parent.mkdir(parents=True, exist_ok=True)
        one = _Budget(self.settings.max_file_mb, "max_file_mb", parts[-1])
        _copy(stream, path, [self.unpacked, one] + extra)


def _unzip(archive: Path, inbox: _Inbox) -> None:
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    continue
                parts = _safe_parts(info.filename)
                if parts is None:
                    continue
                if info.flag_bits & 0x1:
                    raise UnsupportedFileError("Encrypted ZIP files are not supported.")
                # the real size is counted while unpacking; the size in the header can lie
                with zf.open(info) as member:
                    inbox.add(parts, member, [])
    except zipfile.BadZipFile as exc:
        raise UnsupportedFileError(f"{archive.name} is not a valid ZIP file: {exc}")


def store(files: Iterable[Tuple[str, BinaryIO]], folder: Path, settings: Settings) -> int:
    """Save uploaded files into an empty folder; ZIPs are unpacked. Returns the number of files."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    staging = folder.parent / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    uploaded = _Budget(settings.max_upload_mb, "max_upload_mb", "The upload")
    inbox = _Inbox(folder, settings)
    try:
        for name, stream in files:
            parts = _safe_parts(PurePosixPath(name.replace("\\", "/")).name)
            if parts is None:
                continue
            if parts[-1].lower().endswith(".zip"):
                archive = free_target(staging / parts[-1], set())
                _copy(stream, archive, [uploaded])
                _unzip(archive, inbox)
                archive.unlink()
            else:
                inbox.add(parts, stream, [uploaded])
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _unwrap(folder)
    return inbox.count


def _unwrap(folder: Path) -> None:
    """A ZIP of one folder unpacks to that folder alone; its content is moved up, so results are not nested."""
    children = list(folder.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return
    wrapper = children[0].rename(folder / ".unwrap")
    for child in wrapper.iterdir():
        child.rename(folder / child.name)
    wrapper.rmdir()


def zip_names(plan: Plan, selected: Iterable[int], targets: Dict[int, Path]) -> List[Tuple[Path, str]]:
    """(file, name in the ZIP) for every uploaded file: selected ones at their new place, the rest unchanged."""
    root = plan.source_root
    chosen = set(selected)
    moved, kept = [], []
    for index, entry in enumerate(plan.entries):
        if entry.status == SORT and index in chosen:
            moved.append((entry.source, targets.get(index) or entry.target.relative_to(plan.dest_root)))
        else:
            kept.append((entry.source, entry.source.relative_to(root)))
    names: List[Tuple[Path, str]] = []
    taken: Set[str] = set()
    for source, relative in kept + moved:
        name = PurePosixPath(*relative.parts)
        candidate, n = name, 1
        while str(candidate).casefold() in taken:
            candidate = name.with_name(f"{name.stem} ({n}){name.suffix}")
            n += 1
        taken.add(str(candidate).casefold())
        names.append((source, str(candidate)))
    return names


def write_zip(names: List[Tuple[Path, str]], out: Path) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source, name in sorted(names, key=lambda item: item[1].casefold()):
            zf.write(source, name)
