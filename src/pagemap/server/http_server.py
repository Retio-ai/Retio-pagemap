# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP server bootstrap, health/readiness endpoints, and OpenAPI docs.

Extracted from server.py — all symbols are re-exported there for backward compatibility.

Health endpoints use programmatic route registration via ``register_health_routes()``
instead of decorator-based ``@mcp.custom_route``, so they can live outside server.py.

``_run_http_server`` writes module globals via ``srv.X = val`` (lazy import pattern)
instead of ``global X; X = val`` to maintain test patching compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

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

logger = logging.getLogger("pagemap.server")


# ── Health check endpoints ────────────────────────────────────────────


async def _health_check(request):
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    return JSONResponse({"status": "ok", "transport": srv._transport_mode})


async def _readiness_check(request):
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    if srv._transport_mode != "http" or srv._session_manager is None:
        return JSONResponse({"status": "ready", "transport": "stdio"})

    body: dict = {"status": "ready", "transport": "http"}

    if hasattr(srv._session_manager, "_pool"):
        h = srv._session_manager._pool.health()
        ready = h.browser_connected
        body["status"] = "ready" if ready else "not_ready"
        body["pool"] = {
            "active": h.active,
            "max_contexts": h.max_contexts,
            "browser_connected": h.browser_connected,
        }
    else:
        ready = True

    # S6: Telemetry status (informational — never fails readiness)
    try:
        from pagemap.telemetry import _collector

        if _collector is not None:
            body["telemetry"] = {
                "queue_depth": _collector._queue.qsize(),
                "exported": _collector.meta.exported,
                "dropped": _collector.meta.dropped,
            }
    except Exception:  # nosec B110
        pass

    # S6: Alert engine status (informational)
    if srv._alert_engine is not None:
        try:
            active = srv._alert_engine.get_active_alerts()
            body["alerts"] = {
                "active_count": len(active),
                "rules": {name: state.value for name, state in active} if active else {},
            }
        except Exception:  # nosec B110
            pass

    # S2: Ecommerce status (informational)
    try:
        from pagemap.ecommerce import ECOMMERCE_ENABLED

        body["ecommerce"] = {"enabled": ECOMMERCE_ENABLED}
    except Exception:  # nosec B110
        pass

    # S6: Metrics export loop status (informational)
    if srv._metrics_export_loop is not None:
        with suppress(Exception):
            body["metrics_export"] = {"running": srv._metrics_export_loop.running}

    # S7: Circuit breaker states (informational)
    try:
        from pagemap.resilience.circuit_breaker import get_breaker_states

        cb_states = get_breaker_states()
        if cb_states:
            body["circuit_breakers"] = cb_states
    except Exception:  # nosec B110
        pass

    # S7: SLI/SLO snapshot (informational — actual budget from tracker)
    if srv._sli_tracker is not None:
        with suppress(Exception):  # nosec B110
            body["sli"] = {
                "budget_remaining": srv._sli_tracker.budget_remaining,  # type: ignore[union-attr]
                "is_burning_fast": srv._sli_tracker.is_burning_fast(),  # type: ignore[union-attr]
                "p95_latency_ms": srv._sli_tracker.p95_latency_ms,  # type: ignore[union-attr]
            }
    else:
        try:
            from pagemap.resilience.sli_slo import AVAILABILITY_SLO as _SLO  # noqa: F401

            body["sli"] = {"available": True}
        except ImportError:
            pass

    # S7: DB health check (informational — never fails readiness)
    if srv._repository is not None and hasattr(srv._repository, "health_check"):
        try:
            is_healthy = await asyncio.wait_for(srv._repository.health_check(), timeout=2.0)
            body["db"] = {"connected": is_healthy}
        except Exception:  # nosec B110
            body["db"] = {"connected": False}

    # DB pool statistics (informational)
    if srv._repository is not None and hasattr(srv._repository, "pool_stats"):
        try:
            ps = srv._repository.pool_stats()
            body["db_pool"] = {
                "size": ps.pool_size,
                "min": ps.pool_min,
                "max": ps.pool_max,
                "idle": ps.idle,
                "active": ps.active,
                "waiting": ps.waiting,
                "available": ps.pool_available,
            }
        except Exception:  # nosec B110
            pass

    # S8: Paddle + metering status (informational)
    body["paddle"] = {"enabled": srv._paddle_config is not None}
    try:
        from pagemap.metering import METERING_ENABLED

        body["metering"] = {"enabled": METERING_ENABLED}
    except Exception:  # nosec B110
        pass

    # S3: Outbox status (informational — never fails readiness)
    if srv._outbox_poller is not None:
        try:
            counts = await srv._repository.outbox_count_by_status()
            body["outbox"] = {"counts": counts, "poller_running": srv._outbox_poller.running}
        except Exception:  # nosec B110
            pass

    return JSONResponse(body, status_code=200 if ready else 503)


async def _liveness_probe(request):
    """K8s liveness probe — process alive check."""
    return await _health_check(request)


async def _readiness_probe(request):
    """K8s readiness probe — drain mode aware."""
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    if srv._draining:
        return JSONResponse(
            {"status": "draining", "transport": srv._transport_mode},
            status_code=503,
        )
    return await _readiness_check(request)


async def _startup_probe(request):
    """K8s startup probe — browser pool initialization check."""
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    if srv._transport_mode != "http" or srv._session_manager is None:
        return JSONResponse({"status": "not_started"}, status_code=503)
    if hasattr(srv._session_manager, "_pool"):
        h = srv._session_manager._pool.health()
        if h.browser_connected:
            return JSONResponse({"status": "started", "transport": "http"})
        return JSONResponse({"status": "starting"}, status_code=503)
    return JSONResponse({"status": "started", "transport": "http"})


# ── SLA report endpoint ───────────────────────────────────────────────


async def _sla_report(request):
    """Return SLA report from ErrorBudgetTracker."""
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    if srv._sli_tracker is None:
        return JSONResponse({"error": "SLI tracker not initialized"}, status_code=503)

    try:
        report = srv._sli_tracker.get_sla_report()
        return JSONResponse(report)
    except Exception:
        return JSONResponse({"error": "SLA report generation failed"}, status_code=500)


# ── Prometheus metrics endpoint ────────────────────────────────────────


async def _metrics_endpoint(request):
    """Prometheus /metrics endpoint — exposes SLI, pool, and cache gauges."""
    import pagemap.server as srv

    try:
        from prometheus_client import CollectorRegistry, Gauge, generate_latest

        registry = CollectorRegistry()

        # SLI tracker metrics
        if srv._sli_tracker is not None:
            snap = srv._sli_tracker.get_snapshot()
            g = Gauge("pagemap_sli_budget_remaining", "SLO error budget remaining (0-1)", registry=registry)
            g.set(snap["budget_remaining"])
            g_burning = Gauge(
                "pagemap_sli_burning_fast", "1 if burn rate exceeds all window thresholds", registry=registry
            )
            g_burning.set(1.0 if snap["is_burning_fast"] else 0.0)
            for window_key, rate in snap.get("burn_rates", {}).items():
                g_br = Gauge(
                    f"pagemap_sli_burn_rate_{window_key.rstrip('s')}",
                    f"Burn rate for {window_key} window",
                    registry=registry,
                )
                g_br.set(rate)

        # Browser pool metrics
        if srv._session_manager is not None and hasattr(srv._session_manager, "_pool"):
            h = srv._session_manager._pool.health()
            Gauge("pagemap_pool_active", "Active browser contexts", registry=registry).set(h.active)
            Gauge("pagemap_pool_max", "Max browser contexts", registry=registry).set(h.max_contexts)
            Gauge("pagemap_pool_connected", "Browser connected (1/0)", registry=registry).set(
                1.0 if h.browser_connected else 0.0
            )

        # Session manager metrics
        if srv._session_manager is not None:
            Gauge("pagemap_sessions_active", "Active HTTP sessions", registry=registry).set(
                srv._session_manager.active_sessions
            )

        from starlette.responses import Response

        return Response(generate_latest(registry), media_type="text/plain; version=0.0.4; charset=utf-8")
    except ImportError:
        from starlette.responses import JSONResponse

        return JSONResponse(
            {"error": "prometheus-client not installed: pip install prometheus-client"},
            status_code=501,
        )


# ── OpenAPI / Docs endpoints ──────────────────────────────────────────


async def _openapi_spec(request):
    from starlette.responses import JSONResponse

    import pagemap.server as srv

    try:
        from pagemap.openapi import generate_openapi_spec

        spec = generate_openapi_spec(srv.mcp)
        return JSONResponse(spec, headers={"cache-control": "public, max-age=3600"})
    except Exception:
        return JSONResponse({"error": "OpenAPI spec generation failed"}, status_code=500)


async def _openapi_yaml(request):
    from starlette.responses import Response

    import pagemap.server as srv

    try:
        from pagemap.openapi import generate_openapi_spec, spec_to_yaml

        spec = generate_openapi_spec(srv.mcp)
        return Response(spec_to_yaml(spec), media_type="text/yaml", headers={"cache-control": "public, max-age=3600"})
    except Exception:
        return Response("error: OpenAPI spec generation failed\n", media_type="text/yaml", status_code=500)


async def _openapi_docs(request):
    from starlette.responses import HTMLResponse

    try:
        from pagemap.openapi import generate_scalar_html

        html = generate_scalar_html(spec_url="/openapi.json")
        return HTMLResponse(html)
    except Exception:
        return HTMLResponse("<h1>API docs unavailable</h1>", status_code=500)


# ── Programmatic route registration ──────────────────────────────────


def register_health_routes(mcp_instance) -> None:
    """Register health/docs routes on an MCP instance.

    Must be called after ``mcp = FastMCP(...)`` and before ``streamable_http_app()``.
    """
    mcp_instance.custom_route("/metrics", methods=["GET"])(_metrics_endpoint)
    mcp_instance.custom_route("/v1/sla", methods=["GET"])(_sla_report)
    mcp_instance.custom_route("/health", methods=["GET"])(_health_check)
    mcp_instance.custom_route("/ready", methods=["GET"])(_readiness_check)
    mcp_instance.custom_route("/livez", methods=["GET"])(_liveness_probe)
    mcp_instance.custom_route("/readyz", methods=["GET"])(_readiness_probe)
    mcp_instance.custom_route("/startupz", methods=["GET"])(_startup_probe)
    mcp_instance.custom_route("/openapi.json", methods=["GET"])(_openapi_spec)
    mcp_instance.custom_route("/v1/openapi.json", methods=["GET"])(_openapi_spec)
    mcp_instance.custom_route("/openapi.yaml", methods=["GET"])(_openapi_yaml)
    mcp_instance.custom_route("/v1/openapi.yaml", methods=["GET"])(_openapi_yaml)
    mcp_instance.custom_route("/docs", methods=["GET"])(_openapi_docs)
    mcp_instance.custom_route("/v1/docs", methods=["GET"])(_openapi_docs)


# ── HTTP server bootstrap ─────────────────────────────────────────────


async def _run_http_server(
    host: str,
    port: int,
    *,
    trusted_proxies: list[str] | None = None,
    drain_timeout: int = 30,
    enable_otel_traces: bool = False,
    telemetry_enabled: bool = False,
) -> None:
    """Run Streamable HTTP transport with BrowserPool lifecycle.

    Server-level lifecycle: one BrowserPool shared across all MCP sessions.
    MCP session management is handled by StreamableHTTPSessionManager internally.

    Module globals modified via srv.X:
        _session_manager, _draining, _repository, _rate_limiter,
        _paddle_config, _creem_config, _usage_sync, _webhook_cleanup, _sli_tracker, _outbox_poller
    """
    import pagemap.server as srv

    from .browser_pool import BrowserPool
    from .session_manager import HttpSessionManager

    # ── Repository initialization (3-tier: Supabase → SQLite → InMemory) ──
    _enable_supabase = os.environ.get("SUPABASE_DB_URL", "")
    if _enable_supabase:
        try:
            from pagemap.repository_supabase import SupabaseRepository
            from pagemap.supabase_config import SupabaseConfig

            sb_config = SupabaseConfig.from_env()
            if sb_config is None:
                logger.error("SUPABASE_DB_URL set but SupabaseConfig.from_env() returned None")
                raise SystemExit(1)
            srv._repository = await SupabaseRepository.create(sb_config)
            logger.info("Supabase PostgreSQL repository initialized")
        except ImportError:
            logger.error("psycopg not installed: pip install retio-pagemap[supabase]")
            raise SystemExit(1) from None
    elif srv._db_path:
        from pagemap.repository_sqlite import SqliteRepository

        srv._repository = await SqliteRepository.create(srv._db_path)
        logger.info("SQLite repository: %s", srv._db_path)
    else:
        from pagemap.repository import InMemoryRepository

        srv._repository = InMemoryRepository()
        logger.info("In-memory repository (no --db-path)")

    # ── Rate limiter initialization ────────────────────────────────
    from pagemap.rate_limiter import RateLimitConfig, RateLimiter

    if srv._rate_limiter is None:
        rl_config = RateLimitConfig(enabled=True)
        redis_url = os.environ.get("REDIS_URL", "")
        if redis_url:
            try:
                from pagemap.redis_rate_limiter import RedisRateLimiter

                srv._rate_limiter = await RedisRateLimiter.create(redis_url, rl_config)
                logger.info("Redis rate limiter enabled")
            except Exception as e:
                logger.warning("Redis rate limiter init failed, using in-process: %s", e)
                srv._rate_limiter = RateLimiter(rl_config)
        else:
            srv._rate_limiter = RateLimiter()
    logger.info("Rate limiter initialized")

    # S7: SLI tracker — one-time initialization
    try:
        from pagemap.resilience.sli_slo import AVAILABILITY_SLO, ErrorBudgetTracker

        srv._sli_tracker = ErrorBudgetTracker(AVAILABILITY_SLO)
        logger.info("SLI tracker initialized (target=%.3f)", AVAILABILITY_SLO.target)
    except ImportError:
        logger.debug("SLI module not available")

    max_ctx = int(os.environ.get("PAGEMAP_MAX_CONTEXTS", "5"))
    pool = BrowserPool(max_contexts=max_ctx)
    async with pool:
        srv._session_manager = HttpSessionManager(pool, template_cache=srv._state.template_cache)
        logger.info("HTTP mode: BrowserPool started (max_contexts=%d)", max_ctx)
        _degrade_shutdown_event = asyncio.Event()
        _degrade_task = None
        _cqp_shutdown_event = asyncio.Event()
        _cqp_task = None
        _cqp_orchestrator = None
        _outbox_task = None
        try:
            import uvicorn

            starlette_app = srv.mcp.streamable_http_app()

            # ── Middleware chain (outermost wraps first, executes first) ──
            # Wrapping order is reverse of execution: last wrap = outermost.
            # Request flow: Gateway → RateLimit → Paddle → Auth → RestApi → Credit → SecurityHeaders → App
            # ──────────────────────────────────────────────────────────────────────────────────

            # 5. SecurityHeaders (innermost middleware, closest to app)
            from pagemap.security_headers import SecurityHeadersMiddleware

            starlette_app = SecurityHeadersMiddleware(starlette_app, require_tls=srv._require_tls)
            logger.info("SecurityHeaders middleware enabled (require_tls=%s)", srv._require_tls)

            # ── Paddle config (needed by metering + webhook + REST) ──
            from pagemap.paddle.config import PaddleConfig

            srv._paddle_config = PaddleConfig.from_env()

            # ── Creem config (needed by webhook) ──
            from pagemap.cloud.creem.config import CreemConfig

            srv._creem_config = CreemConfig.from_env()

            # 4. Credit (between Auth and SecurityHeaders)
            from pagemap.credit_middleware import CreditMiddleware

            # S8: Metering usage sync (opt-in)
            srv._usage_sync = None
            try:
                from pagemap.metering import METERING_ENABLED

                if METERING_ENABLED and srv._paddle_config is not None:
                    from pagemap.metering.usage_sync import UsageSyncBuffer

                    srv._usage_sync = UsageSyncBuffer(srv._paddle_config)
                    logger.info("Metering usage sync buffer created")
            except ImportError:
                logger.debug("Metering module not available")

            _topup_url = os.environ.get("PAGEMAP_TOPUP_URL", "").strip()
            starlette_app = CreditMiddleware(
                starlette_app, repository=srv._repository, usage_sync=srv._usage_sync, topup_url=_topup_url
            )
            logger.info(
                "Credit middleware enabled (metering=%s, topup_url=%s)", srv._usage_sync is not None, bool(_topup_url)
            )

            # 3c. WS auth + session manager setup
            _ws_token_store = None
            _ws_session_mgr = None
            try:
                from pagemap.ws_auth import EphemeralTokenStore

                from .ws_session_manager import WsSessionManager

                _ws_token_store = EphemeralTokenStore()
                _ws_session_mgr = WsSessionManager(browser_pool=pool)
                logger.info("WS auth + session manager enabled")
            except ImportError:
                logger.debug("WS auth modules not available")

            # 3b. REST API (intercepts /v1/* before Credit middleware)
            from pagemap.rest_api import RestApiHandler

            _rest_handler = RestApiHandler(
                starlette_app,
                repository=srv._repository,
                credit_repo=srv._repository,
                paddle_config=srv._paddle_config,
            )
            if _ws_token_store is not None:
                _rest_handler._ws_token_store = _ws_token_store
            # S1: Wire signup limiter into REST handler
            from pagemap.security.signup_limiter import SignupLimiter

            _rest_handler._signup_limiter = SignupLimiter()
            starlette_app = _rest_handler
            logger.info("REST API handler enabled (/v1/*)")

            # 3a'. A/B Assignment (S5 CQP) — after BOLA, before RestApi
            _enable_ab = os.environ.get("PAGEMAP_ENABLE_AB", "").strip().lower() in ("1", "true")
            if _enable_ab:
                try:
                    from pagemap.cqp.ab_framework import ABAssignmentMiddleware

                    starlette_app = ABAssignmentMiddleware(starlette_app, repository=srv._repository)
                    logger.info("A/B assignment middleware enabled")
                except ImportError:
                    logger.debug("A/B assignment middleware not available")

            # 3a. BOLA (S4) — Auth → BOLA → RestApi execution order
            try:
                from pagemap.security import BOLA_ENABLED
                from pagemap.security.bola_validator import BolaConfig, BolaMiddleware

                _bola_audit = os.environ.get("PAGEMAP_BOLA_AUDIT_ONLY", "").strip().lower() in ("1", "true")
                starlette_app = BolaMiddleware(
                    starlette_app,
                    config=BolaConfig(enabled=BOLA_ENABLED, audit_only=_bola_audit),
                )
                logger.info("BOLA middleware enabled (enabled=%s, audit_only=%s)", BOLA_ENABLED, _bola_audit)
            except ImportError:
                logger.debug("BOLA middleware not available")

            # 3. Auth (dual: API key + JWT)
            from pagemap.auth_middleware import AuthMiddleware

            _jwt_config = None
            _jwk_client = None
            try:
                from pagemap.jwt_verifier import SupabaseJwtConfig

                _jwt_config = SupabaseJwtConfig.from_env()
                if _jwt_config:
                    from jwt import PyJWKClient

                    _jwk_client = PyJWKClient(_jwt_config.jwks_url, cache_jwk_set=True, lifespan=300)
                    logger.info("JWT auth enabled (project=%s)", _jwt_config.project_url)
            except ImportError:
                logger.debug("JWT auth not available (PyJWT not installed)")

            _auth_middleware = AuthMiddleware(
                starlette_app, srv._repository, jwt_config=_jwt_config, jwk_client=_jwk_client
            )
            if _ws_token_store is not None:
                _auth_middleware._ws_token_store = _ws_token_store
            starlette_app = _auth_middleware
            logger.info(
                "Auth middleware enabled (dual-auth=%s, ws_auth=%s)",
                _jwt_config is not None,
                _ws_token_store is not None,
            )

            # 2c. Incident response (IncidentResponder + ActionExecutor)
            try:
                from pagemap.security.action_executor import ActionExecutor
                from pagemap.security.incident_response import IncidentResponder

                _action_executor = ActionExecutor(session_manager=srv._session_manager, cache=srv._state.cache)
                _incident_responder = IncidentResponder(executor=_action_executor)
                logger.info("Incident response enabled (P1-P4 classification)")
            except ImportError:
                logger.debug("Incident response modules not available")

            # 2b. Paddle webhook (between Auth and RateLimit)
            if srv._paddle_config is not None:
                from pagemap.paddle.webhook import PaddleWebhookHandler

                # S8: Subscription and payment failure handlers
                _subscription_handler = None
                _payment_failure_handler = None
                try:
                    from pagemap.paddle.payment_failure import PaymentFailureHandler
                    from pagemap.paddle.subscription_credits import SubscriptionCreditHandler

                    _subscription_handler = SubscriptionCreditHandler(
                        credit_repo=srv._repository, repository=srv._repository
                    )
                    _payment_failure_handler = PaymentFailureHandler(
                        credit_repo=srv._repository, repository=srv._repository
                    )
                    logger.info("Subscription + payment failure handlers enabled")
                except ImportError:
                    logger.debug("Subscription handlers not available")

                # S3: Outbox mode (feature flag)
                _enable_outbox = os.environ.get("PAGEMAP_ENABLE_OUTBOX", "").strip().lower() in ("1", "true", "yes")
                _outbox_fsm = None
                _outbox_saga = None

                if _enable_outbox:
                    try:
                        from pagemap.paddle.outbox import OutboxPoller
                        from pagemap.paddle.saga_coordinator import SagaCoordinator
                        from pagemap.paddle.webhook_fsm import TransactionFSM

                        _outbox_fsm = TransactionFSM()
                        _outbox_saga = SagaCoordinator(credit_repo=srv._repository)

                        _webhook_handler = PaddleWebhookHandler(
                            starlette_app,
                            srv._paddle_config,
                            credit_repo=srv._repository,
                            audit_repo=srv._repository,
                            subscription_handler=_subscription_handler,
                            payment_failure_handler=_payment_failure_handler,
                            enable_outbox=True,
                            outbox_repo=srv._repository,
                            fsm=_outbox_fsm,
                            saga=_outbox_saga,
                        )

                        srv._outbox_poller = OutboxPoller(
                            srv._repository,
                            process_fn=_webhook_handler.process_event,
                            poll_interval=5.0,
                        )
                        starlette_app = _webhook_handler
                        logger.info("Paddle webhook: outbox mode enabled")
                    except ImportError:
                        logger.warning("Paddle outbox mode requested but dependencies missing, falling back to sync")
                        _enable_outbox = False

                if not _enable_outbox:
                    starlette_app = PaddleWebhookHandler(
                        starlette_app,
                        srv._paddle_config,
                        credit_repo=srv._repository,
                        audit_repo=srv._repository,
                        subscription_handler=_subscription_handler,
                        payment_failure_handler=_payment_failure_handler,
                    )

                logger.info(
                    "Paddle webhook handler enabled (env=%s, outbox=%s)",
                    srv._paddle_config.environment,
                    _enable_outbox,
                )

                # S8: Webhook cleanup background task
                srv._webhook_cleanup = None
                try:
                    from pagemap.paddle.webhook_cleanup import WebhookCleanup

                    srv._webhook_cleanup = WebhookCleanup(srv._repository)
                    logger.info("Webhook cleanup task created (retention_days=90)")
                except ImportError:
                    logger.debug("Webhook cleanup not available")

            # 2c. Creem webhook (same layer as Paddle — between Auth and RateLimit)
            if srv._creem_config is not None:
                from pagemap.cloud.creem.webhook import CreemWebhookHandler

                starlette_app = CreemWebhookHandler(
                    starlette_app,
                    srv._creem_config,
                    credit_repo=srv._repository,
                    audit_repo=srv._repository,
                )
                logger.info(
                    "Creem webhook handler enabled (env=%s)",
                    srv._creem_config.environment,
                )

            # 2. RateLimit
            from pagemap.rate_limit_middleware import RateLimitMiddleware

            starlette_app = RateLimitMiddleware(starlette_app, srv._rate_limiter, repository=srv._repository)
            logger.info("RateLimit middleware enabled")

            # 1d. Circuit Breaker (S7)
            _enable_cb = os.environ.get("PAGEMAP_ENABLE_CIRCUIT_BREAKER", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if _enable_cb:
                try:
                    from pagemap.resilience.middleware import CircuitBreakerMiddleware

                    starlette_app = CircuitBreakerMiddleware(starlette_app)
                    logger.info("CircuitBreaker middleware enabled")
                except ImportError:
                    logger.debug("CircuitBreaker middleware not available")

            # 1c. Deadline Propagation (S7)
            _enable_deadline = os.environ.get("PAGEMAP_ENABLE_DEADLINE", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if _enable_deadline:
                try:
                    from pagemap.resilience.middleware import DeadlinePropagationMiddleware

                    starlette_app = DeadlinePropagationMiddleware(starlette_app)
                    logger.info("Deadline propagation middleware enabled")
                except ImportError:
                    logger.debug("Deadline propagation middleware not available")

            # 1b. API Versioning (S5)
            try:
                from pagemap.api_versioning import ApiVersioningMiddleware

                starlette_app = ApiVersioningMiddleware(starlette_app)
                logger.info("API versioning middleware enabled")
            except ImportError:
                logger.debug("API versioning not available")

            # 1. Gateway
            if trusted_proxies:
                from pagemap.gateway import GatewayMiddleware, parse_trusted_proxies

                gw_config = parse_trusted_proxies(trusted_proxies)
                starlette_app = GatewayMiddleware(starlette_app, gw_config)
                logger.info("Gateway middleware enabled (trusted_proxies=%s)", trusted_proxies)

            # 0. OTel Trace (outermost — S6)
            if enable_otel_traces:
                if not telemetry_enabled:
                    logger.warning("--enable-otel-traces requires --telemetry; ignoring")
                else:
                    try:
                        from pagemap.telemetry.otel_traces import OTelTraceMiddleware

                        starlette_app = OTelTraceMiddleware(starlette_app)
                        logger.info("OTel trace middleware enabled")
                    except Exception:  # nosec B110
                        logger.warning("Failed to enable OTel trace middleware")

            config = uvicorn.Config(
                starlette_app,
                host=host,
                port=port,
                log_level="info",
                timeout_graceful_shutdown=drain_timeout,
            )
            server = uvicorn.Server(config)

            # C1: Wrap uvicorn's handle_exit to set drain flag before shutdown.
            # capture_signals() registers signal.signal(sig, self.handle_exit),
            # so instance override takes priority. Cross-platform via signal.signal().
            _original_handle_exit = server.handle_exit

            def _drain_then_exit(sig: int, frame) -> None:
                srv._draining = True
                logger.info("Shutdown signal (sig=%d), drain mode (timeout=%ds)", sig, drain_timeout)
                _original_handle_exit(sig, frame)

            server.handle_exit = _drain_then_exit  # type: ignore[assignment]

            # S3: Start outbox poller + saga sweeper as background tasks
            if srv._outbox_poller is not None:

                async def _resilient_worker(name, fn):
                    """Individual worker failure isolation: crash → 5s wait → restart."""
                    while True:
                        try:
                            await fn()
                        except Exception:
                            logger.exception("worker_%s_crashed", name)
                            await asyncio.sleep(5)

                _outbox_task = asyncio.create_task(
                    _resilient_worker("outbox_poller", srv._outbox_poller.run),
                    name="outbox_poller",
                )
                logger.info("Outbox poller background task started")

            # S8: Start metering usage sync + webhook cleanup background tasks
            if srv._usage_sync is not None:
                srv._usage_sync.start()
                logger.info("Metering usage sync started")
            if srv._webhook_cleanup is not None:
                srv._webhook_cleanup.start()
                logger.info("Webhook cleanup background task started")

            # S1: Data retention cleanup background task
            srv._data_retention = None
            try:
                from pagemap.data_retention import DataRetentionCleanup

                srv._data_retention = DataRetentionCleanup(srv._repository)
                srv._data_retention.start()
                logger.info("Data retention cleanup started")
            except ImportError:
                logger.debug("Data retention module not available")

            # S1: Degradation engine periodic wiring (pool stats → DegradationSignals)
            if srv._repository is not None and hasattr(srv._repository, "pool_stats"):
                try:
                    from pagemap.resilience.degradation import DegradationConfig, DegradationEngine, DegradationSignals

                    _degrade_engine = DegradationEngine(DegradationConfig.from_env())

                    async def _degradation_periodic():
                        while not _degrade_shutdown_event.is_set():
                            try:
                                async with asyncio.timeout(30.0):
                                    await _degrade_shutdown_event.wait()
                            except TimeoutError:
                                try:
                                    ps = srv._repository.pool_stats()
                                    # S3: Feed pool utilization to degradation signals
                                    _pool_util = 0.0
                                    if hasattr(srv._session_manager, "_pool"):
                                        _ph = srv._session_manager._pool.health()
                                        _pool_util = _ph.active / max(_ph.max_contexts, 1)
                                    signals = DegradationSignals(
                                        error_rate=ps.requests_errors / max(ps.requests_num, 1),
                                        health_check_passed=ps.pool_available,
                                        pool_utilization=_pool_util,
                                    )
                                    _degrade_engine.update(signals)
                                    # S3: SLI burn-rate → notification dispatch
                                    if srv._sli_tracker is not None and srv._sli_tracker.is_burning_fast():
                                        try:
                                            from pagemap.telemetry.notification_dispatcher import (
                                                NotificationDispatcher,
                                                NotificationPayload,
                                            )

                                            _notifier = NotificationDispatcher()
                                            if _notifier.is_enabled():
                                                _notifier.dispatch(
                                                    NotificationPayload(
                                                        source="sli",
                                                        severity="critical",
                                                        rule_name="error_budget_burn_rate",
                                                        title="Error budget burning fast",
                                                        description=f"All burn-rate windows exceeded. Budget remaining: {srv._sli_tracker.budget_remaining:.2%}",
                                                        metric_value=srv._sli_tracker.budget_remaining,
                                                        threshold=0.0,
                                                    )
                                                )
                                        except Exception:  # nosec B110
                                            pass
                                except Exception:
                                    logger.debug("Degradation update failed", exc_info=True)

                    _degrade_task = asyncio.create_task(_degradation_periodic(), name="degradation_periodic")
                    logger.info("Degradation engine periodic task started (interval=30s)")
                except ImportError:
                    logger.debug("Degradation engine not available")

            # S5: CQP modules + orchestrator + DB persistence
            if srv._cqp_emitter is not None:
                try:
                    from pagemap.cqp.ab_framework import ExperimentStore, ThompsonSampler
                    from pagemap.cqp.adaptive_alpha import AdaptiveAlphaController
                    from pagemap.cqp.cold_start import ColdStartManager
                    from pagemap.cqp.disagreement_detector import DisagreementDetector
                    from pagemap.cqp.eqpv_registry import EQPVRegistry
                    from pagemap.cqp.guardrail import GuardrailChecker
                    from pagemap.cqp.orchestrator import CQPOrchestrator
                    from pagemap.cqp.rule_adjuster import RuleAdjuster
                    from pagemap.telemetry import _collector as _telem_collector

                    srv._cqp_detector = DisagreementDetector(
                        alpha_controller=AdaptiveAlphaController(),
                    )
                    _cold_start = ColdStartManager()
                    _guardrail = GuardrailChecker()
                    _experiment_store = ExperimentStore()
                    _thompson = ThompsonSampler()
                    _rule_adjuster = RuleAdjuster()
                    _eqpv_registry = EQPVRegistry(emit_fn=srv._cqp_emitter)

                    if _telem_collector is not None:
                        _telem_collector.register_batch_hook(srv._cqp_detector)
                        logger.info("CQP disagreement detector batch hook registered")

                    srv._cqp_eqpv_registry = _eqpv_registry
                    srv._cqp_cold_start = _cold_start
                    srv._cqp_thompson = _thompson

                    # A3: Cross-site pruning transfer (env-gated)
                    _transfer_registry = None
                    if os.environ.get("PAGEMAP_CQP_CROSS_SITE_TRANSFER") == "1":
                        from pagemap.cqp.cross_site_transfer import CrossSiteTransferRegistry

                        _transfer_registry = CrossSiteTransferRegistry(emit_fn=srv._cqp_emitter)
                    srv._cqp_transfer_registry = _transfer_registry

                    _cqp_orchestrator = CQPOrchestrator(
                        detector=srv._cqp_detector,
                        cold_start=_cold_start,
                        guardrail=_guardrail,
                        adjuster=_rule_adjuster,
                        experiment_store=_experiment_store,
                        thompson_sampler=_thompson,
                        eqpv_registry=_eqpv_registry,
                        transfer_registry=_transfer_registry,
                        repository=srv._repository,
                        emit_fn=srv._cqp_emitter,
                    )

                    # Initialize from DB (load persisted state)
                    await _cqp_orchestrator.initialize_all()
                    logger.info("CQP orchestrator initialized from DB")
                except ImportError:
                    logger.debug("CQP modules not available")
                except Exception:
                    logger.warning("CQP orchestrator initialization failed", exc_info=True)

            # S11: CQP periodic session eviction + DB flush (Event.wait + asyncio.timeout pattern)
            if srv._cqp_emitter is not None:
                _flush_counter = 0

                async def _cqp_periodic():
                    nonlocal _flush_counter
                    while not _cqp_shutdown_event.is_set():
                        try:
                            async with asyncio.timeout(60.0):
                                await _cqp_shutdown_event.wait()
                                break
                        except TimeoutError:
                            pass
                        _flush_counter += 1
                        # DB flush every 60s
                        if _cqp_orchestrator is not None:
                            try:
                                await _cqp_orchestrator.flush_all_to_db()
                            except Exception:
                                logger.debug("CQP DB flush failed", exc_info=True)
                        # Session eviction every 300s (5 cycles)
                        if _flush_counter % 5 == 0:
                            try:
                                srv._cqp_emitter.evict_stale_sessions()  # type: ignore[union-attr]
                            except Exception:
                                logger.debug("CQP eviction failed", exc_info=True)

                _cqp_task = asyncio.create_task(_cqp_periodic(), name="cqp_periodic")
                logger.info("CQP periodic flush+eviction task started (flush=60s, evict=300s)")

            await server.serve()
        finally:
            # S3: Shutdown outbox poller
            if srv._outbox_poller is not None:
                try:
                    srv._outbox_poller.request_shutdown()
                    if _outbox_task is not None:
                        _outbox_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await _outbox_task
                    logger.info("Outbox poller stopped")
                except Exception:  # nosec B110
                    pass

            # S8: Shutdown metering usage sync + webhook cleanup
            if srv._usage_sync is not None:
                try:
                    await srv._usage_sync.shutdown()
                    logger.info("Metering usage sync stopped")
                except Exception:  # nosec B110
                    pass
            if srv._webhook_cleanup is not None:
                try:
                    await srv._webhook_cleanup.shutdown()
                    logger.info("Webhook cleanup stopped")
                except Exception:  # nosec B110
                    pass

            # S1: Shutdown data retention cleanup
            if srv._data_retention is not None:
                try:
                    await srv._data_retention.shutdown()
                    logger.info("Data retention cleanup stopped")
                except Exception:  # nosec B110
                    pass

            # S1: Shutdown degradation periodic task
            if _degrade_task is not None:
                _degrade_shutdown_event.set()
                with suppress(asyncio.CancelledError):
                    await _degrade_task

            # S11: CQP shutdown — final DB flush + emit sequences + stop periodic task
            if _cqp_orchestrator is not None:
                try:
                    await _cqp_orchestrator.flush_all_to_db()
                    logger.info("CQP final DB flush completed")
                except Exception:  # nosec B110
                    pass
            try:
                srv._emit_and_clear_sequences()
                logger.info("CQP sequences emitted on shutdown")
            except Exception:  # nosec B110
                pass
            if _cqp_task is not None:
                _cqp_shutdown_event.set()
                with suppress(asyncio.CancelledError):
                    await _cqp_task

            # S6: Shutdown metrics export loop and anomaly detector
            if srv._metrics_export_loop is not None:
                with suppress(Exception):  # nosec B110
                    srv._metrics_export_loop.shutdown()
            if srv._anomaly_detector is not None:
                with suppress(Exception):  # nosec B110
                    srv._anomaly_detector.shutdown()
            await srv._session_manager.shutdown()
            srv._session_manager = None
            if srv._repository is not None:
                await srv._repository.close()
                srv._repository = None
            srv._draining = False
            logger.info("HTTP mode: shutdown complete")
