import pytest

import doc_sorter
from conftest import CONTRACT, INVOICE, INVOICE_RULES
from doc_sorter import ConfigError, LimitError, Plan, Rules


def _plan(server, docs, instruction="Invoices by year, contracts separately, the rest in Other", **kw):
    return doc_sorter.plan(docs.root, instruction, base_url=server.base_url, model="m", max_retries=0, **kw)


def _snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def inbox(docs, server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE, "Vonovia": CONTRACT}
    docs("scan_001.pdf", "Invoice Telekom 2026-03-15 amount 39.99 EUR")
    docs("contract.docx", "Rental contract Vonovia signed 2025-01-02")
    docs("note.txt", "Buy milk")
    docs("song.mp3", "x")
    docs(".hidden.txt", "secret")
    return docs


def test_plan_targets(inbox, server):
    plan = _plan(server, inbox)
    targets = {e.source.name: e.target.relative_to(plan.dest_root).as_posix() for e in plan.to_sort}
    assert targets == {
        "scan_001.pdf": "Invoices/2026/2026-03_Telekom_Invoice.pdf",
        "contract.docx": "Contracts/2025-01-02_Vonovia_Rental contract.docx",
        "note.txt": "Other/note.txt",
    }
    assert [e.source.name for e in plan.entries if e.status == "skip"] == ["song.mp3"]
    assert ".hidden.txt" not in {e.source.name for e in plan.entries}
    assert server.kinds() == ["rules", "fields", "fields", "fields"]


def test_planning_changes_no_file(inbox, server):
    before = _snapshot(inbox.root)
    _plan(server, inbox)
    assert _snapshot(inbox.root) == before


def test_the_model_never_sees_paths_outside_its_job(inbox, server):
    _plan(server, inbox)
    fields_request = server.requests[1]["body"]["messages"][1]["content"]
    assert str(inbox.root) not in fields_request


def test_name_conflicts_get_numbers(docs, server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE}
    docs("a.pdf", "Invoice Telekom March one, amount 39.99 EUR")
    docs("b.pdf", "Invoice Telekom March two, amount 12.00 EUR")
    docs("Invoices/2026/2026-03_Telekom_Invoice.pdf", "already there")
    plan = _plan(server, docs)
    names = sorted(e.target.name for e in plan.to_sort)
    assert names == ["2026-03_Telekom_Invoice (1).pdf", "2026-03_Telekom_Invoice (2).pdf"]


def test_file_already_in_place_is_kept(docs, server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE}
    docs("Invoices/2026/2026-03_Telekom_Invoice.pdf", "Invoice Telekom, amount 39.99 EUR")
    plan = _plan(server, docs, recursive=True)
    assert [(e.status, e.reason) for e in plan.entries] == [("keep", "already in place")]


def test_subfolders_only_with_recursive(docs, server):
    server.rules = INVOICE_RULES
    docs("sub/deep.txt", "text")
    assert _plan(server, docs).entries == []
    assert [e.source.name for e in _plan(server, docs, recursive=True).entries] == ["deep.txt"]


def test_no_fallback_leaves_other_files(docs, server):
    server.rules = dict(INVOICE_RULES, fallback=None)
    docs("note.txt", "Buy milk")
    plan = _plan(server, docs)
    assert [(e.status, e.reason) for e in plan.entries] == [("keep", "no matching rule")]


def test_without_instruction_the_model_picks_categories(docs, server):
    server.fields = {"HUK": {"category": "Insurance", "date": "2024-05-01", "sender": "HUK", "title": "Policy"}}
    docs("x.txt", "HUK insurance policy")
    plan = doc_sorter.plan(docs.root, base_url=server.base_url, model="m")
    assert plan.to_sort[0].target.relative_to(docs.root).as_posix() == "Insurance/2024/2024-05-01_HUK_Policy.txt"
    assert server.kinds() == ["fields"]


def test_saved_rules_need_no_rule_request(docs, server, tmp_path):
    Rules.from_dict(INVOICE_RULES).save(tmp_path / "rules.json")
    docs("note.txt", "Buy milk")
    plan = doc_sorter.plan(docs.root, rules=tmp_path / "rules.json", base_url=server.base_url, model="m")
    assert plan.rules.categories[0].name == "Invoices"
    assert server.kinds() == ["fields"]


def test_separate_target_folder(inbox, server, tmp_path):
    plan = _plan(server, inbox, dest=tmp_path / "sorted")
    assert all(str(e.target).startswith(str((tmp_path / "sorted").resolve())) for e in plan.to_sort)


def test_failing_file_does_not_stop_the_rest(docs, server):
    server.rules = INVOICE_RULES
    docs("note.txt", "Buy milk")
    broken = docs("broken.txt", "x")
    broken.rename(broken.with_suffix(".pdf"))
    plan = _plan(server, docs)
    assert [e.status for e in plan.entries] == ["error", "sort"]


def test_unusable_model_answer_is_an_error(docs, server):
    server.rules = INVOICE_RULES
    docs("note.txt", "Buy milk")
    server.raw_reply = "I cannot help with that."
    with pytest.raises(doc_sorter.ModelError):
        _plan(server, docs)
    plan = doc_sorter.plan(docs.root, rules=INVOICE_RULES, base_url=server.base_url, model="m")
    assert plan.entries[0].status == "error"


def test_limits_and_bad_input(docs, server, tmp_path):
    docs("a.txt", "a")
    docs("b.txt", "b")
    with pytest.raises(LimitError):
        doc_sorter.plan(docs.root, base_url=server.base_url, model="m", max_files=1)
    with pytest.raises(ConfigError):
        doc_sorter.plan(tmp_path / "missing", base_url=server.base_url, model="m")
    with pytest.raises(ConfigError):
        doc_sorter.plan(docs.root, "x", rules=INVOICE_RULES, base_url=server.base_url, model="m")


def test_plan_round_trip(inbox, server, tmp_path):
    plan = _plan(server, inbox)
    plan.save(tmp_path / "plan.json")
    loaded = Plan.load(tmp_path / "plan.json")
    assert loaded.to_dict() == plan.to_dict()
    assert "Move 3 file(s):" in loaded.preview()
