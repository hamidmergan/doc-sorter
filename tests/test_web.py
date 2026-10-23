import time

import pytest

from conftest import CONTRACT, INVOICE, INVOICE_RULES

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from doc_sorter.config import resolve  # noqa: E402
from doc_sorter.web.app import create_app  # noqa: E402


@pytest.fixture
def web(docs, server, tmp_path):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE, "Vonovia": CONTRACT}
    docs("scan.pdf", "Invoice Telekom 2026-03-15 amount 39.99 EUR")
    docs("contract.txt", "Rental contract Vonovia")
    docs("note.txt", "Buy milk")
    settings = resolve(base_url=server.base_url, model="m", max_retries=0)
    return TestClient(create_app(settings, tmp_path))


def _plan(web, **body):
    response = web.post("/api/plan", json=dict({"folder": "inbox", "instruction": "sort"}, **body))
    assert response.status_code == 200, response.text
    deadline = time.time() + 10
    while time.time() < deadline:
        job = web.get("/api/job").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("plan did not finish")


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".doc-sorter" not in p.parts)


def test_page_and_info(web):
    assert "doc-sorter" in web.get("/").text
    assert web.get("/api/info").json()["model"] == "m"
    assert web.get("/static/app.js").status_code == 200


def test_folders_stay_inside_root(web):
    data = web.get("/api/folders").json()
    assert data == {"path": "", "parent": None, "folders": ["inbox"]}
    assert web.get("/api/folders", params={"path": "inbox"}).json()["parent"] == ""
    assert web.get("/api/folders", params={"path": "../.."}).status_code == 400
    assert web.post("/api/plan", json={"folder": "inbox", "dest": "../outside"}).status_code == 400


def test_plan_apply_selected_and_edited(web, docs):
    job = _plan(web)
    assert job["state"] == "done"
    entries = {e["source"]: e for e in job["plan"]["entries"]}
    assert entries["scan.pdf"]["target"] == "Invoices/2026/2026-03_Telekom_Invoice.pdf"

    chosen = [entries["scan.pdf"]["index"], entries["note.txt"]["index"]]
    edited = {entries["note.txt"]["index"]: "../Notes/../milk"}
    result = web.post("/api/apply", json={"selected": chosen, "targets": edited})
    assert result.status_code == 200, result.text
    assert result.json()["applied"] == 2
    assert _files(docs.root) == ["Invoices/2026/2026-03_Telekom_Invoice.pdf", "Notes/milk.txt", "contract.txt"]

    assert web.post("/api/apply", json={"selected": chosen}).status_code == 409
    undone = web.post("/api/undo", json={"folder": "inbox"}).json()
    assert undone["restored"] == 2
    assert _files(docs.root) == ["contract.txt", "note.txt", "scan.pdf"]


def test_apply_rejects_bad_selection(web):
    job = _plan(web)
    kept = len(job["plan"]["entries"])
    assert web.post("/api/apply", json={"selected": [kept + 5]}).status_code == 400
    assert web.post("/api/apply", json={"selected": []}).status_code == 400


def test_model_error_is_reported(web, server):
    server.fail = True
    job = _plan(web)
    assert job["state"] == "error" and job["error"]


def test_undo_without_run(web):
    assert web.post("/api/undo", json={"folder": "inbox"}).status_code == 404


def test_foreign_origin_and_host_are_refused(server, tmp_path):
    settings = resolve(base_url=server.base_url, model="m")
    client = TestClient(create_app(settings, tmp_path, allowed_hosts=["localhost", "127.0.0.1"]),
                        base_url="http://localhost")
    assert client.get("/api/info").status_code == 200
    assert client.post("/api/undo", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    other = TestClient(create_app(settings, tmp_path, allowed_hosts=["localhost"]), base_url="http://evil.example")
    assert other.get("/api/info").status_code == 400
