"""Backward-compat shim — import from pagemap.core.template_cache instead."""

from pagemap.core.template_cache import (  # noqa: F401
    DEFAULT_MAX_TEMPLATES,
    DEFAULT_TTL_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    InMemoryTemplateCache,
    PageTemplate,
    TemplateCacheStats,
    TemplateData,
    TemplateKey,
    ValidationResult,
    _infer_card_strategy,
    _infer_pagination_param,
    extract_template_domain,
    infer_metadata_source,
    learn_template,
    validate_template,
)

__all__ = [
    "DEFAULT_MAX_TEMPLATES",
    "DEFAULT_TTL_SECONDS",
    "InMemoryTemplateCache",
    "MAX_CONSECUTIVE_FAILURES",
    "PageTemplate",
    "TemplateCacheStats",
    "TemplateData",
    "TemplateKey",
    "ValidationResult",
    "extract_template_domain",
    "infer_metadata_source",
    "learn_template",
    "validate_template",
]
