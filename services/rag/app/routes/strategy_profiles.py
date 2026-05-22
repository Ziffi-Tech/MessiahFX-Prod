"""
Strategy profile endpoints.

GET  /strategies                  — list all extracted profiles (summary view)
GET  /strategies/{source_id}      — full StrategyProfile for one document
DELETE /strategies/{source_id}    — remove a profile (chunks in Qdrant unaffected)

These profiles are generated automatically when a PDF is uploaded via
POST /ingest/pdf and ANTHROPIC_API_KEY is configured.

A profile contains the structured risk management and trading strategy
that Claude Sonnet extracted from the uploaded book or research note.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any

import structlog

from ..strategy_store import get_profile, list_profiles, delete_profile

log = structlog.get_logger()
router = APIRouter(prefix="/strategies", tags=["Strategy Profiles"])


# ── Response models ───────────────────────────────────────────────────────────

class ProfileSummary(BaseModel):
    """Lightweight summary returned by the list endpoint."""
    source_id: str
    source_title: str
    strategy_name: str
    strategy_type: str
    confidence: str
    extracted_at: str
    extraction_complete: bool
    entry_rules_count: int
    exit_rules_count: int


class RiskRules(BaseModel):
    max_risk_per_trade_pct: float | None = None
    max_portfolio_risk_pct: float | None = None
    position_sizing_method: str = "Not stated"
    max_drawdown_rule: str | None = None
    correlation_rules: str | None = None
    additional_rules: list[str] = []


class MarketConditions(BaseModel):
    works_in: list[str] = []
    avoid_in: list[str] = []


class StrategyProfileResponse(BaseModel):
    """Full strategy profile as stored in Redis."""
    source_id: str
    source_title: str
    strategy_name: str
    strategy_type: str
    core_thesis: str
    entry_criteria: list[str]
    exit_criteria: list[str]
    risk_rules: RiskRules
    instruments: list[str]
    timeframes: list[str]
    market_conditions: MarketConditions
    key_principles: list[str]
    edge_source: str
    expected_win_rate: str
    expected_rr_ratio: str
    confidence: str
    implementation_notes: str
    extracted_at: str
    sections_analysed: int
    total_sections: int
    extraction_complete: bool


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[ProfileSummary],
    summary="List all extracted strategy profiles",
)
async def list_strategy_profiles(request: Request) -> list[ProfileSummary]:
    """
    Return summary information for every strategy profile that has been
    extracted from an uploaded document.

    Sorted by extraction date, newest first.
    Returns an empty list if Redis is unavailable or no documents have been analysed.
    """
    redis = _get_redis(request)
    if redis is None:
        log.warning("strategy_profiles.no_redis")
        return []

    summaries = await list_profiles(redis)
    return [ProfileSummary(**s) for s in summaries]


@router.get(
    "/{source_id:path}",
    response_model=StrategyProfileResponse,
    summary="Get the full strategy profile for a document",
)
async def get_strategy_profile(
    source_id: str,
    request: Request,
) -> StrategyProfileResponse:
    """
    Retrieve the complete extracted StrategyProfile for a specific document.

    `source_id` is the identifier used when the PDF was uploaded
    (e.g. `books/turtle-trading`).

    The profile contains:
    - Entry and exit criteria extracted from the book
    - Full risk management framework
    - Market conditions where the strategy applies
    - Key principles from the author
    - Confidence rating (how explicitly the rules are stated)

    404 if the document has not been analysed yet or if ANTHROPIC_API_KEY
    was not configured at the time of upload.
    """
    redis = _get_redis(request)
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Redis is unavailable — strategy profiles cannot be retrieved",
        )

    profile = await get_profile(redis, source_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No strategy profile found for '{source_id}'. "
                "Upload the PDF via POST /ingest/pdf to generate one."
            ),
        )

    # Normalise nested objects into Pydantic models
    risk_raw: dict[str, Any] = profile.get("risk_rules", {})
    mc_raw: dict[str, Any] = profile.get("market_conditions", {})

    return StrategyProfileResponse(
        source_id=profile.get("source_id", source_id),
        source_title=profile.get("source_title", ""),
        strategy_name=profile.get("strategy_name", ""),
        strategy_type=profile.get("strategy_type", "other"),
        core_thesis=profile.get("core_thesis", ""),
        entry_criteria=profile.get("entry_criteria", []),
        exit_criteria=profile.get("exit_criteria", []),
        risk_rules=RiskRules(
            max_risk_per_trade_pct=risk_raw.get("max_risk_per_trade_pct"),
            max_portfolio_risk_pct=risk_raw.get("max_portfolio_risk_pct"),
            position_sizing_method=risk_raw.get("position_sizing_method", "Not stated"),
            max_drawdown_rule=risk_raw.get("max_drawdown_rule"),
            correlation_rules=risk_raw.get("correlation_rules"),
            additional_rules=risk_raw.get("additional_rules", []),
        ),
        instruments=profile.get("instruments", []),
        timeframes=profile.get("timeframes", []),
        market_conditions=MarketConditions(
            works_in=mc_raw.get("works_in", []),
            avoid_in=mc_raw.get("avoid_in", []),
        ),
        key_principles=profile.get("key_principles", []),
        edge_source=profile.get("edge_source", ""),
        expected_win_rate=profile.get("expected_win_rate", "Not stated"),
        expected_rr_ratio=profile.get("expected_rr_ratio", "Not stated"),
        confidence=profile.get("confidence", "low"),
        implementation_notes=profile.get("implementation_notes", ""),
        extracted_at=profile.get("extracted_at", ""),
        sections_analysed=profile.get("sections_analysed", 0),
        total_sections=profile.get("total_sections", 0),
        extraction_complete=profile.get("extraction_complete", False),
    )


@router.delete(
    "/{source_id:path}",
    summary="Delete a strategy profile",
    status_code=204,
)
async def delete_strategy_profile(
    source_id: str,
    request: Request,
) -> None:
    """
    Remove a strategy profile from Redis.

    This does NOT delete the document's chunks from Qdrant — you can still
    query the document via POST /query. Only the strategy extraction is removed.

    To re-extract, re-upload the PDF via POST /ingest/pdf.
    """
    redis = _get_redis(request)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    existed = await delete_profile(redis, source_id)
    if not existed:
        raise HTTPException(
            status_code=404,
            detail=f"No strategy profile found for '{source_id}'",
        )
