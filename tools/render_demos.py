"""Reproducible demo media for the README (media/demo_*).

    python tools/render_demos.py            # all outputs, ~2 min
    python tools/render_demos.py --quick    # fewer frames, for iterating

Everything geometric is REAL: the six camera matrices come from the live
calibration JSON, the grating parameters from configs/rig.yaml, the crops from
the recordings, and 3D points are reconstructed with the production
`multiview3d.triangulate3d.triangulate_views`. Only the FISH are synthetic:
a kinematic model (bout-and-glide vs. cruising, travelling-wave undulation,
optomotor following) drives two animals through the tank so the pipeline can be
shown end to end without publishing animal data. Every frame is watermarked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from multiview3d import triangulate3d as tri  # noqa: E402
from rig_studio.stimulus import grating as gr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
WATERMARK = ("SYNTHETIC fish trajectories  ·  REAL calibration, crops, "
             "stimulus parameters and triangulation code")

# ---- tank & stimulus (board frame: x along tank, y across, z DOWN) ----------
R_TROUGH, Z_CENTER, Y_CENTER = 25.0, -22.5, 10.0   # half-cylinder, surface at its axis
Z_FLOOR, Z_SURFACE = 0.0, -22.4
PX_PER_MM, PERIOD_PX, SPEED_PX_S = 7.858, 157.5, 78.6
PERIOD_MM, DRIFT_MM_S = PERIOD_PX / PX_PER_MM, SPEED_PX_S / PX_PER_MM   # 20.0 mm, 10.0 mm/s
FPS = 120
NODES = ["head", "body", "tail_base", "tail_end"]            # the real SLEAP skeleton
NODE_S = np.array([0.0, 0.33, 0.66, 1.0])                    # arc position along body
CROPS = {0: (1472, 1450), 1: (2048, 1450), 2: (2048, 1450), 3: (1472, 1450),
         4: (2048, 1408), 5: (2048, 1408)}                    # (W, H) from the recordings

SPECIES = {
    "zebrafish": dict(bl=28.0, lane_y=14.0, base_v=6.0, bout_rate=1.6, peak=(60, 180),
                      tau=0.12, tail_hz=24.0, amp=0.13, color="#1f77b4"),
    "medaka":    dict(bl=24.0, lane_y=6.0, base_v=8.0, bout_rate=1.0, peak=(25, 60),
                      tau=0.25, tail_hz=14.0, amp=0.10, color="#ff7f0e"),
}


def half_width(z: float) -> float:
    """Free lateral half-width of the trough at depth z (2 mm wall margin)."""
    return max(np.sqrt(max(R_TROUGH**2 - (z - Z_CENTER) ** 2, 0.0)) - 2.0, 1.0)


def midline(head, heading, bl, phase, activity, amp, s=NODE_S):
    """Body points at arc fractions s: travelling wave, amplitude grows toward
    the tail (∝ s^1.5) and with activity; wavelength = one body length."""
    d = np.array([np.cos(heading), np.sin(heading), 0.0])
    n = np.array([-np.sin(heading), np.cos(heading), 0.0])
    lateral = amp * bl * activity * s**1.5 * np.sin(phase - 2 * np.pi * s)
    return head[None, :] - np.outer(s * bl, d) + np.outer(lateral, n)


def simulate(seconds: float, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    F = int(seconds * FPS)
    dt = 1.0 / FPS
    out = {"t": np.arange(F) * dt, "truth": {}, "midline": {}, "speed": {}}
    for name, sp in SPECIES.items():
        pos = np.array([18.0, sp["lane_y"], -11.0])
        heading, v, phase = 0.0, sp["base_v"], 0.0
        boost, finished = 0.0, False
        truth = np.zeros((F, 4, 3)); mid = np.zeros((F, 13, 3)); speed = np.zeros(F)
        for i in range(F):
            if not finished and rng.random() < sp["bout_rate"] * dt:      # start a bout
                boost = rng.uniform(*sp["peak"])
            boost *= np.exp(-dt / sp["tau"])
            v_target = (0.0 if finished else sp["base_v"]) + boost
            v += (v_target - v) * min(1.0, dt / 0.05)
            # optomotor following (+x) with lane keeping and a little wander
            hw = half_width(pos[2])
            steer = (0.15 * (sp["lane_y"] - pos[1]) / hw
                     + rng.normal(0, 0.9) * dt)
            heading += (steer - heading) * min(1.0, dt / 0.25)
            heading = np.clip(heading, -0.6, 0.6)
            pos += v * dt * np.array([np.cos(heading), np.sin(heading), 0.0])
            pos[2] = np.clip(pos[2] + rng.normal(0, 4.0) * dt, -19.0, -3.5)
            pos[1] = np.clip(pos[1], Y_CENTER - hw, Y_CENTER + hw)
            if pos[0] > 176.0:
                finished, pos[0] = True, 176.0
            activity = float(np.clip(v / sp["peak"][1], 0.12, 1.0))
            phase += 2 * np.pi * sp["tail_hz"] * (0.4 + 0.6 * activity) * dt
            truth[i] = midline(pos, heading, sp["bl"], phase, activity, sp["amp"])
            mid[i] = midline(pos, heading, sp["bl"], phase, activity, sp["amp"],
                             s=np.linspace(0, 1, 13))
            speed[i] = v
        out["truth"][name], out["midline"][name], out["speed"][name] = truth, mid, speed
    return out


def observe(truth: dict, cameras: dict, rng, sigma_px=1.5, p_miss=0.04,
            p_outlier=0.03) -> dict:
    """SLEAP-like 2D detections per camera: pixel noise, misses, gross outliers."""
    det = {}
    for name, T in truth.items():
        F = T.shape[0]
        per_cam = {}
        for cam, c in cameras.items():
            W, H = CROPS[cam]
            X = np.concatenate([T, np.ones((F, 4, 1))], axis=2)      # homogeneous
            x = np.einsum("ij,fnj->fni", c["P"], X)
            px = x[..., :2] / x[..., 2:3]
            visible = (x[..., 2] > 0) & (px[..., 0] >= 0) & (px[..., 0] < W) \
                & (px[..., 1] >= 0) & (px[..., 1] < H)
            noisy = px + rng.normal(0, sigma_px, px.shape)
            outlier = rng.random((F, 4)) < p_outlier
            noisy[outlier] += rng.uniform(25, 70, (outlier.sum(), 2)) \
                * rng.choice([-1, 1], (outlier.sum(), 2))
            miss = rng.random((F, 4)) < p_miss
            noisy[~visible | miss] = np.nan
            per_cam[cam] = {"px": noisy, "outlier": outlier & visible & ~miss}
        det[name] = per_cam
    return det


def reconstruct(det: dict, cameras: dict) -> dict:
    rec = {}
    for name, per_cam in det.items():
        F = next(iter(per_cam.values()))["px"].shape[0]
        P = np.full((F, 4, 3), np.nan); err = np.full((F, 4), np.nan)
        used = np.zeros((F, 4), int); avail = np.zeros((F, 4), int)
        for f in range(F):
            for n in range(4):
                views = [(cam, per_cam[cam]["px"][f, n]) for cam in cameras
                         if np.all(np.isfinite(per_cam[cam]["px"][f, n]))]
                avail[f, n] = len(views)
                if len(views) >= 2:
                    X, e, k = tri.triangulate_views(views, cameras)
                    P[f, n], err[f, n], used[f, n] = X, e, k
        rec[name] = {"points": P, "reproj": err, "used": used, "avail": avail}
    return rec


# ---- drawing helpers --------------------------------------------------------
def _watermark(fig):
    fig.text(0.005, 0.005, WATERMARK, fontsize=7, color="0.45", ha="left", va="bottom")


def _trough(ax):
    th = np.linspace(np.pi, 2 * np.pi, 40)
    xs = np.linspace(-12, 193, 30)
    TH, XS = np.meshgrid(th, xs)
    Y = Y_CENTER + R_TROUGH * np.cos(TH)
    Z = Z_CENTER - R_TROUGH * np.sin(TH)          # z DOWN: bottom is positive
    ax.plot_wireframe(XS, Y, Z, color="0.75", lw=0.4, rstride=6, cstride=4, alpha=0.6)
    ax.plot([-12, 193, 193, -12, -12], [Y_CENTER - R_TROUGH] * 2 + [Y_CENTER + R_TROUGH] * 2
            + [Y_CENTER - R_TROUGH], [Z_SURFACE] * 5, color="c", lw=0.8, alpha=0.7)


def _cameras(ax, cameras):
    for cam, c in cameras.items():
        C = -c["R"].T @ c["t"].ravel()
        axis = c["R"].T @ np.array([0, 0, 1.0]) * 25
        ax.scatter(*C, s=28, c="tab:blue" if cam < 4 else "tab:orange", depthshade=False)
        ax.plot([C[0], C[0] + axis[0]], [C[1], C[1] + axis[1]], [C[2], C[2] + axis[2]],
                color="r", lw=0.8)
        ax.text(C[0], C[1], C[2] - 6, f"cam{cam}", fontsize=7)


def _bars(t):
    """Dark grating bars on the floor at time t (drift +x at 10 mm/s); the
    bright half of each period is the page background."""
    phase = (DRIFT_MM_S * t) % PERIOD_MM
    starts = np.arange(-PERIOD_MM, 190, PERIOD_MM) + phase + PERIOD_MM / 2
    return [(max(s, 0), min(s + PERIOD_MM / 2, 180)) for s in starts
            if s < 180 and s + PERIOD_MM / 2 > 0]


def _style_3d(ax, zoom=1.6):
    ax.set_xlim(-15, 195); ax.set_ylim(-16, 36); ax.set_zlim(6, -56)
    ax.set_box_aspect((210, 52, 62), zoom=zoom)
    ax.set_xlabel("x (mm)", labelpad=2); ax.set_ylabel("y", labelpad=2); ax.set_zlabel("z (down)", labelpad=2)
    ax.tick_params(labelsize=7)


# ---- 1. race in 3D ----------------------------------------------------------
def render_race_3d(sim, cameras, frames_idx, path, fps=20):
    fig = plt.figure(figsize=(11, 5.6))
    ax = fig.add_subplot(111, projection="3d")
    ax.view_init(elev=28, azim=-58)
    fig.subplots_adjust(left=0, right=1, bottom=0.02, top=0.9)
    _trough(ax); _cameras(ax, cameras); _style_3d(ax)
    ax.plot([176, 176], [0, 20], [Z_FLOOR, Z_FLOOR], "r--", lw=1.2)
    ax.text(176, 22, Z_FLOOR, "finish", color="r", fontsize=8)
    ax.plot([0, 180, 180, 0, 0], [0, 0, 20, 20, 0], [Z_FLOOR] * 5, color="0.5", lw=0.8)
    bar_art, fish_art, trail_art, node_art, label_art = [], {}, {}, {}, {}
    for name, sp in SPECIES.items():
        fish_art[name], = ax.plot([], [], [], color=sp["color"], lw=4.0, solid_capstyle="round")
        label_art[name] = ax.text(0, 0, 0, name, color=sp["color"], fontsize=8, weight="bold")
        trail_art[name], = ax.plot([], [], [], color=sp["color"], lw=0.8, alpha=0.5)
        node_art[name] = ax.scatter([], [], [], s=14, c="k", depthshade=False)
    title = ax.set_title("")
    _watermark(fig)
    fig.text(0.5, 0.95, "Optomotor race under the drifting grating (1.00 cm/s, 20 mm period)",
             ha="center", fontsize=11)
    t = sim["t"]

    def update(k):
        i = frames_idx[k]
        for a in bar_art:
            a.remove()
        bar_art.clear()
        quads = [[(x0, 0, Z_FLOOR), (x1, 0, Z_FLOOR), (x1, 20, Z_FLOOR), (x0, 20, Z_FLOOR)]
                 for x0, x1 in _bars(t[i])]
        coll = Poly3DCollection(quads, facecolor="k", edgecolor="none", alpha=0.8)
        ax.add_collection3d(coll); bar_art.append(coll)
        for name in SPECIES:
            m = sim["midline"][name][i]
            fish_art[name].set_data_3d(m[:, 0], m[:, 1], m[:, 2])
            lo = max(0, i - FPS)
            tr = sim["truth"][name][lo:i + 1, 0]
            trail_art[name].set_data_3d(tr[:, 0], tr[:, 1], tr[:, 2])
            n = sim["truth"][name][i]
            node_art[name]._offsets3d = (n[:, 0], n[:, 1], n[:, 2])
            label_art[name].set_position_3d((n[0, 0] + 2, n[0, 1] + 3, n[0, 2] - 4))
        z, md = sim["truth"]["zebrafish"][i, 0, 0], sim["truth"]["medaka"][i, 0, 0]
        title.set_text(f"t = {t[i]:5.2f} s   hw frame {310 + i:4d}   "
                       f"zebrafish x = {z:5.1f} mm   medaka x = {md:5.1f} mm   "
                       f"grating phase {((DRIFT_MM_S * t[i]) % PERIOD_MM) / PERIOD_MM * 360:5.0f}°")
        return []

    FuncAnimation(fig, update, frames=len(frames_idx), blit=False).save(
        path, writer=PillowWriter(fps=fps), dpi=85)
    plt.close(fig)


# ---- 2. six camera views ----------------------------------------------------
def render_six_views(sim, det, cameras, frames_idx, path, fps=20):
    order = [(0, 0, 3), (0, 1, 2), (0, 2, 1), (0, 3, 0), (1, 0, 5), (1, 3, 4)]  # row, col, cam
    fig = plt.figure(figsize=(12, 6.2))
    gs = fig.add_gridspec(2, 4, hspace=0.35, wspace=0.15)
    axes, arts = {}, {}
    for r, c, cam in order:
        ax = fig.add_subplot(gs[r, c]); axes[cam] = ax
        W, H = CROPS[cam]
        ax.set_facecolor("#0b0b0b"); ax.set_xlim(0, W); ax.set_ylim(H, 0)
        ax.set_xticks([]); ax.set_yticks([])
        arts[cam] = {}
        corners = np.array([[0, 0, 0, 1], [180, 0, 0, 1], [180, 20, 0, 1], [0, 20, 0, 1], [0, 0, 0, 1]], float)
        xc = (cameras[cam]["P"] @ corners.T).T
        if (xc[:, 2] > 0).all():
            ax.plot(xc[:, 0] / xc[:, 2], xc[:, 1] / xc[:, 2], color="0.35", lw=0.8, ls="--")
        for name, sp in SPECIES.items():
            arts[cam][name] = (ax.plot([], [], "-", color=sp["color"], lw=1.6)[0],
                               ax.plot([], [], "o", color=sp["color"], ms=3.5)[0],
                               ax.plot([], [], "x", color="r", ms=6, mew=1.5)[0])
        arts[cam]["title"] = ax.set_title("", fontsize=8)
    for r, c in ((1, 1), (1, 2)):
        ax = fig.add_subplot(gs[r, c]); ax.axis("off")
    fig.text(0.5, 0.965, "What each camera sees (near-IR: no projector, bright fish) — "
             "2D keypoints as a pose network would report them; red × = gross outlier",
             ha="center", fontsize=10)
    fig.text(0.375, 0.28, "tops: cam3 → cam0 left to right along the tank\n"
             "sides: cam5 (x≈−32) and cam4 (x≈211) look along the tank axis",
             ha="center", fontsize=8, color="0.3")
    _watermark(fig)

    def update(k):
        i = frames_idx[k]
        for cam in axes:
            W, H = CROPS[cam]
            arts[cam]["title"].set_text(f"cam{cam}  {W}×{H}   hw {310 + i}")
            for name in SPECIES:
                px = det[name][cam]["px"][i]; bad = det[name][cam]["outlier"][i]
                line, dots, xs = arts[cam][name]
                ok = np.isfinite(px[:, 0])
                line.set_data(px[ok & ~bad, 0], px[ok & ~bad, 1])
                dots.set_data(px[ok & ~bad, 0], px[ok & ~bad, 1])
                xs.set_data(px[ok & bad, 0], px[ok & bad, 1])
        return []

    FuncAnimation(fig, update, frames=len(frames_idx)).save(
        path, writer=PillowWriter(fps=fps), dpi=80)
    plt.close(fig)


# ---- 3. reconstruction dashboard --------------------------------------------
def render_reconstruction(sim, rec, cameras, frames_idx, path, fps=20):
    t = sim["t"]
    fig = plt.figure(figsize=(12, 7))
    gs = fig.add_gridspec(2, 3, height_ratios=(1.35, 1), hspace=0.3, wspace=0.28)
    ax3 = fig.add_subplot(gs[0, :2], projection="3d"); ax3.view_init(elev=30, azim=-62)
    _trough(ax3); _style_3d(ax3, zoom=1.45)
    ax3.plot([0, 180, 180, 0, 0], [0, 0, 20, 20, 0], [Z_FLOOR] * 5, color="0.5", lw=0.8)
    ax3.set_title("Reconstructed (●) vs simulated truth (—), tail 1 s", fontsize=9)
    axe = fig.add_subplot(gs[0, 2]); axe.set_title("3D error per node (mm)", fontsize=9)
    axr = fig.add_subplot(gs[1, :]); axr.set_title(
        "Race plot: head x(t) vs the grating drift (dashed, 10 mm/s)", fontsize=9)
    axr.set_xlabel("t (s)"); axr.set_ylabel("x (mm)"); axr.set_xlim(0, t[-1]); axr.set_ylim(0, 185)
    axr.plot(t, 18 + DRIFT_MM_S * t, "k--", lw=0.8)
    axe.set_xlim(0, t[-1]); axe.set_ylim(0, 3.0); axe.set_xlabel("t (s)"); axe.grid(alpha=.3)
    art = {}
    for name, sp in SPECIES.items():
        P = rec[name]["points"]; T = sim["truth"][name]
        err3d = np.linalg.norm(P - T, axis=2)                       # [F, 4]
        art[name] = dict(
            truth=ax3.plot([], [], [], color=sp["color"], lw=1.2, alpha=0.6)[0],
            rec=ax3.scatter([], [], [], s=16, c=sp["color"], depthshade=False),
            err=axe.plot([], [], color=sp["color"], lw=0.8, label=name)[0],
            race=axr.plot([], [], color=sp["color"], lw=2, label=name)[0],
            err3d=np.nanmedian(err3d, axis=1))
        rej = np.where((rec[name]["used"] < rec[name]["avail"]).any(axis=1))[0]
        art[name]["rej_t"] = t[rej]
    axe.legend(fontsize=7, loc="upper right"); axr.legend(fontsize=8, loc="upper left")
    rej_art = axe.plot([], [], "|", color="r", ms=6, mew=0.8, alpha=0.35)[0]
    rej_txt = axe.text(0.02, 0.92, "", transform=axe.transAxes, fontsize=7, color="r")
    stats = fig.text(0.5, 0.955, "", ha="center", fontsize=10)
    _watermark(fig)

    def update(k):
        i = frames_idx[k]
        lo = max(0, i - FPS)
        for name in SPECIES:
            T = sim["truth"][name]; P = rec[name]["points"]
            art[name]["truth"].set_data_3d(T[lo:i + 1, 0, 0], T[lo:i + 1, 0, 1], T[lo:i + 1, 0, 2])
            p = P[i]; ok = np.isfinite(p[:, 0])
            art[name]["rec"]._offsets3d = (p[ok, 0], p[ok, 1], p[ok, 2])
            art[name]["err"].set_data(t[:i + 1], art[name]["err3d"][:i + 1])
            art[name]["race"].set_data(t[:i + 1], T[:i + 1, 0, 0])
        allrej = np.concatenate([art[n]["rej_t"] for n in SPECIES])
        allrej = allrej[allrej <= t[i]]
        rej_art.set_data(allrej, np.full(len(allrej), 0.06))
        rej_txt.set_text(f"{len(allrej)} views rejected (rug)")
        z = art["zebrafish"]["err3d"][:i + 1]; m = art["medaka"]["err3d"][:i + 1]
        stats.set_text(f"t = {t[i]:5.2f} s   median 3D error so far: zebrafish "
                       f"{np.nanmedian(z):.2f} mm, medaka {np.nanmedian(m):.2f} mm   "
                       f"(2D noise σ = 1.5 px, 3 % gross outliers, 4 % misses)")
        return []

    FuncAnimation(fig, update, frames=len(frames_idx)).save(
        path, writer=PillowWriter(fps=fps), dpi=80)
    plt.close(fig)


# ---- 4. acquisition timing --------------------------------------------------
def render_timing(path, fps=20, n_frames=160):
    period = 1000 / FPS                      # ms
    exposure = 5.0
    proj = 1000 / 240
    starts = {0: 0, 1: -2, 2: 0, 3: 0, 4: 0, 5: -2}   # ragged start, in frames
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.set_ylim(-0.5, 9.5); ax.set_yticks(range(10))
    ax.set_yticklabels(["projector flips 240 Hz", "trigger 120 Hz"] + [f"cam{c} exposure 5 ms" for c in range(6)]
                       + ["HDF5 rows written", "grating phase"], fontsize=8)
    ax.set_xlabel("time (ms) from the write gate"); ax.grid(axis="x", alpha=.25)
    _watermark(fig)
    fig.text(0.5, 0.95, "Drivers: one clock, six cameras, one projector — slow motion ×50",
             ha="center", fontsize=11)
    dyn = []

    def update(k):
        for a in dyn:
            a.remove()
        dyn.clear()
        t0 = -25 + k * 0.5; t1 = t0 + 45
        ax.set_xlim(t0, t1)
        now = t1 - 10
        for n in range(-6, 12):
            te = n * period
            if te + period < t0 or te > t1:
                continue
            dyn.append(ax.plot([te, te, te + period / 2, te + period / 2], [1, 1.7, 1.7, 1], "k", lw=1.2)[0])
            for c in range(6):
                fid = n - starts[c]
                if fid < 0 or te > now or te < t0 + 3:
                    continue
                col = "tab:blue" if c < 4 else "tab:orange"
                dyn.append(ax.barh(2 + c, exposure, left=te, height=0.55, color=col, alpha=0.75))
                dyn.append(ax.text(te + exposure + 0.3, 2 + c, f"hw {310 + fid}", fontsize=6, va="center"))
        for m in range(-12, 30):
            tp = m * proj
            if t0 <= tp <= t1:
                dyn.append(ax.plot([tp, tp], [-0.3, 0.3], color="m", lw=1)[0])
        rows = max(0, int(now // period) + 1)
        dyn.append(ax.text(now, 8, f"{6 * rows} rows · {rows * 16.0:.0f} MB "
                           f"({16.0 * FPS / 1000:.2f} GB/s sustained)", fontsize=8, va="center", ha="right"))
        ph = ((DRIFT_MM_S * now / 1000) % PERIOD_MM) / PERIOD_MM * 360
        dyn.append(ax.text(now, 9, f"φ = {ph:6.2f}° (dt-corrected per flip)", fontsize=8, va="center", ha="right"))
        dyn.append(ax.axvline(now, color="0.3", lw=0.8, ls=":"))
        return []

    FuncAnimation(fig, update, frames=n_frames).save(path, writer=PillowWriter(fps=fps), dpi=85)
    plt.close(fig)


# ---- 5. DLP beat figure (static) -------------------------------------------
def render_beat(path):
    def sampled_mean(f_proj, f_cam, exposure_s, seconds=2.0):
        # simple dither model: within each projector frame the lamp shows a 50 %
        # on/off micro-pattern; a camera integrates over its exposure window
        t = np.arange(0, seconds, 1 / f_cam)
        fine = np.linspace(0, exposure_s, 200)
        tt = t[:, None] + fine[None, :]
        frame_phase = (tt * f_proj) % 1.0
        return (frame_phase < 0.5).mean(axis=1), t
    fig, axes = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
    for ax, (fp, fc, ex, label) in zip(axes, [(60, 178, 0.0055, "60 Hz projector, 178 Hz capture, 5.5 ms exposure → |3·60−178| = 2 Hz beat"),
                                             (240, 120, 0.005, "240 Hz projector, 120 Hz capture (240/120 = 2, phase-locked) → flat")]):
        m, t = sampled_mean(fp, fc, ex)
        ax.plot(t, m, ".", ms=3.5, color="C0")
        ax.set_title(label, fontsize=9); ax.set_ylim(0.25, 0.75); ax.grid(alpha=.3)
        ax.set_ylabel("per-frame mean")
    axes[1].set_xlabel("t (s)")
    fig.suptitle("Why the recordings breathed: projector dither sampled by the camera (simplified 50 % dither model)", fontsize=10)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


# ---- 6. interactive viewer (self-contained HTML, no dependencies) ----------
def render_interactive(sim, cameras, path, step=4):
    idx = np.arange(0, len(sim["t"]), step)
    data = {
        "dt": step / FPS, "period_mm": PERIOD_MM, "drift": DRIFT_MM_S,
        "fish": {name: {"color": sp["color"],
                        "midline": np.round(sim["midline"][name][idx], 2).tolist()}
                 for name, sp in SPECIES.items()},
        "cameras": {str(c): {"C": np.round(-v["R"].T @ v["t"].ravel(), 1).tolist(),
                             "axis": np.round(v["R"].T @ np.array([0, 0, 1.0]), 3).tolist()}
                    for c, v in cameras.items()},
    }
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data))
    Path(path).write_text(html, encoding="utf-8")


HTML_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>rig-studio — interactive race (synthetic fish, real geometry)</title>
<style>body{margin:0;background:#111;color:#ddd;font:13px system-ui}#c{display:block}
#bar{position:fixed;left:0;right:0;bottom:0;padding:8px 12px;background:#1b1b1b;display:flex;gap:12px;align-items:center}
#bar input[type=range]{flex:1}#note{position:fixed;top:8px;left:12px;color:#999}</style></head>
<body><canvas id="c"></canvas>
<div id="note">drag = rotate · wheel = zoom · <b>synthetic fish</b>, real six-camera calibration, real stimulus (1 cm/s, 20 mm)</div>
<div id="bar"><button id="play">⏸</button><input id="t" type="range" min="0" value="0" step="1"><span id="lab"></span></div>
<script>
const D=__DATA__;const cv=document.getElementById('c'),ctx=cv.getContext('2d');
const N=D.fish.zebrafish.midline.length;const slider=document.getElementById('t');slider.max=N-1;
let yaw=-0.9,pitch=0.5,zoom=3.2,k=0,playing=true;
function resize(){cv.width=innerWidth;cv.height=innerHeight-44}resize();addEventListener('resize',resize);
function proj(p){ // board frame: x along, y across, z down -> screen
  const x=p[0]-90,y=p[1]-10,z=-p[2]-11;
  const cx=Math.cos(yaw),sx=Math.sin(yaw),cy=Math.cos(pitch),sy=Math.sin(pitch);
  let X=x*cx-y*sx,Y=x*sx+y*cx;let Z=z*cy-Y*sy;Y=z*sy+Y*cy;
  const f=1/(1+Y/600);return [cv.width/2+X*zoom*f,cv.height/2-Z*zoom*f];}
function line(pts,col,w){ctx.strokeStyle=col;ctx.lineWidth=w;ctx.beginPath();pts.forEach((p,i)=>{const s=proj(p);i?ctx.lineTo(s[0],s[1]):ctx.moveTo(s[0],s[1])});ctx.stroke();}
function draw(){ctx.fillStyle='#111';ctx.fillRect(0,0,cv.width,cv.height);const t=k*D.dt;
  // trough wireframe (half cylinder r=25, axis at z=-22.5, y=10)
  for(let a=0;a<=8;a++){const th=Math.PI+a*Math.PI/8;const y=10+25*Math.cos(th),z=-22.5-25*Math.sin(th);line([[-12,y,z],[193,y,z]],'#333',1);}
  for(let x=-12;x<=193;x+=41){const pts=[];for(let a=0;a<=16;a++){const th=Math.PI+a*Math.PI/16;pts.push([x,10+25*Math.cos(th),-22.5-25*Math.sin(th)]);}line(pts,'#333',1);}
  line([[-12,-15,-22.4],[193,-15,-22.4],[193,35,-22.4],[-12,35,-22.4],[-12,-15,-22.4]],'#2a7',1);
  // grating bars on the floor (board 0..180 x 0..20)
  const ph=(D.drift*t)%D.period_mm;ctx.fillStyle='rgba(255,255,255,0.85)';
  for(let s=-D.period_mm+ph;s<180;s+=D.period_mm){const a=Math.max(s,0),b=Math.min(s+D.period_mm/2,180);if(b<=a)continue;
    const q=[[a,0,0],[b,0,0],[b,20,0],[a,20,0]].map(proj);ctx.beginPath();q.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fill();}
  // cameras
  for(const [id,c] of Object.entries(D.cameras)){const s=proj(c.C);ctx.fillStyle=+id<4?'#4aa3ff':'#ffa040';ctx.beginPath();ctx.arc(s[0],s[1],5,0,7);ctx.fill();
    line([c.C,[c.C[0]+c.axis[0]*25,c.C[1]+c.axis[1]*25,c.C[2]+c.axis[2]*25]],'#f55',1);ctx.fillStyle='#ccc';ctx.fillText('cam'+id,s[0]+7,s[1]-6);}
  // fish
  for(const [name,f] of Object.entries(D.fish)){line(f.midline[k],f.color,4);const h=proj(f.midline[k][0]);ctx.fillStyle=f.color;ctx.fillText(name+'  x='+f.midline[k][0][0].toFixed(0)+' mm',h[0]+8,h[1]-8);}
  document.getElementById('lab').textContent='t = '+t.toFixed(2)+' s   grating phase '+((ph/D.period_mm)*360).toFixed(0)+'°';slider.value=k;}
let drag=null;cv.onmousedown=e=>drag=[e.clientX,e.clientY];cv.onmouseup=()=>drag=null;
cv.onmousemove=e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*0.008;pitch=Math.max(-1.4,Math.min(1.4,pitch+(e.clientY-drag[1])*0.008));drag=[e.clientX,e.clientY];draw();};
cv.onwheel=e=>{zoom*=e.deltaY<0?1.1:0.9;draw();e.preventDefault();};
slider.oninput=()=>{k=+slider.value;playing=false;document.getElementById('play').textContent='▶';draw();};
document.getElementById('play').onclick=function(){playing=!playing;this.textContent=playing?'⏸':'▶';};
setInterval(()=>{if(playing){k=(k+1)%N;draw();}},1000*D.dt);draw();
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seconds", type=float, default=12.0)
    args = ap.parse_args()
    MEDIA.mkdir(exist_ok=True)
    _, cameras = tri.load_calibration(None)
    rng = np.random.default_rng(1)
    sim = simulate(args.seconds)
    det = observe(sim["truth"], cameras, rng)
    rec = reconstruct(det, cameras)
    every = 12 if args.quick else 6                        # 120 Hz -> 10 or 20 fps GIF
    frames_idx = np.arange(0, len(sim["t"]), every)
    fps = 10 if args.quick else 20

    stats = {}
    for name in SPECIES:
        P, T = rec[name]["points"], sim["truth"][name]
        e = np.linalg.norm(P - T, axis=2)
        stats[name] = {
            "median_3d_error_mm": float(np.nanmedian(e)),
            "p95_3d_error_mm": float(np.nanpercentile(e, 95)),
            "coverage": float(np.isfinite(P[..., 0]).mean()),
            "views_rejected": int((rec[name]["avail"] - rec[name]["used"]).sum()),
            "node_frames_with_3plus_views": int((rec[name]["avail"] >= 3).sum()),
            "finish_time_s": float(sim["t"][np.argmax(T[:, 0, 0] >= 176.0)])
            if (T[:, 0, 0] >= 176.0).any() else None,
        }
    (MEDIA / "demo_stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))

    render_beat(MEDIA / "projector_beat.png")
    render_interactive(sim, cameras, MEDIA / "interactive_race.html")
    render_timing(MEDIA / "demo_acquisition_timing.gif", fps=fps,
                  n_frames=80 if args.quick else 160)
    render_race_3d(sim, cameras, frames_idx, MEDIA / "demo_race_3d.gif", fps=fps)
    render_six_views(sim, det, cameras, frames_idx, MEDIA / "demo_six_views.gif", fps=fps)
    render_reconstruction(sim, rec, cameras, frames_idx, MEDIA / "demo_reconstruction.gif", fps=fps)
    for p in sorted(MEDIA.glob("demo_*")) + [MEDIA / "interactive_race.html", MEDIA / "projector_beat.png"]:
        print(f"{p.name:36s} {p.stat().st_size / 1e6:6.2f} MB")


if __name__ == "__main__":
    main()
