"""A sorting plan: which file goes where. Building it never changes a file."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from .errors import PlanError
from .rules import Rules

LOG_DIR = ".doc-sorter"
PLAN_VERSION = 1

# status of an entry
SORT = "sort"  # gets a new place
KEEP = "keep"  # stays where it is (no matching rule, or already in place)
SKIP = "skip"  # not read (unsupported type)
ERROR = "error"  # reading or the model failed


@dataclass
class Entry:
    source: Path
    status: str
    target: Optional[Path] = None
    fields: Dict[str, Optional[str]] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": str(self.source),
            "status": self.status,
            "target": str(self.target) if self.target else None,
            "fields": self.fields,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entry":
        target = data.get("target")
        return cls(
            source=Path(data["source"]),
            status=str(data.get("status", KEEP)),
            target=Path(target) if target else None,
            fields=dict(data.get("fields") or {}),
            reason=str(data.get("reason") or ""),
        )


def _key(path: Path) -> str:
    # case-insensitive, because macOS and Windows file systems usually are
    return str(path).casefold()


def free_target(path: Path, taken: Set[str]) -> Path:
    """path, or 'name (1).pdf', 'name (2).pdf', ... if it exists or is already planned."""
    candidate, n = path, 1
    while candidate.exists() or _key(candidate) in taken:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass
class Plan:
    source_root: Path
    dest_root: Path
    rules: Rules
    copy: bool = False
    entries: List[Entry] = field(default_factory=list)

    @property
    def to_sort(self) -> List[Entry]:
        return [e for e in self.entries if e.status == SORT]

    @property
    def errors(self) -> List[Entry]:
        return [e for e in self.entries if e.status == ERROR]

    def add(self, source: Path, relative: Optional[Path], fields: Dict[str, Optional[str]]) -> Entry:
        """Add a file with its relative target; resolves name conflicts against disk and the plan."""
        if relative is None:
            entry = Entry(source, KEEP, fields=fields, reason="no matching rule")
        else:
            wanted = self.dest_root / relative
            if not inside(wanted, self.dest_root) or LOG_DIR in relative.parts:
                raise PlanError(f"Unsafe target {relative} for {source.name}.")
            if _key(wanted) == _key(source):
                entry = Entry(source, KEEP, fields=fields, reason="already in place")
            else:
                taken = {_key(e.target) for e in self.to_sort if e.target}
                entry = Entry(source, SORT, target=free_target(wanted, taken), fields=fields)
        self.entries.append(entry)
        return entry

    def ops(self) -> List[Dict[str, str]]:
        """The operations to run: mkdir for every missing folder (parents first), then move or copy."""
        dirs: List[Path] = []
        seen: Set[str] = set()
        for entry in self.to_sort:
            chain = []
            folder = entry.target.parent
            while not folder.exists() and _key(folder) not in seen:
                chain.append(folder)
                seen.add(_key(folder))
                folder = folder.parent
            dirs.extend(reversed(chain))
        dirs.sort(key=lambda p: len(p.parts))
        op = "copy" if self.copy else "move"
        return [{"op": "mkdir", "path": str(d)} for d in dirs] + [
            {"op": op, "from": str(e.source), "to": str(e.target)} for e in self.to_sort
        ]

    def preview(self) -> str:
        def src(p: Path) -> str:
            return _relative(p, self.source_root)

        def dst(p: Path) -> str:
            return _relative(p, self.dest_root)

        lines = []
        sort = self.to_sort
        verb = "Copy" if self.copy else "Move"
        if self.dest_root != self.source_root:
            lines.append(f"Target folder: {self.dest_root}")
        lines.append(f"{verb} {len(sort)} file(s):" if sort else "Nothing to sort.")
        width = max([len(src(e.source)) for e in sort] + [0])
        for e in sort:
            lines.append(f"  {src(e.source):<{width}}  ->  {dst(e.target)}")
        for status, title in ((KEEP, "Unchanged"), (SKIP, "Skipped"), (ERROR, "Errors")):
            group = [e for e in self.entries if e.status == status]
            if group:
                lines.append(f"{title} ({len(group)}):")
                lines.extend(f"  {src(e.source)}  ({e.reason})" for e in group)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": PLAN_VERSION,
            "source_root": str(self.source_root),
            "dest_root": str(self.dest_root),
            "copy": self.copy,
            "rules": self.rules.to_dict(),
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Plan":
        try:
            plan = cls(
                source_root=Path(data["source_root"]),
                dest_root=Path(data["dest_root"]),
                rules=Rules.from_dict(data["rules"]),
                copy=bool(data.get("copy", False)),
                entries=[Entry.from_dict(e) for e in data["entries"]],
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise PlanError(f"The plan is incomplete: {exc}")
        for entry in plan.to_sort:
            if entry.target is None:
                raise PlanError(f"The plan has no target for {entry.source}.")
        return plan

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Plan":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanError(f"Cannot read plan from {path}: {exc}")
        return cls.from_dict(data)

    def apply(self) -> Path:
        """Run the plan and return the path of the log. Nothing runs if any check fails."""
        from .apply import execute

        return execute(self.ops(), self.source_root, self.dest_root)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
