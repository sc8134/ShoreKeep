# Shorekeep

![Shorekeep](assets/brand/shorekeep-lockup-light.png)

Shorekeep is a video/audio download platform with a hybrid engine. The product is Shorekeep; **MY-DL** remains the transfer engine inside it.

- **yt-dlp** handles extraction (URL → media streams) and fragmented protocols (HLS/DASH, live)
- **MY-DL** ([sc8134/MY-DL](https://github.com/sc8134/MY-DL)) handles byte transfer for progressive HTTP streams: parallel ranges, resume, rate limiting, integrity checks
- **ffmpeg** muxes/remuxes (`-c copy` where possible) and extracts MP3 audio
- **FastAPI + SQLite** API and single-page UI, with SSE progress and a DB-polling worker

Status: **Phase 4**. Single jobs and playlists, presets, progress streaming, auto-expiry, email/password accounts, free-tier limits, optional Turnstile, encrypted per-job cookies, VTT subtitles and chapter metadata, structured error codes, and an optional desktop app that downloads locally with automatic browser cookies and server-enforced metering. Billing and Postgres/Redis scaling come next.

## Architecture

```
Browser UI (served by API)
   │  session cookie / Bearer token
   │  POST /v1/jobs            GET /v1/jobs/{id}/events (SSE)
   ▼
FastAPI (backend/api)
   │  auth + quota + Turnstile gate
   │  rows in SQLite (queued jobs, users, sessions)
   ▼
Worker loop (python -m backend.engine.worker)
   │  claim job → pipeline
   ├─ direct media URL ─────────────► MY-DL transfer
   └─ site URL ─► yt-dlp extract ──► format planner
                      │                ├─ progressive https → MY-DL
                      │                └─ HLS/DASH/live    → yt-dlp downloader
                      ▼
                  ffmpeg merge -c copy / MP3 encode
                      ▼
              storage (local now, R2-ready) → signed/ephemeral link
```

Optional **desktop companion** path (signed-in users only):

```
Browser UI ── pair token ──► shorekeep://run (or localhost HTTP) ──► Shorekeep agent 127.0.0.1:47821
                                                                    ├─ POST /v1/agent/jobs (reserve quota)
                                                                    ├─ same extraction/transfer engine
                                                                    ├─ browser cookies read on demand (local only)
                                                                    ├─ final file → ~/Downloads/Shorekeep
                                                                    └─ PUT /v1/agent/jobs/{id} (report usage)
```

Routing is decided per stream in `engine/extract.py` (`build_plan`), not per site, so a single job can mix engines (e.g. MY-DL video + MY-DL audio → ffmpeg merge, or yt-dlp for a fragmented stream).

### Signed-URL refresh

Extractor-provided URLs are IP-bound and short-lived. The pipeline retries once on 401/403/410: it re-runs extraction, rebuilds the plan, and resumes the transfer in the same worker (`engine/pipeline.py::_download_with_refresh`). Extraction and transfer must stay on the same worker. Do not split them across machines.

## Playlists, subtitles, and chapters

A playlist URL is still **one job**. The worker enumerates entries with a flat extraction, downloads each entry with the normal plan/transfer path, and stores the result as a single ZIP (`01 - Title.mp4`, `02 - Title.mp4`, ...). Items that fail are skipped and listed in `_playlist.txt` inside the ZIP; the job only fails when nothing could be saved. Playlists are capped by `PLAYLIST_MAX_ITEMS` (default 20) and `PLAYLIST_MAX_TOTAL_MB` (default 4096). Channels and nested playlists are not expanded; single-item playlists behave like a normal video.

`subtitles: true` on `POST /v1/jobs` (or the **Subtitles + chapters** chip in the UI) adds two optional extras:

- a VTT sidecar, preferring human captions over automatic ones and English over other languages, downloaded without failing the media job if it is missing; HLS subtitle playlists (segmented WebVTT, e.g. TED) are merged into one file;
- chapter markers from the extractor, stored as JSON on the job and rendered as a timestamp list in the UI.

The API exposes them at `GET /v1/jobs/{id}/subtitles` (mirroring `/file`, including R2 presigning). In playlist mode subtitles and a chapters text file are placed next to each video inside the ZIP.

## Error taxonomy

Failures carry a stable `error_code` alongside the human message (`engine/errors.py`): `bot_check`, `age_restricted`, `login_required`, `cookies_rejected`, `cookies_unreadable`, `private_video`, `drm`, `live_stream`, `unsupported_url`, `geo_blocked`, `not_found`, `network`, `timeout`, `size_limit`, `playlist_empty`, `ffmpeg_missing`, `storage_error`, `unsafe_url`, and `unknown`. The API returns `retryable` so the UI can offer a **Try again** button only when another attempt can plausibly succeed.

## Plans and limits

Limits are enforced server-side (persistent across API restarts), with an in-process sliding window as a burst guard. The counters combine cloud jobs (`job` table) and desktop downloads (`desktopjob` table), so a paired desktop app cannot be used to bypass the plan: each download reserves quota before it starts.

| | Jobs/hour | Concurrent | Max preset |
|---|---|---|---|
| Guest | 5 per IP | 1 | 720p (audio always allowed) |
| Free account | 20 per user | 2 | all |
| Pro (reserved for Phase 3) | 100 per user | 4 | all |

A global `MAX_QUEUE_DEPTH` still caps total queued/running work. IPs are stored only as a salted SHA-256 prefix (`client_hash`), never in plaintext.

## Accounts

Email + password only (no OAuth, no email verification yet). Passwords are hashed with scrypt; sessions are opaque 256-bit tokens stored as SHA-256 hashes with a 30-day expiry. The browser gets an `HttpOnly`, `SameSite=Lax` cookie; API clients can use `Authorization: Bearer <token>`.

Cookie-authenticated mutations check the `Origin` header (CSRF guard) against the public base URL. Sign-up and sign-in are rate-limited per IP.

## Bring-your-own cookies (BYO)

Signed-in users can paste or upload a Netscape `cookies.txt` with a job. It is:

1. validated (size, format, at least one cookie),
2. encrypted at rest with Fernet, keyed from `SECRET_KEY`,
3. written to the per-job work dir with `0600` permissions and passed to yt-dlp as `cookiefile`,
4. also converted to a matching `Cookie` header for MY-DL transfers,
5. cleared from the database as soon as the job finishes (success or failure), and the work dir is removed.

Cookies are never logged, never shared between users, and never pooled. DRM and paywall bypass remain unsupported.

## Desktop companion (local downloads, automatic cookies)

Pasting `cookies.txt` is too much to ask of non-technical users, so the web app can hand a job to a small companion app running on the user's own machine. The companion:

- downloads with the same hybrid engine (yt-dlp extraction + MY-DL transfer + ffmpeg) and saves to `~/Downloads/Shorekeep`;
- reads the user's own browser cookies **on demand**, only when a site actually asks for a login, and never sends them to the server;
- is reachable only on `127.0.0.1:47821` and only from configured web origins;
- reserves each download with the API before starting, so desktop and cloud jobs share one hourly quota and one concurrency cap.

### Setup (Windows, one time)

```bash
.venv/Scripts/python.exe -m pip install -e services/backend -e services/desktop
.venv/Scripts/python.exe -m mydl_desktop.agent --register   # registers shorekeep:// for the current user
```

The Python package keeps the internal name `mydl_desktop`; only the product, protocol, and visible strings are branded Shorekeep.

Restart the API/worker, open the UI, sign in, and click **Download with Shorekeep**. If the agent is already running the UI posts to it directly; otherwise it opens a `shorekeep://run?...` link, which starts the agent and hands over the job.

Useful commands:

```bash
python -m mydl_desktop.agent --serve      # run the agent in the foreground
python -m mydl_desktop.agent --status     # print health JSON, browsers, download dir
python -m mydl_desktop.agent --pair TOKEN # claim a pairing token without a browser
python -m mydl_desktop.agent --unregister # remove the shorekeep:// handler
```

`--register` writes only `HKCU\Software\Classes\shorekeep`; it needs no admin rights and is removed by `--unregister`. On macOS/Linux, protocol registration is not implemented; start `--serve` manually and keep the site's origin in `allowed_origins`.

### Dev installer (Windows, unsigned)

The companion can also be built as a standalone app and a per-user installer, without a Python environment on the target machine:

```powershell
powershell -ExecutionPolicy Bypass -File infra/windows/build.ps1
```

This produces:

- `dist\Shorekeep\Shorekeep.exe`: the portable app. Launched normally it starts the bundled web app (API, UI, and worker) on `http://127.0.0.1:8000`, opens a native window against it, and keeps a tray icon; it also starts or reuses the local download agent and handles `shorekeep://` links. The same UI stays available in any browser at `http://127.0.0.1:8000`;
- `dist\installer\Shorekeep-Setup-0.2.0.exe`: a self-extracting setup built with the same local PyInstaller toolchain.

The setup installs to `%LOCALAPPDATA%\Programs\Shorekeep` for the current user only (no admin prompt), registers `shorekeep://` under `HKCU`, adds a Start Menu shortcut and an entry in Settings > Apps, and optionally starts the app at sign-in. `--silent` and `--no-autostart` are supported for scripted test installs. Uninstall from Settings > Apps or run the installed `uninstall.ps1`; it stops the app and removes the program folder, protocol, autostart entry, shortcut, and Settings > Apps entry. Your device token, settings, and log in `%APPDATA%\Shorekeep` and your files in `%USERPROFILE%\Downloads\Shorekeep` are kept, so delete those folders by hand for a full wipe.

This is an **unsigned development build**: no code-signing certificate is involved, so SmartScreen shows "Unknown publisher" on first run and the app is not suitable for public distribution yet. The Inno Setup script (`infra/windows/Shorekeep.iss`) is kept for the later step when a certificate is purchased; `build.ps1` uses it automatically if `ISCC.exe` is available.

### Brand assets

The wave-S mark, PNG sizes, tray silhouette, favicon, ICO, and lockups live in `assets/brand`. `services/desktop/mydl_desktop/assets` holds the copies bundled into the app, and `infra/brand_assets.py` rebuilds all of them from `assets/brand/shorekeep-icon.png`.

### Pairing and security

1. The signed-in UI calls `POST /v1/agent/pair` and gets a one-time token (5 min TTL, 256-bit).
2. The agent exchanges it at `POST /v1/agent/pair/claim` for a device token, stores that in `%APPDATA%\Shorekeep\config.json`, and caches the browser's token in memory for polling.
3. The local API requires `X-Agent-Key` (or `?key=` for file downloads), checks the `Host` header (DNS-rebinding guard), and allows only configured origins via CORS. Unknown origins cannot read responses or trigger preflighted mutations.
4. Exported cookies live only in a per-job temp directory (mode `0600`), are deleted when the job ends, and are never written to the server DB or logs.
5. The same SSRF guard (`assert_public_http_url`) rejects private/loopback targets before any transfer.
6. Before a download starts, the agent calls `POST /v1/agent/jobs` with its device token. The server checks the plan preset and combined quota, then stores a `desktopjob` row (host, preset, status, size, never the media). Completion is reported with `PUT /v1/agent/jobs/{id}`; this is an upsert, so a download that ran while offline is still counted once the agent reconnects (`outbox.jsonl`, replayed every minute). The agent refuses to start a download when the server answers 429.

### Configuration

`%APPDATA%\Shorekeep\config.json` (created on first pair):

| Key | Default | Meaning |
|---|---|---|
| `server_base` | `http://127.0.0.1:8000` | API used to claim pairing tokens |
| `download_dir` | `~/Downloads/Shorekeep` | Where finished files are saved |
| `cookie_browser` | `auto` | `auto`, `firefox`, `chrome`, `edge`, `brave`, or `off` |
| `allowed_origins` | `http://127.0.0.1:8000`, `http://localhost:8000` | Web origins allowed to call the agent (add your deployed origin) |
| `idle_timeout_minutes` | `30` | Agent exits after this long with no jobs/requests |
| `state_dir` | `%APPDATA%\Shorekeep` | Where the usage outbox (`outbox.jsonl`) is kept |

Environment overrides: `SHOREKEEP_SERVER`, `SHOREKEEP_DOWNLOAD_DIR`, `SHOREKEEP_ALLOWED_ORIGINS` (comma-separated).

### Browser support (important)

Firefox is the reliable browser for automatic cookies: their store is not encrypted with a per-app key. **Chrome, Edge, and Brave on Windows 127+ use App-Bound Encryption**, which commonly blocks cookie export from outside the browser. This was verified on the development machine: all three Chromium browsers failed with `DPAPI`/App-Bound errors while Firefox was not installed. When automatic export fails, the agent says so clearly and leaves public downloads working; login-gated sites can still be handled through the web app's `cookies.txt` flow. If you want zero-friction cookies for a Chromium-only audience, a browser extension using `chrome.cookies` is the next step.

### Metering

Desktop and cloud downloads draw from the same counters. For each job the agent first calls `POST /v1/agent/jobs`; the server enforces the plan preset, the hourly limit, and the concurrent-job cap (both include cloud jobs in progress) before the agent touches the network. On success or failure the agent reports `PUT /v1/agent/jobs/{job_id}` with the final status and size. Only the URL's host is stored (`desktopjob.host`), never media, cookies, or full URLs. If the API is briefly unreachable the agent still downloads and queues the usage event locally; the event is replayed every minute and counted then (the upsert means a job is recorded exactly once). Running reservations from a crashed agent stop counting as active after `JOB_TIMEOUT_SECONDS`, and records older than `DESKTOP_USAGE_RETENTION_DAYS` are pruned.

The web UI's usage line (from `/v1/auth/me`) therefore includes desktop downloads, and desktop history still appears as **on device** (the agent keeps its job list in memory, so it resets when the agent restarts).

## Bot protection (Turnstile)

Set `TURNSTILE_ENABLED=true`, `TURNSTILE_SITE_KEY`, and `TURNSTILE_SECRET` to require a Cloudflare Turnstile token on job creation. The API verifies the token server-side (`backend/turnstile.py`); the UI renders the widget from `/v1/config`. When disabled (default), no network calls are made.

## Quickstart (local)

Requires Python 3.11+, ffmpeg on PATH (or the bundled `imageio-ffmpeg` fallback).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "services/backend[dev]"      # Windows Git Bash
# source .venv/bin/activate && pip install -e "services/backend[dev]"   # macOS/Linux

cp .env.example .env

# terminal 1: API + UI
.venv/Scripts/python -m uvicorn backend.api.main:app --port 8000

# terminal 2: worker
.venv/Scripts/python -m backend.engine.worker
```

Open http://127.0.0.1:8000, paste a link, pick a preset. Accounts, quota, and history appear in the top bar.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/v1/auth/register` | `{ "email", "password" }` → session cookie + usage |
| POST | `/v1/auth/login` | `{ "email", "password" }` |
| POST | `/v1/auth/logout` | Revoke the current session |
| GET | `/v1/auth/me` | Current user, plan, and usage |
| GET | `/v1/config` | Public client config (Turnstile, limits) |
| POST | `/v1/jobs` | `{ "url", "preset", "cookies"?, "turnstile_token"? }` |
| GET | `/v1/jobs` | Signed-in user's recent jobs (`?limit=20`) |
| GET | `/v1/jobs/{id}` | Job state, progress, metadata, `download_url` |
| GET | `/v1/jobs/{id}/events` | Server-Sent Events stream of the same payload |
| GET | `/v1/jobs/{id}/file` | Output file (redirects to presigned URL on R2) |
| POST | `/v1/agent/pair` | Signed-in: create a one-time desktop pairing token |
| POST | `/v1/agent/pair/claim` | Desktop app: exchange a pair token for a device token |
| GET | `/v1/agent/me` | Desktop app: validate its device token and read usage (`Authorization: Bearer`) |
| POST | `/v1/agent/jobs` | Desktop app: reserve quota before a local download (`{job_id,url,preset}`) |
| PUT | `/v1/agent/jobs/{job_id}` | Desktop app: report the final status/size (upsert, safe to replay) |
| GET | `/v1/health` | Liveness |

The desktop agent itself exposes `GET /health`, `GET /v1/me`, `POST /v1/run`, `GET /v1/jobs`, `GET /v1/jobs/{id}`, and `GET /v1/jobs/{id}/file?key=` on `127.0.0.1:47821`.

Anonymous jobs are readable by their unguessable ID. Jobs created by a signed-in user are only visible to that user.

## Configuration

See `.env.example`. Notable knobs:

- `SECRET_KEY`: **required in production**: encrypts BYO cookies and salts IP hashing. Changing it invalidates stored cookies (jobs fail with a clear message) but not sessions.
- `COOKIE_SECURE=true` when served over HTTPS.
- `DATA_DIR`: SQLite DB, media, and per-job work dirs
- `RETENTION_SECONDS`: auto-delete finished files and mark jobs `expired` (default 1 h)
- `MAX_FILESIZE_MB`: enforced twice: format selection refuses larger estimates, and the transfer aborts if actual bytes exceed it
- `PLAYLIST_MAX_ITEMS`: entries expanded per playlist job (default 20)
- `PLAYLIST_MAX_TOTAL_MB`: total uncompressed size of one playlist ZIP (default 4096)
- `SUBTITLE_MAX_KB`: cap for a downloaded VTT sidecar (default 2048)
- `JOB_TIMEOUT_SECONDS`: hard wall-clock limit per job (default 30 min); stale jobs from crashed workers are reaped after the same window
- `MAX_QUEUE_DEPTH`: reject new jobs with HTTP 429 when this many are queued/running
- `ANON_JOBS_PER_IP_HOUR`, `FREE_JOBS_PER_USER_HOUR`, `PRO_JOBS_PER_USER_HOUR`
- `ANON_MAX_ACTIVE_JOBS`, `FREE_MAX_ACTIVE_JOBS`, `PRO_MAX_ACTIVE_JOBS`
- `ANON_MAX_PRESET`: guest quality cap (`720p` default; empty disables)
- `TURNSTILE_ENABLED`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET`
- `MAX_COOKIE_KB`: upload cap for `cookies.txt` (default 256)
- `AGENT_PAIR_TTL_SECONDS`: lifetime of a one-time desktop pairing token (default 300)
- `DESKTOP_USAGE_RETENTION_DAYS`: how long desktop usage rows are kept for quotas/abuse review (default 30; `0` disables pruning)
- `MYDL_CONNECTIONS`: parallel ranges per MY-DL transfer
- `STORAGE_BACKEND=local|r2` + `R2_*`: switch output storage

## Tests

```bash
.venv/Scripts/python.exe -m pip install -e "services/backend[dev]"
.venv/Scripts/python.exe -m pytest services/backend/tests -q
```

118 tests cover format planning/routing, size caps, DRM/playlist rejection, playlist ZIP building, subtitle selection, HLS subtitle merging, chapter parsing, the error taxonomy, URL safety (SSRF), rate limiting, auth/sessions, quotas, job ownership, CSRF origin checks, Turnstile gating, cookie parsing/encryption, agent pairing, job flows, shared cloud/desktop metering (idempotent reserves, concurrency, stale reservations, offline replay), and the desktop agent (host guard, local pairing, plan gating, SSRF). Desktop tests are skipped automatically if the `mydl_desktop` package is not installed. `conftest.py` points `DATA_DIR` at a temp directory so tests never touch your dev database.

CI runs the same suite on every push and pull request (`.github/workflows/ci.yml`). A scheduled workflow (`.github/workflows/ytdlp-update.yml`) checks PyPI every Monday, bumps the `yt-dlp` floor in both packages, runs the suite against that exact release, and opens a pull request when a newer version exists.

## Deploy

```bash
cp .env.example .env      # set SECRET_KEY (and COOKIE_SECURE=true behind HTTPS)
docker compose -f infra/docker-compose.yml up --build
```

Both containers share a `media_data` volume (SQLite + files). For multi-host scale, move to Postgres (`FOR UPDATE SKIP LOCKED` claiming), Redis for queue priority, and R2 for storage. The interfaces are already isolated (`db.py`, `engine/storage.py`). Multi-process API deployments should move quota checks into a transaction/lock (or Postgres advisory locks) since SQLite count-then-insert is not atomic across processes.

## Roadmap

- [x] Phase 0: skeleton, config, DB, worker
- [x] Phase 1: hybrid pipeline, presets, SSE, retention, UI
- [x] Phase 2: accounts, free-tier limits, Turnstile, BYO-cookies (encrypted, per-job)
- [x] Phase 2.5: desktop companion: local downloads, automatic browser cookies, `shorekeep://` pairing, shared server-side metering
- [ ] Phase 3: billing (freemium tiers), priority queue, R2 in production
- [x] Phase 4: playlists, subtitles/chapters, error taxonomy, weekly yt-dlp updates in CI

## Known limitations

- Playlists are exported as one ZIP (no per-item download links), capped by `PLAYLIST_MAX_ITEMS`; channels and nested lists are not expanded. Live streams and DRM content are still rejected
- Subtitles are VTT sidecars only; other caption formats are skipped, and there is no embedding/translation yet
- One job per worker at a time; add workers for concurrency
- SQLite only; SSE polls the DB every 500 ms (off the event loop)
- No email verification, password reset, or OAuth yet; a verification flow is needed before a public launch
- Turnstile is off by default; enable it (with `SECRET_KEY` set) before exposing the API publicly
- Desktop cookie extraction depends on the browser: Firefox works; Chromium 127+ App-Bound Encryption often blocks it. Device tokens are not revocable from the UI yet. A dev installer exists (`infra/windows/build.ps1`), but it is unsigned and unpublishable: code signing, auto-update, and a revocation UI are deferred until the product is validated
- Desktop job history is in memory in the agent (gone on restart); files themselves remain on disk. Metering is cooperative: the server enforces quota for the shipped agent, but a modified client could under-report (it runs on the user's machine). Usage reports queued while offline are replayed later, so a job can land in a later hour bucket
- SSRF guard blocks private/loopback targets at enqueue and before MY-DL transfers; production should still enforce egress firewall rules (redirect-based SSRF via yt-dlp extractors is not fully covered)

## Reliability safeguards

- Stale jobs from crashed workers are failed automatically after `JOB_TIMEOUT_SECONDS`; work dirs are cleaned
- Downloads abort when actual bytes exceed `MAX_FILESIZE_MB`, not just when the estimate does
- Signed-URL expiry triggers one re-extraction and resume in the same worker
- Progress persistence is throttled and thread-safe; SSE reads run in a thread pool
- Expired sessions are purged by the worker; cookies are cleared when a job ends; expired pairing tokens are purged too
- Desktop reservations from a crashed agent are closed after `JOB_TIMEOUT_SECONDS`; desktop usage rows older than `DESKTOP_USAGE_RETENTION_DAYS` are pruned; queued usage reports are replayed every minute
- Reserve and report calls are idempotent per (device, job id), so retries and outbox replays never double-count a download
- Container restart policies and an API healthcheck are configured in compose

## Legal

Run this only for content you own or are licensed to download. The platform intentionally excludes DRM circumvention and paywall bypass. Any public deployment needs Terms of Service, a DMCA agent, and a takedown process. Cookies are BYO-only and per-job: users bring their own, they are encrypted, never pooled or shared, and deleted when the job finishes. With the desktop companion, cookies never leave the user's machine at all; the server only records the site host, job status, and output size for quota accounting.
