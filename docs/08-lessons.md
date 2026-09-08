# 8. Hard-won lessons

Things that cost real time and are not in any manual. Kept terse on purpose.

**Cameras (FLIR Grasshopper3 / Spinnaker)**
* `VideoMode` is an enum (`Mode0/1/2`), not an integer. Mode2 = vertical
  binning 2 → non-square pixels; nothing downstream may assume one pixel scale.
* Nodes gate each other in both directions: `Gamma` needs `GammaEnabled`,
  `TriggerOverlap` needs `TriggerMode=On`, the frame-rate group needs it `Off`.
  Restore must skip nodes that are inaccessible *now*.
* `ExposureMode=Timed` before `ExposureAuto`. `TriggerMode` is one shared node —
  the selector does not bank it.
* `AcquisitionFrameRate` silently ignores writes in some ROI modes; the only
  frame-rate authority is the external clock.
* `LineDebouncerTimeRaw=1000` is load-bearing (trigger ringing doubles the
  rate, invisible when the sensor has no idle time).
* A GenTL fetch with `timeout=0` on an idle stream is a native crash. Block
  first.
* `pgrExposureCompensationAuto` defaults to Continuous — a silent brightness
  drift that ruined calibration captures until forced Off.
* Never force-kill a *connected* GUI: the stale crash marker restores an old
  camera state at the next connect (this reverted a day of crop work once).

**Trigger hardware (Digilent)**
* `stop()` pins DIOs low; `start()` must release the override or the restart is
  edge-less with no error.
* A half-open device wedges the whole process; close all same-process claims
  on open. If still failed with no holder: replug USB. WaveForms holds the
  device while its window is open.

**Projector (DLP)**
* Dither beat with the camera rate (see the stimulus doc). Run at 240 Hz;
  capture at a divisor.
* No true black — dynamic dimming changes lamp state on black frames. Any
  calibration pattern sequence needs a pedestal so the lamp never idles.
* Windows may report the projector at 60 Hz through EDID even in 240 Hz mode;
  select the display by resolution and measure the flip interval instead.
* An RG830 long-pass sheet between camera and tank: measured 0.06 px median
  geometric effect — optically negligible, no map correction warranted.

**Optics and water**
* Warm water evaporates onto the mid-tank lens within 30–60 min and degrades
  detections progressively; fog does not move, so stability checks miss it.
  Wipe before every calibration; a lens heater is the real fix.
* Water keeps moving for ~10 min after any disturbance. Calibrate on still
  water and verify with a start/end re-capture (temporal QC), not with spatial
  smoothness metrics — those failed to separate real curvature from artefact.
* Grazing-angle marker decoding does not get better with tuning. Physics.

**Python / Windows environment**
* PySide6 ≥ 6.9 wheels link ICU and collide with conda's `icuuc.dll`
  (WinError 127 on `import QtCore`); pin `<6.9` and point `QT_PLUGIN_PATH` at
  PySide6's own plugins.
* Plain `pip install torch` on Windows is CPU-only; CUDA needs the
  `download.pytorch.org/whl/cu128` index with `--force-reinstall --no-deps`.
* One environment crashed on bare NumPy `matmul` (0xC06D007F) unless `cv2` was
  imported first — DLL load-order collision.
* The native Windows button style ignores stylesheet `background-color` on a
  `QPushButton` unless `border` is styled too. Colour swatches looked broken.
* Joint two-GPU DDP training is impossible on Windows (no NCCL); run one model
  per GPU via `CUDA_VISIBLE_DEVICES` instead.

**Process**
* Never run experiments inside the current-truth session directory; tools
  overwrite in place. Copy first.
* Measure the renderer, do not read about it. Plot the geometry, do not trust
  the label.
* When the operator says "I did not ask you to change that", the fix is to stop
  and ask — optical state (aperture, filters, illumination) is a deliberate
  trade-off owned by whoever runs the rig.
