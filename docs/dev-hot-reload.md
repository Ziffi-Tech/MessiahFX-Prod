# Dev hot-reload (Podman Desktop / Windows)

Edit code on the host → it takes effect in the running containers **instantly**, no
rebuild. The base compose already bind-mounts each service's `app/`; the dev override
([podman-compose.dev.yml](../podman-compose.dev.yml)) makes that reload.

## Start

```powershell
pwsh scripts/dev-up.ps1            # start / update the stack (hot-reload)
pwsh scripts/dev-up.ps1 -Build      # force-rebuild images first
pwsh scripts/dev-up.ps1 -Down       # stop
```

Or directly: `podman compose -p mezna -f podman-compose.yml -f podman-compose.dev.yml up -d`

- **Gateway:** <http://localhost:8080>  · **Terminal:** <http://localhost:3001>
- Backend images already exist → no rebuild; only the dashboard dev image is built.

## How it works

- **Python services:** uvicorn runs with `--reload`, watching the bind-mounted
  `app/` and `shared/mezna_shared`. `WATCHFILES_FORCE_POLLING=true` is **required**:
  on Windows/WSL2 + podman, bind-mount file events don't reach the container via
  inotify, so the reloader must poll.
- **shared on PYTHONPATH:** `./shared/mezna_shared` is mounted at `/app/mezna_shared`
  with `PYTHONPATH=/app`, so it shadows the baked copy. Edits to the shared package
  reload live — and the container runs the **latest** shared code even if its image
  predates a module (no rebuild).
- **Dashboard:** `next dev` with `WATCHPACK_POLLING=true` (same WSL reason).

## Verify

```powershell
podman logs mezna-gateway | Select-String "WatchFiles"     # reloader active
# edit services/gateway/app/main.py → logs show:
#   WatchFiles detected changes in 'app/main.py'. Reloading...
```

## Dashboard on a small machine (≤ 6 GiB)

`next dev` is memory-hungry; building/running it inside the WSL machine alongside the
backend can OOM the machine. On a constrained host, run the terminal on the **host**
instead (lighter, best HMR) — it proxies to the containerised gateway:

```powershell
cd services/dashboard-next
$env:GATEWAY_URL = "http://localhost:8080"
pnpm dev            # http://localhost:3000 ; login user=any pass=mezna (dev default)
```

The containerised dev terminal (`dashboard-next` in the override) is preferred once
the machine has headroom — give it more RAM: `podman machine set --memory 8192`
(stop/start the machine after).

## Notes / gotchas

- `.dockerignore` mirrors `.containerignore`: the `podman compose` provider
  (docker-compose) reads `.dockerignore`. Without it the build-context scan of the
  host `node_modules` fails on Windows. Keep both in sync.
- First base-image pull may fail with `docker-credential-wincred not found`. Pre-pull
  with podman (its own auth): `podman pull docker.io/library/node:22-alpine`.
- Stale containers/network from a prior `podman-compose` (python) run conflict with
  the `podman compose` provider's labels. Clean once:
  `podman rm -f $(podman ps -aq --filter name=mezna-)` then
  `podman network rm -f mezna-quantfx_mezna-net`.
- This is a DEV workflow. Production uses the standalone build + `--workers`
  (no reload) — just `podman compose -f podman-compose.yml up -d --build`.
