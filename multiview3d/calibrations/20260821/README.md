# Calibration session 2026-08-21 — static submerged ChArUco strip

World frame = the strip itself, in mm: x along the tank (0 at the cam5 end,
180 at the cam4 end), y across the strip (0–20), z up out of the board plane.

## Method (no board movement — the closed box is an asset, not a problem)

All cameras reference the SAME static 45x5 ChArUco strip (4 mm squares,
DICT_4X4_250, `dragstrip_charuco_calibration_target_A4.pdf`):

1. **Tops (cam0–cam3)**: auto ChArUco detection on 15–30 averaged synced frames
   per camera (`detect_charuco.py`) → per-camera PnP against the strip
   (`solve_static_172921.py`). Corner IDs make partial board views sufficient;
   no camera pair needs simultaneous full coverage.
2. **cam4 (side)**: marker bits cannot decode at grazing angle through water —
   physics. Instead: 6 manual clicks on known crossings near the strip end
   (`click_side_pnp.py`, guided by `reference_board_cam4.png` +
   `reference_view_cam4.png`) → PnP with automatic mirror disambiguation.
3. **Validation** = triangulate corners seen by two cameras, compare to the
   printed 4 mm grid:
   - tops, 88 shared corners: **mean 0.65 mm** (95th pct 0.97 mm)
   - cam4+cam0, all 6 clicked crossings: **mean 0.09 mm, max 0.12 mm**

## Files

- `calibration_5cam.json` — merged result (cam0–cam4): K, dist, R, t per camera,
  extrinsics in board-mm. Nominal intrinsics (6 mm lens / 5.5 um pitch), no
  distortion model yet; refraction is absorbed empirically (board underwater,
  like the fish).
- `calibration_tops_172921.json`, `side_cam4_pnp.json` — raw solves.
- Scripts to reproduce: `detect_charuco.py`, `solve_static_172921.py`,
  `click_side_pnp.py`, `make_click_reference.py`.
- Source frames: `charucoall_20260821_172921_cam{0..3}_png` (tops),
  `run_20260821_174123_cam4_png` (cam4) under C:/D: `h5_rec_cam*`.

## Hard rules

- **Valid only for the camera settings used in those captures.** Any change to
  crop/mode/focus/aperture/mounting ⇒ recapture (board is static: ~30 frames,
  one minute) and re-run the two scripts.
- cam1/cam2 reprojection RMS ~11 px is expected (widest views, no distortion
  model) — their cross-camera 3D error is still sub-mm where it matters.

## Open items

- **cam5**: strip end too blurred at that distance for confident manual clicks.
  Options when picked up: stronger IR near that end / one-off refocus capture
  (then restore focus and recalibrate cam5 only), more clicks over multiple
  frames, or accept single-end side coverage (cam4 end has full 3D; cam5 end
  depth interpolates from the cam2/cam3 overlap strip).
- Distortion + focal refinement for cam1/cam2 (would tighten mid-tank pairs).
- `triangulate3d` + plotting layer (2D labels in → 3D tracks + race graphs).
