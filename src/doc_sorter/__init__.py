from .apply import UndoResult, undo
from .errors import (
    ConfigError,
    LimitError,
    ModelError,
    PlanError,
    SortError,
    UnsupportedFileError,
)
from .plan import Entry, Plan
from .rules import Category, Rules, default_rules

# last, so the name 'plan' is the function and not the submodule
from .core import make_rules, plan  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "plan",
    "make_rules",
    "undo",
    "Plan",
    "Entry",
    "Rules",
    "Category",
    "default_rules",
    "UndoResult",
    "SortError",
    "ConfigError",
    "LimitError",
    "ModelError",
    "PlanError",
    "UnsupportedFileError",
    "__version__",
]
