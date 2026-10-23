# PLAN

Sort a folder of documents by content. The model only returns fields (JSON),
the code builds the plan (mkdir/move), the user confirms, every run is logged for undo.

## Decisions

- Default target is the source folder itself, `--dest` for another folder
- Only top level by default, `--recursive` for subfolders
- Instruction in plain language becomes a rule set once (shown, can be saved with `--save-rules`)
- Per file the model returns `category, date, sender, title`; paths come from templates
- Never overwrite, never delete; conflicts get ` (1)`, ` (2)`
- Log in `<dest>/.doc-sorter/` is written before the first move; `--undo` reverts it
- Scans and images go through doc-extract with a vision model

## Progress

- [x] 1 Scaffold: pyproject, config, errors, CI
- [x] 2 Text reading (txt, md, docx, pdf, scans) and fields
- [x] 3 Rules and plan (conflicts, path guard)
- [x] 4 Apply, log, undo
- [x] 5 CLI with preview
- [x] 6 README, CHANGELOG
- [x] 7 Tests green, local git commit (no push yet)

## Web UI and Docker

- `doc-sorter serve` starts a local page (FastAPI, one HTML page), optional extra `doc-sorter[ui]`
- The page only reaches folders under `--root` (default: home folder, in Docker `/data`)
- Flow: folder + instruction -> preview with progress -> tick files, edit targets -> apply -> undo
- Host and origin guard like invoice-extract; warning without loopback, no login
- Dockerfile runs `serve --root /data`, folder mounted with `-v`

- [x] 8 Web API (`web/app.py`) with tests
- [x] 9 Page (`web/static`)
- [x] 10 `serve` command
- [x] 11 Dockerfile, README section, tested with a real container
