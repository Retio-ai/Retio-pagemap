"""Backward-compat shim — import from pagemap.server.http_server instead."""

from pagemap.server.http_server import (  # noqa: F401
    _health_check,
    _liveness_probe,
    _metrics_endpoint,
    _openapi_docs,
    _openapi_spec,
    _openapi_yaml,
    _readiness_check,
    _readiness_probe,
    _run_http_server,
    _startup_probe,
    register_health_routes,
)

__all__ = [
    "_health_check",
    "_liveness_probe",
    "_metrics_endpoint",
    "_openapi_docs",
    "_openapi_spec",
    "_openapi_yaml",
    "_readiness_check",
    "_readiness_probe",
    "_run_http_server",
    "_startup_probe",
    "register_health_routes",
]
