# Sumeru desert portability test

This is a deliberately isolated stress test, not product support for Sumeru. It
reuses the Fontaine ROI, screen gate, matcher, local-search and tracker thresholds
without tuning them for the new region. POI, progress, guidance and sync are disabled.

## Prepare the official reference

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_atlas.py `
  datasets\local\references\hoyolab_sumeru_desert_n1 `
  --region-id sumeru_desert --level-id sumeru_desert_surface_n1 `
  --zoom N1 --x 32:43 --y 22:29 --allow-missing
```

The atlas and all real recordings stay under `datasets/local` and are excluded from
Git. The regular `config.json`, Fontaine atlas, database and progress are not used.

## Smoke test

Travel to the Sumeru desert surface and run:

```powershell
.venv\Scripts\genshin-navigator track `
  --config config.sumeru-portability.example.json
```

The expected result is a stable marker on the test atlas. `LOST` is preferable to a
confident position in a wrong place.

## Record the local portability suite

Record three independent surface samples. Use distant, visually different places;
do not use underground maps, the desert city map zoom or special quest-only maps.

```powershell
.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\sumeru_desert_walk `
  --config config.sumeru-portability.example.json --duration 20 `
  --name "Sumeru desert walk and stop" --expected-start-layer surface `
  --expected-end-layer surface --stationary-last-seconds 5

.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\sumeru_desert_teleport `
  --config config.sumeru-portability.example.json --duration 30 `
  --name "Sumeru desert teleport" --expected-start-layer surface `
  --expected-end-layer surface

.venv\Scripts\genshin-navigator record-sequence `
  datasets\local\scenarios\sumeru_desert_landmark `
  --config config.sumeru-portability.example.json --duration 15 `
  --name "Sumeru desert second landmark" --expected-start-layer surface `
  --expected-end-layer surface --stationary-last-seconds 5
```

Run the exact same hard KPIs as the Fontaine golden suite:

```powershell
.venv\Scripts\genshin-navigator benchmark-suite `
  benchmarks\sumeru-desert-portability.example.json `
  --config config.sumeru-portability.example.json `
  --report datasets\local\benchmarks\sumeru-desert-portability.json
```

Passing means the current localization assumptions generalize to this surface domain.
It does not imply complete Sumeru support: city zooms, forests, caves, layers, POI and
navigation remain explicitly out of scope.
