"""
Quant Book Analyser — Claude Sonnet strategy extraction pipeline.

When a trading book or research note is uploaded, this module reads the full
document text and extracts a structured StrategyProfile containing:

  - strategy_type (trend_following, mean_reversion, momentum, etc.)
  - entry_criteria (specific, actionable entry rules from the book)
  - exit_criteria (stop loss, take profit, trailing stop, time-based exits)
  - risk_rules (position sizing, max drawdown policy, correlation limits)
  - market_conditions (when to use / when to avoid)
  - key_principles (the author's core philosophical points)
  - edge_source (what gives this strategy its statistical edge)
  - expected win rate and R:R ratio if stated
  - confidence rating (high = explicit rules, low = general principles only)

Large books are processed in sections (ANALYSIS_SECTION_CHARS per section)
to stay within Claude's context window.  Section extractions are then merged
in a final synthesis pass.

Claude Sonnet is used (not Haiku) because this is a deep reasoning task
that runs once per document.  Haiku is used for per-query synthesis.

Prompt caching is applied on the system prompt — the same system prompt is
reused for every section of the same book, reducing token costs.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import anthropic
import structlog

from .config import Settings

log = structlog.get_logger()


# ── StrategyProfile schema ────────────────────────────────────────────────────

_EXTRACTION_TOOL: dict[str, Any] = {
    "name": "extract_trading_strategy",
    "description": (
        "Extract a complete, actionable trading strategy and risk management "
        "framework from the provided text. Only include rules and principles "
        "that are explicitly stated in the text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy_name": {
                "type": "string",
                "description": (
                    "Short descriptive name for this strategy "
                    "(e.g. 'Turtle Trading System', 'Momentum Breakout', "
                    "'Mean-Reversion Pairs')"
                ),
            },
            "strategy_type": {
                "type": "string",
                "enum": [
                    "trend_following", "mean_reversion", "momentum",
                    "carry", "arbitrage", "breakout", "swing",
                    "scalping", "multi_strategy", "risk_management_only", "other",
                ],
            },
            "core_thesis": {
                "type": "string",
                "description": (
                    "1–3 sentences explaining the core market hypothesis "
                    "or inefficiency this strategy exploits."
                ),
            },
            "entry_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific, actionable entry conditions. Each item is one "
                    "concrete rule (e.g. 'Buy when price closes above 20-day "
                    "high with volume > 1.5× average'). Omit vague statements."
                ),
                "maxItems": 12,
            },
            "exit_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific exit rules: hard stop loss, take profit targets, "
                    "trailing stops, time-based exits, re-entry conditions."
                ),
                "maxItems": 10,
            },
            "risk_rules": {
                "type": "object",
                "description": "Complete risk management framework from the book.",
                "properties": {
                    "max_risk_per_trade_pct": {
                        "type": "number",
                        "description": (
                            "Maximum % of capital risked per trade. "
                            "null if not stated."
                        ),
                    },
                    "max_portfolio_risk_pct": {
                        "type": "number",
                        "description": (
                            "Maximum % of capital at risk across all open "
                            "positions simultaneously. null if not stated."
                        ),
                    },
                    "position_sizing_method": {
                        "type": "string",
                        "description": (
                            "How position size is determined: "
                            "fixed %, ATR-based, Kelly criterion, "
                            "volatility-normalised, equal-weight, etc."
                        ),
                    },
                    "max_drawdown_rule": {
                        "type": "string",
                        "description": (
                            "What action to take when drawdown exceeds a "
                            "threshold (reduce size, stop trading, etc.)."
                        ),
                    },
                    "correlation_rules": {
                        "type": "string",
                        "description": (
                            "How correlated or concentrated positions are "
                            "managed. 'Not stated' if absent."
                        ),
                    },
                    "additional_rules": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Any other explicitly stated risk rules.",
                        "maxItems": 8,
                    },
                },
                "required": ["position_sizing_method"],
            },
            "instruments": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Asset classes and markets the strategy applies to: "
                    "crypto, forex, equities, futures, options, bonds, commodities."
                ),
            },
            "timeframes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Applicable trading timeframes: scalp (< 1h), "
                    "intraday (1h–1d), swing (1d–2w), position (> 2w)."
                ),
            },
            "market_conditions": {
                "type": "object",
                "properties": {
                    "works_in": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Conditions where the strategy performs well.",
                    },
                    "avoid_in": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Conditions to avoid or reduce exposure.",
                    },
                },
            },
            "key_principles": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Core philosophical principles from the author — the "
                    "trading rules they consider non-negotiable. Max 8."
                ),
                "maxItems": 8,
            },
            "edge_source": {
                "type": "string",
                "description": (
                    "What gives this strategy its statistical edge — the "
                    "specific market inefficiency or behavioural bias it exploits."
                ),
            },
            "expected_win_rate": {
                "type": "string",
                "description": (
                    "Expected win rate if explicitly stated (e.g. '40%', "
                    "'40–50%'). Write 'Not stated' if absent."
                ),
            },
            "expected_rr_ratio": {
                "type": "string",
                "description": (
                    "Expected risk:reward ratio per trade if explicitly stated "
                    "(e.g. '1:3', '>2:1'). Write 'Not stated' if absent."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "high = book gives explicit, mechanical rules you can follow "
                    "directly. medium = clear guidelines but requires interpretation. "
                    "low = general principles only, no concrete rules."
                ),
            },
            "implementation_notes": {
                "type": "string",
                "description": (
                    "Caveats, warnings, or implementation details the author "
                    "specifically flags. Include any data/infrastructure requirements."
                ),
            },
        },
        "required": [
            "strategy_name", "strategy_type", "core_thesis",
            "entry_criteria", "exit_criteria", "risk_rules", "confidence",
        ],
    },
}

_ANALYSIS_SYSTEM = """\
You are a senior quantitative analyst and systematic trader at MeznaQuantFX.
Your job is to read trading books and research notes and extract a precise,
actionable strategy profile.

RULES:
1. Extract ONLY what is explicitly written in the text. Do not infer, invent,
   or add rules the author did not state.
2. Be specific. "Buy on breakout" is useless. "Buy when price closes above
   the 20-day high on volume > 1.5× the 20-day average" is valuable.
3. If the book states a specific number (1%, 2R, 20-period, etc.) always
   include that exact number in your extraction.
4. If a rule is not present in the text, omit it or write "Not stated".
5. Set confidence = "high" only when the book gives mechanical rules a
   programmer could implement directly. Set "low" for philosophy books
   with no concrete trading rules.
6. Risk management is the most important section. Extract every risk rule
   you find, no matter how small.
"""


# ── Section-level extraction ──────────────────────────────────────────────────

async def _extract_section(
    section_text: str,
    section_num: int,
    total_sections: int,
    client: anthropic.AsyncAnthropic,
    settings: Settings,
) -> dict[str, Any] | None:
    """
    Run a single extraction pass on one section of the document.
    Returns the raw tool input dict, or None on failure.
    """
    prompt = (
        f"[Section {section_num} of {total_sections}]\n\n"
        f"{section_text}"
    )

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.ANALYSIS_MODEL,
                max_tokens=settings.ANALYSIS_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _ANALYSIS_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[_EXTRACTION_TOOL],
                tool_choice={"type": "tool", "name": "extract_trading_strategy"},
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=settings.ANALYSIS_TIMEOUT_SECONDS,
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_trading_strategy":
                return block.input
    except asyncio.TimeoutError:
        log.warning(
            "analyser.section_timeout",
            section=section_num,
            total=total_sections,
        )
    except anthropic.APIError as exc:
        log.error("analyser.api_error", section=section_num, error=str(exc)[:120])
    except Exception as exc:
        log.error("analyser.section_error", section=section_num, error=str(exc)[:120])

    return None


# ── Multi-section merge ───────────────────────────────────────────────────────

def _merge_section_results(sections: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Merge extraction results from multiple book sections into one profile.

    Strategy:
      - Lists (entry_criteria, exit_criteria, key_principles, etc.)
        are deduplicated and concatenated across sections.
      - Scalar fields (strategy_name, core_thesis, etc.) take the value
        from the section with the highest confidence.
      - risk_rules are merged field-by-field, preferring non-null / non-"Not stated" values.
      - confidence is the maximum across all sections.
    """
    if not sections:
        return {}

    if len(sections) == 1:
        return sections[0]

    # Confidence ranking
    _conf_rank = {"high": 3, "medium": 2, "low": 1}
    sections_sorted = sorted(
        sections,
        key=lambda s: _conf_rank.get(s.get("confidence", "low"), 1),
        reverse=True,
    )
    primary = sections_sorted[0]

    def _merge_list(key: str) -> list:
        seen: set[str] = set()
        merged: list[str] = []
        for s in sections:
            for item in s.get(key, []):
                if item not in seen:
                    seen.add(item)
                    merged.append(item)
        return merged

    def _merge_risk(sections: list[dict]) -> dict:
        merged: dict[str, Any] = {}
        for s in sections:
            risk = s.get("risk_rules", {})
            for k, v in risk.items():
                if k == "additional_rules":
                    existing = merged.get("additional_rules", [])
                    for item in (v or []):
                        if item not in existing:
                            existing.append(item)
                    merged["additional_rules"] = existing
                elif v is not None and v != "Not stated" and k not in merged:
                    merged[k] = v
        return merged

    def _merge_conditions(sections: list[dict]) -> dict:
        works_in: set[str] = set()
        avoid_in: set[str] = set()
        for s in sections:
            mc = s.get("market_conditions", {})
            works_in.update(mc.get("works_in", []))
            avoid_in.update(mc.get("avoid_in", []))
        return {"works_in": list(works_in), "avoid_in": list(avoid_in)}

    def _merge_instruments(sections: list[dict]) -> list[str]:
        seen: set[str] = set()
        for s in sections:
            seen.update(s.get("instruments", []))
        return list(seen)

    def _merge_timeframes(sections: list[dict]) -> list[str]:
        seen: set[str] = set()
        for s in sections:
            seen.update(s.get("timeframes", []))
        return list(seen)

    return {
        "strategy_name": primary.get("strategy_name", "Unknown Strategy"),
        "strategy_type": primary.get("strategy_type", "other"),
        "core_thesis": primary.get("core_thesis", ""),
        "entry_criteria": _merge_list("entry_criteria"),
        "exit_criteria": _merge_list("exit_criteria"),
        "risk_rules": _merge_risk(sections),
        "instruments": _merge_instruments(sections),
        "timeframes": _merge_timeframes(sections),
        "market_conditions": _merge_conditions(sections),
        "key_principles": _merge_list("key_principles"),
        "edge_source": primary.get("edge_source", ""),
        "expected_win_rate": primary.get("expected_win_rate", "Not stated"),
        "expected_rr_ratio": primary.get("expected_rr_ratio", "Not stated"),
        "confidence": primary.get("confidence", "low"),
        "implementation_notes": primary.get("implementation_notes", ""),
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def analyse_document(
    full_text: str,
    source_id: str,
    title: str,
    client: anthropic.AsyncAnthropic,
    settings: Settings,
) -> dict[str, Any]:
    """
    Analyse a full document and return a StrategyProfile dict.

    For short documents (< ANALYSIS_SECTION_CHARS): single extraction pass.
    For long documents: extract section-by-section, then merge.

    Always returns a dict (never raises). On total failure, returns a
    minimal profile with confidence="low" and an error note.
    """
    if not full_text.strip():
        return _error_profile(source_id, title, "Document text was empty")

    section_size = settings.ANALYSIS_SECTION_CHARS
    sections_text: list[str] = []

    if len(full_text) <= section_size:
        sections_text = [full_text]
    else:
        # Split at section_size boundaries, preferring paragraph breaks
        pos = 0
        while pos < len(full_text):
            end = pos + section_size
            if end >= len(full_text):
                sections_text.append(full_text[pos:])
                break
            # Try to break at a paragraph boundary
            para_break = full_text.rfind("\n\n", pos, end)
            if para_break != -1 and para_break > pos + section_size // 2:
                end = para_break
            sections_text.append(full_text[pos:end])
            pos = end

    log.info(
        "analyser.started",
        source_id=source_id,
        title=title,
        total_chars=len(full_text),
        sections=len(sections_text),
        model=settings.ANALYSIS_MODEL,
    )

    # Extract each section (sequentially to respect API rate limits)
    section_results: list[dict[str, Any]] = []
    for i, section_text in enumerate(sections_text, 1):
        result = await _extract_section(
            section_text, i, len(sections_text), client, settings
        )
        if result:
            section_results.append(result)
            log.info(
                "analyser.section_done",
                source_id=source_id,
                section=i,
                total=len(sections_text),
                confidence=result.get("confidence"),
            )
        else:
            log.warning(
                "analyser.section_failed",
                source_id=source_id,
                section=i,
                total=len(sections_text),
            )

    if not section_results:
        return _error_profile(source_id, title, "All extraction sections failed")

    # Merge sections
    merged = _merge_section_results(section_results)

    # Stamp with source metadata
    now = datetime.now(timezone.utc).isoformat()
    profile = {
        **merged,
        "source_id": source_id,
        "source_title": title,
        "extracted_at": now,
        "sections_analysed": len(section_results),
        "total_sections": len(sections_text),
        "extraction_complete": len(section_results) == len(sections_text),
    }

    log.info(
        "analyser.complete",
        source_id=source_id,
        strategy_name=profile.get("strategy_name"),
        strategy_type=profile.get("strategy_type"),
        confidence=profile.get("confidence"),
        entry_rules=len(profile.get("entry_criteria", [])),
        exit_rules=len(profile.get("exit_criteria", [])),
    )

    return profile


def _error_profile(source_id: str, title: str, reason: str) -> dict[str, Any]:
    """Minimal profile returned when extraction completely fails."""
    return {
        "strategy_name": title or source_id,
        "strategy_type": "other",
        "core_thesis": "",
        "entry_criteria": [],
        "exit_criteria": [],
        "risk_rules": {
            "position_sizing_method": "Not stated",
            "additional_rules": [],
        },
        "instruments": [],
        "timeframes": [],
        "market_conditions": {"works_in": [], "avoid_in": []},
        "key_principles": [],
        "edge_source": "",
        "expected_win_rate": "Not stated",
        "expected_rr_ratio": "Not stated",
        "confidence": "low",
        "implementation_notes": f"Extraction failed: {reason}",
        "source_id": source_id,
        "source_title": title,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "sections_analysed": 0,
        "total_sections": 0,
        "extraction_complete": False,
    }
