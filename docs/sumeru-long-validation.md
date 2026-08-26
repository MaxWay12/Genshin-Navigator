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

## Measured result (2026-08-26)

The five-minute scenario contains 3,000 frames and 39 manually placed positional
checkpoints. It was evaluated without changing matcher or tracker thresholds.

| Metric | Result | Hard target | Verdict |
| --- | ---: | ---: | --- |
| false locks | 0 | 0 | pass |
| wrong region/layer locks | 0 | 0 | pass |
| tracking coverage | 91.86% | >= 95% | fail |
| longest unavailable streak | 1.80 s | <= 2 s | pass |
| reacquisition P95 | 1.60 s | <= 3 s | pass |
| stationary jitter P95 | 4.43 px | <= 5 px | pass |
| positional error median | 4.69 px | diagnostic | — |
| positional error P95 | 13.91 px | diagnostic | — |
| accepted edge fixes | 8 | diagnostic | — |
| absolute-fix age P95 / max | 5.30 / 13.40 s | diagnostic | — |

Coverage is strongly localized by visual domain:

| Scenario interval | Tracking coverage |
| --- | ---: |
| before the difficult ruins (0–103 s) | 95.81% |
| difficult ruins (103–149 s) | 58.77% |
| after the difficult ruins (149–301 s) | 99.27% |

The accepted edge-correlation fix that coincides with a positional checkpoint at
147.9 s has a 2.03 px error and a 0.1722 ambiguity margin. No accepted localization
exceeded its checkpoint tolerance. The remaining edge fixes do not coincide closely
enough with manually annotated frames to claim a positional error for each fix.

### Decision

The experiment confirms portability of the localization architecture and safe failure
behaviour: ordinary Sumeru terrain works, and the difficult domain becomes unavailable
instead of producing confident false locks. It does not yet prove release-grade
coverage in sparse desert ruins.

Do not lower global SIFT/tracker thresholds. The next controlled experiment should add
a real higher-detail reference limited to the 103–149 s ruins domain, keep all current
thresholds fixed, and compare it against this report. If no genuinely more detailed
source exists, test a denser pyramid and then a gated fallback matcher as separate
experiments.

### Low-observability classification

The genuine AppSample detail experiment had already been completed before this long
validation. It improved the isolated ruins scenario from 39.86% to 40.79% coverage,
without regressions but also without a meaningful gain. Repeating or tuning that
reference is therefore out of scope.

The recorded sparse-ruins domain is classified as `low_observability`. The dedicated
suite manifest may waive availability-only failures (`tracking_coverage` and an
unreacquired short visibility interval), while preserving all safety and quality gates.
False locks and wrong region/layer positions can never be waived. The raw failures stay
visible in `observed_failures`; accepted limitations are listed separately in
`waived_failures`.

```powershell
.venv\Scripts\genshin-navigator benchmark-suite `
  benchmarks\sumeru-long-observability.example.json `
  --config config.sumeru-portability-anchors.example.json `
  --report datasets\local\benchmarks\sumeru-long-observability.json
```

This classification is a benchmark decision, not a runtime geofence: the recording
does not establish the complete geographic boundary of the weak visual domain. Runtime
behaviour remains conservative—unconfirmed positions become stale/LOST, navigation is
frozen, and bounded motion cannot cross a hidden-minimap or loading interval.
