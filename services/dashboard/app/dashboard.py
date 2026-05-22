"""
MeznaQuantFX AI — Operator Dashboard (Streamlit)

Tabs:
  Overview   — system health, kill switch, strategy toggles, live risk state
  Settings   — exchange credentials, API keys (write-only, never displayed)
  Journal    — recent trades and opportunities (Phase 5)
  Risk       — risk event history (Phase 5)

Access: http://localhost:8501
"""

import os
from datetime import datetime
from typing import Any

import httpx
import redis as redis_lib
import streamlit as st

# ── Configuration ─────────────────────────────────────────────────────────────
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
RUNNING_IN_CONTAINER = bool(os.getenv("RUNNING_IN_CONTAINER"))

# MT5 bridge runs natively on Windows (not a container).
# In containers: host.containers.internal resolves to the Windows host.
# Running locally (dev): localhost.
MT5_BRIDGE_HOST = os.getenv(
    "MT5_BRIDGE_HOST",
    "host.containers.internal" if RUNNING_IN_CONTAINER else "localhost",
)
MT5_BRIDGE_PORT = int(os.getenv("MT5_BRIDGE_PORT", "8010"))

SERVICE_PORTS = {
    "gateway": 8000,
    "market-data": 8001,
    "strategy": 8002,
    "risk": 8003,
    "executor": 8004,
    "ai-filter": 8005,
    "journal": 8006,
    "notifications": 8007,
    "backtest": 8008,
    "rag": 8009,
}

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")

STRATEGY_LABELS = {
    "funding_arb": "Funding Rate Arb",
    "stat_arb": "Statistical Arb",
    "swing": "Swing / MF",
}

CREDENTIAL_FIELDS: dict[str, list[dict[str, Any]]] = {
    "binance": [
        {"key": "api_key",    "label": "API Key",    "secret": True,  "help": "From Binance API Management"},
        {"key": "secret_key", "label": "Secret Key", "secret": True,  "help": "Shown once at key creation"},
        {"key": "testnet",    "label": "Testnet",    "secret": False, "help": "true or false"},
    ],
    "oanda": [
        {"key": "account_id", "label": "Account ID", "secret": False, "help": "e.g. 101-004-XXXXXXXX-001"},
        {"key": "api_key",    "label": "API Key",    "secret": True,  "help": "From Oanda My Account → API Access"},
        {"key": "environment","label": "Environment","secret": False, "help": "practice or live"},
    ],
    "anthropic": [
        {"key": "api_key", "label": "API Key", "secret": True, "help": "From console.anthropic.com"},
    ],
    "telegram": [
        {"key": "bot_token", "label": "Bot Token", "secret": True,  "help": "From @BotFather"},
        {"key": "chat_id",   "label": "Chat ID",   "secret": False, "help": "Your Telegram chat/channel ID"},
    ],
    "discord": [
        {"key": "webhook_url", "label": "Webhook URL", "secret": True, "help": "From Discord server settings"},
    ],
}

SERVICE_LABELS = {
    "binance":   "Binance Exchange",
    "oanda":     "Oanda (Forex/CFD)",
    "anthropic": "Anthropic AI",
    "telegram":  "Telegram Alerts",
    "discord":   "Discord Alerts",
}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MeznaQuantFX — Operator Panel",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_resource
def get_redis_client():
    try:
        client = redis_lib.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def service_host(service_name: str) -> str:
    return service_name if RUNNING_IN_CONTAINER else "localhost"


def check_service_health(service_name: str, port: int) -> dict:
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"http://{service_host(service_name)}:{port}/health/live")
            return {"status": "ok" if r.status_code == 200 else "degraded", "data": r.json()}
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)[:60]}


def gateway_post(endpoint: str, payload: dict) -> tuple[bool, dict]:
    try:
        host = service_host("gateway")
        with httpx.Client(timeout=8.0) as client:
            r = client.post(f"http://{host}:8000{endpoint}", json=payload)
            return r.status_code < 300, r.json()
    except Exception as exc:
        return False, {"error": str(exc)}


def gateway_get(endpoint: str) -> tuple[bool, dict]:
    try:
        host = service_host("gateway")
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"http://{host}:8000{endpoint}")
            return r.status_code < 300, r.json()
    except Exception as exc:
        return False, {"error": str(exc)}


def get_kill_switch_state(r) -> bool:
    if r is None:
        return True
    try:
        return r.get("risk:halt") == "1"
    except Exception:
        return True


def get_strategy_states(r) -> dict:
    states = {}
    if r is None:
        return states
    for strategy in ("funding_arb", "stat_arb", "swing"):
        try:
            state = r.hgetall(f"strategy:state:{strategy}")
            states[strategy] = {
                "enabled": state.get("enabled", "0") == "1",
                "paper_mode": state.get("paper_mode", "1") == "1",
                "latency_profile": state.get("latency_profile", "standard"),
            }
        except Exception:
            states[strategy] = {"enabled": False, "paper_mode": True, "latency_profile": "standard"}
    return states


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("MeznaQuantFX")
    st.caption(f"v0.1.0 · {TRADING_MODE.upper()}")

    if TRADING_MODE == "live":
        st.error("⚡ LIVE TRADING ACTIVE")
    else:
        st.success("📋 Paper Trading")

    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    auto_refresh = st.checkbox("Auto-refresh (30s)")
    if auto_refresh:
        import time
        time.sleep(30)
        st.rerun()

    st.markdown("---")
    st.caption(f"Last updated:\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ── Main tabs ─────────────────────────────────────────────────────────────────
tab_overview, tab_settings, tab_journal, tab_risk_log, tab_backtest, tab_rag = st.tabs([
    "📊 Overview",
    "⚙️ Settings & Credentials",
    "📒 Journal",
    "🛡️ Risk Log",
    "🔬 Backtest",
    "🧠 Knowledge",
])

r = get_redis_client()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tab_overview:
    is_halted = get_kill_switch_state(r)
    strategy_states = get_strategy_states(r)

    # ── BOT START / STOP ──────────────────────────────────────────────────────
    st.subheader("🤖 Trading Bot")

    bot_col1, bot_col2, bot_col3 = st.columns([1, 1, 2])

    with bot_col1:
        bot_mode = st.radio(
            "Mode",
            ["📋 Paper", "💰 Live"],
            index=0,
            horizontal=True,
            key="bot_mode_radio",
            help="Paper = simulate trades with no real money. Live = real orders on exchange.",
        )
        is_paper = bot_mode == "📋 Paper"

    with bot_col2:
        if is_halted:
            # Bot is stopped — show START button
            if st.button(
                "🚀 START BOT",
                type="primary",
                use_container_width=True,
                help="Clears kill switch + enables all strategies",
            ):
                if not is_paper:
                    st.warning("⚠️ You selected LIVE mode. This will place REAL orders.")
                ok, resp = gateway_post("/api/v1/control/bot/start", {
                    "started_by": "dashboard",
                    "paper_mode": is_paper,
                })
                if ok:
                    mode_label = resp.get("mode", "")
                    st.success(f"✅ Bot started in {mode_label} mode")
                    st.rerun()
                else:
                    st.error(f"Start failed: {resp.get('detail', resp)}")
        else:
            # Bot is running — show STOP button
            if st.button(
                "🛑 STOP BOT",
                type="primary",
                use_container_width=True,
                help="Activates kill switch + disables all strategies immediately",
            ):
                ok, resp = gateway_post("/api/v1/control/bot/stop", {
                    "stopped_by": "dashboard",
                    "reason": "Manual stop from dashboard",
                })
                if ok:
                    st.success("Bot stopped. Kill switch active.")
                    st.rerun()
                else:
                    st.error(f"Stop failed: {resp.get('detail', resp)}")

    with bot_col3:
        if is_halted:
            st.error("🔴 **BOT STOPPED** — no orders executing")
        else:
            mode_display = "📋 PAPER" if all(
                strategy_states.get(s, {}).get("paper_mode", True)
                for s in ("funding_arb", "stat_arb", "swing")
            ) else "💰 LIVE"
            st.success(f"🟢 **BOT RUNNING** — {mode_display}")

    st.markdown("---")

    # ── Kill switch banner ────────────────────────────────────────────────────
    if is_halted:
        st.error("🛑 **TRADING HALTED** — Kill switch is active. No orders will execute.")
    else:
        st.success("✅ **Trading Active** — Kill switch is OFF.")

    # ── Kill switch controls (emergency override) ─────────────────────────────
    with st.expander("⚡ Emergency Kill Switch (manual override)", expanded=False):
        st.caption("Use the START/STOP bot button above for normal operation. This is the raw kill switch.")
        col_halt, col_reset, _ = st.columns([1, 1, 4])

        with col_halt:
            if st.button("🛑 HALT ALL", type="primary", use_container_width=True):
                ok, resp = gateway_post("/api/v1/control/kill", {
                    "reason": "Manual halt from dashboard",
                    "activated_by": "dashboard",
                })
                if ok:
                    st.success("Kill switch activated")
                    st.rerun()
                else:
                    st.error(f"Failed: {resp.get('detail', resp)}")

        with col_reset:
            if not is_halted:
                st.button("▶ Reset", disabled=True, use_container_width=True)
            else:
                if st.button("▶ Reset Halt", type="secondary", use_container_width=True):
                    ok, resp = gateway_post("/api/v1/control/reset", {
                        "confirm": True,
                        "reason": "Manual reset from dashboard",
                        "reset_by": "dashboard",
                    })
                    if ok:
                        st.success("Kill switch cleared")
                        st.rerun()
                    else:
                        st.error(f"Failed: {resp.get('detail', resp)}")

    st.markdown("---")

    # ── Strategy toggles ──────────────────────────────────────────────────────
    st.subheader("Strategy Controls")
    s_cols = st.columns(3)

    for i, (strategy_type, label) in enumerate(STRATEGY_LABELS.items()):
        state = strategy_states.get(strategy_type, {"enabled": False, "paper_mode": True, "latency_profile": "standard"})
        with s_cols[i]:
            enabled = state["enabled"]
            st.markdown(f"**{label}**")
            status_text = "🟢 ENABLED" if enabled else "🔴 DISABLED"
            mode_text = "📋 Paper" if state["paper_mode"] else "💰 LIVE"
            st.caption(f"{status_text}")
            st.caption(f"{mode_text} · {state['latency_profile']}")

            latency = st.selectbox(
                "Latency profile",
                ["relaxed", "standard", "fast"],
                index=["relaxed", "standard", "fast"].index(state.get("latency_profile", "standard")),
                key=f"latency_{strategy_type}",
            )

            btn_label = "🔴 Disable" if enabled else "🟢 Enable"
            if st.button(btn_label, key=f"toggle_{strategy_type}", use_container_width=True):
                ok, resp = gateway_post("/api/v1/control/strategy/toggle", {
                    "strategy_type": strategy_type,
                    "enabled": not enabled,
                    "latency_profile": latency,
                })
                if ok:
                    st.rerun()
                else:
                    st.error(f"Failed: {resp.get('detail', resp)}")

    st.markdown("---")

    # ── Service health grid ───────────────────────────────────────────────────
    st.subheader("Service Health")
    h_cols = st.columns(4)

    for idx, (svc, port) in enumerate(SERVICE_PORTS.items()):
        result = check_service_health(svc, port)
        icon = "🟢" if result["status"] == "ok" else ("🟡" if result["status"] == "degraded" else "🔴")
        with h_cols[idx % 4]:
            st.metric(label=f"{icon} {svc}", value=result["status"].upper())

    # ── Grafana link ──────────────────────────────────────────────────────────
    st.markdown(
        f"📊 **[Open Grafana Dashboard]({GRAFANA_URL}/d/mezna-trading-v1)**"
        f"  ·  🔍 **[Explore Logs (Loki)]({GRAFANA_URL}/explore)**"
        f"  ·  📈 **[Prometheus]({GRAFANA_URL}/datasources)**"
    )

    st.markdown("---")

    # ── Feed liveness (market-data) ───────────────────────────────────────────
    with st.expander("📡 Market Data Feeds", expanded=False):
        try:
            host = service_host("market-data")
            with httpx.Client(timeout=2.0) as client:
                feed_resp = client.get(f"http://{host}:8001/health/feeds")
                feed_data = feed_resp.json()
            feeds = feed_data.get("feeds", {})
            if feeds:
                f_cols = st.columns(len(feeds))
                for i, (venue, info) in enumerate(feeds.items()):
                    with f_cols[i]:
                        if not info["configured"]:
                            st.metric(label=f"⚪ {venue}", value="NOT CONFIGURED")
                        elif info["alive"]:
                            ts = info.get("last_heartbeat", "")[:19].replace("T", " ") if info.get("last_heartbeat") else "—"
                            st.metric(label=f"🟢 {venue}", value="LIVE", help=f"Last heartbeat: {ts} UTC")
                        else:
                            st.metric(label=f"🔴 {venue}", value="DEAD", help="No heartbeat — check service logs")
            else:
                st.info("Market-data service returned no feed info.")
        except Exception as exc:
            st.warning(f"Could not reach market-data feed health endpoint: {exc}")

    st.markdown("---")

    # ── Executor status ───────────────────────────────────────────────────────
    with st.expander("⚡ Executor — Execution Queue", expanded=False):
        try:
            host = service_host("executor")
            with httpx.Client(timeout=2.0) as client:
                ex_resp = client.get(f"http://{host}:8004/health/execution")
                ex_data = ex_resp.json() if ex_resp.status_code == 200 else {}
        except Exception:
            ex_data = {}

        if ex_data:
            ex_mode = ex_data.get("trading_mode", "unknown").upper()
            is_paper = ex_data.get("is_paper", True)
            pos_usd = ex_data.get("position_usd", 0)
            mode_icon = "📋" if is_paper else "💰"

            eq = ex_data.get("execution_queue", {})
            con = ex_data.get("consumer", {})
            adapters = ex_data.get("adapters", {})

            ex_c1, ex_c2, ex_c3, ex_c4 = st.columns(4)
            ex_c1.metric("Mode", f"{mode_icon} {ex_mode}")
            ex_c2.metric("Queue Depth", eq.get("depth", "—"))
            ex_c3.metric("Pending Unacked", eq.get("pending_unacked", "—"))
            ex_c4.metric("Position Size", f"${pos_usd:.0f}")

            running = con.get("running", False)
            if running:
                st.success("✅ Consumer loop running")
            else:
                st.error("🔴 Consumer loop STOPPED — executor is not processing signals")

            ad_cols = st.columns(3)
            binance_ad = adapters.get("binance", {})
            oanda_ad = adapters.get("oanda", {})
            with ad_cols[0]:
                if is_paper:
                    st.success("📋 Paper adapter active")
                else:
                    b_ok = binance_ad.get("initialised", False)
                    st.success("🟢 Binance ready") if b_ok else st.error("🔴 Binance not ready")
                    if binance_ad.get("testnet"):
                        st.caption("⚠️ Testnet mode")
            with ad_cols[1]:
                if not is_paper:
                    o_ok = oanda_ad.get("initialised", False)
                    st.success("🟢 Oanda ready") if o_ok else st.warning("⚪ Oanda not configured")
                else:
                    st.caption("Live adapters inactive in paper mode")
            with ad_cols[2]:
                mt5_ad = adapters.get("mt5", {})
                if not is_paper:
                    m_ok = mt5_ad.get("initialised", False)
                    st.success("🟢 MT5 bridge ready") if m_ok else st.warning("⚪ MT5 not configured")
                else:
                    st.caption("MT5 adapter inactive in paper mode")
        else:
            st.warning("Could not reach executor service.")

    # ── MT5 Bridge health (Windows-native service) ────────────────────────────
    with st.expander("🖥️ MT5 Bridge (Windows)", expanded=False):
        try:
            with httpx.Client(timeout=3.0) as client:
                mt5_live = client.get(
                    f"http://{MT5_BRIDGE_HOST}:{MT5_BRIDGE_PORT}/health/live"
                )
                mt5_ready = client.get(
                    f"http://{MT5_BRIDGE_HOST}:{MT5_BRIDGE_PORT}/health/ready"
                )
            live_data = mt5_live.json() if mt5_live.status_code == 200 else {}
            ready_data = mt5_ready.json() if mt5_ready.status_code == 200 else {}
            mt5_reachable = mt5_live.status_code == 200
        except Exception as mt5_exc:
            live_data, ready_data, mt5_reachable = {}, {}, False
            mt5_err = str(mt5_exc)[:80]

        if mt5_reachable:
            mt5_connected = ready_data.get("mt5_connected", False)
            mt5_account = ready_data.get("account", "—")
            mt5_balance = ready_data.get("balance")
            mt5_pkg = live_data.get("mt5_package", False)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Bridge", "🟢 REACHABLE")
            m2.metric("MT5 Terminal", "🟢 Connected" if mt5_connected else "🔴 Disconnected")
            m3.metric("Account", str(mt5_account))
            m4.metric("Balance", f"${mt5_balance:,.2f}" if mt5_balance is not None else "—")

            if not mt5_pkg:
                st.warning("⚠️ MetaTrader5 Python package not installed on bridge host.")
            if not mt5_connected:
                st.error(
                    "MT5 terminal not connected — ensure MT5 is open and logged in, "
                    "then run `run.bat` or `run.ps1` in services/mt5-bridge/."
                )
        else:
            st.error(
                f"🔴 MT5 bridge unreachable at {MT5_BRIDGE_HOST}:{MT5_BRIDGE_PORT}. "
                "Start it with `run.bat` in services/mt5-bridge/ on your Windows machine."
            )
            if not mt5_reachable:
                st.caption(f"Error: {mt5_err if 'mt5_err' in dir() else 'connection refused'}")

    st.markdown("---")

    # ── Live risk state ───────────────────────────────────────────────────────
    st.subheader("Live Risk State")
    ok_risk, risk_data = gateway_get("/api/v1/risk/state") if False else (False, {})

    # Read directly from risk service (same network, faster than going via gateway)
    try:
        host = service_host("risk")
        with httpx.Client(timeout=2.0) as client:
            rr = client.get(f"http://{host}:8003/health/state")
            risk_data = rr.json() if rr.status_code == 200 else {}
    except Exception:
        risk_data = {}

    if risk_data:
        rs = risk_data.get("risk_state", {})
        limits = risk_data.get("limits", {})
        cooldowns = risk_data.get("cooldowns", {})

        r1, r2, r3, r4 = st.columns(4)
        pnl = float(rs.get("daily_pnl_usd", 0))
        dd = float(rs.get("daily_drawdown_pct", 0))
        pos = int(rs.get("open_position_count", 0))
        losses = int(rs.get("consecutive_losses", 0))
        max_dd = float(limits.get("max_daily_drawdown_pct", 0.03))

        r1.metric("Daily P&L", f"${pnl:+,.2f}")
        r2.metric(
            "Drawdown",
            f"{dd:.2%}",
            delta=f"{dd - max_dd:.2%}" if dd > 0 else None,
            delta_color="inverse",
            help=f"Limit: {max_dd:.1%}",
        )
        r3.metric("Open Positions", pos, help=f"Max: {limits.get('max_open_positions', 5)}")
        r4.metric("Consecutive Losses", losses, help=f"Limit: {limits.get('max_consecutive_losses', 5)}")

        # Strategy signal counts
        sig_cols = st.columns(3)
        sig_cols[0].metric("Funding Arb Signals Today", rs.get("funding_arb_signals_today", 0))
        sig_cols[1].metric("Stat Arb Signals Today", rs.get("stat_arb_signals_today", 0))
        sig_cols[2].metric("Swing Signals Today", rs.get("swing_signals_today", 0))

        # Cooldown indicators
        active_cooldowns = [s for s, v in cooldowns.items() if v]
        if active_cooldowns:
            st.warning(f"⏸ Strategy cooldown active: {', '.join(active_cooldowns)}")
    else:
        st.info("Risk state not available — start the risk service.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SETTINGS & CREDENTIALS
# ══════════════════════════════════════════════════════════════════════════════
with tab_settings:
    st.subheader("Exchange & Service Credentials")
    st.info(
        "**Security:** All credentials are encrypted with AES-128 before storage. "
        "Values entered here are never displayed again — only confirmation that a key is set. "
        "Use HTTPS in production. Never share credentials in chat or logs."
    )

    # Check if credential store is available
    ok, cred_status = gateway_get("/api/v1/credentials/status")
    store_available = ok and cred_status.get("store_active", False)

    if not store_available:
        st.error(
            "**Credential store not configured.**\n\n"
            "Add `CREDENTIAL_ENCRYPTION_KEY` to your `.env` file and restart the gateway.\n\n"
            "Generate a key:\n"
            "```bash\n"
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "```"
        )
    else:
        # Fetch current status
        ok_meta, meta = gateway_get("/api/v1/credentials/")
        services_meta = meta.get("services", {}) if ok_meta else {}

        # Render one section per service
        for service_name, fields in CREDENTIAL_FIELDS.items():
            service_label = SERVICE_LABELS.get(service_name, service_name)
            service_info = services_meta.get(service_name, {})
            is_configured = service_info.get("fully_configured", False)

            config_badge = "✅ Configured" if is_configured else "⚠️ Incomplete"

            with st.expander(f"{service_label}  —  {config_badge}", expanded=not is_configured):

                # Show existing credential status
                existing_creds = {
                    c["key"]: c
                    for c in service_info.get("credentials", [])
                }

                if existing_creds:
                    st.markdown("**Current status:**")
                    status_cols = st.columns(len(fields))
                    for i, field in enumerate(fields):
                        cred = existing_creds.get(field["key"])
                        with status_cols[i]:
                            if cred and cred.get("is_set") and cred.get("is_active"):
                                src = cred.get("source", "unknown")
                                updated = cred.get("updated_at", "")[:10] if cred.get("updated_at") else "env"
                                st.success(f"**{field['label']}**\n\n✓ Set ({src}, {updated})")
                            else:
                                st.warning(f"**{field['label']}**\n\n✗ Not set")
                    st.markdown("---")

                # Update form
                st.markdown("**Update credentials:**")
                with st.form(key=f"form_{service_name}"):
                    form_values = {}
                    for field in fields:
                        if field["secret"]:
                            val = st.text_input(
                                field["label"],
                                type="password",
                                placeholder="Enter new value to update (leave blank to keep existing)",
                                help=field["help"],
                                key=f"input_{service_name}_{field['key']}",
                            )
                        else:
                            val = st.text_input(
                                field["label"],
                                placeholder="Enter value",
                                help=field["help"],
                                key=f"input_{service_name}_{field['key']}",
                            )
                        form_values[field["key"]] = val

                    submitted = st.form_submit_button(
                        f"💾 Save {service_label} Credentials",
                        type="primary",
                        use_container_width=True,
                    )

                    if submitted:
                        updates_sent = 0
                        errors = []

                        for field in fields:
                            value = form_values.get(field["key"], "").strip()
                            if not value:
                                continue  # Skip blank — keep existing

                            ok_set, resp_set = gateway_post("/api/v1/credentials/set", {
                                "service_name": service_name,
                                "credential_key": field["key"],
                                "value": value,
                                "updated_by": "dashboard",
                            })

                            if ok_set:
                                updates_sent += 1
                            else:
                                errors.append(f"{field['label']}: {resp_set.get('detail', 'failed')}")

                        if errors:
                            for err in errors:
                                st.error(err)
                        elif updates_sent > 0:
                            st.success(f"✅ {updates_sent} credential(s) updated for {service_label}. "
                                       f"Services will reload automatically.")
                            st.rerun()
                        else:
                            st.warning("No values entered — nothing updated.")

        st.markdown("---")
        st.markdown("### Advanced")

        col_reload, col_info = st.columns([1, 3])
        with col_reload:
            if st.button("🔄 Signal Full Reload", use_container_width=True, help="Force all services to reload credentials from DB"):
                ok_rel, _ = gateway_post("/api/v1/credentials/reload", {})
                if ok_rel:
                    st.success("Reload signal sent to all services")
                else:
                    st.error("Failed to send reload signal")

        with col_info:
            st.caption(
                "Credentials are reloaded automatically when updated. "
                "Use 'Signal Full Reload' only if a service missed an update."
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — JOURNAL (Phase 5)
# ══════════════════════════════════════════════════════════════════════════════
with tab_journal:
    st.subheader("Trade Journal")

    journal_host = service_host("journal")
    journal_base = f"http://{journal_host}:8006"

    def journal_get(path: str) -> tuple[bool, dict]:
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(f"{journal_base}{path}")
                return r.status_code < 300, r.json()
        except Exception as exc:
            return False, {"error": str(exc)}

    # ── Funnel ────────────────────────────────────────────────────────────────
    st.markdown("#### Today's Signal Funnel")
    ok_funnel, funnel_data = journal_get("/opportunities/funnel")
    if ok_funnel and funnel_data:
        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.metric("Detected", funnel_data.get("detected", 0))
        fc2.metric("AI Scored", funnel_data.get("ai_scored", 0),
                   help=f"AI filter rate: {funnel_data.get('ai_filter_rate', 0):.1%}")
        fc3.metric("Risk Approved", funnel_data.get("risk_approved", 0),
                   help=f"Risk approval rate: {funnel_data.get('risk_approval_rate', 0):.1%}")
        fc4.metric("Executed", funnel_data.get("executed", 0),
                   help=f"Execution rate: {funnel_data.get('execution_rate', 0):.1%}")
    else:
        st.warning("Journal service not available — funnel data not loaded.")

    st.markdown("---")

    # ── Trade summary ─────────────────────────────────────────────────────────
    st.markdown("#### Today's Trading Activity")
    ok_sum, sum_data = journal_get("/trades/summary")
    if ok_sum and sum_data:
        totals = sum_data.get("totals", {})
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Filled Orders", totals.get("filled", 0))
        sc2.metric("Rejected", totals.get("rejected", 0))
        sc3.metric("Notional Traded", f"${float(totals.get('total_notional', 0)):,.2f}")
        sc4.metric("Fees Paid", f"${float(totals.get('total_fees', 0)):.4f}")

        by_strategy = sum_data.get("by_strategy", [])
        if by_strategy:
            st.markdown("**By strategy:**")
            import pandas as pd
            df = pd.DataFrame(by_strategy)
            display_cols = [c for c in [
                "strategy_type", "paper_mode", "filled", "rejected",
                "total_notional", "total_fees"
            ] if c in df.columns]
            st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No trade summary data yet.")

    st.markdown("---")

    # ── Recent trades table ───────────────────────────────────────────────────
    st.markdown("#### Recent Trades (last 20)")
    ok_trades, trades_data = journal_get("/trades?limit=20")
    if ok_trades and trades_data.get("trades"):
        import pandas as pd
        df_trades = pd.DataFrame(trades_data["trades"])
        display_cols = [c for c in [
            "opened_at", "strategy_type", "venue", "symbol", "side",
            "filled_qty", "average_fill_price", "fee", "status", "paper_mode"
        ] if c in df_trades.columns]
        st.dataframe(df_trades[display_cols], use_container_width=True, hide_index=True)
        st.caption(f"Showing 20 of {trades_data.get('total', 0)} total trades.")
    elif ok_trades:
        st.info("No trades recorded yet. Start the executor and let strategies run.")
    else:
        st.warning("Could not load trade history from journal service.")

    st.markdown("---")
    st.markdown("#### Daily Activity (last 7 days)")
    ok_pnl, pnl_data = journal_get("/pnl/daily?days=7")
    if ok_pnl and pnl_data.get("rows"):
        import pandas as pd
        df_pnl = pd.DataFrame(pnl_data["rows"])
        display_cols = [c for c in [
            "trade_date", "strategy_type", "paper_mode",
            "fill_count", "total_notional", "total_fees", "realized_pnl"
        ] if c in df_pnl.columns]
        st.dataframe(df_pnl[display_cols], use_container_width=True, hide_index=True)
        st.caption("realized_pnl populated in Phase 7 (position close tracking).")
    else:
        st.info("No daily activity yet.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — RISK LOG (Phase 5)
# ══════════════════════════════════════════════════════════════════════════════
with tab_risk_log:
    st.subheader("Risk Event Log")

    journal_host_r = service_host("journal")
    journal_base_r = f"http://{journal_host_r}:8006"

    def journal_get_r(path: str) -> tuple[bool, dict]:
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(f"{journal_base_r}{path}")
                return r.status_code < 300, r.json()
        except Exception as exc:
            return False, {"error": str(exc)}

    # ── Risk events ───────────────────────────────────────────────────────────
    st.markdown("#### Risk Events")
    ok_re, re_data = journal_get_r("/audit/risk-events?limit=20")
    if ok_re and re_data.get("risk_events"):
        import pandas as pd
        df_re = pd.DataFrame(re_data["risk_events"])
        display_cols = [c for c in [
            "created_at", "event_type", "strategy_type", "trigger_value",
            "threshold_value", "description", "auto_resolved"
        ] if c in df_re.columns]
        st.dataframe(df_re[display_cols], use_container_width=True, hide_index=True)
        st.caption(f"Showing 20 of {re_data.get('total', 0)} total risk events.")
    elif ok_re:
        st.success("✅ No risk events recorded — system is running clean.")
    else:
        st.warning("Could not load risk events from journal service.")

    st.markdown("---")

    # ── Audit log ─────────────────────────────────────────────────────────────
    st.markdown("#### Audit Log (last 30 entries)")
    audit_filter = st.selectbox(
        "Filter by event type",
        ["(all)", "risk.rejected", "risk.approved", "kill_switch.activated",
         "kill_switch.reset", "strategy.toggled", "reconciler.stale_trade_closed"],
        key="audit_filter",
    )
    audit_event = None if audit_filter == "(all)" else audit_filter
    audit_path = f"/audit?limit=30{f'&event_type={audit_event}' if audit_event else ''}"

    ok_audit, audit_data = journal_get_r(audit_path)
    if ok_audit and audit_data.get("entries"):
        import pandas as pd
        df_audit = pd.DataFrame(audit_data["entries"])
        display_cols = [c for c in [
            "created_at", "event_type", "service", "entity_type"
        ] if c in df_audit.columns]
        st.dataframe(df_audit[display_cols], use_container_width=True, hide_index=True)
        st.caption(f"Showing 30 of {audit_data.get('total', 0)} total audit entries.")
    elif ok_audit:
        st.info("No audit log entries match the current filter.")
    else:
        st.warning("Could not load audit log from journal service.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.subheader("Strategy Backtester")
    st.info(
        "Runs historical simulations using Binance public OHLCV + funding rate data. "
        "No API key required. Results are computed on demand (5–30 seconds)."
    )

    backtest_host = service_host("backtest")
    backtest_base = f"http://{backtest_host}:8008"

    def bt_post(path: str, body: dict) -> tuple[bool, dict]:
        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(f"{backtest_base}{path}", json=body)
                return r.status_code < 300, r.json()
        except Exception as exc:
            return False, {"error": str(exc)}

    bt_strategy = st.radio(
        "Strategy",
        ["Funding Rate Arb", "Statistical Arb"],
        horizontal=True,
        key="bt_strategy",
    )

    if bt_strategy == "Funding Rate Arb":
        st.markdown("**Simulation: long spot + short perp, collect funding payments**")
        bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
        bt_symbol   = bt_c1.selectbox("Symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], key="bt_fa_sym")
        bt_days     = bt_c2.slider("Days", 7, 365, 30, key="bt_fa_days")
        bt_edge     = bt_c3.number_input("Min Edge (bps)", 1.0, 50.0, 5.0, key="bt_fa_edge")
        bt_capital  = bt_c4.number_input("Capital ($)", 1000, 100000, 5000, key="bt_fa_cap")

        if st.button("▶ Run Funding Arb Backtest", type="primary"):
            with st.spinner("Downloading data and simulating..."):
                ok, result = bt_post("/backtest/funding-arb", {
                    "symbol": bt_symbol,
                    "days": bt_days,
                    "capital_usd": bt_capital,
                    "min_edge_bps": bt_edge,
                    "fee_bps": 7.5,
                })
            if ok and result:
                _show_backtest_result(result)
            else:
                st.error(f"Backtest failed: {result.get('error', result)}")

    else:
        st.markdown("**Simulation: sell overpriced leg, buy underpriced leg when spread diverges**")
        sa_c1, sa_c2, sa_c3 = st.columns(3)
        sa_symbol  = sa_c1.selectbox("Symbol", ["BTCUSDT", "ETHUSDT"], key="bt_sa_sym")
        sa_days    = sa_c2.slider("Days", 7, 365, 90, key="bt_sa_days")
        sa_capital = sa_c3.number_input("Capital ($)", 1000, 100000, 5000, key="bt_sa_cap")
        sa_c4, sa_c5, sa_c6 = st.columns(3)
        sa_window  = sa_c4.slider("Z-score window (candles)", 20, 500, 100, key="bt_sa_win")
        sa_entry   = sa_c5.number_input("Entry Z", 0.5, 5.0, 2.0, key="bt_sa_entry")
        sa_exit    = sa_c6.number_input("Exit Z", 0.0, 2.0, 0.5, key="bt_sa_exit")
        sa_interval = st.selectbox("Candle interval", ["1h", "4h", "1d", "15m", "5m"], key="bt_sa_int")

        if st.button("▶ Run Stat Arb Backtest", type="primary"):
            with st.spinner("Downloading data and simulating..."):
                ok, result = bt_post("/backtest/stat-arb", {
                    "spot_symbol": sa_symbol,
                    "perp_symbol": sa_symbol,
                    "interval": sa_interval,
                    "days": sa_days,
                    "window": sa_window,
                    "entry_z": sa_entry,
                    "exit_z": float(sa_exit),
                    "capital_usd": sa_capital,
                    "fee_bps": 7.5,
                })
            if ok and result:
                _show_backtest_result(result)
            else:
                st.error(f"Backtest failed: {result.get('error', result)}")

    # ── Walk-forward Compare vs Live ──────────────────────────────────────────
    st.markdown("---")
    st.subheader("Walk-Forward: Backtest vs Live Trades")
    st.caption(
        "Compare what the backtest predicts against your actual filled orders "
        "in the journal. A large divergence signals look-ahead bias, slippage, "
        "or latency issues in the live strategy."
    )

    cmp_c1, cmp_c2, cmp_c3 = st.columns(3)
    cmp_strategy = cmp_c1.selectbox(
        "Strategy",
        ["Funding Rate Arb", "Stat Arb"],
        key="cmp_strategy",
    )
    cmp_days    = cmp_c2.slider("Lookback (days)", 7, 90, 30, key="cmp_days")
    cmp_capital = cmp_c3.number_input("Capital ($)", 1000, 100000, 5000, key="cmp_capital")

    cmp_endpoint = (
        "/backtest/compare/funding-arb"
        if cmp_strategy == "Funding Rate Arb"
        else "/backtest/compare/stat-arb"
    )

    if st.button("🔬 Run Walk-Forward Compare", type="secondary", key="btn_compare"):
        with st.spinner("Reading live trades + running backtest simulation..."):
            ok, cmp_result = bt_post(cmp_endpoint, {
                "days": cmp_days,
                "capital_usd": cmp_capital,
            })

        if ok and cmp_result and "backtest" in cmp_result:
            period = cmp_result.get("period", {})
            st.caption(
                f"Period: {period.get('start','')[:10]} → {period.get('end','')[:10]} "
                f"({period.get('days', cmp_days)} days)"
            )

            bt = cmp_result["backtest"]
            ac = cmp_result["actual"]
            div = cmp_result.get("divergence", {})

            # Side-by-side comparison table
            import pandas as pd
            cmp_df = pd.DataFrame({
                "Metric": [
                    "Total Trades", "Win Rate", "Net P&L (USD)",
                    "Total P&L (USD)", "Total Fees (USD)", "Sharpe Ratio", "Max Drawdown %"
                ],
                "Backtest": [
                    bt.get("total_trades", 0),
                    f"{float(bt.get('win_rate', 0)):.1%}",
                    f"${float(bt.get('net_pnl_usd', 0)):+,.2f}",
                    f"${float(bt.get('total_pnl_usd', 0)):+,.2f}",
                    f"${float(bt.get('total_fees_usd', 0)):.4f}",
                    f"{float(bt.get('sharpe_ratio', 0)):.3f}",
                    f"{float(bt.get('max_drawdown_pct', 0)):.2f}%",
                ],
                "Live (Actual)": [
                    ac.get("total_trades", 0),
                    f"{float(ac.get('win_rate', 0)):.1%}",
                    f"${float(ac.get('net_pnl_usd', 0)):+,.2f}",
                    f"${float(ac.get('total_pnl_usd', 0)):+,.2f}",
                    f"${float(ac.get('total_fees_usd', 0)):.4f}",
                    "—",
                    "—",
                ],
            })
            st.dataframe(cmp_df, use_container_width=True, hide_index=True)

            # Divergence callout
            pnl_delta = float(div.get("net_pnl_delta_usd", 0))
            if abs(pnl_delta) < 10:
                st.success(f"✅ Divergence: ${pnl_delta:+.2f} — backtest closely tracks live performance.")
            elif pnl_delta > 0:
                st.success(f"✅ Live outperformed backtest by ${pnl_delta:+.2f}")
            else:
                st.warning(
                    f"⚠️ Backtest was ${abs(pnl_delta):.2f} more optimistic than live. "
                    "Check for look-ahead bias, slippage, or latency mismatch."
                )

            st.caption(div.get("note", ""))

            # Sample of actual trades
            sample = cmp_result.get("live_trade_sample", [])
            if sample:
                with st.expander(f"📋 Live trade sample ({len(sample)} trades)", expanded=False):
                    df_s = pd.DataFrame(sample)
                    display_cols = [c for c in [
                        "opened_at", "venue", "symbol", "side",
                        "filled_qty", "average_fill_price", "fee", "realized_pnl", "paper_mode"
                    ] if c in df_s.columns]
                    st.dataframe(df_s[display_cols], use_container_width=True, hide_index=True)
            elif ac.get("total_trades", 0) == 0:
                st.info("No live trades found for this strategy in the selected period. "
                        "Run the bot in paper mode first to accumulate data.")
        elif ok and cmp_result.get("error"):
            st.error(f"Compare error: {cmp_result['error']}")
        else:
            st.error(f"Compare failed: {cmp_result.get('error', cmp_result)}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — RAG KNOWLEDGE ASSISTANT
# ══════════════════════════════════════════════════════════════════════════════
with tab_rag:
    st.subheader("Knowledge Base")
    st.caption(
        "Retrieval-Augmented Generation — ground answers in indexed documents. "
        "Ingest strategy notes, research PDFs, or trade rationales; query with natural language."
    )

    rag_host = service_host("rag")
    RAG_BASE = f"http://{rag_host}:8009"

    def rag_get(endpoint: str) -> tuple[bool, dict]:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{RAG_BASE}{endpoint}")
                return r.status_code < 300, r.json()
        except Exception as exc:
            return False, {"error": str(exc)}

    def rag_post(endpoint: str, payload: dict) -> tuple[bool, dict]:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(f"{RAG_BASE}{endpoint}", json=payload)
                return r.status_code < 300, r.json()
        except Exception as exc:
            return False, {"error": str(exc)}

    # ── Service status ──────────────────────────────────────────────────────
    col_status, col_stats = st.columns(2)

    with col_status:
        ok_ready, ready = rag_get("/health/ready")
        if ok_ready and ready.get("status") == "ok":
            st.success("✅ RAG service online")
        else:
            st.error("❌ RAG service unreachable")

        if ok_ready:
            st.caption(f"Qdrant: {ready.get('qdrant', 'unknown')}")
            st.caption(f"Collection: {ready.get('collection', 'unknown')}")
            oai = "✅" if ready.get("openai_configured") else "⚠️ not set"
            ant = "✅" if ready.get("anthropic_configured") else "⚠️ not set"
            st.caption(f"OpenAI embeddings: {oai}")
            st.caption(f"Anthropic synthesis: {ant}")

    with col_stats:
        ok_stats, stats_data = rag_get("/health/stats")
        if ok_stats:
            st.metric("Documents indexed (chunks)", stats_data.get("points_count", "—"))
            idx = stats_data.get("indexed_vectors_count")
            if idx is not None:
                st.metric("Indexed vectors", idx)
            st.caption(f"Collection: {stats_data.get('collection', '—')} · {stats_data.get('status', '—')}")
        else:
            st.warning("Could not reach Qdrant stats")

    st.markdown("---")

    # ── Query ───────────────────────────────────────────────────────────────
    st.subheader("Ask the Knowledge Base")

    q_col1, q_col2 = st.columns([3, 1])
    with q_col1:
        question = st.text_area(
            "Question",
            placeholder="e.g. What are the entry conditions for the funding arbitrage strategy?",
            height=100,
            key="rag_question",
        )
    with q_col2:
        rag_category = st.selectbox(
            "Filter by category",
            ["(all)", "strategy_note", "market_research", "trade_rationale", "general"],
            key="rag_cat_filter",
        )
        rag_top_k = st.slider("Chunks to retrieve", 1, 10, 5, key="rag_top_k")

    if st.button("🔍 Ask", type="primary", disabled=not question.strip()):
        category_filter = None if rag_category == "(all)" else rag_category
        with st.spinner("Embedding → retrieving → synthesising..."):
            ok, resp = rag_post("/query", {
                "question": question.strip(),
                "category": category_filter,
                "top_k": rag_top_k,
            })

        if ok:
            timed_out = resp.get("timed_out", False)
            if timed_out:
                st.warning("⏱️ Synthesis timed out. Showing retrieved context only.")

            st.markdown("#### Answer")
            st.markdown(resp.get("answer", "No answer returned."))

            sources = resp.get("sources", [])
            if sources:
                with st.expander(f"📎 Sources ({len(sources)} chunks used)", expanded=False):
                    import pandas as pd
                    df_src = pd.DataFrame(sources)
                    if "score" in df_src.columns:
                        df_src["score"] = df_src["score"].apply(lambda x: f"{x:.3f}")
                    st.dataframe(df_src, use_container_width=True, hide_index=True)

            st.caption(
                f"Model: {resp.get('model','—')} · "
                f"Chunks used: {resp.get('chunks_used', 0)} / retrieved: {resp.get('retrieval_count', 0)}"
            )
        else:
            st.error(f"Query failed: {resp.get('error', resp)}")

    st.markdown("---")

    # ── Ingest ──────────────────────────────────────────────────────────────
    st.subheader("Ingest Documents")

    ingest_tab_pdf, ingest_tab_text = st.tabs(["📄 Upload PDF (books)", "📝 Paste Text"])

    # ── PDF upload ────────────────────────────────────────────────────────
    with ingest_tab_pdf:
        st.caption(
            "Upload trading books or research PDFs directly. "
            "Text is extracted, chunked, and embedded automatically. "
            "Re-uploading the same source_id safely overwrites previous content. "
            "Large books (300+ pages) may take up to 60 seconds."
        )

        pdf_col1, pdf_col2 = st.columns(2)
        with pdf_col1:
            pdf_source_id = st.text_input(
                "Source ID",
                placeholder="e.g. books/trading-evolved",
                key="pdf_source_id",
            )
            pdf_title = st.text_input(
                "Title",
                placeholder="e.g. Trading Evolved — Andreas Clenow",
                key="pdf_title",
            )
        with pdf_col2:
            pdf_category = st.selectbox(
                "Category",
                ["strategy_note", "market_research", "trade_rationale", "general"],
                index=0,
                key="pdf_cat",
            )

        uploaded_pdf = st.file_uploader(
            "Choose a PDF file",
            type=["pdf"],
            key="pdf_uploader",
            help="Max 50 MB. Scanned image-only PDFs cannot be extracted.",
        )

        if uploaded_pdf is not None:
            st.caption(
                f"📎 {uploaded_pdf.name} — "
                f"{uploaded_pdf.size / 1_048_576:.1f} MB"
            )

        if st.button(
            "📥 Ingest PDF",
            disabled=not (uploaded_pdf is not None and pdf_source_id.strip()),
            type="primary",
            key="btn_ingest_pdf",
        ):
            with st.spinner(f"Extracting text from {uploaded_pdf.name} → chunking → embedding..."):
                try:
                    files = {"file": (uploaded_pdf.name, uploaded_pdf.getvalue(), "application/pdf")}
                    data = {
                        "source_id": pdf_source_id.strip(),
                        "title": pdf_title.strip() or uploaded_pdf.name,
                        "category": pdf_category,
                    }
                    with httpx.Client(timeout=120.0) as client:
                        r = client.post(
                            f"{RAG_BASE}/ingest/pdf",
                            files=files,
                            data=data,
                        )
                    ok_pdf = r.status_code < 300
                    resp_pdf = r.json()
                except Exception as exc:
                    ok_pdf = False
                    resp_pdf = {"error": str(exc)}

            if ok_pdf:
                st.success(
                    f"✅ **{resp_pdf.get('chunks_ingested', '?')} chunks** ingested from "
                    f"**{resp_pdf.get('filename', '')}** · "
                    f"{resp_pdf.get('chars_extracted', 0):,} chars extracted · "
                    f"category: {resp_pdf.get('category', '')}"
                )
            else:
                detail = resp_pdf.get("detail", resp_pdf.get("error", str(resp_pdf)))
                st.error(f"PDF ingestion failed: {detail}")

    # ── Paste text ────────────────────────────────────────────────────────
    with ingest_tab_text:
        st.caption(
            "Paste raw text (strategy notes, research, trade rationale) to add to the knowledge base. "
            "Re-ingesting the same source_id safely overwrites previous content."
        )

        ing_col1, ing_col2 = st.columns(2)
        with ing_col1:
            ing_source_id = st.text_input(
                "Source ID",
                placeholder="e.g. strategy/funding-arb-v2",
                key="ing_source_id",
            )
            ing_title = st.text_input(
                "Title",
                placeholder="e.g. Funding Arbitrage Strategy — v2 Notes",
                key="ing_title",
            )
        with ing_col2:
            ing_category = st.selectbox(
                "Category",
                ["strategy_note", "market_research", "trade_rationale", "general"],
                key="ing_cat",
            )

        ing_text = st.text_area(
            "Document text",
            placeholder="Paste full document text here...",
            height=200,
            key="ing_text",
        )

        if st.button(
            "📥 Ingest Text",
            disabled=not (ing_source_id.strip() and ing_text.strip()),
        ):
            with st.spinner("Chunking → embedding → upserting..."):
                ok, resp = rag_post("/ingest", {
                    "source_id": ing_source_id.strip(),
                    "title": ing_title.strip(),
                    "text": ing_text.strip(),
                    "category": ing_category,
                })

            if ok:
                st.success(
                    f"✅ Ingested **{resp.get('chunks_ingested', '?')} chunks** "
                    f"from **{resp.get('source_id', '')}** "
                    f"(category: {resp.get('category', '')})"
                )
            else:
                detail = resp.get("detail", resp.get("error", str(resp)))
                st.error(f"Ingestion failed: {detail}")


def _show_backtest_result(result: dict) -> None:
    """Render a BacktestResult dict in the dashboard."""
    import pandas as pd

    st.markdown("---")
    st.markdown(f"#### Results: {result.get('strategy','').replace('_',' ').title()} — {result.get('symbol','')}")
    st.caption(f"Period: {result.get('start_dt','')[:10]} → {result.get('end_dt','')[:10]}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Trades", result.get("total_trades", 0))
    m2.metric("Win Rate", f"{float(result.get('win_rate', 0)):.1%}")
    m3.metric("Net P&L", f"${float(result.get('net_pnl_usd', 0)):+,.2f}")
    m4.metric("Total Return", f"{float(result.get('total_return_pct', 0)):+.2f}%")

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Sharpe Ratio", f"{float(result.get('sharpe_ratio', 0)):.3f}")
    m6.metric("Max Drawdown", f"{float(result.get('max_drawdown_pct', 0)):.2f}%")
    m7.metric("Total Fees", f"${float(result.get('total_fees_usd', 0)):.4f}")
    m8.metric("Avg Hold (candles)", f"{float(result.get('avg_hold_candles', 0)):.1f}")

    # Equity curve chart
    equity = result.get("equity_curve", [])
    if equity:
        df_eq = pd.DataFrame(equity)
        st.markdown("**Equity Curve**")
        st.line_chart(df_eq.set_index("ts")["equity_usd"])

    # Trade log table (last 50)
    trades = result.get("trade_log", [])
    if trades:
        st.markdown(f"**Trade Log** ({len(trades)} trades, showing last 50)")
        df_t = pd.DataFrame(trades[-50:])
        display = [c for c in ["entry_ts", "exit_ts", "side", "entry_price",
                                "exit_price", "pnl_usd", "fee_usd", "net_pnl_usd",
                                "hold_candles"] if c in df_t.columns]
        st.dataframe(df_t[display], use_container_width=True, hide_index=True)
