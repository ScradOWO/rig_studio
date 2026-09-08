# 7. Decision log

Chronological, with the reasoning and — where it exists — the measurement that
settled it. Superseded decisions are kept: the path matters as much as the
destination.

### 2026-07 — Stitch the four top views into one panorama (superseded)
*Approach:* projected labelled board → click correspondences → thin-plate-spline
canvas→camera maps → hard-seam stitched strip as the tracking surface.
*Then:* replaced clicking with **structured light** (Gray code + phase shift,
40 patterns) for dense correspondences, which cut seam misregistration to
0.01–0.27 px and made recalibration a 30 s operation. Along the way:
projector glare self-masks the decode (saturated pixels lose phase
amplitude) → per-camera "shaped" illumination; the DLP has no true black
(dynamic dimming) → pedestal level in all patterns; water convection during
capture bends the maps → temporal stability check re-captures one pattern at
the end of the run and flags > 0.5 px drift.

### 2026-08-14 — Abandon the stitch as a measurement surface
*Why:* the stitched canvas is a map of the **tank surface**. A fish 10–20 mm
below the surface is seen at different parallax by adjacent cameras; a surface
map cannot be metric for it. The stitch remained useful for viewing, never for
measurement. Replaced by per-camera calibration to a common metric tank frame
and 3D by triangulation. The retired structured-light/stitch implementation
is described here but is not included in this repository snapshot. *Lesson:*
optimise the objective you actually need; sub-pixel seams were the wrong
objective.

### 2026-08-17 → 21 — Rewrite acquisition around process isolation
The predecessor was MATLAB driving a compiled MEX with camera settings baked
into C++. Rewritten as: C++ bridge with no policy, one Python worker process
per camera, byte-compatible HDF5, guarded camera writes with verified restore.
*Why:* a crash in one camera must not cost the others' files; settings must be
visible and diff-able (YAML), not compiled.

### 2026-08-21 — Static ChArUco board on the floor as the world frame
*Alternative considered:* waving a board through the volume and bundle-adjusting
(anipose). *Chosen:* one motionless board, temporally averaged, solved per
camera directly against the printed grid, cross-validated on shared corners.
*Why:* no chaining, no optimiser to trust blindly, an acceptance number in
millimetres. Result 0.65 mm on first try, 0.515 mm after the final water
change.

### 2026-08-21 — Side cameras: click, do not fight the decoder
ChArUco decoding at a grazing angle fails for optical reasons. Ten manual
clicks + PnP gave 0.09–0.15 mm agreement with the adjacent top camera. A
"bookend" target for automatic side decoding is designed for the next time the
enclosure is opened, but was not allowed to block data collection.

### 2026-08-25 — Nominal intrinsics, metric gate
The 5-row strip cannot fit a full intrinsic model. Rather than pretend, use the
lens-spec focal length, no distortion, and gate on metric agreement in the
overlaps (where triangulation happens). Reprojection RMS of ≈10 px on the
2048-wide cameras is recorded, not hidden, as the reason a distortion model is
the next upgrade.

### 2026-08-25 — 120 Hz as the experiment standard
Row-readout bound at the frozen 1450-row crops; certified with zero
hardware-frame-id gaps over 30 s on all six cameras. 178 Hz (the legacy value)
was only possible at the old 800-row crops.

### 2026-08-25 — Stimulus defined in millimetres
7.858 px/mm measured from the projector footprint; defaults re-expressed as
1.00 cm/s and 20.0 mm. The legacy 120 px/s turned out to be 1.53 cm/s.

### 2026-08-25 — Lossless video for tracking
x264 `qp 0` gray, verified bit-exact against the HDF5. CRF encoding visibly
degraded the dark NIR footage where keypoints live.

### 2026-08-26 — Align by hardware frame id, never by index
Certification showed ±2-frame start raggedness between cameras. Made a hard
rule and enforced in the triangulation code.

### 2026-08-26 — Aegisub as the shape-layout editor
Build nothing the operator already has. Placement rule measured against libass
rather than assumed (the assumption was 61 px wrong).

### 2026-08-26 — Backdrops are inert
Rectangles never resize, pulse or recolour; circles do. Adopted after a
per-wall colour override recoloured the wall bands themselves — the operator
saying "I do not want the walls to do anything" was the right spec.

### 2026-09-07 — Consensus outlier rejection in triangulation
"Drop the worst residual" was shown to remove the *clean* camera in a
parallel-camera configuration and report a perfect fit 11.5 mm off. Replaced by
pairwise-RANSAC voting with a strict-majority + error-margin rule; ambiguous
cases keep all views and report the residual.

### 2026-09-08 — The frame's z axis points down
While plotting the extrinsics for this documentation, all top cameras came out
at z ≈ −50 mm. The code's label said "z up". The extrinsics are right, the label
was wrong; corrected in code and here. Recorded because it is exactly the kind
of silent sign error that costs weeks downstream.

### Portfolio documentation refresh — physical layout and larval scope
The author confirmed upward projection from below onto the coated curved trough,
four overhead and two end-view RG830-filtered cameras, and larval zebrafish/medaka
racing as the research context. Real apparatus photos and new larval illustrations
now accompany the README. The projector-to-camera timing claim was narrowed to
configured rates plus recorded flips, without assuming hardware phase lock.
Existing application, analysis, test, calibration, and configuration files were unchanged.
