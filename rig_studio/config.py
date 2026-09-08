"""Frozen config dataclasses + YAML loader. All rig tunables live in configs/rig.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REGION_PRESETS = ("bottomhalf", "tophalf", "full", "custom")


@dataclass(frozen=True, slots=True)
class Roi:
    width: int
    height: int
    offset_x: int
    offset_y: int

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class CameraEntry:
    position: int
    serial: str
    label: str
    roi: Roi
    output_root: str
    video_mode: str = "Mode0"  # GS3 enum: Mode0 | Mode1 (2x2 bin) | Mode2 (vert bin 2)
    preview_rotation: int = 0  # display only; raw recording orientation is unchanged


@dataclass(frozen=True, slots=True)
class CameraDefaults:
    pixel_format: str
    binning_horizontal: int
    binning_vertical: int
    exposure_auto: str
    exposure_mode: str
    exposure_time_us: float
    gain_auto: str
    gain_db: float
    gamma_enabled: bool
    trigger_selector: str
    trigger_source: str
    trigger_activation: str
    trigger_overlap: str
    line_debouncer_raw: int
    stream_buffer_count_mode: str
    stream_buffer_count_max: int
    stream_handling_mode: str
    device_link_throughput_limit: int
    chunk_selectors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Quirks:
    gamma_canary_serial: str
    min_fetch_timeout_s: float
    trigger_selectors_both: tuple[str, ...]  # arm/disarm BOTH (FrameStart, ExposureActive)


@dataclass(frozen=True, slots=True)
class Compression:
    enabled: bool
    zstd_level: int


@dataclass(frozen=True, slots=True)
class RecordingCfg:
    fps_nominal: float
    max_frames_overhead: int
    chunk_frames: int
    compression: Compression
    gate_delay_s: float
    preroll_s: float
    session_tag_format: str
    filename_template: str
    pool_seconds: float
    queue_cap: int
    drop_log_every: int
    default_positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TriggerCfg:
    ownership: str  # app | external
    sdk_dir: str
    fps: float
    duty: float
    channels_by_position: tuple[int, ...]
    waveforms_exe: str = r"C:\Program Files (x86)\Digilent\WaveForms3\WaveForms.exe"


@dataclass(frozen=True, slots=True)
class ProjectorCfg:
    width: int
    height: int
    refresh_hz: float
    name_hint: str


@dataclass(frozen=True, slots=True)
class GratingCfg:
    period_px: float
    speed_px_s: float
    direction: int
    contrast: float
    rotation_deg: float
    smoothstep_edges: tuple[float, float]
    region_preset: str
    custom_rect: tuple[int, int, int, int] | None
    color_bright: str = "#ffffff"  # bar colors: luminance ramps dark -> bright
    color_dark: str = "#000000"
    bg_color: str = "#000000"      # screen outside the stimulus region


@dataclass(frozen=True, slots=True)
class CirclesCfg:
    enabled: bool
    ass_path: str
    cycle_s: float
    expand_row: str  # 'top' | 'bottom' wall row enlarges; the other contracts
    soft_px: float   # edge transition width; ~5 matches the grating bar edges
    pulse: bool = True        # False = shapes stay static at placed size
    style: str = "fill"       # 'fill' = opaque shapes over the grating;
                              # 'window' = grating shows only inside shapes
    # per-wall circle controls (rectangles in the layout never resize):
    size_top: float = 1.0     # size multiplier, rim-side edge pinned
    size_bottom: float = 1.0
    nth_top: int = 1          # keep every Nth circle (2 = alternate ones)
    nth_bottom: int = 1
    colors_from_ass: bool = True  # False = the two wall colors below apply
    color_top: str = "#000000"
    color_bottom: str = "#000000"
    window_bg: str = "#636363"  # flat color around windows (style=window)


@dataclass(frozen=True, slots=True)
class StimulusCfg:
    projector: ProjectorCfg
    grating: GratingCfg
    circles: CirclesCfg | None
    baseline_s: float
    grating_s: float
    log_format: str
    profiles_path: str  # named stimulus profiles YAML, next to rig.yaml


@dataclass(frozen=True, slots=True)
class SafetyCfg:
    snapshot_dir: str
    marker_path: str
    exposure_tolerance_us: float


@dataclass(frozen=True, slots=True)
class GuiCfg:
    preview_hz: float
    preview_decimate: int
    grid_rows: int
    grid_cols: int
    strip_top_order: tuple[int, ...] = (0, 1, 2, 3)  # readable left-to-right
    default_use_positions: tuple[int, ...] = ()
    default_view_positions: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RigConfig:
    name: str
    gentl_cti: str
    backend: str  # spinnaker | harvesters | sim
    apply_profile_on_connect: bool = False  # False = adopt SpinView state as-is
    frame_metadata_nodes: dict[str, str] = field(default_factory=dict)
    cameras: tuple[CameraEntry, ...] = ()
    defaults: CameraDefaults | None = None
    quirks: Quirks | None = None
    recording: RecordingCfg | None = None
    trigger: TriggerCfg | None = None
    stimulus: StimulusCfg | None = None
    safety: SafetyCfg | None = None
    gui: GuiCfg | None = None

    def by_position(self, position: int) -> CameraEntry:
        for cam in self.cameras:
            if cam.position == position:
                return cam
        raise KeyError(f"no camera at position {position}")

    def by_serial(self, serial: str) -> CameraEntry:
        for cam in self.cameras:
            if cam.serial == str(serial):
                return cam
        raise KeyError(f"no camera with serial {serial}")

    @property
    def serials(self) -> list[str]:
        return [cam.serial for cam in sorted(self.cameras, key=lambda c: c.position)]


def _cameras(raw: list[dict[str, Any]]) -> tuple[CameraEntry, ...]:
    cams = tuple(
        CameraEntry(
            position=int(row["position"]),
            serial=str(row["serial"]),
            label=str(row["label"]),
            roi=Roi(**{k: int(v) for k, v in row["roi"].items()}),
            output_root=str(row["output_root"]),
            video_mode=str(row.get("video_mode", "Mode0")),
            preview_rotation=int(row.get("preview_rotation", 0)),
        )
        for row in raw
    )
    positions = [cam.position for cam in cams]
    serials = [cam.serial for cam in cams]
    if sorted(positions) != list(range(len(cams))):
        raise ValueError(f"camera positions must be 0..{len(cams) - 1}, got {sorted(positions)}")
    if len(set(serials)) != len(serials):
        raise ValueError("duplicate camera serials in config")
    invalid_rotations = {
        cam.position: cam.preview_rotation
        for cam in cams
        if cam.preview_rotation not in (0, 90, 180, 270)
    }
    if invalid_rotations:
        raise ValueError(
            "camera preview_rotation must be one of 0, 90, 180, 270; "
            f"got {invalid_rotations}"
        )
    return cams


def _position_selection(
    raw: Any,
    *,
    name: str,
    available: tuple[int, ...],
    default: tuple[int, ...],
    allow_empty: bool = False,
) -> tuple[int, ...]:
    selected = tuple(int(position) for position in (default if raw is None else raw))
    if len(set(selected)) != len(selected):
        raise ValueError(f"{name} contains duplicate camera positions")
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError(f"{name} contains unknown camera positions: {unknown}")
    if not selected and not allow_empty:
        raise ValueError(f"{name} must contain at least one camera position")
    return selected


def load_config(path: str | Path) -> RigConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rig = data["rig"]
    cams = data["cameras"]
    d = cams["defaults"]
    quirks = cams["quirks"]
    rec = data["recording"]
    gui = data["gui"]
    trig = data["trigger"]
    stim = data["stimulus"]
    grating = stim["grating"]
    region = grating["region"]
    edges = tuple(float(v) for v in grating["smoothstep_edges"])
    if len(edges) != 2:
        raise ValueError("smoothstep_edges must have exactly 2 values")
    direction = int(grating["direction"])
    if direction not in (1, -1):
        raise ValueError("grating direction must be +1 or -1")
    preset = str(region["preset"])
    if preset not in REGION_PRESETS:
        raise ValueError(f"region preset must be one of {REGION_PRESETS}")
    ownership = str(trig["ownership"])
    if ownership not in ("app", "external"):
        raise ValueError("trigger ownership must be 'app' or 'external'")
    channels = tuple(int(c) for c in trig["channels_by_position"])
    custom_rect = region.get("custom_rect")
    circ = stim.get("circles")
    if circ:
        expand_row = str(circ.get("expand_row", "top"))
        if expand_row not in ("top", "bottom"):
            raise ValueError("circles expand_row must be 'top' or 'bottom'")
        circles_cfg = CirclesCfg(
            enabled=bool(circ.get("enabled", False)),
            # relative paths resolve against the yaml's own folder
            ass_path=str((Path(path).parent / str(circ["ass_path"])).resolve()),
            cycle_s=float(circ.get("cycle_s", 2.0)),
            expand_row=expand_row,
            soft_px=float(circ.get("soft_px", 5.0)),
            pulse=bool(circ.get("pulse", True)),
            style=str(circ.get("style", "fill")),
            size_top=float(circ.get("size_top", circ.get("size_factor", 1.0))),
            size_bottom=float(circ.get("size_bottom",
                                       circ.get("size_factor", 1.0))),
            nth_top=int(circ.get("nth_top", 1)),
            nth_bottom=int(circ.get("nth_bottom", 1)),
            colors_from_ass=bool(circ.get("colors_from_ass", True)),
            color_top=str(circ.get("color_top", "#000000")),
            color_bottom=str(circ.get("color_bottom", "#000000")),
            window_bg=str(circ.get("window_bg", "#636363")),
        )
        if circles_cfg.cycle_s <= 0:
            raise ValueError("circles cycle_s must be positive")
        if circles_cfg.size_top <= 0 or circles_cfg.size_bottom <= 0:
            raise ValueError("circles sizes must be positive")
        if circles_cfg.style not in ("fill", "window"):
            raise ValueError("circles style must be 'fill' or 'window'")
    else:
        circles_cfg = None
    camera_entries = _cameras(cams["positions"])
    all_positions = tuple(cam.position for cam in camera_entries)
    use_positions = _position_selection(
        gui.get("default_use_positions"), name="gui.default_use_positions",
        available=all_positions, default=all_positions,
    )
    view_positions = _position_selection(
        gui.get("default_view_positions"), name="gui.default_view_positions",
        available=all_positions, default=use_positions, allow_empty=True,
    )
    record_positions = _position_selection(
        rec.get("default_positions"), name="recording.default_positions",
        available=all_positions, default=use_positions,
    )
    if not set(record_positions) <= set(use_positions):
        raise ValueError("recording.default_positions must be a subset of "
                         "gui.default_use_positions")
    if not set(view_positions) <= set(use_positions):
        raise ValueError("gui.default_view_positions must be a subset of "
                         "gui.default_use_positions")
    return RigConfig(
        name=str(rig["name"]),
        gentl_cti=str(rig["gentl_cti"]),
        backend=str(rig["backend"]),
        apply_profile_on_connect=bool(cams.get("apply_profile_on_connect", False)),
        frame_metadata_nodes=dict(data["backend"]["frame_metadata_nodes"]),
        cameras=camera_entries,
        defaults=CameraDefaults(
            pixel_format=str(d["pixel_format"]),
            binning_horizontal=int(d["binning"]["horizontal"]),
            binning_vertical=int(d["binning"]["vertical"]),
            exposure_auto=str(d["exposure"]["auto"]),
            exposure_mode=str(d["exposure"]["mode"]),
            exposure_time_us=float(d["exposure"]["time_us"]),
            gain_auto=str(d["gain"]["auto"]),
            gain_db=float(d["gain"]["db"]),
            gamma_enabled=bool(d["gamma_enabled"]),
            trigger_selector=str(d["trigger"]["selector"]),
            trigger_source=str(d["trigger"]["source"]),
            trigger_activation=str(d["trigger"]["activation"]),
            trigger_overlap=str(d["trigger"]["overlap"]),
            line_debouncer_raw=int(d["trigger"].get("line_debouncer_raw", 1000)),
            stream_buffer_count_mode=str(d["stream"]["buffer_count_mode"]),
            stream_buffer_count_max=int(d["stream"]["buffer_count_max"]),
            stream_handling_mode=str(d["stream"]["handling_mode"]),
            device_link_throughput_limit=int(d["device_link_throughput_limit"]),
            chunk_selectors=tuple(str(s) for s in d["chunk_selectors"]),
        ),
        quirks=Quirks(
            gamma_canary_serial=str(quirks["gamma_canary_serial"]),
            min_fetch_timeout_s=float(quirks["min_fetch_timeout_s"]),
            trigger_selectors_both=tuple(str(s) for s in quirks["trigger_selectors_both"]),
        ),
        recording=RecordingCfg(
            fps_nominal=float(rec["fps_nominal"]),
            max_frames_overhead=int(rec["max_frames_overhead"]),
            chunk_frames=int(rec["chunk_frames"]),
            compression=Compression(
                enabled=bool(rec["compression"]["enabled"]),
                zstd_level=int(rec["compression"]["zstd_level"]),
            ),
            gate_delay_s=float(rec["gate_delay_s"]),
            preroll_s=float(rec["preroll_s"]),
            session_tag_format=str(rec["session_tag_format"]),
            filename_template=str(rec["filename_template"]),
            pool_seconds=float(rec["pool_seconds"]),
            queue_cap=int(rec["queue_cap"]),
            drop_log_every=int(rec["drop_log_every"]),
            default_positions=record_positions,
        ),
        trigger=TriggerCfg(
            ownership=ownership,
            sdk_dir=str(trig["sdk_dir"]),
            fps=float(trig["fps"]),
            duty=float(trig["duty"]),
            channels_by_position=channels,
            waveforms_exe=str(trig.get(
                "waveforms_exe",
                r"C:\Program Files (x86)\Digilent\WaveForms3\WaveForms.exe",
            )),
        ),
        stimulus=StimulusCfg(
            projector=ProjectorCfg(
                width=int(stim["projector"]["width"]),
                height=int(stim["projector"]["height"]),
                refresh_hz=float(stim["projector"]["refresh_hz"]),
                name_hint=str(stim["projector"]["name_hint"]),
            ),
            grating=GratingCfg(
                period_px=float(grating["period_px"]),
                speed_px_s=float(grating["speed_px_s"]),
                direction=direction,
                contrast=float(grating["contrast"]),
                rotation_deg=float(grating["rotation_deg"]),
                smoothstep_edges=(edges[0], edges[1]),
                region_preset=preset,
                custom_rect=tuple(int(v) for v in custom_rect) if custom_rect else None,
                color_bright=str(grating.get("color_bright", "#ffffff")),
                color_dark=str(grating.get("color_dark", "#000000")),
                bg_color=str(grating.get("bg_color", "#000000")),
            ),
            circles=circles_cfg,
            baseline_s=float(stim["baseline_s"]),
            grating_s=float(stim["grating_s"]),
            log_format=str(stim["log_format"]),
            profiles_path=str((Path(path).parent /
                               "stimulus_profiles.yaml").resolve()),
        ),
        safety=SafetyCfg(
            snapshot_dir=str(data["safety"]["snapshot_dir"]),
            marker_path=str(data["safety"]["marker_path"]),
            exposure_tolerance_us=float(data["safety"]["exposure_tolerance_us"]),
        ),
        gui=GuiCfg(
            preview_hz=float(gui["preview_hz"]),
            preview_decimate=int(gui["preview_decimate"]),
            grid_rows=int(gui["grid"]["rows"]),
            grid_cols=int(gui["grid"]["cols"]),
            strip_top_order=tuple(int(p) for p in
                                  gui.get("strip_top_order", (0, 1, 2, 3))),
            default_use_positions=use_positions,
            default_view_positions=view_positions,
        ),
    )
