class SortError(Exception):
    """Base class of all errors in this package."""


class ConfigError(SortError):
    """A setting is missing or a value is invalid."""


class LimitError(SortError):
    """A limit was exceeded."""


class UnsupportedFileError(SortError):
    """The file type is not supported or the file cannot be read."""


class ModelError(SortError):
    """The model server did not answer, or the answer was unusable."""


class PlanError(SortError):
    """A plan or log is invalid, unsafe or no longer matches the files on disk."""
