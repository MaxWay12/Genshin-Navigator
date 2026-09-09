# v0.2.0-alpha — implementation checkpoint

Date: 2026-09-09. Development branch: `codex/exploration-0.2.0`.
This is an incomplete development checkpoint, not a release candidate or a
statement that the full v0.2.0 plan has passed acceptance. Application version
remains 0.1.4a1 until candidate preparation. Nothing has been published.

## Verified baseline

- Before changes: 244 unit tests passed. Current checkpoint: 264 passed.
- Launcher JavaScript syntax check passed; native WebView2 interaction has not
  been manually accepted for the new screens.
- Existing Fontaine suite: PASS; surface teleport coverage 98.51%, acquisition
  1.5002 s, no reported false locks. The old manifest lacks coordinate checkpoints;
  this is regression evidence, not complete positional validation.
- Existing Sumeru recovery suite: 4/4 mandatory PASS; four known informational
  failures remain. This suite is recovery-only without positional ground truth.
- CV thresholds were not changed. No CPU claim is made for the unavailable tester.

## Implemented at this checkpoint

- Transactional SQLite schema v4 with pre-migration backup through the existing
  provider. Categories distinguish permanent progress, outings and landmarks.
- Outing collection/reset is separate from permanent progress and remote sync.
  Public progress sync includes only known permanent categories.
- Persistent single-space collection plans, current step, activation, session
  skip/undo and selection by the existing Navigation Controller. Launcher has
  a basic plan editor, category settings and catalog statistics.
- Cursor-centred map zoom, pan, reset, player/target centring and explicit follow.
- HoYoLAB launcher controls use an isolated child process: preview waits for a
  separate confirmation. EOF/cancel does not apply sync. No account sync was
  performed as part of development verification.
- Opt-in route recording from map/tray, at most 2 points/second, timestamp
  deduplication, discontinuities and bounded non-blocking journal queue. JSONL
  recovery and coordinate-only JSON export are implemented. No screenshots or
  credentials are part of this format.
- Pure directed graph/A* prototype requires explicitly reviewed segments and
  connections. Geometric crossings and reverse edges are not connected
  automatically. It is NOT wired to live guidance and has no accepted pilot.
- Portable transfer includes routes/plans directories and Mondstadt config.

## Official data evidence

Public HoYoLAB label/tree and regional metadata were queried without credentials.
Verified IDs include statue 2, waypoint 3, anemoculus 5, dendroculus 403,
hydroculus 508, crimson agate 141 and time trial 64. Specialty labels are listed
explicitly in `hoyolab_poi.py`; display filters do not control catalog ingestion.

Mondstadt setup completed in ignored local data: 176 surface tiles, official
underground group 196 with 3 floor images, and 1163 selected POIs. All 66
anemoculi and 80 crimson agates in the inspected response belong to area 1.
The statue/waypoint split was corrected after that download; regenerate/sync
the development catalog before testing category display.

Downloaded images and official group membership do not demonstrate successful
localization or traversal. Mondstadt/Dragonspine remain experimental. No new
gameplay coverage is claimed.

## Still required before a candidate

- Map search, clustering, independent layer browsing, complete state sections,
  and plan creation/visualization from the map.
- Full plan editing/import/export UX and explicit ordering preview.
- Hint gallery with lazy loading, compatible cache/LRU and image viewer.
- Trace naming, trimming/exclusion, viewer, reviewed import UI and metadata
  registration. Current recorder stores raw journals and exports only.
- Versioned graph decisions/repository, connection editor, endpoint preview,
  off-path/frozen guidance and manual pilot. Keep guidance disabled until accepted.
- Native GUI acceptance, migration/transfer acceptance for all new data, privacy
  and failure-path tests beyond the current unit coverage.
- Five Mondstadt scenarios, outstanding eight Sumeru scenarios, Fontaine
  checkpoint strengthening and same-recording performance comparison.
- Documentation for users, version bump, portable ZIP/SHA, clean-install and
  updater acceptance. Publication requires a separate action.

The tables for hint galleries and route documents are scaffolding, not proof
that their user-facing workflows are implemented. Route traces are not ground
truth for the localization benchmark.
