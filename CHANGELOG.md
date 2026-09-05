# Changelog

## v0.1.4-alpha

- Refresh compact/expanded HUD styling: large distance, north-up compass, readable status and mouse buttons/tooltips.
- Safe mouse-hold collection, clickable hint pagination, tray lock/unlock and Russian tray labels; global shortcuts unchanged.

- Includes full experimental Sumeru from the unpublished 0.1.3 candidate.
- Unicode-safe atomic image I/O; nearly-square minimap validation and preview confirmation.
- Single localization worker and a latest-result mailbox; HUD remains responsive and expired fixes become unavailable.
- Stable target ordering, per-space selection persistence, map/list selection and restoring skipped/hidden POI.
- Manual public release checks, ZIP/SHA-256 validation and side-by-side update with staged data transfer.
- Launcher data status, progress export/import and local diagnostics access.
- Formal full-Sumeru gameplay acceptance and external tester CPU verification remain pending.

## v0.1.3-alpha — candidate, not published

- Add experimental full Sumeru surface and official underground overlays.
- Resolve current map revision/origin from public metadata and group membership from official POI links.
- Build overlapping surface sections in canonical coordinates; validate the locator before installing assets.
- Preserve SQLite progress/hints when upgrading desert POI to Sumeru; keep absent legacy points inactive in their original space.
- Keep legacy desert configs for replay. New full-region config disables desert-only fallback profiles.
- Full-region gameplay scenarios and hardware performance acceptance remain pending.

## v0.1.2-alpha — 2026-09-05

- Add local styled WebView2 launcher, region readiness and settings.
- Add independent NumPad toggle, preserve Ctrl+Alt controls and existing CLI.
- Close launcher before GPS; return to settings from the tray.
- Transfer previous portable data into a clean installation using SQLite Backup API and staged validation/rollback.
- Hardware CPU validation on the external tester's computer remains pending.

## v0.1.1-alpha — 2026-09-01

Alpha performance and distribution hardening patch.

### Changed

- Reuse one minimap SIFT extraction across compatible reference levels.
- Vectorize local reference feature selection instead of scanning keypoints in Python.
- Capture and convert only the configured minimap ROI in live modes.
- Add quality, balanced, and low-CPU scheduling with global-search backoff.
- Add pause/resume, live CV metrics, and notification-area controls.
- Add complete Ctrl+Alt keyboard controls for laptops and keyboards without NumPad.
- Add target-kind filters, persistent blacklist, and regional progress summary.
- Add privacy-safe minimap ROI selection and first-run readiness checks.
- Add Windows CI for the verified Python 3.12 environment.
- Add first-run asset setup, idempotency, failure, and atomic rollback tests.
- Remove the tracked game-derived compass crop; local development copies stay outside Git.

### Validation

- Surface benchmark mean processing: 53.88 ms → 15.19 ms.
- Surface benchmark P95 processing: 63.58 ms → 17.98 ms.
- Fontaine golden gating scenarios remain passing with zero false locks.

## v0.1.0-alpha — 2026-08-26

First public alpha of Genshin Navigator.

### Included

- Passive minimap localization and stateful tracking.
- Fontaine surface and underground navigation with official POI.
- Experimental Sumeru desert surface navigation and official POI.
- Compact, detailed, and full-map HUD modes with global numpad controls.
- Sticky chest targets, skip, collected, and undo.
- Offline-first SQLite progress and cached HoYoLAB hints.
- Optional manual additive progress synchronization with HoYoLAB.
- Privacy-safe minimap diagnostics and replay benchmarks.
- Portable Windows build with no bundled personal state.

### Known limitations

- Fontaine is the supported alpha vertical slice; Sumeru is experimental.
- Sumeru underground floors are not included.
- Sparse desert ruins can temporarily become `LOST`; false positions are rejected.
- Distance in Sumeru can be shown as uncalibrated until a regional calibration exists.
- Navigation uses straight-line direction and distance, not obstacle-aware routes.
- Global hotkeys require Navigator and Genshin to run at the same privilege level.
- HoYoLAB APIs are undocumented and can change independently of Navigator.
