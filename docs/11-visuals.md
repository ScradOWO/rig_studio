# Visuals: photographs, models, and provenance

## What the new media show

| Asset | Source and interpretation |
|---|---|
| `media/photos/rig_overview.jpg` | Author photo `1000218567.jpg`, copied without editing |
| `media/photos/camera_and_trough.jpg` | Author photo `1000224898.jpg`, copied without editing |
| `media/photos/projector_below.png` | Author photo `1000236609.png`, copied without editing |
| `media/portfolio/rig_concept.png` | AI-generated studio concept, revised using the author photos; six-camera arrangement and below-trough projection follow the author description; not measured CAD or an actual photograph |
| `rig_geometry.png`, `rig_overview.gif` | Programmatic shaded 3D geometry with exact saved camera centres/axes; housing sizes and projector placement are schematic |
| `stimulus_3d.gif` | Two illustrative 5 mm larvae in a curved-screen grating; no measured species differences or multi-animal identity inference |
| `six_views_3d.gif` | One synthetic larva projected through saved K, R, t and shown in six aspect-preserving image crops, with injected noisy keypoints |
| `pose_reconstruction.gif` | Magnified synthetic body, reconstructed landmarks, observed synthetic 3D error, and number of retained views |
| `acquisition_timing.gif` | Schematic 120 Hz shared camera trigger and 240 Hz projector refresh; projector phase is an illustration, not a genlock measurement |

All GIF frames carry a synthetic/schematic footer. The body mesh is a small generic larval illustration with eyes, a tapering body, and a finfold; it is not an anatomical species/age model. The 5 mm length is a rendering assumption, not a reported specimen measurement.

## Rebuild the technical media

From the repository root:

```bash
python -m pip install numpy pillow pyyaml
python tools/render_portfolio.py
```

This new, documentation-only script leaves existing runtime and analysis code unchanged. It reads the fixed calibration tag `charucofin0825_20260825_153755`; it never chooses a calibration from filesystem modification times. It records hashes of input text with line endings normalized to LF in `media/portfolio/render_manifest.json`.

For a small dependency footprint, it compiles the unchanged AST definitions of `triangulate_dlt`, `reproject_px`, `_solve`, and `triangulate_views`, plus their threshold constant, from `multiview3d/triangulate3d.py`. Thus the numerical reconstruction uses the existing function bodies without importing the HDF5/GUI/acquisition stack. This does not exercise recording alignment or file I/O. The renderer imports the existing stimulus `profile()` function directly.

The new single-larva observation example uses seed 23, 1.5 px Gaussian noise, 4% misses, and 3% gross outliers. Statistics are written to `simulation_stats.json`. GIFs display 16 frames per second; that is a presentation cadence, not a claim that the acquisition runs at 16 Hz. No temporal tracking or species classification is evaluated.

The grating's luminance is computed with the existing function at the documented axial scale, but the mapping onto the curved screen is illustrative. Water refraction, camera lens distortion, light scattering, animal occlusion by the near wall in the cutaway, and actual projector rays are not modeled. The larval trajectory is a smooth illustrative loop rather than a model of endurance or optomotor physiology.

## Concept-render prompt

Generated with the built-in image tool, then edited using the real camera/trough photographs as visual references. The final prompt specified: a clean dark studio render of a compact open half-cylinder coated trough; four overhead FLIR-style machine-vision cameras and two inward-looking end cameras; dark RG830 filter glass, long lenses, silver rings, machined mounts, and blue connectors; a projector below shining upward onto the coated curved surface; a black-and-white grating; two tiny translucent larval fish with prominent eyes and slender tails; no adult fish, university logos, labels, or fabricated measurements. Housings and support details remain illustrative.

## Legacy media

The original `tools/render_demos.py`, `media/demo_*`, and `media/interactive_race.html` remain unchanged for provenance. Their older 24/28 mm fish model is not appropriate as a larval scale reference. They also include idealized phase-locked timing and a flat stimulus-floor representation. See [the historical demonstration notes](09-simulation-and-demos.md) for that separate model. The README uses the new media instead.
