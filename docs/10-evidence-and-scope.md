# Evidence and implementation scope

This map keeps the portfolio claims tied to the available repository evidence.

| Claim | Source | What it establishes |
|---|---|---|
| Six-camera geometry | `multiview3d/calibrations/charucofin0825_20260825_153755/calibration_charucofin0825_20260825_153755.json` | Saved K, R, t for cameras 0–5; not measured CAD dimensions for housings or projector |
| 0.515 mm top-camera mean | `media/calibration_report_20260825.txt` | Shared target-corner consistency on the calibration plane; the points also contribute to calibration, so this is not an independent held-out benchmark |
| 0.155 / 0.147 mm side agreement | The named calibration JSON's `qc` object | Stored side-to-neighbor consistency; the text report predates adding manual side poses |
| 120 Hz, contiguous IDs over 30 s | `docs/02-acquisition.md`, trial `trial2_20260825_172019` | Historical instrument result recorded in the project notes; the raw trial is not shipped |
| 1.917 GB/s image payload | 2 × 1472 × 1450 + 2 × 2048 × 1450 + 2 × 2048 × 1408 = 15,975,168 B; multiply by 120 | Arithmetic at the documented crops and Mono8; not storage overhead, buffer capacity, or an independent sustained-write measurement |
| One process per camera | `rig_studio/recorder/worker.py`, `supervisor.py`; `native/src/bridge.cpp` | Implemented acquisition architecture; process separation does not guarantee survival of every hardware/OS failure |
| DLT + consensus | `multiview3d/triangulate3d.py`, `tests/test_triangulate3d.py` | Implemented algorithm and regression cases |
| SLEAP integration | `load_analysis`, `find_analysis_files`, and `run` in `triangulate3d.py` | Loads track 0 from SLEAP exports; consumes confidence scores and associates cameras by filenames |
| Larval demo accuracy | `media/portfolio/simulation_stats.json` | Synthetic geometry check with a 5 mm illustrative body, fixed seed, specified observation noise, and 96 displayed samples; not trained-model or live accuracy |
| Historical two-fish demo accuracy | `media/demo_stats.json`, `tools/render_demos.py` | Separate synthetic 24/28 mm body model, not the new larval demonstration |
| Research questions and species/age scope | Author-supplied project description | Experimental aims, not completed biological findings |
| Projector below coated trough; RG830 filters | Author clarification and apparatus photographs | Physical arrangement; filter material and coating are not characterized quantitatively by these photos |

## Configuration is not a substitute for trial metadata

The committed `configs/rig.yaml` retains earlier Mode2 ROI presets and a nominal recording-rate value of 178 Hz, alongside the current 120 Hz trigger setting. The calibration report describes larger full-resolution crops. The README's image-payload calculation refers to those documented crops, not to every possible preset in the YAML.

`apply_profile_on_connect: false` means connection adopts the cameras' current imaging settings; loading the preset is a separate action. Reproduce an instrument trial from its actual camera state and metadata, and validate calibration after changes. No configuration values were edited in this documentation refresh.

## Coordinate convention

The saved final calibration JSON still contains the historical text label `z up`. Its numerical extrinsics place overhead camera centres near z = −50 mm; the documented operational frame and reconstruction output use **z down**. The matrices have not been altered. The new visuals use those numerical poses and explicitly treat negative z as above the board.

## What is not established

- A new model architecture, trained weights, training-set size, or held-out SLEAP accuracy.
- Independent millimetre accuracy throughout the water volume; distortion and refraction remain unmodeled.
- Automatic identities for two fish across six views. `load_analysis` selects track 0.
- A species-specific advantage, endurance result, optimal depth, or winning race strategy.
- Hardware phase lock between projector refresh and camera trigger merely from a 240:120 frequency ratio.
- A public race website or betting system.

## Verification of the portfolio refresh

Existing application, native, calibration, configuration, and test files were preserved. The new renderer performs exact noiseless projection/reconstruction checks using the existing mathematical function definitions, then generates the noisy synthetic example. Its scope is numerical geometry and documentation media, not hardware, HDF5 end-to-end, or GUI verification.

The full application test suite requires dependencies not present in the documentation-rendering environment and was not run as part of this refresh. Historical test-count statements elsewhere in the notes refer to the original project environment.

Four existing numerical regression cases were also executed directly from their unchanged test definitions: exact DLT recovery, four-view majority outlier rejection, ambiguous three-view retention, and two-view retention. This direct execution avoids the unavailable pytest/HDF5 imports and is not a full pytest run. All four passed.
