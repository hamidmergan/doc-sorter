"""Rule sets and turning document fields into a safe relative path."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Union

from .errors import ConfigError

PLACEHOLDERS = ("category", "sender", "title", "year", "month", "date", "name")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_DATE = re.compile(r"^\s*(\d{4})(?:[-./](\d{1,2}))?(?:[-./](\d{1,2}))?")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SEPARATORS = re.compile(r"([_-])(?:\s*[_-])+")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
MAX_PART = 100


@dataclass
class Category:
    name: str
    folder: str
    filename: str = "{date}_{sender}_{title}"
    description: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "description": self.description, "folder": self.folder,
                "filename": self.filename}


@dataclass
class Rules:
    """Where each category goes.

    open: the model may create categories of its own; they use the fallback templates.
    fallback: templates for documents without a matching category; None leaves them in place.
    """

    categories: List[Category] = field(default_factory=list)
    fallback: Optional[Category] = None
    open: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "categories": [c.to_dict() for c in self.categories],
            "fallback": self.fallback.to_dict() if self.fallback else None,
            "open": self.open,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Rules":
        if not isinstance(data, dict):
            raise ConfigError("Rules must be a JSON object.")
        raw = data.get("categories") or []
        if not isinstance(raw, list):
            raise ConfigError("Rules: 'categories' must be a list.")
        categories = [_category(c, f"category {i}") for i, c in enumerate(raw, 1)]
        fallback = data.get("fallback")
        rules = cls(
            categories=categories,
            fallback=_category(fallback, "fallback", name="Other") if fallback else None,
            open=bool(data.get("open", False)),
        )
        if not rules.categories and not rules.fallback:
            raise ConfigError("Rules contain no category and no fallback, so nothing would be sorted.")
        if rules.open and not rules.fallback:
            raise ConfigError("Rules with 'open' need a fallback for the new categories.")
        return rules

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Rules":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read rules from {path}: {exc}")
        return cls.from_dict(data)

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def find(self, name: Optional[str]) -> Optional[Category]:
        key = (name or "").strip().casefold()
        for category in self.categories:
            if category.name.strip().casefold() == key:
                return category
        return None


def default_rules() -> Rules:
    """Used without an instruction: the model names the categories itself."""
    return Rules(fallback=Category(name="Other", folder="{category}/{year}"), open=True)


def _category(data: Any, where: str, name: str = "") -> Category:
    if not isinstance(data, dict):
        raise ConfigError(f"Rules: {where} must be an object.")
    category = Category(
        name=str(data.get("name") or name).strip(),
        folder=str(data.get("folder") or "").strip(),
        filename=str(data.get("filename") or "{date}_{sender}_{title}").strip(),
        description=str(data.get("description") or "").strip(),
    )
    if not category.name:
        raise ConfigError(f"Rules: {where} has no name.")
    for template in (category.folder, category.filename):
        for placeholder in _PLACEHOLDER.findall(template):
            if placeholder not in PLACEHOLDERS:
                raise ConfigError(
                    f"Rules: unknown placeholder {{{placeholder}}} in {where}. "
                    f"Allowed: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"
                )
    return category


def parse_date(value: Any) -> Dict[str, str]:
    """year, month (YYYY-MM) and date (YYYY-MM-DD, or less if parts are missing)."""
    match = _DATE.match(str(value or ""))
    if not match:
        return {"year": "", "month": "", "date": ""}
    year, month, day = match.group(1), match.group(2), match.group(3)
    if not 1900 <= int(year) <= 2100:
        return {"year": "", "month": "", "date": ""}
    if month is None or not 1 <= int(month) <= 12:
        return {"year": year, "month": "", "date": year}
    month = f"{year}-{int(month):02d}"
    if day is None or not 1 <= int(day) <= 31:
        return {"year": year, "month": month, "date": month}
    return {"year": year, "month": month, "date": f"{month}-{int(day):02d}"}


def clean_part(text: str) -> str:
    """One safe folder or file name part; empty if nothing usable is left."""
    text = unicodedata.normalize("NFC", str(text))
    text = _BAD_CHARS.sub(" ", text)
    text = " ".join(text.split())
    text = _SEPARATORS.sub(r"\1", text)
    text = text[:MAX_PART].strip(" ._-")
    if text in ("", ".", ".."):
        return ""
    if _RESERVED.match(text):
        text = "_" + text
    return text


def _fill(template: str, values: Dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), template)


def target_path(rules: Rules, fields: Dict[str, Any], original: Path) -> Optional[PurePosixPath]:
    """The relative target path for a document, or None to leave it in place."""
    category = rules.find(fields.get("category"))
    if category is not None:
        name = category.name
    elif rules.fallback is None:
        return None
    else:
        category = rules.fallback
        name = str(fields.get("category") or "") if rules.open else ""
        name = clean_part(name) or rules.fallback.name

    values = {
        "category": name,
        "sender": str(fields.get("sender") or ""),
        "title": str(fields.get("title") or ""),
        "name": original.stem,
    }
    values.update(parse_date(fields.get("date")))
    values = {k: clean_part(v) for k, v in values.items()}

    folders = [clean_part(_fill(part, values)) for part in category.folder.split("/")]
    stem = clean_part(_fill(category.filename, values)) or clean_part(original.stem) or "document"
    parts = [p for p in folders if p] + [stem + original.suffix]
    return PurePosixPath(*parts)
