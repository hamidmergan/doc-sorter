import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from . import config
from .apply import undo
from .core import plan as build_plan
from .errors import SortError
from .plan import Plan
from .rules import Rules


LOOPBACK = ("127.0.0.1", "localhost", "::1")
UI_HINT = (
    'The web page needs extra packages: pip install '
    '"doc-sorter[ui] @ git+https://github.com/hamidmergan/doc-sorter.git"'
)
NO_LOGIN = "Warning: the page has no login. Anyone who can reach this port can use it."


def _model_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base-url", help="URL of the OpenAI-compatible server, e.g. http://localhost:11434/v1")
    p.add_argument("--model", help="Name of the model")
    p.add_argument("--vision-model", help="Model for scans and images. Default: --model")
    p.add_argument("--api-key", help="API key, if the server requires one")
    p.add_argument("--max-files", type=int)
    p.add_argument("--max-file-mb", type=float)
    p.add_argument("--max-chars", type=int, help="Characters of text per document sent to the model")
    p.add_argument("--scan-pages", type=int, help="Pages of a scan that are read")
    p.add_argument("--timeout", type=float, help="Seconds per request")
    p.add_argument("--language", help='Language of folder names and titles, e.g. "English". '
                   "Default: the language of the instruction")


def _model_options(args: argparse.Namespace) -> dict:
    keys = ("base_url", "model", "vision_model", "api_key", "max_files", "max_file_mb", "max_chars",
            "scan_pages", "timeout", "language")
    return {k: getattr(args, k) for k in keys}


def _serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="doc-sorter serve", description="Start the local web page.")
    p.add_argument("--root", help="Only folders below this one can be sorted. "
                   "Default: DOC_SORTER_ROOT or your home folder")
    p.add_argument("--upload", action="store_true",
                   help="Upload mode: files or ZIPs come from the browser and go back as a sorted ZIP. "
                   "No folder on this computer is touched")
    p.add_argument("--max-upload-mb", type=float, help="Largest upload in upload mode")
    p.add_argument("--max-unpacked-mb", type=float, help="Largest unpacked size of an upload")
    p.add_argument("--host", default="127.0.0.1", help="Default: 127.0.0.1 (this computer only)")
    p.add_argument("--port", type=int, default=8000)
    _model_args(p)
    return p


def _serve(argv: List[str]) -> int:
    args = _serve_parser().parse_args(argv)
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError:
        _err(UI_HINT)
        return 2
    root = args.root or os.environ.get("DOC_SORTER_ROOT") or str(Path.home())
    try:
        app = create_app(
            config.resolve(**_model_options(args), max_upload_mb=args.max_upload_mb,
                           max_unpacked_mb=args.max_unpacked_mb),
            Path(root),
            upload=args.upload,
            # on loopback, only accept our own host names, so other web pages cannot reach it via DNS rebinding
            allowed_hosts=list(LOOPBACK) if args.host in LOOPBACK else None,
        )
    except SortError as exc:
        _err(f"Error: {exc}")
        return 2
    if args.host not in LOOPBACK:
        _err(NO_LOGIN)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="doc-sorter",
        description="Sort and rename documents by content. Shows a plan first and changes nothing "
        "until you confirm. Every run can be undone.",
        epilog='Example: doc-sorter ~/Downloads "Invoices by year, contracts separately, the rest in Other". '
        "Run 'doc-sorter serve' for the web page.",
    )
    p.add_argument("folder", nargs="?", help="Folder with the documents")
    p.add_argument("instruction", nargs="?", help="How to sort, in plain language (optional)")
    p.add_argument("--dest", help="Target folder. Default: sort inside the folder itself")
    p.add_argument("--copy", action="store_true", help="Copy files instead of moving them")
    p.add_argument("-r", "--recursive", action="store_true", help="Include subfolders")
    p.add_argument("--dry-run", action="store_true", help="Only show the plan")
    p.add_argument("-y", "--yes", action="store_true", help="Apply without asking")
    p.add_argument("--rules", help="Use a saved rules file instead of an instruction")
    p.add_argument("--save-rules", help="Save the rules to this JSON file for later runs")
    p.add_argument("--save-plan", help="Save the plan to this JSON file")
    p.add_argument("--from-plan", help="Apply a saved plan file")
    p.add_argument("--undo", metavar="PATH", help="Undo the last run in this target folder, or a given log file")
    _model_args(p)
    p.add_argument("--version", action="version", version=f"doc-sorter {__version__}")
    return p


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _rules_text(rules: Rules) -> str:
    lines = ["Rules:"]
    for c in rules.categories:
        lines.append(f"  {c.name}: {c.folder}/{c.filename}" + (f"  ({c.description})" if c.description else ""))
    if rules.fallback:
        name = "New categories" if rules.open else "Everything else"
        lines.append(f"  {name}: {rules.fallback.folder}/{rules.fallback.filename}")
    else:
        lines.append("  Everything else: stays in place")
    return "\n".join(lines)


def _confirm(count: int, copy: bool) -> bool:
    if not sys.stdin.isatty():
        _err("Not applied: no terminal to ask. Use --yes to apply without asking.")
        return False
    answer = input(f"{'Copy' if copy else 'Move'} {count} file(s)? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _apply(result: Plan, yes: bool) -> int:
    count = len(result.to_sort)
    if not count:
        return 1 if result.errors else 0
    if not yes and not _confirm(count, result.copy):
        _err("Nothing was changed.")
        return 1
    log = result.apply()
    _err(f"Done. Log: {log}")
    _err(f"Undo with: doc-sorter --undo {result.dest_root}")
    return 1 if result.errors else 0


def _undo(path: str) -> int:
    result = undo(path)
    _err(f"Restored {result.restored} file(s), removed {result.removed_dirs} empty folder(s).")
    for problem in result.problems:
        _err(f"Kept: {problem}")
    return 0 if result.ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    args = _parser().parse_args(argv)
    try:
        if args.undo:
            return _undo(args.undo)
        if args.from_plan:
            result = Plan.load(args.from_plan)
            print(result.preview())
            return 0 if args.dry_run else _apply(result, args.yes)
        if not args.folder:
            _err("Give a folder, --from-plan or --undo. See doc-sorter --help.")
            return 2
        if args.instruction and args.rules:
            _err("Give either an instruction or --rules, not both.")
            return 2

        def progress(index: int, total: int, path: Path) -> None:
            _err(f"[{index}/{total}] {path.name}")

        result = build_plan(
            args.folder,
            args.instruction,
            dest=args.dest,
            rules=args.rules,
            recursive=args.recursive,
            copy=args.copy,
            progress=progress,
            **_model_options(args),
        )
        print(_rules_text(result.rules))
        print()
        print(result.preview())
        if args.save_rules:
            result.rules.save(args.save_rules)
            _err(f"Rules saved to {args.save_rules}")
        if args.save_plan:
            result.save(args.save_plan)
            _err(f"Plan saved to {args.save_plan}")
        if args.dry_run:
            return 1 if result.errors else 0
        return _apply(result, args.yes)
    except SortError as exc:
        _err(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
