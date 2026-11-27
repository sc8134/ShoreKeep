# Changelog

Notable changes to Shorekeep. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions use [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.1] - 2026-11-24

### Added

- Bandwidth limiting — global speed cap (`GET/POST /v1/bandwidth`) persisted across restarts, plus per-job `bandwidth_limit_kbps` on job create and `PATCH /v1/jobs/{id}`. Both MY-DL and yt-dlp transfer paths throttled via a token-bucket limiter; effective rate is the minimum of global and per-job caps.
- Queue pause/resume — `PATCH /v1/jobs/{id}` accepts `pause` and `resume` actions on queued jobs. Paused jobs are skipped by the worker until resumed.
- Queue reordering — `move_top` bumps a job to the front of the queue; `move_bottom` sends it to the back.
- Auto-retry with exponential backoff — jobs with `max_retries > 0` are automatically re-queued after failure on retryable error codes (network, timeout, bot_check, etc.) with backoff starting at 15s and doubling each retry, capped at 10 minutes.

## [0.5.0] - 2026-11-24

### Fixed

- Webhook signature verification used `hmac.new` which does not exist in the Python standard library. Replaced with `hmac.HMAC`. Paddle webhook delivery now verifies and processes correctly on all Python versions.
- In-process rate limiter was per-worker and not shared across multiple API processes. Signed-in users now rely exclusively on the DB-backed hourly quota enforced inside `quota_guard`, which uses `pg_advisory_xact_lock` on Postgres and is accurate across any number of workers. The in-memory burst limiter is kept for anonymous requests as a fast pre-DB guard only.
- The in-memory sliding-window limiter had no eviction path for unique keys, allowing unbounded memory growth under a key-flood. Keys outside the window are now evicted when the table exceeds 50 000 entries.
- The desktop agent `reveal` endpoint used `Path.resolve()` to check that a revealed file sits inside the download folder. On systems where the download folder is a symlink `resolve()` and `realpath()` can disagree. Switched to `os.path.realpath` on both sides so the check is consistent regardless of symlinks.
- Postgres deployments that skipped `alembic upgrade head` received a 500 at the first request with no explanation. The startup schema check now also verifies that all required columns exist on the `job` table and raises a clear error listing the missing ones.
- The app now hard-fails at startup when `SECRET_KEY` is the insecure development default and the environment looks like production (`COOKIE_SECURE=true` or a non-SQLite `DATABASE_URL`). BYO cookies encrypted with the dev key in a production deployment are trivially decryptable.

### Added

- Launching Shorekeep again raises the existing window instead of starting a second copy. The window answers focus requests over a loopback control channel.
- The tray menu can pause and resume new downloads. Downloads already running finish, and paused launches are rejected cleanly.
- The tray supervises the desktop agent, the local web app, and the window. It restarts them after a crash with a restart budget and shows a notification when a service keeps stopping.
- The tray menu now shows live status: starting, ready, paused, and the number of active downloads.
- The native window title shows live download progress and pause state, and the tray tooltip follows the active download.
- Finished and failed downloads raise a tray notification, and the tray menu lists the current download with its progress.
- Completed desktop downloads are recorded in a local activity log that survives restarts. The web page merges them into the download history and adds an Activity panel with an unread count.
- **Show in folder** opens the saved file from the page. The agent only reveals files inside the download folder and requires the local control header.
- Drag a link, or a small `.txt` file containing one, onto the page to start a download.
- `Ctrl+Alt+S` opens the Shorekeep window from anywhere on Windows. The shortcut is configurable with the `hotkey` setting or `SHOREKEEP_HOTKEY`.
- Opt-in link detection: watch the clipboard or a folder for new links. A detected link fills the URL box with a short hint, the tray offers **Review copied link** or **Review link from watch folder**, and nothing downloads until the user clicks Download.
- The page gains a **Link detection** panel that saves the clipboard and watch folder settings through the agent. The agent keeps running while detection is on.
- The watch folder scans `.txt`, `.url`, and `.webloc` files, remembers each file's size and mtime in `watch.json`, and ignores files larger than 256 KB.

### Fixed

- Child process log handles are closed in the parent after each spawn, so long tray sessions no longer leak file handles.

## [0.4.2] - 2026-11-23

### Fixed

- The combined `SHA256SUMS.txt` now includes the Windows, macOS, and Linux artifacts in every release. The duplicate upload of two files with the same name could leave only the Windows hash on the release page.
- macOS and Linux `.sha256` files now reference filenames relative to the download directory, so `shasum -a 256 -c` and `sha256sum -c` succeed without editing paths.

### Added

- The release workflow can now publish an existing tag through `workflow_dispatch`.

## [0.4.1] - 2026-11-23

### Fixed

- Brand lockup generation resolves a bold system font on Windows, macOS, and Linux instead of assuming a Windows font path. This unblocked the macOS and Linux release builds.

## [0.4.0] - 2026-11-23

### Added

- Postgres support with Alembic migrations, advisory-lock quota enforcement, and `SELECT ... FOR UPDATE SKIP LOCKED` job claiming.
- Cross-platform desktop companion for Windows, macOS, and Linux, with server-side metering, local browser cookies, a system tray, and a native window.
- Server-enforced public demo mode with preset, file-size, jobs-per-hour, and concurrency caps, billing disabled, and cookies rejected.
- Deploy configurations for Render, Fly.io, and Docker Compose, including a single-container start script for the API and worker.
- GitHub release workflow with optional Windows code signing, macOS signing and notarization hooks, and SHA256 checksums.
- Proprietary license.

### Changed

- Raised the minimum yt-dlp version.
- Unified version numbers across the backend, desktop app, installer, and release artifacts.

## [0.3.0] - 2026-11-21

### Added

- Paddle billing and Pro plans at $5/month or $45/year, including per-job file caps.

## [0.2.0] - 2026-11-20

### Added

- Playlists, VTT subtitles, chapter metadata, and structured error codes.
- Crossed-revolver crest branding and a single-screen landing page.
- A bundled desktop app that launches the local web app.

### Fixed

- Downloads from the native window and a retry when the first start fails.

[0.5.1]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.5.1
[0.5.0]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.5.0
[0.4.2]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.4.2
[0.4.1]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.4.1
[0.4.0]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.4.0
[0.3.0]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.3.0
[0.2.0]: https://github.com/sc8134/ShoreKeep/releases/tag/v0.2.0
