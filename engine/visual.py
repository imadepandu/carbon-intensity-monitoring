"""Generate README visuals: event replay (ASSETS/demo.gif) and overview (ASSETS/hasil.png)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

from cep import PERIOD, build_cep, detect, summarize
from data import CACHE_PATH, complete_events, get_data
from eda import split_reference

ASSETS = Path("ASSETS")
INK, DARK, MID, SOFT, SOFTER = "#111111", "#333333", "#8a8a8a", "#e4e4e4", "#c9c9c9"
LANES = ("Absence", "Trend", "Repetition", "Alarm EWMA")  # bottom to top
BEFORE, AFTER = 12, 16  # periods shown around the longest alarm run
FRAME_MS, HOLD_MS = 140, 3200
DASH = (0, (4, 3))


def load(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Run the pipeline once; per-period results in gCO2/kWh plus the monitoring start."""
    data = get_data(args.days, args.mode, args.path).frame
    reference, monitoring = split_reference(data, args.reference_days)
    monitor, events = build_cep(reference), pd.concat([reference, monitoring])
    frame = detect(monitor, events).rename(columns={"z": "ewma"})
    frame["error"] = complete_events(events)["error"].to_numpy()
    center, scale = monitor.detector.norm.center[0], monitor.detector.norm.scale[0]
    for column in ("ewma", "lcl", "ucl"):
        frame[column] = center + scale * frame[column]
    return frame, monitoring["timestamp"].min()


def style(ax, ylabel: str = "") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MID)
        ax.spines[side].set_linewidth(0.7)
    ax.tick_params(colors=DARK, labelsize=8, width=0.6, length=3)
    ax.set_ylabel(ylabel, fontsize=8.5, color=DARK)
    ax.xaxis_date()


def lanes(ax, d: pd.DataFrame, xlim: tuple[float, float]) -> None:
    """One row per pattern, filled where the pattern is active."""
    x = mdates.date2num(d["timestamp"])
    width = mdates.date2num(PERIOD + pd.Timestamp(0)) - mdates.date2num(pd.Timestamp(0))
    for row, flag, color in (
        (3, d["alarm"], DARK),
        (2, d["repetition"], INK),
        (0, d["absence"], MID),
    ):
        ax.broken_barh(
            [(s, width) for s in x[flag.to_numpy()]], (row - 0.32, 0.64), facecolors=color
        )
    for sign, marker in ((1, "^"), (-1, "v")):
        mask = (d["trend"] == sign).to_numpy()
        ax.scatter(x[mask] + width / 2, np.ones(mask.sum()), marker=marker, s=16, color=INK, lw=0)
    if not d["absence"].any():
        ax.text(sum(xlim) / 2, 0, "tidak ada", fontsize=8, color=MID, ha="center", va="center")
    ax.set_xlim(*xlim)
    ax.set_ylim(-0.6, 3.6)
    ax.set_yticks(range(4), LANES)
    style(ax)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)


def heading(fig, left: float, right: float, title: str, line2: str, line3: str, extra=("", "")):
    fig.text(left, 0.945, title, fontsize=15, fontweight="bold", color=INK)
    fig.text(left, 0.905, line2, fontsize=9.5, color=DARK)
    fig.text(left, 0.868, line3, fontsize=8, color=MID)
    fig.text(right, 0.945, extra[0], fontsize=12, fontweight="bold", color=INK, ha="right")
    fig.text(right, 0.905, extra[1], fontsize=9, color=MID, ha="right")


def replay_frame(d: pd.DataFrame, upto: int, ylim, xlim, day: pd.Timestamp) -> Image.Image:
    fig = Figure(figsize=(9, 5.2), dpi=100)
    canvas = FigureCanvasAgg(fig)
    grid = fig.add_gridspec(2, 1, height_ratios=(3.2, 1.25), hspace=0.14, left=0.1, right=0.93)
    grid.update(top=0.80, bottom=0.09)
    top, bottom = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    seen, all_x = d.iloc[: upto + 1], mdates.date2num(d["timestamp"])
    x, step = all_x[: upto + 1], all_x[1] - all_x[0]

    for flag, color in ((seen["alarm"], SOFT), (seen["repetition"], SOFTER)):
        top.fill_between(x, *ylim, where=flag.to_numpy(), step="post", color=color, lw=0)
    for column, name in (("ucl", " UCL"), ("lcl", " LCL")):
        top.plot(all_x, d[column], color=MID, lw=0.8, ls=DASH)
        top.text(xlim[1], d[column].iloc[-1], name, fontsize=8, color=MID, va="center")
    top.scatter(x, seen["error"], s=14, color=MID, lw=0, zorder=3)
    top.plot(x, seen["ewma"], color=INK, lw=2.4, solid_capstyle="round", zorder=4)
    top.scatter(
        x[seen["alarm"].to_numpy()], seen.loc[seen["alarm"], "ewma"], s=26, color=INK, zorder=5
    )
    top.axvline(x[-1] + step / 2, color=INK, lw=0.7, alpha=0.5)
    for text, flag, side in (
        ("Alarm EWMA", seen["alarm"], -1),
        ("Repetition", seen["repetition"], 1),
    ):
        if flag.any():
            at = mdates.date2num(seen.loc[flag, "timestamp"].iloc[0]) + side * 0.4 * step
            y = ylim[1] - 0.05 * (ylim[1] - ylim[0])
            top.text(
                at,
                y,
                text,
                fontsize=9,
                fontweight="bold",
                va="top",
                ha="left" if side > 0 else "right",
            )
    top.set_xlim(*xlim)
    top.set_ylim(*ylim)
    style(top, "gCO2/kWh")
    top.tick_params(labelbottom=False)

    lanes(bottom, seen, xlim)
    bottom.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    bottom.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    bottom.set_xlabel("waktu (UTC)", fontsize=8.5, color=DARK)
    heading(
        fig,
        0.1,
        0.93,
        "Forecast Sentinel",
        f"Pemutaran ulang kejadian {day:%d %b %Y}, satu periode per langkah",
        "titik = galat mentah \u00b7 garis = EWMA (\u03bb 0,2) \u00b7 putus-putus = batas kendali",
        (f"{seen['timestamp'].iloc[-1]:%d %b  %H:%M} UTC", f"periode {upto + 1} dari {len(d)}"),
    )
    canvas.draw()
    return Image.fromarray(np.asarray(canvas.buffer_rgba())).convert("RGB")


def save_gif(frames: list[Image.Image], path: Path) -> None:
    palette = frames[-1].quantize(colors=48, dither=Image.Dither.NONE)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    durations = [FRAME_MS] * (len(frames) - 1) + [HOLD_MS]
    frames[0].save(
        path, save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2
    )


def overview(d: pd.DataFrame, split: pd.Timestamp, path: Path) -> None:
    fig = Figure(figsize=(12, 6.2), dpi=130)
    grid = fig.add_gridspec(2, 1, height_ratios=(3, 1.35), hspace=0.12, left=0.075, right=0.975)
    grid.update(top=0.84, bottom=0.08)
    top, bottom = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    x = mdates.date2num(d["timestamp"])
    xlim = (x.min(), x.max())
    ylim = (min(d["ewma"].min(), d["lcl"].min()) - 6, d["ewma"].max() + 10)
    cut = mdates.date2num(split)
    top.fill_between(x, d["lcl"], d["ucl"], color="#f1f1f1", lw=0)
    top.fill_between(x, *ylim, where=d["repetition"].to_numpy(), step="post", color=SOFTER, lw=0)
    top.plot(x, d["ewma"], color=INK, lw=1.1)
    top.scatter(x[d["alarm"].to_numpy()], d.loc[d["alarm"], "ewma"], s=9, color=INK, zorder=4)
    top.axvline(cut, color=MID, lw=0.8, ls=DASH)
    top.text(cut, ylim[1], " pantauan \u2192", fontsize=8.5, color=DARK, va="top")
    top.text(cut, ylim[1], "\u2190 referensi ", fontsize=8.5, color=DARK, va="top", ha="right")
    top.set_xlim(*xlim)
    top.set_ylim(*ylim)
    style(top, "EWMA galat (gCO2/kWh)")
    top.tick_params(labelbottom=False)
    lanes(bottom, d, xlim)
    bottom.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    bottom.xaxis.set_major_locator(mdates.DayLocator(interval=3))

    counts = summarize(d, split).set_index("pattern")
    names = (
        ("Alarm EWMA", "ewma_alarm"),
        ("Repetition", "repetition"),
        ("Trend naik", "trend_up"),
        ("Trend turun", "trend_down"),
        ("Absence", "absence"),
    )
    total = (counts["reference_events"] + counts["monitoring_events"]).astype(int)
    first, last = d["timestamp"].iloc[0], d["timestamp"].iloc[-1]
    rows = f"{len(d):,}".replace(",", ".")
    heading(
        fig,
        0.075,
        0.975,
        "Hasil pemantauan",
        f"{first:%d %b} \u2013 {last:%d %b %Y}  \u00b7  {rows} periode 30 menit",
        "abu muda = batas kendali \u00b7 blok abu = Repetition \u00b7 titik = alarm EWMA",
        ("   ".join(f"{label} {total[key]}" for label, key in names), ""),
    )
    FigureCanvasAgg(fig).print_png(path)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate README visuals from the cached data.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reference-days", type=int, default=21)
    parser.add_argument("--mode", choices=("auto", "live", "csv"), default="csv")
    parser.add_argument("--path", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)

    d, split = load(args)
    ASSETS.mkdir(exist_ok=True)
    overview(d, split, ASSETS / "hasil.png")

    run = (d["alarm"] != d["alarm"].shift()).cumsum()
    longest = d[d["alarm"]].groupby(run).size().idxmax()
    members = d.index[(run == longest) & d["alarm"]]
    part = d.iloc[max(members.min() - BEFORE, 0) : members.max() + AFTER + 1].reset_index(drop=True)
    pad = (part["error"].max() - part["error"].min()) * 0.08
    ylim = (min(part["error"].min(), part["lcl"].min()) - pad, part["error"].max() + 3 * pad)
    xlim = (
        mdates.date2num(part["timestamp"].iloc[0]),
        mdates.date2num(part["timestamp"].iloc[-1] + PERIOD),
    )
    day = part.loc[part["alarm"], "timestamp"].iloc[0]
    save_gif([replay_frame(part, i, ylim, xlim, day) for i in range(len(part))], ASSETS / "demo.gif")
    print(f"wrote {ASSETS / 'hasil.png'} and {ASSETS / 'demo.gif'}")


if __name__ == "__main__":
    main()