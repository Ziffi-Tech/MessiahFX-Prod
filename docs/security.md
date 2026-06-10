# Security & secrets — production hardening

The system is internal-by-design (only the gateway and dashboard face the
operator; services talk over the private `mezna-net`). These are the controls and
the production checklist.

## Edge controls (gateway)

- **Auth at the boundary.** The dashboard verifies signed session tokens; the
  gateway *also* verifies the token (HS256, shared `SESSION_SECRET`) + revocation,
  so the `X-Mezna-*` headers are never the sole trust. Set `GATEWAY_REQUIRE_AUTH=true`
  in production to reject control-plane writes without a verified token.
- **Rate limiting.** Redis fixed-window limiter, keyed per operator (`X-Mezna-User`)
  or client IP. Tune `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`; health,
  the SSE stream, metrics, and CORS preflight are exempt. Fails open on Redis error.
- **CORS.** Lock `CORS_ALLOWED_ORIGINS` to the terminal origin(s) in production
  (empty = permissive localhost dev defaults).
- **Fail-loud startup.** With `ENVIRONMENT=production` the gateway refuses to start
  on a missing/dev-default `SESSION_SECRET`, and logs loud errors if
  `GATEWAY_REQUIRE_AUTH` is off, `CREDENTIAL_ENCRYPTION_KEY` is unset, CORS still
  allows localhost, or `DEBUG` is on.

## Secrets — do NOT ship in the image or repo

- `.env` is gitignored and excluded from images by `.containerignore`. Images must
  never bake secrets.
- In production, inject secrets via the platform (Coolify secrets / env), not a
  committed file. The repo only ships `.env.example`.
- Secrets that matter: `SESSION_SECRET` (must match between gateway + dashboard),
  `CREDENTIAL_ENCRYPTION_KEY` (losing it loses all stored exchange creds),
  `POSTGRES_PASSWORD`, `ANTHROPIC_API_KEY`, exchange API keys, `DASHBOARD_USERS`.
- Generate strong values:
  - `SESSION_SECRET`: `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`
  - `CREDENTIAL_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Exchange API keys

- Provision **trade-only** scope — never enable withdrawals.
- Rotate on suspicion; revoke immediately on a leak. Rotating `SESSION_SECRET`
  also invalidates every dashboard session (or use the in-app "Sign out all").

## Production checklist

- [ ] `ENVIRONMENT=production`
- [ ] `SESSION_SECRET` strong + identical on gateway and dashboard
- [ ] `GATEWAY_REQUIRE_AUTH=true`
- [ ] `CORS_ALLOWED_ORIGINS` = the real terminal origin only
- [ ] `CREDENTIAL_ENCRYPTION_KEY` set (and backed up)
- [ ] `DASHBOARD_USERS` roster set with scrypt-hashed passwords (no plaintext)
- [ ] Secrets injected via the platform, not a baked `.env`
- [ ] Only the gateway + dashboard ports exposed; DB/Redis bound to localhost
- [ ] Postgres password rotated off the dev default
- [ ] Rate limits tuned for expected operator count
