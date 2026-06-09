# OHLCV persistence layer

Durable storage of OHLCV candles in the `ohlcv_bars` table — the persisted-history
prerequisite for DB-backed backtests, deeper history for the directional bar-mode
strategies, and a future vectorbt portfolio backtest (see
[ADR 0001](decisions/0001-vectorbt-backtest-deferral.md)).

## Pieces

| Layer | Where | What |
|-------|-------|------|
| Schema | `migrations/versions/004_ohlcv_bars.py`, `mezna_shared/models/ohlcv_bar.py` | `ohlcv_bars` table, natural PK `(venue, symbol, interval, bucket_start)` |
| Persistence API | `mezna_shared/ohlcv.py` | `upsert_bars`, `read_bars`, `count_bars`, `latest_bar_epoch`, interval↔seconds helpers |
| Live writer | `services/market-data/app/bar_writer.py` | Resamples the tick cache → completed candles → `ohlcv_bars` (`source=live_ticks`) |
| Backfill | `services/market-data/app/backfill.py` + `POST /backfill` | ccxt REST history → `ohlcv_bars` (`source=exchange_rest`) |
| Backtest read | `services/backtest/app/data.fetch_candles_from_db`, `GET /ohlcv`, stat-arb `source=db` | Consume persisted candles |

Two producers, **last-writer-wins** on the natural key. `volume` is real traded
volume for `exchange_rest` and a tick count (liquidity proxy) for `live_ticks`.

## Deploy

1. **Run the migration**: `alembic upgrade head` (adds `ohlcv_bars`, revision 004).
2. **Rebuild containers** so the new `mezna_shared.ohlcv` module + `OHLCVBar`
   model are baked in (shared is installed at image build): at minimum
   `market-data` and `backtest`.
3. No new env is required — the live writer defaults ON.

## Live bar writer (market-data)

Runs as a background task beside the feeds. Every `BAR_WRITER_INTERVAL_SECONDS`
it resamples each configured feed symbol's tick cache into candles and persists
the **completed** buckets (the forming bucket is skipped; re-persisting a
completed bucket is idempotent).

| Env | Default | Meaning |
|-----|---------|---------|
| `BAR_WRITER_ENABLED` | `true` | Master switch |
| `BAR_WRITER_INTERVAL_SECONDS` | `60` | Flush cadence |
| `BAR_WRITER_BAR_SECONDS` | `60` | Candle width (`60` → `1m`) |

Best-effort + cancellation-safe: a DB blip is logged and retried next cycle and
never disrupts the feeds.

## Backfill (seed deep history)

```bash
# via the gateway / market-data service
curl -X POST $MARKET_DATA/backfill -H 'content-type: application/json' -d '{
  "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1m", "days": 30
}'
```

- ccxt public OHLCV on **mainnet** (testnet has no history). Venues:
  `binance / bybit / okx / kraken` (Oanda FX would need a separate v20 path).
- **Incremental** by default: resumes from the newest stored bucket, so re-runs
  only fetch the gap. Paginates `since → now` with a hard call cap.
- `symbol` is the ccxt unified symbol (`BTC/USDT`, `BTC/USDT:USDT`).

## Backtest on persisted data

```bash
# inspect what's stored
curl "$BACKTEST/ohlcv?venue=binance&symbol=BTC/USDT&interval=1m&days=7"

# stat-arb on persisted bars (source: auto | binance | db)
curl -X POST $BACKTEST/stat-arb -H 'content-type: application/json' -d '{
  "venue": "binance", "spot_symbol": "BTC/USDT", "perp_symbol": "BTC/USDT:USDT",
  "interval": "1m", "source": "db"
}'
```

- `auto` (default): DB when populated, else Binance REST — backward compatible.
- `db`: persisted bars only; **404** with a `/backfill` hint if none exist.
- `binance`: prior REST behaviour. The response carries `data_source`.
- Funding-arb stays on REST (it needs funding rates, which aren't in `ohlcv_bars`).
