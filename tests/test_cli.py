import io

import pytest

from conftest import INVOICE, INVOICE_RULES
from doc_sorter.cli import main


@pytest.fixture
def setup(docs, server):
    server.rules = INVOICE_RULES
    server.fields = {"Telekom": INVOICE}
    docs("scan.pdf", "Invoice Telekom 2026-03-15 amount 39.99 EUR")
    docs("note.txt", "Buy milk")
    return docs.root, ["--base-url", server.base_url, "--model", "m"]


def _names(root):
    return sorted(p.name for p in root.iterdir())


def test_dry_run_shows_plan_and_changes_nothing(setup, capsys):
    root, conn = setup
    assert main([str(root), "Invoices by year", "--dry-run"] + conn) == 0
    out = capsys.readouterr().out
    assert "Invoices: Invoices/{year}/{month}_{sender}_{title}" in out
    assert "scan.pdf  ->  Invoices/2026/2026-03_Telekom_Invoice.pdf" in out
    assert _names(root) == ["note.txt", "scan.pdf"]


def test_yes_applies_and_undo_reverts(setup, capsys):
    root, conn = setup
    assert main([str(root), "Invoices by year", "--yes"] + conn) == 0
    assert _names(root) == [".doc-sorter", "Invoices", "Other"]
    assert "--undo" in capsys.readouterr().err
    assert main(["--undo", str(root)]) == 0
    assert _names(root) == [".doc-sorter", "note.txt", "scan.pdf"]


def test_without_terminal_nothing_is_applied(setup, monkeypatch, capsys):
    root, conn = setup
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main([str(root), "Invoices by year"] + conn) == 1
    assert "--yes" in capsys.readouterr().err
    assert _names(root) == ["note.txt", "scan.pdf"]


def test_confirmation_prompt(setup, monkeypatch):
    root, conn = setup
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main([str(root), "Invoices by year"] + conn) == 1
    assert _names(root) == ["note.txt", "scan.pdf"]
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert main([str(root), "Invoices by year"] + conn) == 0
    assert "Invoices" in _names(root)


def test_saved_rules_and_plan(setup, server, tmp_path):
    root, conn = setup
    rules, plan = tmp_path / "rules.json", tmp_path / "plan.json"
    main([str(root), "Invoices by year", "--dry-run", "--save-rules", str(rules), "--save-plan", str(plan)] + conn)
    count = len(server.requests)
    assert main([str(root), "--rules", str(rules), "--dry-run"] + conn) == 0
    assert server.kinds()[count:] == ["fields", "fields"]
    assert main(["--from-plan", str(plan), "--yes"]) == 0
    assert "Invoices" in _names(root)


def test_copy_to_other_folder(setup, tmp_path):
    root, conn = setup
    dest = tmp_path / "sorted"
    assert main([str(root), "Invoices by year", "--copy", "--dest", str(dest), "-y"] + conn) == 0
    assert _names(root) == ["note.txt", "scan.pdf"]
    assert (dest / "Invoices" / "2026" / "2026-03_Telekom_Invoice.pdf").is_file()


def test_errors_exit_codes(setup, tmp_path, capsys):
    root, conn = setup
    assert main([str(root)]) == 2
    assert "base_url" in capsys.readouterr().err
    assert main([]) == 2
    assert main([str(root), "x", "--rules", "r.json"] + conn) == 2
    assert main(["--undo", str(tmp_path)]) == 2
