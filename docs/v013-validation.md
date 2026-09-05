# v0.1.3-alpha — validation and release gate

Status: implementation candidate; **not approved for publication**.

## Data inspected on 2026-09-05

Public sources: HoYoLAB `/v1/map/list`, `/v3/map/point/list`, `/v2/map/point_group`.
Surface revision: `eea752b746ae1f2e0c1988a574f2b7b0`, origin `[24206, 8918]`.
The new downloader discovers these values at runtime; `data.asset_revision` can pin a revision.
POI payload pinning remains `data.map_version`; these are distinct values.

| Component | Download/inspection | Gameplay coverage |
|---|---|---|
| Surface | 169 tiles, canonical atlas, overlapping references | New full-region recordings pending |
| Underground | 53 drawable groups, 78 images, 77 usable localization floors | Pending |
| Catalog | 762 POI: 637 chests, 125 waypoints | Pending |
| Exclusions | 6 POI on known floors without overlays; 3 outside their reference | Not navigable |

Group 32 / floor 89 has only four detected features and is excluded from localization.
No unavailable floor is substituted with surface. Unknown group/floor links abort sync.
Missing legacy POI remain inactive in their original coordinate space, preserving progress without inventing coordinates.
Downloaded provenance is in `regional_source.json`; low-feature surface sections are recorded in `section_report.json`.
The counts are a snapshot of the existing categories returned by the public API, not a claim that all in-game puzzles/chests are represented.

## Automated checks

- 218 unit tests passed during implementation (rerun before release).
- Full locator loads 96 references: 19 surface sections and 77 underground floors. Three synthetic surface crops matched their canonical positions within 0.04 px; these are geometry smoke tests, not gameplay accuracy measurements.
- Fontaine golden suite: PASS, surface teleport coverage 98.51%, reacquire 1.50 s, false locks 0. Its old manifest lacks coordinate checkpoints: this is a regression guard, not a full positional accuracy proof.
- Legacy Sumeru recovery suite: 4/4 gating PASS; four existing informational failures preserved. Recovery-only, no positional ground truth.
- Isolated SQLite Backup API snapshot: all 9 progress records, 33 hints, 33 cached-image rows, 2 sync runs and 3968 unknown-remote records preserved exactly after two imports. Source database read-only.

Reproduce snapshot migration without touching the source:

```powershell
python scripts/verify_sumeru_upgrade.py <source.db> datasets/local/poi/sumeru.json
python -m unittest discover -s tests
```

## Still required before publication

Record eight scenarios using the full Sumeru config: forest, city, ordinary desert, sparse ruins, forest/desert teleport, forest cave, desert dungeon, floor transition.
Use the example suite `benchmarks/sumeru-full.example.json`; crops belong only in `datasets/local/scenarios/sumeru_full`.
Each scenario needs visible/optional phases and at least two position checkpoints; moving scenarios need start/middle/end. Do not relabel new failures informational to ship.
Required KPI: zero false locks/wrong layers/one-frame switches; coverage >=95%; longest unavailable <=2s; reacquire P95 <=3s; stationary jitter P95 <=5px.

Compare balanced and low_cpu on identical recordings, including process memory and CV time. The tester's earlier 60% CPU issue is **not verified resolved**.
Manual checks: clean portable setup, transfer from 0.1.2, forest/cave tracking, hints, collected/undo, restart and offline GPS. Remote sync only with genuinely collected chests.

## Developer launch

```powershell
python -m genshin_navigator setup-region --config config.sumeru-full.example.json --region sumeru --yes
python -m genshin_navigator track --config config.sumeru-full.example.json
```

Prefer the launcher for personal settings: it creates a separate full-region config and preserves the previous desert config. Existing world-unit Sumeru calibration remains compatible; no map-pixel distances are presented as metres.
