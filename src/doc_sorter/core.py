from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from . import config, llm
from .errors import ConfigError, LimitError, SortError
from .plan import ERROR, LOG_DIR, SKIP, Entry, Plan, inside
from .read import is_supported, read_text
from .rules import Rules, default_rules, target_path

PathLike = Union[str, Path]
Progress = Callable[[int, int, Path], None]

# files that are never touched
IGNORED_NAMES = {"desktop.ini", "thumbs.db"}


def _ignored(path: Path) -> bool:
    name = path.name
    return name.startswith((".", "~$")) or name.lower() in IGNORED_NAMES or path.is_symlink()


def collect(source_root: Path, dest_root: Path, recursive: bool = False) -> Tuple[List[Path], List[Path]]:
    """Supported files and skipped (unsupported) files, in a stable order."""
    files, skipped = [], []
    pending = [source_root]
    while pending:
        folder = pending.pop(0)
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if _ignored(path) or path.name == LOG_DIR:
                continue
            if path.is_dir():
                # with a separate target inside the source folder, already sorted files are left alone
                if recursive and not (dest_root != source_root and inside(path, dest_root)):
                    pending.append(path)
            elif path.is_file():
                (files if is_supported(path) else skipped).append(path)
    return files, skipped


def _rules(rules: Union[Rules, dict, PathLike, None]) -> Optional[Rules]:
    if rules is None or isinstance(rules, Rules):
        return rules
    if isinstance(rules, dict):
        return Rules.from_dict(rules)
    return Rules.load(rules)


def make_rules(
    instruction: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    client=None,
    **settings,
) -> Rules:
    """Turn a sorting instruction in plain language into a rule set (one model call)."""
    resolved = config.resolve(base_url=base_url, model=model, api_key=api_key, **settings)
    return llm.make_rules(instruction, client or llm.make_client(resolved), resolved)


def plan(
    source: PathLike,
    instruction: Optional[str] = None,
    *,
    dest: Optional[PathLike] = None,
    rules: Union[Rules, dict, PathLike, None] = None,
    recursive: bool = False,
    copy: bool = False,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    client=None,
    progress: Optional[Progress] = None,
    **settings,
) -> Plan:
    """Read every document in a folder and plan where it goes. No file is changed.

    Without instruction and rules, the model picks the categories itself.
    Files that fail are kept in the plan with status "error"; the rest continues.
    """
    source_root = Path(source).expanduser().resolve()
    if not source_root.is_dir():
        raise ConfigError(f"Folder not found: {source}")
    dest_root = Path(dest).expanduser().resolve() if dest else source_root
    if dest_root.exists() and not dest_root.is_dir():
        raise ConfigError(f"Target {dest_root} exists and is not a folder.")
    if instruction and rules is not None:
        raise ConfigError("Give either an instruction or rules, not both.")

    resolved = config.resolve(base_url=base_url, model=model, api_key=api_key, **settings)
    files, skipped = collect(source_root, dest_root, recursive)
    if len(files) > resolved.max_files:
        raise LimitError(
            f"{len(files)} documents found. The limit is {resolved.max_files} (max_files)."
        )

    client = client or llm.make_client(resolved)
    if instruction:
        rule_set = llm.make_rules(instruction, client, resolved)
    else:
        rule_set = _rules(rules) or default_rules()
        if resolved.language:
            rule_set.language = resolved.language

    result = Plan(source_root, dest_root, rule_set, copy=copy)
    for index, path in enumerate(files, 1):
        if progress:
            progress(index, len(files), path)
        try:
            text, _ = read_text(path, resolved, client)
            fields = llm.classify(text, path.name, rule_set, client, resolved)
            relative = target_path(rule_set, fields, path)
            result.add(path, Path(relative) if relative is not None else None, fields)
        except SortError as exc:
            result.entries.append(Entry(path, ERROR, reason=str(exc)))
    result.entries.extend(Entry(p, SKIP, reason="unsupported file type") for p in skipped)
    return result
