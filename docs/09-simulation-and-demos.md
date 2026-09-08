# 9. Historical demonstrations: what is real, what is simulated

> These are the original demonstrations, retained unchanged. The refreshed README uses [new larval visuals](11-visuals.md). This older model uses 24/28 mm bodies and should not be read as a larval size or species-performance model. Its projector timing assumes an ideal phase relationship; that is not evidence of actual hardware genlock. Its flat stimulus-floor rendering is a simplification of the coated curved trough.

The animated media in `media/demo_*` and the interactive viewer are generated
by one script, `tools/render_demos.py`, so that every figure in this repository
can be reproduced from the committed calibration and configuration:

```
python tools/render_demos.py          # ≈2 min, writes media/demo_*.gif, interactive_race.html, demo_stats.json
```

## 9.1 Honesty line

| Real (taken from the instrument as shipped) | Synthetic |
|---|---|
| the six camera matrices `P = K[R\|t]` from the live calibration JSON | the two fish and their movement |
| sensor crops (1472×1450, 2048×1450, 2048×1408) → field of view of every camera | the 2D detection noise model standing in for a pose network |
| the grating: 20.0 mm period, 1.00 cm/s drift, 240 Hz projector | |
| the trial timeline (baseline, 120 Hz clock, hardware frame ids, ±2-frame ragged starts) | |
| the reconstruction: production `triangulate_views` with consensus rejection | |
| the 4-node SLEAP skeleton actually used for labelling (head, body, tail_base, tail_end) | |

No animal data is used. Every animated frame carries the watermark
*"SYNTHETIC fish trajectories · REAL calibration, crops, stimulus parameters
and triangulation code"*.

## 9.2 Fish kinematics

Each animal is a point (head position **p**, heading θ in the horizontal plane,
depth z) plus a body midline. Per 120 Hz step:

* **Bouts.** A Poisson process at rate λ starts a bout; a bout adds a speed
  boost that decays exponentially, `b ← b·e^{−dt/τ}`, so speed is
  `v → v_base + b` with first-order relaxation (50 ms). Zebrafish:
  λ = 1.6 s⁻¹, peaks 60–180 mm/s, τ = 0.12 s (beat-and-glide); medaka:
  λ = 1.0 s⁻¹, peaks 25–60 mm/s, τ = 0.25 s (steadier cruising).
* **Optomotor following.** Heading relaxes toward +x (the grating drift
  direction) with lane keeping toward a preferred y and a small random wander;
  depth is a bounded random walk in −19 … −3.5 mm. The lateral bound is the
  trough's free half-width at that depth,
  `w(z) = √(r² − (z − z_c)²) − 2 mm`, with r = 25 mm and the trough axis at the
  water surface z_c = −22.5 mm.
* **Undulation.** Body points at arc fractions s ∈ {0, 0.33, 0.66, 1} of the
  body length L (the SLEAP nodes) are displaced laterally by a travelling wave

  `Δ(s, t) = A · L · a(t) · s^1.5 · sin(φ(t) − 2π s)`,  `φ̇ = 2π f_tail (0.4 + 0.6 a)`

  with amplitude growing toward the tail, wavelength one body length, and the
  activity `a = clip(v / v_peak, 0.12, 1)` suppressing the wave while gliding.
  Zebrafish: L = 28 mm, A = 0.13, f_tail = 24 Hz; medaka: L = 24 mm, A = 0.10,
  f_tail = 14 Hz.
* The race ends at x = 176 mm; the fish then idles.

This is a phenomenological model chosen to *look* like the two species and to
exercise the pipeline with realistic speeds (bursts up to ≈180 mm/s = 1.5 mm
per frame at 120 Hz) — not a biomechanical claim.

## 9.3 Observation model (stand-in for SLEAP)

For every camera, node and frame the true point is projected with the real
`P`; a detection exists if it lands inside that camera's crop and in front of
the camera. Then: Gaussian pixel noise σ = 1.5 px; 4 % of detections dropped;
3 % replaced by gross outliers displaced 25–70 px in a random direction (a
confident mis-detection). The six-camera GIF shows exactly these detections.

## 9.4 Reconstruction and what the numbers say

`multiview3d.triangulate3d.triangulate_views` — the production code, not a
demo copy — triangulates each node from all cameras that detected it and
applies the pairwise-consensus outlier vote (§5). From `media/demo_stats.json`
for the 12 s race (2 fish × 4 nodes × 1440 frames):

| | zebrafish | medaka |
|---|---|---|
| node-frames reconstructed | 99.6 % | 99.6 % |
| median 3D error vs truth | 0.14 mm | 0.14 mm |
| 95th-percentile 3D error | 0.50 mm | 0.46 mm |
| camera views rejected by consensus | 480 | 482 |
| finish time | 6.2 s | 9.5 s |

Two points worth noticing. With σ = 1.5 px at ≈50 mm working distance
(≈0.05 mm/px on the floor), a sub-0.2 mm median is the expected
noise-limited figure — the demo is a correctness check on the geometry and
alignment, not a claim about live accuracy, which will be bounded by the
intrinsics and refraction issues in §3.6. And the tail of the error
distribution (spikes to ≈1.5 mm in the dashboard) is exactly the ambiguous
case discussed in §5.3: an outlier in a node seen by only two or three
cameras, where consensus cannot vote it out and the residual is reported
instead of hidden.

## 9.5 The individual pieces

* `demo_race_3d.gif` — the race in the tank frame with the drifting grating on
  the floor, camera centres and optical axes from the calibration, 1 s trails.
* `demo_six_views.gif` — each camera's crop with the noisy 2D keypoints,
  outliers marked; the dashed outline is the calibration board projected
  through that camera (orientation aid).
* `demo_reconstruction.gif` — reconstructed points against truth, per-node 3D
  error over time with rejected views as a rug, and the race plot against the
  grating drift line.
* `demo_acquisition_timing.gif` — the drivers in slow motion: the 120 Hz
  trigger, six 5 ms exposures, the 240 Hz projector flips (phase-locked, two per
  frame), hardware frame ids including the ±2-frame ragged start, and the
  running HDF5 byte count at 1.92 GB/s.
* `projector_beat.png` — the 2 Hz beat of 60 Hz DLP dither sampled at 178 Hz,
  and its absence at 240/120 Hz, from a 50 % dither toy model.
* `interactive_race.html` — a dependency-free canvas viewer: drag to rotate,
  wheel to zoom, scrub or play the race. Open the file locally, or through a
  static-HTML preview service once the repository is public.
