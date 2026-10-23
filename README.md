# doc-sorter

A lightweight Python utility that sorts a folder of documents by their content. It reads each file, asks a model of your choice what kind of document it is, and moves it into folders with a clear name, for example `Invoices/2026/2026-03-15_Telekom_Invoice.pdf`. You can describe how to sort in plain language. Scans and photos are read with a vision model.

The model never touches your files. It only returns a few fields per document (category, date, sender, title). doc-sorter builds the plan from them, shows it to you, and changes nothing until you confirm. Every run is logged and can be undone.

## Example

A folder with mixed, badly named files:

```
inbox/
├── contract_final_v3.pdf    PDF with text (employment contract)
├── cover_letter.md          Markdown (job application)
├── Document1.docx           Word (tenancy agreement)
├── IMG_2041.jpg             photo of a receipt
├── invoice_03.pdf           PDF with text (Telekom invoice)
├── notes.txt                plain text (notes)
├── order_details.pdf        PDF with text (Amazon invoice)
├── scan_0001.pdf            scanned PDF, no text layer (electricity statement)
└── song.mp3                 not a document
```

```bash
doc-sorter inbox "Invoices by year, contracts separately, the rest in Other"
```

Afterwards:

```
inbox/
├── Contracts/
│   ├── 2023-07-10_Greenfield Properties Ltd_Tenancy Agreement.docx
│   └── 2024-02-12_Example Software Ltd_Employment Contract.pdf
├── Invoices/
│   ├── 2025/
│   │   └── 2025-11-02_Amazon EU S.a r.l_Invoice.pdf
│   └── 2026/
│       ├── 2026-01-20_City Energy Ltd_Electricity statement.pdf
│       ├── 2026-03-15_Telekom_Invoice.pdf
│       └── 2026-10-17_TESCO_Invoice.jpg
├── Other/
│   ├── 2026-09-05_Jane Doe_Application Letter.md
│   └── Notes.txt
├── song.mp3                 left in place
└── .doc-sorter/             log for undo
```

`doc-sorter --undo inbox` puts everything back. Folder names and titles follow the language of your instruction, so a German instruction gives German folders. Use `--language English` (or any other language) to choose it yourself.

doc-sorter currently supports:

- PDF (with text, or scanned)
- Images (PNG, JPG, WEBP, GIF, BMP, TIFF)
- Word (DOCX)
- Text and Markdown (TXT, MD)

Other files are left where they are.

## Prerequisites

- Python 3.9 or newer
- A server with an OpenAI-compatible API (for example Ollama, vLLM, LM Studio, a university server or a cloud provider)
- For scans and images, a **vision-capable model**. Documents with text work with a text-only model.
- An API key is optional. Many self-hosted servers do not need one.

If you run your own model, your documents stay on your own network. Nothing leaves your machine or data center.

## Installation

```bash
pip install doc-sorter
```

Alternatively, you can install it from the source:

```bash
git clone https://github.com/hamidmergan/doc-sorter.git
cd doc-sorter
pip install .
```

## Usage

### Command-Line

Show the plan without changing anything:

```bash
doc-sorter ~/Downloads "Invoices by year, contracts separately, the rest in Other" --dry-run \
  --base-url http://localhost:11434/v1 --model your-model
```

```
Rules:
  Invoices: Invoices/{year}/{date}_{sender}_{title}  (All invoice documents)
  Contracts: Contracts/{date}_{sender}_{title}  (All contract documents)
  Everything else: Other/{date}_{sender}_{title}

Move 8 file(s):
  contract_final_v3.pdf  ->  Contracts/2024-02-12_Example Software Ltd_Employment Contract.pdf
  invoice_03.pdf         ->  Invoices/2026/2026-03-15_Telekom_Invoice.pdf
  scan_0001.pdf          ->  Invoices/2026/2026-01-20_City Energy Ltd_Electricity statement.pdf
  ...
Skipped (1):
  song.mp3  (unsupported file type)
```

Leave out `--dry-run` and doc-sorter asks before it moves anything. Use `--yes` to skip the question.

Without an instruction, the model picks the categories itself (`<Category>/<year>/<date>_<sender>_<title>`):

```bash
doc-sorter ~/Downloads --base-url http://localhost:11434/v1 --model your-model
```

Undo the last run:

```bash
doc-sorter --undo ~/Downloads
```

More options:

| Flag | Effect |
| --- | --- |
| `--dest DIR` | Sort into another folder instead of the folder itself |
| `--copy` | Copy instead of move, the originals stay |
| `-r`, `--recursive` | Include subfolders |
| `--dry-run` | Only show the plan |
| `-y`, `--yes` | Apply without asking |
| `--language NAME` | Language of folder names and titles, e.g. `English`. Default: the language of the instruction |
| `--vision-model NAME` | Model for scans and images, if it differs from `--model` |
| `--save-rules FILE` | Save the rules made from your instruction |
| `--rules FILE` | Reuse saved rules (no instruction needed) |
| `--save-plan FILE`, `--from-plan FILE` | Save a plan, check or edit it, apply it later |
| `--undo PATH` | Undo the last run in a target folder, or a given log file |

The exit code is 1 if at least one file failed or nothing was applied, 2 for wrong settings.

### Web Page

```bash
pip install "doc-sorter[ui]"
doc-sorter serve --base-url http://localhost:11434/v1 --model your-model
```

Then open http://127.0.0.1:8000. Pick a folder, describe how to sort, and press Preview. You can untick files or edit a target before you apply, and undo the last run with one click.

![The doc-sorter web page with a plan for eight documents](https://raw.githubusercontent.com/hamidmergan/doc-sorter/main/docs/ui.png)

The page can only reach folders below `--root` (default: your home folder). It listens on this computer only. With `--host 0.0.0.0` it is reachable from your network, but it has no login, so do not expose it to the internet.

### Docker

The container runs the web page in upload mode: drop a ZIP or some documents into the page, check the preview, and download the sorted result as a ZIP. Your original files are not changed, and no folder has to be shared with the container.

```bash
docker build -t doc-sorter .
docker run -p 127.0.0.1:8000:8000 \
  -e DOC_SORTER_BASE_URL=http://host.docker.internal:11434/v1 \
  -e DOC_SORTER_MODEL=your-model \
  doc-sorter
```

Then open http://127.0.0.1:8000. `host.docker.internal` reaches a model server such as Ollama on the same computer (on Linux, add `--add-host=host.docker.internal:host-gateway`). The page has no login, so do not expose the port to the internet.

```
inbox.zip                     inbox_sorted.zip
├── invoice_03.pdf            ├── Contracts/
├── Document1.docx     ──►    │   └── 2023-07-10_Greenfield Properties Ltd_Tenancy Agreement.docx
├── notes.txt                 ├── Invoices/2026/2026-03-15_Telekom_Invoice.pdf
└── song.mp3                  ├── Other/Notes.txt
                              └── song.mp3
```

Files that are not sorted stay in the ZIP at their old place. If the ZIP holds a single folder, its content is used directly. Limits: 200 MB upload, 1 GB unpacked, 500 files, 50 MB per file (see Settings). Paths that try to leave the ZIP, links and encrypted ZIPs are refused. Uploads are kept in a temporary folder and removed when the container stops.

The same mode works without Docker: `doc-sorter serve --upload`.

### Python API

```python
import doc_sorter

plan = doc_sorter.plan(
    "~/Downloads",
    "Invoices by year, contracts separately, the rest in Other",
    base_url="http://localhost:11434/v1",
    model="your-model",
)
print(plan.preview())
log = plan.apply()

doc_sorter.undo(log)
```

`plan()` only reads. Each entry has the source, the target, the fields from the model and a status (`sort`, `keep`, `skip`, `error`). If one file fails, the others continue and the error is stored in its entry.

Rules can also be written by hand and passed as `rules=` (a dict, a `Rules` object or a JSON file):

```json
{
  "categories": [
    {"name": "Invoices", "description": "invoices and bills", "folder": "Invoices/{year}", "filename": "{month}_{sender}_{title}"}
  ],
  "fallback": {"name": "Other", "folder": "Other", "filename": "{name}"},
  "open": false,
  "language": "English"
}
```

Placeholders: `{category}`, `{sender}`, `{title}`, `{year}` (2026), `{month}` (2026-03), `{date}` (2026-03-15) and `{name}` (the old file name). With `"fallback": null`, documents that fit no category stay where they are. With `"open": true`, the model may create new categories, and they use the fallback templates. `language` sets the language of the titles; leave it empty to use the language of each document.

### Safety

- The model only returns JSON. All paths are built and checked by doc-sorter.
- Targets always stay inside the target folder. Unsafe characters, `..` and reserved names are removed.
- Files are never overwritten. If a name is taken, a number is added (`Invoice (1).pdf`).
- Nothing is deleted. Moving keeps the file, and undo removes only empty folders and copies that are still identical to the original.
- Before the first change, a log is written to `<target>/.doc-sorter/`. If a file is missing or a target name was taken since the plan was made, the whole run is refused.
- Hidden files and symbolic links are ignored.

### Settings

Order: argument, then environment variable, then default.

| Argument / flag | Environment variable | Default |
| --- | --- | --- |
| `base_url` / `--base-url` | `DOC_SORTER_BASE_URL` | none, required |
| `model` / `--model` | `DOC_SORTER_MODEL` | none, required |
| `vision_model` / `--vision-model` | `DOC_SORTER_VISION_MODEL` | same as `model` |
| `api_key` / `--api-key` | `DOC_SORTER_API_KEY` (then `OPENAI_API_KEY`) | optional |
| `max_files` / `--max-files` | `DOC_SORTER_MAX_FILES` | 500 |
| `max_file_mb` / `--max-file-mb` | `DOC_SORTER_MAX_FILE_MB` | 50 |
| `max_chars` / `--max-chars` | `DOC_SORTER_MAX_CHARS` | 6000 characters per document |
| `scan_pages` / `--scan-pages` | `DOC_SORTER_SCAN_PAGES` | 3 pages per scan or PDF |
| `timeout` / `--timeout` | `DOC_SORTER_TIMEOUT` | 120 seconds |
| `language` / `--language` | `DOC_SORTER_LANGUAGE` | the language of the instruction |
| `--root` (web page) | `DOC_SORTER_ROOT` | your home folder |
| `max_upload_mb` / `--max-upload-mb` (upload mode) | `DOC_SORTER_MAX_UPLOAD_MB` | 200 |
| `max_unpacked_mb` / `--max-unpacked-mb` (upload mode) | `DOC_SORTER_MAX_UNPACKED_MB` | 1024 |

Only the beginning of each document is sent to the model (`max_chars`, `scan_pages`). That is enough to sort it and keeps runs fast.

## Limitations

The result depends on the chosen model. A small model may pick the wrong category or misread a date. Always check the preview, and use `--copy` or `--dry-run` when in doubt. Scans are read with [doc-extract](https://github.com/hamidmergan/doc-extract).

## Running Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run against a local fake server and need no real model.

## License

MIT
