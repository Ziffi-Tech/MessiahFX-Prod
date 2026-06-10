# Observability

Prometheus + Grafana + Loki ship with the stack (`podman-compose --profile full up`
or the individual services). Every FastAPI service exposes `/metrics`.

## Metrics

- **HTTP** (all services, via the instrumentator): `http_requests_total`,
  `http_request_duration_seconds`, `http_requests_inprogress`.
- **Feed health** (market-data exporter): `mezna_feed_up{venue}` — 1 fresh / 0 dead,
  from the feed heartbeat keys.
- **Risk** (risk exporter): `mezna_daily_drawdown_pct`, `mezna_trading_halted`,
  `mezna_consecutive_losses`, `mezna_open_positions`.

## Dashboards

Auto-provisioned from `infrastructure/grafana/dashboards/`:
- **MeznaQuantFX — Operator Overview** (`mezna-operator`): halt state, drawdown,
  open positions, consecutive losses, feed-up per venue, service-up, request rate,
  p95 latency.

Grafana: <http://localhost:3000> (admin / `GRAFANA_PASSWORD`).

## Alerts

Rules in `infrastructure/prometheus/alerts.yml` (loaded via `rule_files`):

| Alert | Condition |
|---|---|
| ServiceDown | `up == 0` for 1m |
| FeedDown | `mezna_feed_up == 0` for 1m |
| DrawdownNearLimit | `mezna_daily_drawdown_pct >= 2.4` (80% of 3% cap) |
| TradingHalted | `mezna_trading_halted == 1` |
| ConsecutiveLossesHigh | `mezna_consecutive_losses >= 4` |
| HighErrorRate / HighRequestLatencyP95 | 5xx rate > 1/s · p95 > 1s |

These fire in the Prometheus UI (<http://localhost:9090/alerts>). The market-data
and risk exporters **also** push `feed.down` / `risk.drawdown_warning` to the
notifications queue, so operators get Telegram/Discord pings **without** an
alertmanager. To route Prometheus alerts directly, run an alertmanager and
uncomment the `alerting:` block in `prometheus.yml`.

## Logs

Loki + Promtail collect container logs; query in Grafana (Explore → Loki),
filterable by `service`.
