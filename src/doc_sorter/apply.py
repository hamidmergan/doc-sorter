"""Run operations safely, keep a log, and undo a run from its log.

Files are never overwritten and never deleted. The log is written before the first change.
"""

import filecmp
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union

from .errors import PlanError
from .plan import LOG_DIR, _key, inside

LOG_VERSION = 1


@dataclass
class UndoResult:
    log: Path
    restored: int = 0
    removed_dirs: int = 0
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    # write to a temporary file first, so a crash never leaves a half-written log
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _check(ops: List[Dict[str, str]], source_root: Path, dest_root: Path) -> None:
    """Refuse the whole run if a single operation is unsafe or no longer fits the files on disk."""
    planned_dirs = set()
    targets = set()
    for op in ops:
        kind = op.get("op")
        if kind == "mkdir":
            path = Path(op["path"])
            if path.exists() and not path.is_dir():
                raise PlanError(f"{path} exists and is not a folder.")
            planned_dirs.add(_key(path))
            continue
        if kind not in ("move", "copy"):
            raise PlanError(f"Unknown operation: {kind!r}")
        src, dst = Path(op["from"]), Path(op["to"])
        if not src.is_absolute() or not dst.is_absolute():
            raise PlanError("Plan paths must be absolute.")
        if src.is_symlink() or not src.is_file():
            raise PlanError(f"{src} is missing or not a regular file. Build the plan again.")
        if not inside(src, source_root):
            raise PlanError(f"{src} is outside the source folder {source_root}.")
        if not inside(dst, dest_root) or LOG_DIR in dst.resolve().relative_to(dest_root.resolve()).parts:
            raise PlanError(f"{dst} is outside the target folder {dest_root}.")
        if os.path.lexists(dst):
            raise PlanError(f"{dst} already exists. Build the plan again.")
        if _key(dst) in targets:
            raise PlanError(f"{dst} is planned twice.")
        targets.add(_key(dst))
        parent = dst.parent
        if not parent.is_dir() and _key(parent) not in planned_dirs:
            raise PlanError(f"Folder {parent} does not exist and is not planned.")


def _copy_new(src: Path, dst: Path) -> None:
    """Copy src to dst; fails if dst exists (opened with 'x')."""
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        shutil.copyfileobj(fin, fout)
    shutil.copystat(src, dst)


def _move_new(src: Path, dst: Path) -> None:
    """Move src to dst without ever replacing an existing dst."""
    try:
        # a hard link fails if dst exists, unlike rename on POSIX
        os.link(src, dst)
    except FileExistsError:
        raise PlanError(f"{dst} already exists.")
    except OSError:
        # other drive, or a file system without hard links
        _copy_new(src, dst)
        if dst.stat().st_size != src.stat().st_size:
            raise PlanError(f"Copy of {src} to {dst} is incomplete; the original was kept.")
    os.unlink(src)


def _new_log(dest_root: Path) -> Path:
    folder = dest_root / LOG_DIR
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path, n = folder / f"log-{stamp}.json", 2
    while path.exists():
        path, n = folder / f"log-{stamp}-{n}.json", n + 1
    return path


def execute(ops: List[Dict[str, str]], source_root: Path, dest_root: Path) -> Path:
    """Run the operations in order and return the log path."""
    if not any(op["op"] in ("move", "copy") for op in ops):
        raise PlanError("Nothing to do.")
    _check(ops, source_root, dest_root)

    # the target folder must exist to hold the log; record it, so undo can remove it again
    created_root = []
    folder = dest_root
    while not folder.exists():
        created_root.append(folder)
        folder = folder.parent
    for folder in reversed(created_root):
        folder.mkdir()

    entries = [{"op": "mkdir", "path": str(d), "done": True} for d in reversed(created_root)]
    entries += [dict(op, done=False) for op in ops if not (op["op"] == "mkdir" and Path(op["path"]).exists())]
    log_path = _new_log(dest_root)
    log = {
        "version": LOG_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "dest_root": str(dest_root),
        "undone": False,
        "ops": entries,
    }
    _write_json(log_path, log)

    for entry in entries:
        if entry["done"]:
            continue
        try:
            if entry["op"] == "mkdir":
                Path(entry["path"]).mkdir()
            elif entry["op"] == "move":
                _move_new(Path(entry["from"]), Path(entry["to"]))
            else:
                _copy_new(Path(entry["from"]), Path(entry["to"]))
        except (OSError, PlanError) as exc:
            raise PlanError(f"Stopped at {entry.get('from', entry.get('path'))}: {exc}. "
                            f"Finished steps are in {log_path} and can be undone.")
        entry["done"] = True
        _write_json(log_path, log)
    return log_path


def find_log(path: Union[str, Path]) -> Path:
    """A log file, or the newest log in <folder>/.doc-sorter that was not undone yet."""
    path = Path(path).expanduser()
    if path.is_file():
        return path
    logs = sorted((path / LOG_DIR).glob("log-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for log in logs:
        try:
            if not json.loads(log.read_text(encoding="utf-8")).get("undone"):
                return log
        except (OSError, ValueError):
            continue
    raise PlanError(f"No run to undo in {path}. Pass the target folder of the run or its log file.")


def undo(path: Union[str, Path]) -> UndoResult:
    """Revert a run. Files that changed since then are kept and reported, nothing is overwritten."""
    log_path = find_log(path)
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
        ops = log["ops"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PlanError(f"Cannot read log {log_path}: {exc}")
    if log.get("undone"):
        raise PlanError(f"{log_path} was already undone.")

    result = UndoResult(log_path)
    for entry in reversed(ops):
        if not entry.get("done"):
            continue
        kind = entry.get("op")
        if kind == "mkdir":
            try:
                Path(entry["path"]).rmdir()  # only works if the folder is empty
                result.removed_dirs += 1
            except OSError:
                pass
            continue
        src, dst = Path(entry["from"]), Path(entry["to"])
        if not dst.is_file():
            result.problems.append(f"{dst} is gone, nothing to restore.")
        elif kind == "move":
            if os.path.lexists(src):
                result.problems.append(f"{src} exists again, {dst} was kept.")
            elif not src.parent.is_dir():
                result.problems.append(f"Folder {src.parent} is gone, {dst} was kept.")
            else:
                try:
                    _move_new(dst, src)
                    result.restored += 1
                except (OSError, PlanError) as exc:
                    result.problems.append(f"{dst} could not be moved back: {exc}")
        elif src.is_file() and filecmp.cmp(src, dst, shallow=False):
            # the copy is identical to the original, so removing it loses nothing
            dst.unlink()
            result.restored += 1
        else:
            result.problems.append(f"{dst} differs from {src} or the original is gone, it was kept.")

    log["undone"] = True
    log["undone_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(log_path, log)
    return result
