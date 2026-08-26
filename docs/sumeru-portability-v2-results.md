# Sumeru portability v2 results

## Base-only baseline

Four real sequences were replayed with the Fontaine matcher and tracker
thresholds unchanged. Ordinary desert movement and the second landmark passed.
The teleport scenario retained zero false locks but missed the 95% coverage
gate. Sparse ruins were the dominant failure: 39.86% tracking coverage and a
23.50-second longest unavailable interval, with zero false locks and zero wrong
layers.

## Semantic map anchors

Public HoYoLAB points for teleport waypoints, Statues of the Seven, and domains
were projected into the canonical atlas. Official icon images were used as
minimap templates. The fallback runs only after primary matching fails and does
not weaken any SIFT or tracker threshold.

On sparse ruins it achieved 87.88% coverage and a 0.30-second longest
unavailable interval. False locks and wrong layers remained zero. Stationary
jitter was 6.68 canonical pixels, so this variant failed the 95% coverage and
5-pixel jitter gates. It remains disabled by default.

## AppSample detail reference

A genuine level-15 AppSample reference was registered to the official HoYoLAB
canonical atlas. Registration quality was strong: 2476 inliers and 0.166-pixel
median reprojection error. A crop limited to the sparse-ruins area was added as
a 4x detail pyramid level. Matcher and tracker thresholds were unchanged.

The candidate reached 40.79% ruins coverage versus 39.86% for base-only and did
not regress the other three scenarios. This is not a meaningful improvement,
so the detail level is rejected and is not part of the default configuration.

## Bounded relative motion

A conservative optical-flow fallback was tested after an absolute SIFT or
semantic-anchor fix. Lucas-Kanade tracks must agree with phase correlation,
large steps are rejected, and relative motion is limited to five consecutive
frames. Any hidden-minimap/loading interval discards continuity, so the
fallback cannot carry an old position through a teleport.

Combined with semantic anchors, sparse-ruins coverage reached 91.38%, up from
39.86% for base-only. The longest unavailable interval fell to 0.30 seconds,
stationary jitter was 4.88 canonical pixels, and false locks and wrong layers
remained zero. The teleport scenario also retained zero false locks, with a
1.70-second reacquisition and 4.62-pixel P95 checkpoint error.

The variant caused no previously passing scenario to fail, but neither the
ruins scenario (91.38%) nor the recorded teleport scenario (92.47%) reached the
95% coverage gate. It therefore remains disabled by default.

## Weak-evidence dispatch and absolute edge correlation

The remaining unavailable frames were not hard SIFT failures. Many contained a
formally valid SIFT transform with confidence between 0.21 and 0.34, below the
Tracker acceptance threshold. Pyramid dispatch previously returned that weak
candidate immediately, preventing semantic anchors and bounded motion from
running. Weak primary evidence is now retained for diagnostics while the
strict fallback chain is evaluated; all matcher and Tracker thresholds remain
unchanged.

For cold starts in the same sparse domain, an optional north-up edge-correlation
localizer searches fixed map scales and small rotations. It accepts only a
strong, spatially unique peak. Across all 15 annotated ruins checkpoints it
localized from scratch with 1.22–11.52 canonical-pixel error. This method is
also disabled by default and belongs only to the Sumeru portability profile.

The combined candidate reached 100% tracking coverage in sparse ruins, with a
0.05-second longest unavailable interval, 4.97-pixel stationary jitter, zero
false locks, and zero wrong layers. The two ordinary desert scenarios stayed
passing. The recorded teleport scenario remained at 92.47% coverage with zero
false locks and 1.70-second reacquisition; its manifest currently marks the
entire short sequence as required, so the allowed reacquisition interval alone
exceeds the 5% coverage budget.

## Verdict

Semantic anchors, bounded relative motion, correct weak-evidence dispatch, and
unique edge correlation solve the recorded sparse-ruins case without weakening
global thresholds. Fontaine remains regression-free. Sumeru is still an
experimental portability profile until the teleport scenario phases are
correctly annotated and the full suite passes its gating policy.
