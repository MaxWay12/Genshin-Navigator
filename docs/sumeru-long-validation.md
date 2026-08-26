# Long Sumeru localization validation

This experiment measures the existing localization pipeline. Do not change matcher or
tracker thresholds between recording, annotation, and evaluation.

## Route

Record one continuous 3-5 minute route containing:

1. ordinary desert;
2. a sparse area;
3. ruins and a five-second stop;
4. return to a feature-rich area;
5. teleport and loading screen;
6. cold reacquisition in a sparse area;
7. movement through ruins and a final five-second stop.

Opening the world map and loading screens remain optional phases during evaluation.
Add checkpoints immediately before, inside, and after every sparse or ruined segment.

## Record

```powershell
.venv\Scripts\genshin-navigator record-sequence `
  datasets/local/scenarios/sumeru_long_validation `
  --config config.sumeru-portability-anchors.example.json `
  --duration 300 `
  --name sumeru-long-validation `
  --expected-region sumeru_desert `
  --expected-start-layer surface `
  --expected-end-layer surface `
  --stationary-last-seconds 5 `
  --required-throughout
```

## Annotate and evaluate

```powershell
.venv\Scripts\genshin-navigator annotate-scenario `
  datasets/local/scenarios/sumeru_long_validation `
  --config config.sumeru-portability-anchors.example.json `
  --region sumeru_desert `
  --layer surface `
  --timestamps 0.28 7.5 9.3 77.0 78.6 103.0 104.6 114.8 115.8 118.0 122.0 133.8 135.5 137.7 140.0 142.3 147.9 149.0 193.4 220.0

.venv\Scripts\genshin-navigator evaluate-sequence `
  datasets/local/scenarios/sumeru_long_validation `
  --config config.sumeru-portability-anchors.example.json `
  --report datasets/local/portability/sumeru-long-validation-report.json
```

The report includes positional P50/P95, edge ambiguity margins, absolute-fix age,
tracking coverage, false locks, reacquisition, visible LOST streaks, and per-checkpoint
predicted/ground-truth positions. A checkpoint only attributes an edge margin when its
nearest replay frame actually used an accepted edge-correlation fix.
Use `N` and `B` to jump between the suggested times. A coarse click zooms the atlas;
click the same position again to refine it, then press `N`. Press Enter once all
suggested positions are set.
