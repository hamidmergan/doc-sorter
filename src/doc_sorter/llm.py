"""Calls to an OpenAI-compatible chat endpoint. The model only returns JSON, it never touches files."""

import json
import re
from typing import Any, Dict, Optional

from .config import Settings
from .errors import ConfigError, ModelError
from .rules import PLACEHOLDERS, Rules

RULES_PROMPT = """You turn a sorting instruction for documents into a JSON rule set.
Answer with one JSON object and nothing else:
{"categories": [{"name": "...", "description": "...", "folder": "...", "filename": "..."}],
 "fallback": {"name": "...", "folder": "...", "filename": "..."} or null,
 "open": false}
Rules:
- One category per kind of document the instruction names. description says which documents belong to it.
- folder is a relative path with "/" between folders, filename is the new name without extension.
- Use only these placeholders: """ + ", ".join("{" + p + "}" for p in PLACEHOLDERS) + """.
  {year} is like 2026, {month} like 2026-03, {date} like 2026-03-15, {name} is the old file name.
- Use the folder names and the language of the instruction. "invoices by year" means folder "Invoices/{year}".
- If no file name is asked for, use "{date}_{sender}_{title}".
- fallback is where all other documents go. If the instruction does not say, set fallback to null.
- Set open to true only if the instruction allows new categories for the other documents;
  then the fallback folder must contain {category}."""

FIELDS_PROMPT = """You classify one document for sorting.
Answer with one JSON object and nothing else:
{"category": "...", "date": "YYYY-MM-DD", "sender": "...", "title": "..."}
Rules:
- category: CATEGORY_RULE
- date: the date of the document itself (invoice date, letter date, signing date), not today. Use
  "YYYY-MM" or "YYYY" if only that is known, null if there is none.
- sender: the company, office or person who issued the document, short (e.g. "Telekom"), or null.
- title: a short title of 1 to 4 words, e.g. "Invoice" or "Rental contract", in the language of the document.
- Use only what is in the document and its file name. Never invent values."""

CATEGORY_FIXED = "exactly one of the names in the list, or null if none fits."
CATEGORY_OPEN = ("one of the names in the list if one fits, otherwise a short new category name of 1 or 2 words "
                 "in plural (e.g. \"Invoices\", \"Contracts\"), in the language of the document.")


def make_client(settings: Settings):
    from openai import OpenAI

    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )


def _chat(client, settings: Settings, system: str, user: str) -> str:
    try:
        response = client.chat.completions.create(
            model=settings.model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    except Exception as exc:
        raise ModelError(f"Request to {settings.base_url} failed: {exc}")
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise ModelError("The server response has an unexpected format.")
    return content or ""


_THINK = re.compile(r"<think>.*?</think>", re.S)


def json_object(text: str) -> Dict[str, Any]:
    """The first JSON object in a model answer; reasoning blocks, code fences and chatter are skipped."""
    text = _THINK.sub("", text)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ModelError("The model answer contains no valid JSON object.")


def make_rules(instruction: str, client, settings: Settings) -> Rules:
    data = json_object(_chat(client, settings, RULES_PROMPT, "Instruction:\n" + instruction))
    try:
        return Rules.from_dict(data)
    except ConfigError as exc:
        raise ModelError(f"The model returned unusable rules: {exc}")


def classify(text: str, filename: str, rules: Rules, client, settings: Settings) -> Dict[str, Optional[str]]:
    system = FIELDS_PROMPT.replace("CATEGORY_RULE", CATEGORY_OPEN if rules.open else CATEGORY_FIXED)
    categories = [{"name": c.name, "description": c.description} for c in rules.categories]
    user = (
        "Categories:\n" + json.dumps(categories, ensure_ascii=False)
        + "\n\nFile name: " + filename
        + "\n\nDocument text:\n" + (text or "(no text found)")
    )
    data = json_object(_chat(client, settings, system, user))

    def value(key: str) -> Optional[str]:
        raw = data.get(key)
        if raw is None or isinstance(raw, (dict, list)):
            return None
        raw = str(raw).strip()
        return None if raw.lower() in ("", "null", "none") else raw

    return {key: value(key) for key in ("category", "date", "sender", "title")}
