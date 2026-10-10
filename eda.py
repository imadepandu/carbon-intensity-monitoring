"""Exploratory analysis of forecast errors: baseline, autocorrelation, hourly profile."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from data import CACHE_PATH, complete_events, get_data, quality_report

BASELINE_PATH = Path("results/baseline.json")
FIGURE_PATH = Path("results/eda.png")
LAGS = (1, 2, 4, 48)  # in 30-minute periods; 48 = same time on the previous day
MIN_REFERENCE_ROWS = 100
MAD_TO_STD = 1.4826  # scales the MAD to estimate the standard deviation of normal data

INK, DARK, MID, WARM, PAPER = "#111111", "#333333", "#777777", "#BEB8AD", "#F6F4EE"


@dataclass(frozen=True)
class Baseline:
    """Location and scale of the forecast error over the reference period."""

    n: int
    mean: float
    std: float
    median: float
    robust_std: float
    reference_start: str
    reference_end: str

    def save(self, path: Path = BASELINE_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = BASELINE_PATH) -> Baseline:
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def split_reference(frame: pd.DataFrame, reference_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split complete periods chronologically into reference and monitoring sets."""
    events = complete_events(frame)
    cutoff = events["timestamp"].min() + pd.Timedelta(days=reference_days)
    is_reference = events["timestamp"] < cutoff
    return events[is_reference].reset_index(drop=True), events[~is_reference].reset_index(drop=True)


def compute_baseline(reference: pd.DataFrame) -> Baseline:
    errors = reference["error"]
    median = errors.median()
    mad = (errors - median).abs().median()
    return Baseline(
        n=len(errors),
        mean=float(errors.mean()),
        std=float(errors.std()),
        median=float(median),
        robust_std=float(MAD_TO_STD * mad),
        reference_start=str(reference["timestamp"].min()),
        reference_end=str(reference["timestamp"].max()),
    )


def autocorrelation(errors: pd.Series, lags: Sequence[int] = LAGS) -> pd.Series:
    return pd.Series({lag: errors.autocorr(lag) for lag in lags}, name="autocorrelation")


def hourly_profile(frame: pd.DataFrame) -> pd.DataFrame:
    """Mean, standard deviation and count of the error by hour of day (UTC)."""
    grouped = frame.groupby(frame["timestamp"].dt.hour)["error"]
    return grouped.agg(["mean", "std", "count"]).rename_axis("hour_utc")


def _style(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_facecolor(PAPER)
    ax.set_title(title, loc="left", fontsize=9, color=INK, pad=12)
    ax.set_xlabel(xlabel, fontsize=8, color=DARK)
    ax.set_ylabel(ylabel, fontsize=8, color=DARK)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=DARK, labelsize=8, width=0.6)


def plot_summary(
    reference: pd.DataFrame,
    monitoring: pd.DataFrame,
    baseline: Baseline,
    hourly: pd.DataFrame,
    path: Path = FIGURE_PATH,
) -> None:
    fig = Figure(figsize=(14, 9), facecolor=PAPER, layout="constrained")
    (series_ax, hist_ax), (acf_ax, hour_ax) = fig.subplots(2, 2)

    series_ax.plot(reference["timestamp"], reference["error"], lw=0.7, color=MID, label="Referensi")
    series_ax.plot(
        monitoring["timestamp"], monitoring["error"], lw=0.7, color=INK, label="Pantauan"
    )
    series_ax.axhline(baseline.mean, color=DARK, lw=0.9)
    for sign in (-1, 1):
        series_ax.axhline(
            baseline.mean + sign * 3 * baseline.robust_std, color=MID, ls="--", lw=0.8
        )
    _style(series_ax, "GALAT (ACTUAL - FORECAST)", ylabel="gCO2/kWh")
    series_ax.tick_params(axis="x", rotation=30)
    series_ax.legend(fontsize=8, frameon=False, labelcolor=DARK)

    hist_ax.hist(reference["error"], bins=40, color=WARM, edgecolor=PAPER)
    hist_ax.axvline(baseline.mean, color=INK, lw=0.9)
    _style(hist_ax, "SEBARAN GALAT (REFERENSI)", "galat", "jumlah periode")

    lags = range(1, 49)
    acf_ax.bar(lags, [reference["error"].autocorr(lag) for lag in lags], color=DARK, width=0.7)
    bound = 1.96 / np.sqrt(len(reference))
    for sign in (-1, 1):
        acf_ax.axhline(sign * bound, color=MID, ls="--", lw=0.8)
    _style(acf_ax, "AUTOKORELASI GALAT", "lag (periode 30 menit)", "korelasi")

    hour_ax.bar(hourly.index, hourly["std"], color=DARK, width=0.7)
    _style(hour_ax, "SIMPANGAN BAKU GALAT PER JAM", "jam (UTC)", "std")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=PAPER)


def format_report(
    quality: dict[str, object],
    reference: pd.DataFrame,
    monitoring: pd.DataFrame,
    baseline: Baseline,
    acf: pd.Series,
    hourly: pd.DataFrame,
) -> str:
    quality_lines = "\n".join(f"  {key}: {value}" for key, value in quality.items())
    baseline_lines = "\n".join(f"  {key}: {value}" for key, value in asdict(baseline).items())
    summary = reference["error"].describe(percentiles=[0.05, 0.5, 0.95])
    return (
        f"Data quality\n{quality_lines}\n\n"
        f"Reference: {len(reference)} periods | Monitoring: {len(monitoring)} periods\n\n"
        f"Error summary (reference)\n{summary.round(2).to_string()}\n\n"
        f"Baseline\n{baseline_lines}\n\n"
        f"Autocorrelation (significance bound +/-{1.96 / np.sqrt(len(reference)):.3f})\n"
        f"{acf.round(3).rename_axis('lag').to_string()}\n\n"
        f"Hourly profile (UTC)\n{hourly.round(2).to_string()}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Exploratory analysis of forecast errors.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reference-days", type=int, default=21)
    parser.add_argument("--mode", choices=("auto", "live", "csv"), default="auto")
    parser.add_argument("--path", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)

    dataset = get_data(args.days, args.mode, args.path)
    reference, monitoring = split_reference(dataset.frame, args.reference_days)
    if len(reference) < MIN_REFERENCE_ROWS or monitoring.empty:
        raise SystemExit("Not enough data: increase --days or decrease --reference-days.")

    baseline = compute_baseline(reference)
    acf = autocorrelation(reference["error"])
    hourly = hourly_profile(reference)

    baseline.save()
    plot_summary(reference, monitoring, baseline, hourly)
    quality = quality_report(dataset.frame)
    print(format_report(quality, reference, monitoring, baseline, acf, hourly))


if __name__ == "__main__":
    main()