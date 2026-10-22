from pathlib import Path, PurePosixPath

import pytest

from conftest import CONTRACT, INVOICE, INVOICE_RULES
from doc_sorter import ConfigError, Rules, default_rules
from doc_sorter.rules import clean_part, parse_date, target_path


def test_parse_date():
    assert parse_date("2026-03-15") == {"year": "2026", "month": "2026-03", "date": "2026-03-15"}
    assert parse_date("2026-3") == {"year": "2026", "month": "2026-03", "date": "2026-03"}
    assert parse_date("2026") == {"year": "2026", "month": "", "date": "2026"}
    assert parse_date(None)["date"] == ""
    assert parse_date("15.03.2026")["date"] == ""
    assert parse_date("2026-13-01")["month"] == ""


def test_clean_part_removes_unsafe_characters():
    assert clean_part("a/b\\c:d") == "a b c d"
    assert clean_part("..") == ""
    assert clean_part("  .hidden ") == "hidden"
    assert clean_part("CON") == "_CON"
    assert clean_part("2026__Telekom") == "2026_Telekom"
    assert clean_part("Müller & Söhne") == "Müller & Söhne"
    assert len(clean_part("x" * 500)) == 100


def test_target_for_matching_category():
    rules = Rules.from_dict(INVOICE_RULES)
    assert target_path(rules, INVOICE, Path("scan_001.pdf")) == PurePosixPath(
        "Invoices/2026/2026-03_Telekom_Invoice.pdf"
    )
    assert target_path(rules, CONTRACT, Path("x.docx")) == PurePosixPath(
        "Contracts/2025-01-02_Vonovia_Rental contract.docx"
    )


def test_category_match_ignores_case():
    rules = Rules.from_dict(INVOICE_RULES)
    assert target_path(rules, dict(INVOICE, category="invoices"), Path("a.pdf")).parts[0] == "Invoices"


def test_fallback_and_no_fallback():
    rules = Rules.from_dict(INVOICE_RULES)
    other = {"category": None, "date": None, "sender": None, "title": "Note"}
    assert target_path(rules, other, Path("note.txt")) == PurePosixPath("Other/note.txt")
    rules.fallback = None
    assert target_path(rules, other, Path("note.txt")) is None


def test_missing_values_collapse():
    rules = Rules.from_dict(INVOICE_RULES)
    fields = dict(INVOICE, date=None, sender=None)
    assert target_path(rules, fields, Path("a.pdf")) == PurePosixPath("Invoices/Invoice.pdf")


def test_all_values_missing_keeps_old_name():
    rules = Rules.from_dict(INVOICE_RULES)
    fields = {"category": "Invoices", "date": None, "sender": None, "title": None}
    assert target_path(rules, fields, Path("scan 7.pdf")) == PurePosixPath("Invoices/scan 7.pdf")


def test_model_values_cannot_escape_the_folder():
    rules = Rules.from_dict({"categories": [{"name": "X", "folder": "{sender}/{title}", "filename": "{title}"}]})
    fields = {"category": "X", "sender": "../..", "title": "../../etc/passwd"}
    path = target_path(rules, fields, Path("a.txt"))
    assert ".." not in path.parts and len(path.parts) == 2


def test_open_rules_use_the_model_category():
    rules = default_rules()
    fields = {"category": "Insurance", "date": "2024-05-01", "sender": "HUK", "title": "Policy"}
    assert target_path(rules, fields, Path("a.pdf")) == PurePosixPath("Insurance/2024/2024-05-01_HUK_Policy.pdf")
    fields["category"] = None
    assert target_path(rules, fields, Path("a.pdf")).parts[0] == "Other"


def test_unknown_placeholder_is_rejected():
    with pytest.raises(ConfigError):
        Rules.from_dict({"categories": [{"name": "X", "folder": "{customer}"}]})


def test_empty_rules_are_rejected():
    with pytest.raises(ConfigError):
        Rules.from_dict({"categories": [], "fallback": None})


def test_rules_round_trip(tmp_path):
    rules = Rules.from_dict(INVOICE_RULES)
    rules.save(tmp_path / "rules.json")
    assert Rules.load(tmp_path / "rules.json").to_dict() == rules.to_dict()
