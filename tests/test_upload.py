import io
import time
import zipfile

import pytest

from conftest import CONTRACT, INVOICE, INVOICE_RULES, text_pdf
from doc_sorter import LimitError, UnsupportedFileError
from doc_sorter.config import resolve
from doc_sorter.web.uploads import store

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from doc_sorter.web.app import create_app  # noqa: E402

INVOICE_PDF = text_pdf(["Invoice Telekom 2026-03-15 amount 39.99 EUR"])


def make_zip(entries):
    """entries: name -> bytes, or (name, ZipInfo tweak) -> bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            if isinstance(name, tuple):
                name, tweak = name
                info = zipfile.ZipInfo(name)
                tweak(info)
                zf.writestr(info, data)
            else:
                zf.writestr(name, data)
    return buffer.getvalue()


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def settings():
    return resolve(base_url="http://x/v1", model="m")


def test_store_files_and_zip(tmp_path, settings):
    archive = make_zip({
        "inbox/scan.pdf": INVOICE_PDF,
        "inbox/sub/note.txt": b"Buy milk",
        "__MACOSX/inbox/._scan.pdf": b"junk",
        "inbox/.DS_Store": b"junk",
        "inbox/folder/": b"",
    })
    count = store([("docs.zip", io.BytesIO(archive)), ("note.txt", io.BytesIO(b"other"))], tmp_path / "in", settings)
    assert count == 3
    assert _files(tmp_path / "in") == ["inbox/scan.pdf", "inbox/sub/note.txt", "note.txt"]


def test_zip_of_one_folder_is_unwrapped(tmp_path, settings):
    archive = make_zip({"inbox/scan.pdf": INVOICE_PDF, "inbox/sub/note.txt": b"Buy milk"})
    store([("inbox.zip", io.BytesIO(archive))], tmp_path / "in", settings)
    assert _files(tmp_path / "in") == ["scan.pdf", "sub/note.txt"]
    assert not (tmp_path / "staging").exists()


def test_zip_paths_cannot_escape(tmp_path, settings):
    archive = make_zip({
        "../../evil.txt": b"a",
        "/etc/passwd": b"b",
        "C:\\windows\\x.txt": b"c",
        ("link.txt", lambda i: setattr(i, "external_attr", 0o120777 << 16)): b"/etc/passwd",
    })
    store([("a.zip", io.BytesIO(archive))], tmp_path / "in", settings)
    assert _files(tmp_path / "in") == ["C/windows/x.txt", "etc/passwd", "evil.txt"]
    assert not (tmp_path / "evil.txt").exists()


def test_same_names_get_numbers(tmp_path, settings):
    archive = make_zip({"a/note.txt": b"1", "A/Note.txt": b"2"})
    store([("x.zip", io.BytesIO(archive)), ("y.zip", io.BytesIO(archive))], tmp_path / "in", settings)
    assert len(_files(tmp_path / "in")) == 4


def test_limits(tmp_path):
    bomb = make_zip({"zeros.txt": b"\0" * (3 * 1024 * 1024)})
    assert len(bomb) < 100_000
    with pytest.raises(LimitError, match="max_unpacked_mb"):
        store([("bomb.zip", io.BytesIO(bomb))], tmp_path / "in", resolve(base_url="u", model="m", max_unpacked_mb=1))
    assert _files(tmp_path / "in") == []
    with pytest.raises(LimitError, match="max_file_mb"):
        store([("bomb.zip", io.BytesIO(bomb))], tmp_path / "in", resolve(base_url="u", model="m", max_file_mb=1))
    with pytest.raises(LimitError, match="max_files"):
        many = make_zip({f"{i}.txt": b"x" for i in range(4)})
        store([("m.zip", io.BytesIO(many))], tmp_path / "in", resolve(base_url="u", model="m", max_files=3))
    with pytest.raises(LimitError, match="max_upload_mb"):
        store([("big.txt", io.BytesIO(b"x" * 2 * 1024 * 1024))], tmp_path / "in",
              resolve(base_url="u", model="m", max_upload_mb=1))


def test_bad_and_encrypted_zip(tmp_path, settings):
    with pytest.raises(UnsupportedFileError):
        store([("bad.zip", io.BytesIO(b"not a zip"))], tmp_path / "in", settings)
    locked = bytearray(make_zip({"secret.txt": b"x"}))
    central = locked.find(b"PK\x01\x02")
    locked[central + 8] |= 0x1  # mark the entry as encrypted in the central directory
    locked = bytes(locked)
    with pytest.raises(UnsupportedFileError, match="Encrypted"):
        store([("locked.zip", io.BytesIO(locked))], tmp_path / "in", settings)


@pytest.fixture
def web(server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE, "Vonovia": CONTRACT}
    settings = resolve(base_url=server.base_url, model="m", max_retries=0)
    return TestClient(create_app(settings, upload=True))


def _wait(web):
    deadline = time.time() + 10
    while time.time() < deadline:
        job = web.get("/api/job").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("plan did not finish")


def test_upload_preview_download(web):
    assert web.get("/api/info").json()["mode"] == "upload"
    assert web.post("/api/plan", json={"instruction": "sort"}).status_code == 409

    archive = make_zip({"inbox/scan.pdf": INVOICE_PDF, "inbox/contract.txt": b"Rental contract Vonovia",
                        "inbox/song.mp3": b"x"})  # one folder, unwrapped on upload
    response = web.post("/api/upload", files=[("files", ("inbox.zip", archive, "application/zip"))])
    assert response.json() == {"uploaded": 3, "name": "inbox"}

    web.post("/api/plan", json={"instruction": "sort"})
    job = _wait(web)
    entries = {e["source"]: e for e in job["plan"]["entries"]}
    chosen = [entries["scan.pdf"]["index"], entries["contract.txt"]["index"]]
    edited = {entries["contract.txt"]["index"]: "Contracts/rent"}

    result = web.post("/api/download", json={"selected": chosen, "targets": edited})
    assert result.status_code == 200
    assert 'filename="inbox_sorted.zip"' in result.headers["content-disposition"]
    names = sorted(zipfile.ZipFile(io.BytesIO(result.content)).namelist())
    assert names == ["Contracts/rent.txt", "Invoices/2026/2026-03_Telekom_Invoice.pdf", "song.mp3"]

    # the upload itself is unchanged, so the ZIP can be downloaded again
    assert web.post("/api/download", json={"selected": chosen}).status_code == 200


def test_folder_endpoints_are_off(web):
    assert web.get("/api/folders").status_code == 404
    assert web.post("/api/apply", json={"selected": [0]}).status_code == 404
    assert web.post("/api/undo", json={}).status_code == 404


def test_too_large_upload_is_refused_early(server):
    settings = resolve(base_url=server.base_url, model="m", max_upload_mb=1)
    web = TestClient(create_app(settings, upload=True))
    big = b"x" * (3 * 1024 * 1024)
    response = web.post("/api/upload", files=[("files", ("big.txt", big, "text/plain"))])
    assert response.status_code == 413
