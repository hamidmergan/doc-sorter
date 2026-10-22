import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class FakeServer:
    """OpenAI-compatible chat endpoint.

    Rule requests get `rules`. Field requests get the first entry of `fields` whose key is in the
    document text or file name. Vision requests get `vision_text`.
    """

    def __init__(self):
        self.requests = []
        self.fail = False
        self.rules = {}
        self.fields = {}
        self.vision_text = ""
        self.raw_reply = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if outer.fail:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                payload = {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": outer.answer(body)},
                        }
                    ],
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def answer(self, body) -> str:
        messages = body["messages"]
        first = messages[0]["content"]
        if isinstance(first, list):  # doc-extract sends the image in the first message
            return self.vision_text
        if self.raw_reply is not None:
            return self.raw_reply
        if first.startswith("You turn a sorting instruction"):
            return "Here are the rules:\n```json\n" + json.dumps(self.rules) + "\n```"
        user = messages[1]["content"]
        for key, fields in self.fields.items():
            if key in user:
                return json.dumps(fields)
        return json.dumps({"category": None, "date": None, "sender": None, "title": None})

    def kinds(self):
        """'rules', 'fields' or 'vision' for every request so far."""
        out = []
        for r in self.requests:
            first = r["body"]["messages"][0]["content"]
            if isinstance(first, list):
                out.append("vision")
            else:
                out.append("rules" if first.startswith("You turn") else "fields")
        return out

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith(("DOC_SORTER_", "DOC_EXTRACT_")) or name == "OPENAI_API_KEY":
            monkeypatch.delenv(name)


def text_pdf(pages):
    """A minimal PDF with one line of Helvetica text per page; an empty string gives a blank page."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids = []
    for i, text in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = (f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET" if text else "").encode("latin-1")
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>"
        ).encode()
        objects[content_no] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n")
    xref = out.tell()
    count = max(objects) + 1
    out.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
    for num in range(1, count):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


@pytest.fixture
def docs(tmp_path):
    """A folder 'inbox' with a few documents; returns a function to add more."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    def add(name, content=""):
        path = inbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".pdf"):
            pages = content if isinstance(content, list) else [content]
            path.write_bytes(text_pdf(pages))
        elif name.endswith(".docx"):
            import docx

            document = docx.Document()
            document.add_paragraph(content)
            document.save(str(path))
        elif name.endswith((".png", ".jpg")):
            from PIL import Image

            Image.new("RGB", (120, 80), (255, 255, 255)).save(path)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    add.root = inbox
    return add


INVOICE_RULES = {
    "categories": [
        {
            "name": "Invoices",
            "description": "invoices and bills",
            "folder": "Invoices/{year}",
            "filename": "{month}_{sender}_{title}",
        },
        {"name": "Contracts", "description": "contracts", "folder": "Contracts", "filename": "{date}_{sender}_{title}"},
    ],
    "fallback": {"name": "Other", "folder": "Other", "filename": "{name}"},
    "open": False,
}

INVOICE = {"category": "Invoices", "date": "2026-03-15", "sender": "Telekom", "title": "Invoice"}
CONTRACT = {"category": "Contracts", "date": "2025-01-02", "sender": "Vonovia", "title": "Rental contract"}
