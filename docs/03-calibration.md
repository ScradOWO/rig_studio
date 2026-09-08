# 3. Metric calibration of six cameras

Goal: one rigid frame in **tank millimetres** shared by all cameras, good
enough that a point seen by any two cameras triangulates to the same place.

## 3.1 Target

A 45×5 ChArUco strip — 4.0 mm squares, 2.8 mm markers, `DICT_4X4_250`, ids
0–111, 180×20 mm overall (`multiview3d/charuco.py`). It lies along the trough
floor **in water**, bridging the r = 25 mm channel as a chord ≈2.5 mm above the
true bottom. Its printed grid *is* the world frame: corner (i, j) is at
(4i, 4j, 0) mm. Nothing is chained camera-to-camera; every camera is solved
directly against the same physical grid, so errors do not accumulate along the
row of cameras.

## 3.2 Top cameras — automatic

`multiview3d/calibrate_static.py`, one command per calibration session:

1. Each camera records a short burst of the motionless board; frames are
   **temporally averaged** (kills sensor noise and water shimmer) and
   contrast-normalised with CLAHE before detection.
2. ChArUco markers → interpolated chessboard corners with ids.
3. Pose per camera by `solvePnP` + Levenberg–Marquardt refinement against the
   known corner coordinates, giving `R, t` with `X_cam = R·X_world + t`.
4. **Cross-validation on shared corners**: for every adjacent camera pair, the
   corners both cameras saw are triangulated from the two poses and compared
   with the printed grid. This is the acceptance metric, because it measures the
   thing the pipeline actually does later (triangulate across overlaps).

Result for the live calibration (`media/calibration_report_20260825.txt`):

| pair | shared corners | mean error | max |
|---|---|---|---|
| cam0 + cam1 | 16 | 0.339 mm | 0.841 mm |
| cam1 + cam2 | 29 | 0.578 mm | 0.820 mm |
| cam2 + cam3 | 33 | 0.546 mm | 1.180 mm |
| **overall** | **78** | **0.515 mm** | gate 1.5 mm — PASS |

### Intrinsics: a deliberate simplification, stated honestly

The cameras use **nominal intrinsics**: focal length 6 mm / 5.5 µm px
= 1090.9 px, principal point at the crop centre, **no distortion model**. A
5-row strip cannot constrain a full intrinsic model, and a separate in-air
lens calibration was deferred. The consequence is visible in the per-camera
reprojection RMS: ≈1 px on the 1472-wide cameras (cam0 0.95, cam3 1.15) but
≈10 px on the 2048-wide cameras (cam1 9.89, cam2 10.37), i.e. uncorrected lens
distortion at the far edges of the wide crops. The acceptance gate is
nevertheless *metric agreement in the overlap zones* — sub-millimetre — which
is where triangulation happens. Adding a distortion model is the first
accuracy upgrade on the list (see §3.6).

## 3.3 Side cameras — manual PnP

The two end cameras view the strip at a grazing angle through the flat end
windows. Marker bits foreshorten below the decoder's resolution, so automatic
detection fails — this is physics, not a tuning problem. Instead
(`multiview3d/click_side_pnp.py`):

1. the operator clicks ≈10 named chessboard crossings whose world coordinates
   are known from the top-camera solution;
2. PnP is solved for **both mirror hypotheses** (the strip is symmetric along
   its axis, so "which end am I looking at" is ambiguous from the crossings
   alone) and the lower-residual hypothesis wins;
3. agreement with the adjacent top camera on shared points is reported.

Result: cam4 vs cam0 **0.155 mm**, cam5 vs cam3 **0.147 mm** (10 clicks each).
The clicks must be redone whenever the board is re-placed — carrying a pose over
via the new top solution alone agrees only to ≈4 mm because of the nominal
intrinsics' pose wobble.

## 3.4 The resulting geometry

Camera centres `C = −Rᵀ t` in the tank frame (mm):

| camera | x | y | z | role |
|---|---|---|---|---|
| cam3 | 8.2 | 11.2 | −48.2 | top, x≈0 end |
| cam2 | 55.2 | 11.1 | −50.9 | top |
| cam1 | 109.9 | 10.9 | −50.3 | top |
| cam0 | 170.8 | 9.8 | −50.4 | top, x≈180 end |
| cam5 | −32.5 | 9.5 | 4.4 | side, looks along +x |
| cam4 | 210.6 | 11.0 | −5.6 | side, looks along −x |

So the top cameras sit ≈50 mm above the floor at ≈55 mm spacing; the side
cameras are beyond the two ends at roughly fish height. **z increases away from
the top cameras (downward)**; the water surface is at z ≈ −22.4 mm. (An earlier
label in the code said "z up" — wrong, corrected after checking the extrinsics;
see the decision log.)

![Extrinsics](../media/camera_geometry.png)

## 3.5 Validity conditions

A calibration is void if any of these changes: sensor Width/Height/OffsetX/
OffsetY or binning, focus, aperture, mounts, board placement, water level. All
were frozen on 2026-08-25 and the frozen values are recorded in
`configs/rig.yaml`. The calibration directory name is stamped into every
triangulation output.

## 3.6 Known limitations and the planned fixes

* **Refraction.** Rays cross an air→water interface (and, for the side
  cameras, the end window). Because the board was calibrated *in situ*, the
  z = 0 plane is metric; points above it accumulate a depth-dependent bias
  that is not yet modelled. Plan: either a refractive camera model or a
  depth-anchored correction validated with a target moved through the volume.
* **Distortion** (above). Plan: per-lens intrinsic calibration in air as the
  initialisation, extrinsics still solved wet.
* **Side automation.** A "bookend" target (floor strip + parallel raised strip
  + face-on end tabs, `multiview3d/targets/bookend_v1`) is designed and printed;
  it gives the side cameras decodable face-on markers and, with two parallel
  strips at known heights, an actual focal-length estimate. A parallel raised
  strip alone would be useless — with unknown focal length, height and scale
  trade off; the tilt/known-height is what breaks the degeneracy.
