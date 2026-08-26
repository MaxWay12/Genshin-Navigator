# Semantic anchor localization experiment

Genshin Navigator can optionally use teleport waypoints, Statues of the Seven,
and domain symbols as a fallback after ordinary feature matching fails. The
symbols are detected only in the captured minimap crop. Their canonical
coordinates come from the public HoYoLAB Interactive Map point catalogue.

The fallback is deliberately disabled by default. SIFT/pyramid localization
always runs first, and a single symbol is accepted only relative to an already
confirmed position. Acquisition without a prior position requires at least
three geometrically consistent symbols. The normal matcher thresholds are not
weakened.

Local single-anchor continuation is explicitly disarmed whenever the minimap
disappears. After a loading screen or teleport it cannot reuse the previous
position until SIFT or a three-anchor global acquisition establishes a new
absolute fix.

## Build a local catalogue

The catalogue and downloaded icon templates belong under `datasets/local` and
must not be committed:

```powershell
.venv\Scripts\python scripts\fetch_hoyolab_anchors.py `
  datasets\local\references\sumeru_semantic_anchors\anchors.json `
  --metadata datasets\local\references\hoyolab_sumeru_desert_n1\metadata.json `
  --area-id 4 `
  --icons-dir datasets\local\references\sumeru_semantic_anchors\icons
```

`config.sumeru-portability-anchors.example.json` shows the opt-in configuration.

## Sumeru ruins result

On the recorded sparse-ruins scenario, base-only coverage was 39.86%. Semantic
anchors raised it to 87.88%, reduced the longest unavailable interval from
23.50 seconds to 0.30 seconds, and kept false locks and wrong layers at zero.
Stationary jitter was 6.68 canonical pixels, so the experiment does not yet
meet the gating targets of 95% coverage and 5 pixels jitter. It remains an
optional portability experiment rather than the production default.

The result supports using semantic map objects as corroborating evidence, but
not replacing the visual map matcher. Detail-reference and bounded-motion
experiments keep the global matcher thresholds fixed.
