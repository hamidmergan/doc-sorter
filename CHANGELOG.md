# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-23

### Added

- Sorting and renaming of PDF, images, DOCX, TXT and MD by content
- Sorting instruction in plain language, turned into a rule set that can be saved and reused
- Language setting for folder names and titles (`--language`), by default the language of the instruction
- Scans and images are read with a vision model through doc-extract
- Plan with preview, confirmation, dry run, copy mode and a separate target folder
- Log for every run and undo
- Safe names: no overwriting, no deleting, numbered names on conflicts, targets stay inside the target folder
- Command-line tool and Python API (`plan`, `make_rules`, `undo`)
- Works with any OpenAI-compatible server, with or without an API key
- Local web page (`doc-sorter serve`, extra `ui`) with preview, selection, editable targets and undo
- Upload mode (`doc-sorter serve --upload`): ZIP or files in, sorted ZIP out, with size limits and safe unpacking
- Dockerfile that starts the web page in upload mode

[Unreleased]: https://github.com/hamidmergan/doc-sorter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hamidmergan/doc-sorter/releases/tag/v0.1.0
