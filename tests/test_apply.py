import json

import pytest

import doc_sorter
from conftest import CONTRACT, INVOICE, INVOICE_RULES
from doc_sorter import Plan, PlanError


@pytest.fixture
def plan(docs, server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE, "Vonovia": CONTRACT}
    docs("scan_001.pdf", "Invoice Telekom 2026-03-15 amount 39.99 EUR")
    docs("contract.txt", "Rental contract Vonovia")
    docs("note.txt", "Buy milk")

    def make(**kw):
        return doc_sorter.plan(docs.root, "sort", base_url=server.base_url, model="m", **kw)

    make.root = docs.root
    return make


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".doc-sorter" not in p.parts)


def test_apply_moves_and_logs(plan):
    p = plan()
    log = p.apply()
    assert _files(plan.root) == [
        "Contracts/2025-01-02_Vonovia_Rental contract.txt",
        "Invoices/2026/2026-03_Telekom_Invoice.pdf",
        "Other/note.txt",
    ]
    data = json.loads(log.read_text())
    assert log.parent.name == ".doc-sorter" and data["undone"] is False
    assert all(op["done"] for op in data["ops"])


def test_undo_restores_everything(plan):
    before = _files(plan.root)
    plan().apply()
    result = doc_sorter.undo(plan.root)
    assert result.ok and result.restored == 3
    assert _files(plan.root) == before
    assert sorted(p.name for p in plan.root.iterdir()) == [".doc-sorter", "contract.txt", "note.txt", "scan_001.pdf"]


def test_undo_twice_is_refused(plan):
    log = plan().apply()
    doc_sorter.undo(log)
    with pytest.raises(PlanError):
        doc_sorter.undo(log)
    with pytest.raises(PlanError):
        doc_sorter.undo(plan.root)


def test_copy_keeps_the_originals(plan, tmp_path):
    dest = tmp_path / "sorted" / "new"
    plan(copy=True, dest=dest).apply()
    assert _files(plan.root) == ["contract.txt", "note.txt", "scan_001.pdf"]
    assert len(_files(dest)) == 3


def test_undo_of_copy_removes_only_unchanged_copies(plan, tmp_path):
    dest = tmp_path / "sorted"
    plan(copy=True, dest=dest).apply()
    edited = dest / "Other" / "note.txt"
    edited.write_text("changed by the user")
    result = doc_sorter.undo(dest)
    assert result.restored == 2 and len(result.problems) == 1
    assert _files(dest) == ["Other/note.txt"]
    assert _files(plan.root) == ["contract.txt", "note.txt", "scan_001.pdf"]


def test_undo_keeps_files_that_changed_place(plan):
    p = plan()
    p.apply()
    (plan.root / "note.txt").write_text("a new file with the old name")
    result = doc_sorter.undo(plan.root)
    assert result.restored == 2 and "exists again" in result.problems[0]
    assert (plan.root / "Other" / "note.txt").read_text() == "Buy milk"


def test_conflict_at_apply_time_changes_nothing(plan):
    p = plan()
    before = _files(plan.root)
    target = p.to_sort[-1].target
    target.parent.mkdir(parents=True)
    target.write_text("appeared in the meantime")
    with pytest.raises(PlanError):
        p.apply()
    assert _files(plan.root) == sorted(before + [target.relative_to(plan.root).as_posix()])
    assert not (plan.root / ".doc-sorter").exists()


def test_edited_plan_cannot_escape(plan, tmp_path):
    p = plan()
    p.save(tmp_path / "plan.json")
    data = json.loads((tmp_path / "plan.json").read_text())
    data["entries"][0]["target"] = str(tmp_path / "outside.pdf")
    (tmp_path / "plan.json").write_text(json.dumps(data))
    with pytest.raises(PlanError):
        Plan.load(tmp_path / "plan.json").apply()
    assert not (tmp_path / "outside.pdf").exists()


def test_never_overwrites_even_if_check_is_passed(tmp_path):
    from doc_sorter.apply import _move_new

    src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
    src.write_text("a")
    dst.write_text("b")
    with pytest.raises(PlanError):
        _move_new(src, dst)
    assert src.read_text() == "a" and dst.read_text() == "b"


def test_nothing_to_do(plan):
    p = plan()
    p.entries = []
    with pytest.raises(PlanError):
        p.apply()


def test_plan_without_target_is_rejected(plan, tmp_path):
    data = plan().to_dict()
    data["entries"][0]["target"] = None
    with pytest.raises(PlanError):
        Plan.from_dict(data)
