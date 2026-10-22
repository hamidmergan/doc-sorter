"""Calls to an OpenAI-compatible chat endpoint. The model only returns JSON, it never touches files."""

import json
import re
from typing import Any, Dict, Optional

from .config import Settings
from .errors import ConfigError, ModelError
from .rules import DEFAULT_FILENAME, PLACEHOLDERS, Rules

RULES_PROMPT = """You turn a sorting instruction for documents into a JSON rule set.
Answer with one JSON object and nothing else:
{"categories": [{"name": "...", "description": "...", "folder": "...", "filename": "..."}],
 "fallback": {"name": "...", "folder": "...", "filename": "..."} or null,
 "open": false, "language": "...", "keep_names": false}
Rules:
- One category per kind of document the instruction names. description says which documents belong to it.
- language is the language the instruction is written in, in English (e.g. "English", "German").
- Write names and folders in that language, with the words the instruction uses. Never translate them.
- folder is a relative path with "/" between folders, filename is the new name without extension.
- Use only these placeholders: """ + ", ".join("{" + p + "}" for p in PLACEHOLDERS) + """.
  {year} is like 2026, {month} like 2026-03, {date} like 2026-03-15, {name} is the old file name.
- "by year" means a folder per year, e.g. "<category folder>/{year}".
- filename is always "{date}_{sender}_{title}", unless the instruction asks for another name.
  Set keep_names to true only if the instruction says to keep the old file names.
- fallback is where all other documents go ("the rest", "everything else"). It is not also a category.
  If the instruction does not say what happens with other documents, set fallback to null.
- Set open to true only if the instruction allows new categories for the other documents;
  then the fallback folder must contain {category}."""

FIELDS_PROMPT = """You classify one document for sorting.
Answer with one JSON object and nothing else:
{"category": "...", "date": "YYYY-MM-DD", "sender": "...", "title": "..."}
Rules:
- category: CATEGORY_RULE
- date: the date of the document itself (invoice date, letter date, signing date), not today.
  Always give the full date as YYYY-MM-DD when day, month and year are printed (10.07.2023 is 2023-07-10).
  Use "YYYY-MM" or "YYYY" only if the day or month is not printed, null if there is no date.
- sender: the company, office or person who issued the document, short (e.g. "Telekom"), or null.
- title: what the document is, in 1 to 3 words (kind of document, or its subject for notes and letters).
  TITLE_RULE
- Use only what is in the document and its file name. Never invent values."""

CATEGORY_FIXED = "exactly one of the names in the list, or null if none fits."
TITLE_IN = "Write it in {language}, even if the document is in another language."
TITLE_OPEN = "Write it in the language of the document."
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
        rules = Rules.from_dict(data)
    except ConfigError as exc:
        raise ModelError(f"The model returned unusable rules: {exc}")
    if data.get("keep_names") is not True:
        # models like to fall back to the old name; renaming is the point unless the user said otherwise
        for category in rules.categories + ([rules.fallback] if rules.fallback else []):
            if "{name}" in category.filename:
                category.filename = DEFAULT_FILENAME
    if rules.fallback:
        # models sometimes list "the rest" as a category too; the fallback already covers it
        rest = rules.fallback.name.casefold()
        rules.categories = [c for c in rules.categories if c.name.casefold() != rest]
    return rules


def classify(text: str, filename: str, rules: Rules, client, settings: Settings) -> Dict[str, Optional[str]]:
    system = FIELDS_PROMPT.replace("CATEGORY_RULE", CATEGORY_OPEN if rules.open else CATEGORY_FIXED)
    system = system.replace("TITLE_RULE", TITLE_IN.format(language=rules.language) if rules.language else TITLE_OPEN)
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
