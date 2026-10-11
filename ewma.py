"""EWMA control chart on forecast errors: detector, limit calibration, parameter grid."""

from __future__ import annotations

import argparse
import copy
import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from data import CACHE_PATH, get_data, replay
from eda import MIN_REFERENCE_ROWS, Baseline, compute_baseline, split_reference

GRID_PATH = Path("results/ewma_grid.csv")
LAMBDAS = (0.1, 0.2, 0.3)
ALPHAS = (0.01, 0.02, 0.05)  # target share of reference periods outside the limits
SHIFTS = (1.0, 2.0)  # injected mean shifts, in units of the baseline scale
HORIZON = 96  # periods allowed to detect an injected shift (2 days)
STRIDE = 24  # periods between injection start points
WARMUP = 96  # periods before the first injection start point
DEFAULT_LAMBDA = 0.2
DEFAULT_ALPHA = 0.05

class Signal(NamedTuple):
    """Detector output for one period. `z` is in units of the baseline scale."""

    timestamp: pd.Timestamp
    z: float
    lcl: float
    ucl: float
    alarm: bool


@dataclass(frozen=True)
class Normalizer:
    """Centre and scale of the error per hour of day (UTC); a global baseline repeats one value."""

    center: dict[int, float]
    scale: dict[int, float]

    @classmethod
    def from_baselines(cls, by_hour: Mapping[int, Baseline]) -> Normalizer:
        if any(b.robust_std <= 0 for b in by_hour.values()):
            raise ValueError("robust_std must be positive for every hour")
        return cls(
            {int(hour): b.mean for hour, b in by_hour.items()},
            {int(hour): b.robust_std for hour, b in by_hour.items()},
        )

    @classmethod
    def global_(cls, baseline: Baseline) -> Normalizer:
        return cls.from_baselines(dict.fromkeys(range(24), baseline))

    @classmethod
    def hourly(cls, reference: pd.DataFrame) -> Normalizer:
        groups = reference.groupby(reference["timestamp"].dt.hour)
        return cls.from_baselines({hour: compute_baseline(g) for hour, g in groups})

    def score(self, event: dict) -> float:
        hour = event["timestamp"].hour
        return (event["error"] - self.center[hour]) / self.scale[hour]


class EWMA:
    """Two-sided EWMA chart. `update` consumes one event, so batch and stream agree."""

    def __init__(self, norm: Normalizer, lam: float, limit: float) -> None:
        if not 0 < lam <= 1:
            raise ValueError("lam must be in (0, 1]")
        self.norm, self.lam, self.limit = norm, lam, limit
        self.z, self.n = 0.0, 0

    def update(self, event: dict) -> Signal:
        self.n += 1
        self.z = self.lam * self.norm.score(event) + (1 - self.lam) * self.z
        # Standard deviation of z for independent errors with unit variance.
        spread = math.sqrt(self.lam / (2 - self.lam) * (1 - (1 - self.lam) ** (2 * self.n)))
        width = self.limit * spread
        return Signal(event["timestamp"], self.z, -width, width, abs(self.z) > width)


def run(detector: EWMA, frame: pd.DataFrame) -> pd.DataFrame:
    """Feed every complete period of `frame` to `detector`, in order."""
    return pd.DataFrame(map(detector.update, replay(frame)), columns=Signal._fields)


def calibrate_limit(norm: Normalizer, reference: pd.DataFrame, lam: float, alpha: float) -> float:
    """L at which about a share `alpha` of the reference periods raise an alarm."""
    signals = run(EWMA(norm, lam, limit=1.0), reference)  # limit=1 -> ucl is the spread itself
    return float((signals["z"].abs() / signals["ucl"]).quantile(1 - alpha))

def build_detector(
    reference: pd.DataFrame, lam: float = DEFAULT_LAMBDA, alpha: float = DEFAULT_ALPHA
) -> EWMA:
    """Global-baseline EWMA whose limit L is calibrated on the reference period."""
    norm = Normalizer.global_(compute_baseline(reference))
    return EWMA(norm, lam, calibrate_limit(norm, reference, lam, alpha))

def detection_delay(
    norm: Normalizer, frame: pd.DataFrame, lam: float, limit: float, shift: float
) -> float:
    """Median periods to alarm after adding `shift` (in baseline scales) to the error."""
    events = list(replay(frame))
    base, delays = EWMA(norm, lam, limit), []
    for i, event in enumerate(events):
        if WARMUP <= i <= len(events) - HORIZON and (i - WARMUP) % STRIDE == 0:
            probe, delay = copy.copy(base), HORIZON
            for k, later in enumerate(events[i : i + HORIZON], start=1):
                bump = shift * norm.scale[later["timestamp"].hour]
                if probe.update({**later, "error": later["error"] + bump}).alarm:
                    delay = k
                    break
            delays.append(delay)
        base.update(event)
    return float(np.median(delays))


def evaluate_grid(
    reference: pd.DataFrame,
    monitoring: pd.DataFrame,
    norms: Mapping[str, Normalizer],
    lams: Iterable[float] = LAMBDAS,
    alphas: Iterable[float] = ALPHAS,
) -> pd.DataFrame:
    rows = []
    for (name, norm), lam, alpha in itertools.product(norms.items(), lams, alphas):
        limit = calibrate_limit(norm, reference, lam, alpha)
        row = {"baseline": name, "lambda": lam, "alpha": alpha, "L": limit}
        row["monitoring_alarm"] = run(EWMA(norm, lam, limit), monitoring)["alarm"].mean()
        for shift in SHIFTS:
            row[f"delay_{shift:g}sd"] = detection_delay(norm, reference, lam, limit, shift)
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Calibrate and compare EWMA settings.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reference-days", type=int, default=21)
    parser.add_argument("--mode", choices=("auto", "live", "csv"), default="auto")
    parser.add_argument("--path", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)

    dataset = get_data(args.days, args.mode, args.path)
    reference, monitoring = split_reference(dataset.frame, args.reference_days)
    if len(reference) < MIN_REFERENCE_ROWS + WARMUP + HORIZON or monitoring.empty:
        raise SystemExit("Not enough data: increase --days or decrease --reference-days.")

    norms = {
        "global": Normalizer.global_(compute_baseline(reference)),
        "hourly": Normalizer.hourly(reference),
    }
    grid = evaluate_grid(reference, monitoring, norms)
    GRID_PATH.parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(GRID_PATH, index=False)
    print(grid.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()