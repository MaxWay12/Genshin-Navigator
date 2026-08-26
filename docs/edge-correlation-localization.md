# Edge-correlation localization experiment

Sparse cartographic areas can contain too few distinctive SIFT keypoints even
though their large-scale road, cliff, and ruin contours remain recognizable.
The optional edge-correlation localizer uses those contours only as an absolute
surface fallback.

It searches a small configured set of map scales and north-up rotations. The
minimap border and player arrow are masked. A result is accepted only when the
best normalized edge-correlation peak exceeds the score threshold and is
clearly separated from the best spatially distinct alternative. Normal Tracker
confirmation is still required, and SIFT always runs first.

Search is coarse-to-fine: a reduced atlas proposes a few spatially distinct
candidates, then full-resolution correlation refines only small windows around
them. On the current Sumeru atlas a cold-start fallback frame takes about 0.28
seconds instead of roughly 1.16 seconds for a full-resolution global scan.

`config.sumeru-portability-anchors.example.json` enables the experimental
combination of semantic anchors, bounded relative motion, and edge correlation.
Normal Fontaine configuration keeps all three portability fallbacks disabled.

On the recorded Sumeru sparse-ruins scenario the combined candidate achieved
100% tracking coverage, 4.97-pixel stationary jitter, zero false locks, and
zero wrong layers. Cold-start checks at all 15 annotated checkpoints produced
1.22–11.52-pixel error. These results justify continued portability testing,
but not enabling the fallback globally without representative scenarios from
other regions.
