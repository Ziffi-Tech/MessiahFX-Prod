"""
Service reverse proxy — forward requests to internal microservices.

Maps URL path prefixes to upstream service URLs:
  /journal/*     → JOURNAL_URL  (journal service)
  /risk/*        → RISK_URL     (risk engine)
  /strategy/*    → STRATEGY_URL (strategy engine + status endpoints)
  /backtest/*    → BACKTEST_URL (backtest + Monte Carlo + optimiser)
  /ai/*          → AI_FILTER_URL (Claude AI filter, regime, digest)
  /market-data/* → MARKET_DATA_URL (tick feeds)

The dashboard-next uses /api/gateway/{path} which the Next.js proxy
forwards to the gateway. This route handles all intra-service requests
that the specific gateway routes (control, signals, credentials) do not cover.

Auth is handled at the Next.js layer (mxauth cookie). The gateway
is internal-only so it trusts all requests reaching it.
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import httpx
import structlog

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_UPSTREAM: dict[str, str] = {
    "journal":     settings.JOURNAL_URL,
    "risk":        settings.RISK_URL,
    "strategy":    settings.STRATEGY_URL,
    "backtest":    settings.BACKTEST_URL,
    "ai":          settings.AI_FILTER_URL,
    "market-data": settings.MARKET_DATA_URL,
}


@router.api_route("/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, path: str, request: Request) -> StreamingResponse:
    """Forward request to the appropriate upstream service."""
    base = _UPSTREAM.get(service)
    if base is None:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": f"Unknown service: {service}"}, status_code=404)

    upstream = f"{base}/{path}"
    if request.url.query:
        upstream += f"?{request.url.query}"

    body = await request.body()

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)   # httpx re-computes this

    log.debug(
        "proxy.forward",
        service=service,
        path=path,
        upstream=upstream,
        method=request.method,
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=upstream,
                headers=headers,
                content=body,
            )
        return StreamingResponse(
            content=resp.aiter_bytes(),
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except httpx.ConnectError:
        log.error("proxy.connect_error", service=service, upstream=upstream)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"error": f"Service {service!r} is unreachable", "upstream": upstream},
            status_code=502,
        )
    except Exception as exc:
        log.error("proxy.error", service=service, error=str(exc))
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(exc)[:100]}, status_code=500)
