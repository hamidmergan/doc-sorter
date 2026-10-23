"""Default values and resolution of settings.

Order: argument, then environment variable, then default.
"""

import os
from dataclasses import dataclass
from typing import Optional

from .errors import ConfigError

# Limits
MAX_FILES = 500
MAX_FILE_MB = 50
MAX_CHARS = 6000
SCAN_PAGES = 3
# web page in upload mode
MAX_UPLOAD_MB = 200
MAX_UNPACKED_MB = 1024

# Requests
TIMEOUT_SECONDS = 120
MAX_RETRIES = 2

# Placeholder, because the OpenAI library requires a value.
# Local servers ignore it.
PLACEHOLDER_API_KEY = "not-needed"

ENV_PREFIX = "DOC_SORTER_"


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    vision_model: str
    api_key: str
    max_files: int
    max_file_mb: float
    max_chars: int
    scan_pages: int
    max_upload_mb: float
    max_unpacked_mb: float
    timeout: float
    max_retries: int
    # language of folder names and titles, e.g. "English"; empty: the language of the instruction
    language: str = ""


def _env(name: str) -> Optional[str]:
    value = os.environ.get(ENV_PREFIX + name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _pick(arg, env_name: str, default, cast):
    if arg is not None:
        return arg
    raw = _env(env_name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(
            f"Environment variable {ENV_PREFIX}{env_name} has an invalid value: {raw!r}"
        )


def resolve(
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    vision_model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_files: Optional[int] = None,
    max_file_mb: Optional[float] = None,
    max_chars: Optional[int] = None,
    scan_pages: Optional[int] = None,
    max_upload_mb: Optional[float] = None,
    max_unpacked_mb: Optional[float] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    language: Optional[str] = None,
) -> Settings:
    base_url = _pick(base_url, "BASE_URL", None, str)
    model = _pick(model, "MODEL", None, str)
    missing = []
    if not base_url:
        missing.append("base_url (--base-url or DOC_SORTER_BASE_URL)")
    if not model:
        missing.append("model (--model or DOC_SORTER_MODEL)")
    if missing:
        raise ConfigError(
            "Missing: " + " and ".join(missing) + ". "
            "Example for Ollama: base_url http://localhost:11434/v1 and a model name."
        )

    key = _pick(api_key, "API_KEY", None, str)
    if not key:
        key = os.environ.get("OPENAI_API_KEY") or PLACEHOLDER_API_KEY

    settings = Settings(
        base_url=base_url,
        model=model,
        # scans and images need a vision model; without one the main model is used
        vision_model=_pick(vision_model, "VISION_MODEL", None, str) or model,
        api_key=key,
        max_files=_pick(max_files, "MAX_FILES", MAX_FILES, int),
        max_file_mb=_pick(max_file_mb, "MAX_FILE_MB", MAX_FILE_MB, float),
        max_chars=_pick(max_chars, "MAX_CHARS", MAX_CHARS, int),
        scan_pages=_pick(scan_pages, "SCAN_PAGES", SCAN_PAGES, int),
        max_upload_mb=_pick(max_upload_mb, "MAX_UPLOAD_MB", MAX_UPLOAD_MB, float),
        max_unpacked_mb=_pick(max_unpacked_mb, "MAX_UNPACKED_MB", MAX_UNPACKED_MB, float),
        timeout=_pick(timeout, "TIMEOUT", TIMEOUT_SECONDS, float),
        max_retries=_pick(max_retries, "MAX_RETRIES", MAX_RETRIES, int),
        language=(_pick(language, "LANGUAGE", "", str) or "").strip(),
    )
    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    positive = {
        "max_files": s.max_files,
        "max_file_mb": s.max_file_mb,
        "max_chars": s.max_chars,
        "scan_pages": s.scan_pages,
        "max_upload_mb": s.max_upload_mb,
        "max_unpacked_mb": s.max_unpacked_mb,
        "timeout": s.timeout,
    }
    for name, value in positive.items():
        if value <= 0:
            raise ConfigError(f"{name} must be greater than 0, but is {value}.")
    if s.max_retries < 0:
        raise ConfigError("max_retries must not be negative.")
