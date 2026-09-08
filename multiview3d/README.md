# Isolated multi-view 3D calibration branch

This package is intentionally separate from Rig Studio's primary TPS surface
mapping. It uses parametric intrinsics/extrinsics only for off-surface 3D.

Capture original-resolution, synchronized images into:

```text
session/
  cam0/frame0001.png
  cam1/frame0001.png
  cam2/frame0001.png
  cam3/frame0001.png
```

Use identical frame stems for simultaneous observations. Sweep the rigid, flat
target across every adjacent overlap, image edge, depth, tilt, and roll. Do not
resize. Keep camera ROI/offset, focus, filters, vessel position, and medium level
fixed after calibration.

Validate before calibration:

```powershell
python -m multiview3d.validate_session C:\path\to\session --output validation.json
```

The 180 x 20 mm strip is appropriate for bridging narrow adjacent overlaps but
is weak for independently estimating every intrinsic parameter because it has
only four internal-corner rows. Use full-sensor calibration capture where
possible. For reliable depth across the entire volume, include oblique/side
views; four nearly parallel top views can only triangulate where at least two
views overlap and have adequate ray angle.

Acceptance requires held-out reprojection error, an independent known-distance
test throughout the volume, and a depth-dependent refractive-bias report. Do
not accept a calibration solely because an optimizer reports a low training
reprojection error.
