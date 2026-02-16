"""Shared test configuration and fixtures."""

try:
    import pagemap  # noqa: F401
except ImportError:
    raise ImportError("pagemap is not installed. Run: pip install -e '.[dev]'") from None
