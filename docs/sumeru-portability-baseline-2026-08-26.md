# Sumeru desert portability baseline — 2026-08-26

## Scope

This was a stress test of the existing localization assumptions, not an attempt to
ship Sumeru support. It used the Fontaine ROI, screen gate, matcher, local-search and
tracker thresholds unchanged. POI, navigation, progress and underground layers were
disabled.

The isolated official HoYoLAB reference uses map 2, revision
`eea752b746ae1f2e0c1988a574f2b7b0`, N1 tiles `x=32:43`, `y=22:29`. The initial
exploratory range `x=22:33` was incorrect (it covered Natlan) and its diagnostics are
excluded from the baseline.

## Observations

- A normal moving desert crop was localized in 16/16 replayed frames. The stateful
  tracker reached `TRACKING` on frame 2 and remained there for 15/16 frames.
- Confidence on that sample was approximately 0.76–1.00 with an observed minimap
  scale of approximately 0.83.
- A same-layer teleport was handled conservatively: the old position became `LOST`,
  the new global candidate was confirmed over two frames, and `TRACKING` resumed
  about 0.30 seconds after the new minimap appeared. No confident intermediate
  position was emitted.
- A visually sparse desert-ruin segment is a real limitation. Strict matching falls
  below the existing eight-inlier requirement and the tracker eventually reports
  `LOST`. A diagnostic-only relaxed search finds weak geometric evidence near the
  previous location, but that evidence is intentionally not accepted by production
  thresholds.
- Automatic initial-acquisition diagnostics previously repeated every cooldown.
  They are now latched to one report per never-acquired session; manual reports and
  established-track interruptions remain available.

## Verdict

The pipeline and Position Model are portable to a second surface region without
Fontaine-specific CV tuning, but the current single N1 reference is not sufficient
for reliable product support across all desert terrain. Portability is therefore
**promising but incomplete**.

The safe next investigation is to collect several sparse-ruin sequences and compare
an additional official detail/zoom reference against the current N1 level. Lowering
the global inlier or ratio thresholds from this single sample is explicitly not
recommended because it would weaken the existing false-lock protection.

## Regression guard

- Unit/integration tests after the portability changes: 126/126.
- Existing Fontaine golden suite: passed.
- Fontaine CV thresholds and local user data were not changed.
- Sumeru assets, crops and reports remain under `datasets/local` and outside Git.
