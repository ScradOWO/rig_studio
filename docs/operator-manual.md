# rig-studio — operator manual

> Day-to-day operating reference for the rig software. For the design, math,
> and results, start at the [repository README](../README.md) and `docs/`.


PySide6 GUI for the 6-camera dragstrip rig: native Spinnaker C++ acquisition,
SpinView-style live tuning, MEX-compatible HDF5 recording, variable-Hz Digilent trigger
control, and an exact Python port of the
Psychtoolbox drifting-grating stimulus (`record_cameras_show_stimulus.m`).

## One-command build and launch

```powershell
.\build.ps1
.\RigStudio.exe
```

The build script compiles the native Spinnaker bridge, refreshes the editable
Python package, runs the tests through RTK, and places `RigStudio.exe` beside
the source. `Launch Rig Studio.cmd` is the double-click launcher. This EXE is a
small environment-bound launcher; it intentionally does not duplicate the
Spinnaker runtime or the conda environment.

## Capture controls

- Select `Use`, `View`, and `Record` cameras independently.
- Set camera/recording Hz once in the Trigger panel. The value sizes HDF5 files,
  the memory pool, and the app-owned Digilent clock together.
- `Launch WaveForms` switches trigger ownership to external mode.
- `Measure simultaneous rate` reports every active camera and a safe common Hz.
- VideoMode transitions preserve sensor coverage per axis: Mode2 → native Mode0
  doubles vertical geometry only, while Mode1 → Mode0 doubles both axes.
- Crop helper (Camera Settings dock, right side): click a camera tile, start
  preview so a frame arrives, then "Freeze previous frame for cropping" — the
  tile holds that frame and draws an orange rectangle with drag handles.
  Drag inside the rectangle to move it, drag an edge/corner handle to resize;
  releasing the mouse quantizes to node increments and writes the new
  Width/Height/OffsetX/OffsetY to the camera. The spinboxes stay in sync and
  still work for exact values (VideoMode changes are rescaled onto the frozen
  reference). The button refuses and says so if no frame has been received yet.
- Open the selected camera in a separate Fit / 1:1 preview window.
- Preview orientation follows the camera's raw/SpinView orientation. Optional
  per-camera display rotation remains available through `preview_rotation` in
  the config, but is disabled for the current rig.
- Preview-only auto contrast and display gamma improve visibility without
  changing exposure, gain, HDF5 pixels, or exported raw pixels.
- Choose either the normal HDF5 recording or frames-only output. Frames-only
  samples the requested count of full-resolution triggered frames evenly across
  the run directly into lossless Mono8 PNGs (0 means every frame). It never
  creates an HDF5 clip.
- Trigger/Hz controls are locked during recording, and the simultaneous-rate
  check restores the previous preview mode, acquisition state, and app clock.

The isolated `multiview3d/` package validates exact-board, synchronized
calibration image sessions without changing the primary TPS surface pipeline.

## Install / run

```bat
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -File C:\Dragstrip\atharva_work\rig_studio\native\build.ps1
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m pip install -e C:\Dragstrip\atharva_work\rig_studio
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m rig_studio.app                 :: GUI (hardware)
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m rig_studio.app --backend sim   :: GUI (no hardware)
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m rig_studio.app --backend harvesters :: GenTL fallback
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m rig_studio.app probe --apply   :: CLI camera check
C:\Users\medaka\anaconda3\envs\stitch\python.exe -m rig_studio.app restore         :: crash-marker restore
```

The native build uses the installed Spinnaker SDK and Visual Studio C++ toolchain.
Set `SPINNAKER_ROOT` or `RIG_STUDIO_SPINNAKER_DLL` only for non-default locations.
Install `.[gentl]` if the optional Harvesters fallback is needed.

Tests: `rtk test pytest` from this directory (hardware-free by default).

## Architecture

- **GUI process** (PySide6): panels only; never touches camera buffers.
- **One worker process per selected camera**, each with its own native Spinnaker handle, fetch
  thread, HDF5 writer thread). Native acquisition writes directly into caller-owned
  NumPy/ring storage. Commands/events over a pipe; pipe EOF = clean stop, so a
  GUI crash cannot corrupt files. Preview frames reach the GUI via per-camera
  shared-memory triple buffers.
- **Stimulus host process** (pygame fullscreen on the GamePix projector),
  JSON-lines on stdin + status file — 240 Hz pacing isolated from the GUI.

## Camera selection

The **Cameras** dock has three checkboxes per camera:

- **Use**: opens a worker when Connect is pressed. This is locked while connected.
- **View**: shows or hides the live tile and can be changed at any time.
- **Record**: creates an HDF5 file for that camera. Preview-only workers remain live.

Use/View/Record All and None buttons make common subsets quick. Record selection is
locked while a run is active. Defaults live in `configs/rig.yaml` as
`gui.default_use_positions`, `gui.default_view_positions`, and
`recording.default_positions`.

Every camera write goes snapshot → allowlist → write → cache-bypassed readback →
guaranteed restore on exit (vendored racestrip `GuardedRig`). Forbidden nodes
(UserSet/FactoryReset/...) are never written. `AcquisitionFrameRate` is never
written — frame rate comes from the Line0 clock only.

## Recording format (byte-compatible with the MEX)

`<root>/<base>_cam<pos>.h5`: `/images` uint8 `[max_frames × W*H]` (chunks
`[32 × W*H]`), `/timestamps` float64 `[max_frames × 3]` = (t_host_s from the gate,
writer_seq 1-based, hw_frame_id), root attrs uint64 camera_index / max_frames /
image_width / image_height. Unwritten rows read as zeros (same
first-all-zero-row = end-of-data convention). `h5_to_video.py` works unchanged.
Compression is OFF by default (the MEX baseline); enable zstd-3 in `configs/rig.yaml`.
In camera reports, `stopped_cleanly: true` plus `writer_error: null` indicates a
successful timed/manual stop. The legacy `complete` field means only that the
preallocated `max_frames` capacity was reached, so it is normally false.

t_host origin differs benignly from the MEX: seconds from the shared gate instant
(so all 6 files use one origin) instead of per-process start.

## Combined run timeline (Run panel, mode "both")

arm (files open, gate closed) → trigger start (app Digilent or external WaveForms)
→ all 6 cameras streaming (start gate) → 0.5 s preroll → absolute-POSIX write gate
broadcast (`gate_delay_s` = 2.0) → 2 s black baseline → grating for `grating_s`
→ timed stop → per-camera reports + `stim_log_<base>.mat` in roots[0].

Stimulus onset = gate + `baseline_s` exactly (the MATLAB script had ~0.2 s of
extra pause slop; here it is exact and logged).

## Grating (exact PTB port)

`luminance(x) = clip(smoothstep(-0.2, 0.2, sin(2πx/157.5 + phase))·2 − 1, 0, 1)` —
a smoothstep-hardened sine (≈5 px rendered edge), NOT an ideal square wave; the
PTB `duty` parameter was a shader no-op and is intentionally not implemented.
120 px/s drift, dt-corrected from measured flips (fallback ifi, reject dt ≤ 0 or
> 0.05 s), bottom-half region by default, black inactive area.

## Hardware checklists (first light)

1. **Cameras**: close SpinView/MATLAB → `rig_studio.app probe` (read-only) →
   `probe --apply` → values match the table in `configs/rig.yaml`.
2. **Preview**: GUI → Connect → clock "Free-run" → Start preview → 6 live tiles;
   switch to the desired triggered Hz, start the Digilent clock (Trigger panel or
   WaveForms) → tiles live again; stop clock → tiles stall (expected).
3. **Benchmark before trusting recording**: 60 s triggered preview with zero
   DROP badges; then a short recording; then a 25 s full-rate soak — reports
   must show dropped=0 and ~4450 frames/cam.
4. **Byte-compat**: run `h5_to_video.py` on a rig-studio file; compare structure
   with a MATLAB-era file (`h5dump -H`).
5. **Projector**: Stimulus panel → Start host (uncheck windowed) → flip-timing
   test ≈ 240 Hz → Preview grating and compare side-by-side with
   `stimulus_only.m`.
6. **Full run**: Run panel mode "both", 25 s — verify gate alignment across the
   6 files (first t_host within one trigger period) and the stim log fields.

## Known env quirks

- PySide6 pinned `<6.9`: 6.11 wheels link ICU and collide with the conda env's
  `icuuc.dll` (WinError 127 on QtCore import).
- `rig_studio/gui/__init__.py` sets `QT_PLUGIN_PATH` to PySide6's own plugins —
  the conda env ships a different Qt build that otherwise hijacks plugin search.
- State (snapshots, crash markers, stimulus status) lives in
  `%USERPROFILE%\.rig_studio\state`.
