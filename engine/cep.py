"""Complex event processing on top of the EWMA chart: repetition, trend and absence patterns."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from engine.data import CACHE_PATH, get_data, replay
from engine.eda import MIN_REFERENCE_ROWS, split_reference
from engine.ewma import EWMA, build_detector

SUMMARY_PATH = Path("results/cep_summary.csv")
REPEAT = 3  # consecutive EWMA alarms that make a repetition (1.5 hours)
WINDOW = 4  # periods in the rolling mean of the error
STEPS = 3  # consecutive moves of the rolling mean that make a trend
LEVEL = 1.5  # minimum distance of the rolling mean from normal, in baseline scales
GAP = pd.Timedelta(minutes=90)  # silence longer than this is an absence
PERIOD = pd.Timedelta(minutes=30)


class Detection(NamedTuple):
    """EWMA signal merged with the pattern state for one period. `trend` is +1, -1 or 0."""

    timestamp: pd.Timestamp
    z: float
    lcl: float
    ucl: float
    alarm: bool
    streak: int
    repetition: bool
    trend: int
    absence: bool


class CEP:
    """Stateful pattern detector. `update` consumes one event, so batch and stream agree."""

    def __init__(
        self,
        detector: EWMA,
        repeat: int = REPEAT,
        window: int = WINDOW,
        steps: int = STEPS,
        level: float = LEVEL,
        gap: pd.Timedelta = GAP,
    ) -> None:
        self.detector = detector
        self.repeat, self.steps, self.level, self.gap = repeat, steps, level, gap
        self.scores: deque[float] = deque(maxlen=window)
        self.means: deque[float] = deque(maxlen=steps + 1)
        self.streak = 0
        self.last: pd.Timestamp | None = None

    def silent(self, now: pd.Timestamp) -> bool:
        """True if no event has arrived for longer than the gap (use with a live clock)."""
        return self.last is not None and now - self.last > self.gap

    def update(self, event: dict) -> Detection:
        signal = self.detector.update(event)
        absence = self.silent(event["timestamp"])
        if absence:  # the sequence is broken: patterns start over
            self.streak = 0
            self.scores.clear()
            self.means.clear()
        self.last = event["timestamp"]

        self.streak = self.streak + 1 if signal.alarm else 0
        self.scores.append(self.detector.norm.score(event))
        if len(self.scores) == self.scores.maxlen:
            self.means.append(float(np.mean(self.scores)))
        return Detection(*signal, self.streak, self.streak >= self.repeat, self._trend(), absence)

    def _trend(self) -> int:
        if len(self.means) < self.means.maxlen:
            return 0
        steps, latest = np.diff(self.means), self.means[-1]
        if (steps > 0).all() and latest >= self.level:
            return 1
        if (steps < 0).all() and latest <= -self.level:
            return -1
        return 0


def build_cep(reference: pd.DataFrame, **settings: Any) -> CEP:
    """CEP on the default EWMA detector, calibrated on the reference period."""
    return CEP(build_detector(reference), **settings)


def detect(monitor: CEP, frame: pd.DataFrame) -> pd.DataFrame:
    """Feed every complete period of `frame` to `monitor`, in order."""
    return pd.DataFrame(map(monitor.update, replay(frame)), columns=Detection._fields)


def count_events(flag: pd.Series) -> int:
    """Number of separate runs of True."""
    return int((flag & ~flag.shift(fill_value=False)).sum())


def summarize(detections: pd.DataFrame, split: pd.Timestamp) -> pd.DataFrame:
    """Events per pattern before (reference) and after (monitoring) `split`."""
    flags = {
        "ewma_alarm": detections["alarm"],
        "repetition": detections["repetition"],
        "trend_up": detections["trend"] > 0,
        "trend_down": detections["trend"] < 0,
        "absence": detections["absence"],
    }
    before = detections["timestamp"] < split
    rows = []
    for name, flag in flags.items():
        row = {"pattern": name}
        for label, mask in (("reference", before), ("monitoring", ~before)):
            days = mask.sum() * PERIOD / pd.Timedelta(days=1)
            events = count_events(flag[mask])
            row[f"{label}_events"], row[f"{label}_per_day"] = events, events / days
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Count pattern events over the dataset.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reference-days", type=int, default=21)
    parser.add_argument("--mode", choices=("auto", "live", "csv"), default="auto")
    parser.add_argument("--path", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)

    dataset = get_data(args.days, args.mode, args.path)
    reference, monitoring = split_reference(dataset.frame, args.reference_days)
    if len(reference) < MIN_REFERENCE_ROWS or monitoring.empty:
        raise SystemExit("Not enough data: increase --days or decrease --reference-days.")

    detections = detect(build_cep(reference), pd.concat([reference, monitoring]))
    summary = summarize(detections, monitoring["timestamp"].min())
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY_PATH, index=False)
    print(summary.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()