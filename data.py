"""Ingestion of half-hourly GB carbon-intensity data (forecast vs. actual)."""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal, NamedTuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.carbonintensity.org.uk"
MAX_CHUNK = pd.Timedelta(days=13)  # the API accepts at most 14 days per request
PERIOD = pd.Timedelta(minutes=30)
REQUEST_PAUSE = 0.2  # seconds between requests
CACHE_PATH = Path("results/history.csv")
COLUMNS = ["timestamp", "period_end", "forecast", "actual", "error"]
_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"

Mode = Literal["auto", "live", "csv"]


class DataError(RuntimeError):
    """No usable data could be obtained."""


class ApiError(DataError):
    """The API could not be reached or returned an error."""


class Dataset(NamedTuple):
    frame: pd.DataFrame
    source: str  # "live", "csv" or "csv-fallback"


def _to_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def make_session(retries: int = 3) -> requests.Session:
    """HTTP session that retries on rate-limiting and server errors."""
    retry = Retry(
        total=retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["Accept"] = "application/json"
    return session


def _get_json(session: requests.Session, url: str, timeout: float = 20.0) -> dict:
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ApiError(f"Request failed: {url}") from exc


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute the error, drop duplicate periods (latest row wins) and sort."""
    frame = frame.assign(error=frame["actual"] - frame["forecast"])
    frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    return frame[COLUMNS].reset_index(drop=True)


def _concat(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return _normalize(pd.concat(frames, ignore_index=True))


def parse_records(records: list[dict]) -> pd.DataFrame:
    """Convert the API's JSON records into the dataset schema (see README)."""
    if not records:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.json_normalize(records).rename(
        columns={
            "from": "timestamp",
            "to": "period_end",
            "intensity.forecast": "forecast",
            "intensity.actual": "actual",
        }
    )
    frame = frame.reindex(columns=COLUMNS[:4])
    for column in ("timestamp", "period_end"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    for column in ("forecast", "actual"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return _normalize(frame)


def fetch_range(
    start: object, end: object, session: requests.Session | None = None
) -> pd.DataFrame:
    """Fetch an arbitrary time range, split into API-sized chunks."""
    start, end = _to_utc(start), _to_utc(end)
    session = session or make_session()
    frames = []
    cursor = start
    while cursor < end:
        upper = min(cursor + MAX_CHUNK, end)
        url = f"{BASE_URL}/intensity/{cursor.strftime(_TIME_FORMAT)}/{upper.strftime(_TIME_FORMAT)}"
        logger.info("GET %s", url)
        frames.append(parse_records(_get_json(session, url).get("data", [])))
        cursor = upper
        time.sleep(REQUEST_PAUSE)
    return _concat(frames)


def load_cache(path: Path = CACHE_PATH) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.read_csv(path)
    for column in ("timestamp", "period_end"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return _normalize(frame)


def save_cache(frame: pd.DataFrame, path: Path = CACHE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Combine two frames; rows from `new` replace rows from `old`."""
    return _concat([old, new])


def update_cache(
    days: int = 30,
    path: Path = CACHE_PATH,
    now: object = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Extend the cache to `now`, and back to `days` days ago if needed."""
    now = pd.Timestamp.now(tz="UTC") if now is None else _to_utc(now)
    wanted_start = now - pd.Timedelta(days=days)
    cache = load_cache(path)
    session = session or make_session()

    if cache.empty:
        windows = [(wanted_start, now)]
    else:
        first, last = cache["timestamp"].min(), cache["timestamp"].max()
        windows = [(wanted_start, first)] if wanted_start < first - PERIOD else []
        # Re-fetch the last day: recent actuals are published late and may be revised.
        windows.append((last - pd.Timedelta(days=1), now))

    for start, end in windows:
        cache = merge(cache, fetch_range(start, end, session))
    save_cache(cache, path)
    return cache


def get_data(days: int = 30, mode: Mode = "auto", path: Path = CACHE_PATH) -> Dataset:
    """Return the last `days` days of data.

    `live` requires the API, `csv` reads the cache only, and `auto` tries the API
    and falls back to the cache if it is unavailable.
    """
    if mode == "csv":
        frame, source = load_cache(path), "csv"
    else:
        try:
            frame, source = update_cache(days, path), "live"
        except ApiError:
            if mode == "live":
                raise
            logger.warning("API unavailable; falling back to cached data", exc_info=True)
            frame, source = load_cache(path), "csv-fallback"
    if frame.empty:
        raise DataError("No data available: the API failed and the cache is empty")
    cutoff = frame["timestamp"].max() - pd.Timedelta(days=days)
    return Dataset(frame[frame["timestamp"] >= cutoff].reset_index(drop=True), source)


def quality_report(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {"rows": 0}
    timestamps = pd.DatetimeIndex(frame["timestamp"])
    expected = pd.date_range(timestamps.min(), timestamps.max(), freq=PERIOD)
    return {
        "rows": len(frame),
        "start": timestamps.min(),
        "end": timestamps.max(),
        "missing_periods": len(expected.difference(timestamps)),
        "null_actual": int(frame["actual"].isna().sum()),
        "null_forecast": int(frame["forecast"].isna().sum()),
    }


def complete_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Periods for which both forecast and actual are available."""
    return frame.dropna(subset=["forecast", "actual"]).reset_index(drop=True)


def replay(frame: pd.DataFrame, delay: float = 0.0, start: int = 0) -> Iterator[dict]:
    """Yield complete periods one at a time, simulating a live stream."""
    for event in complete_events(frame).iloc[start:].to_dict("records"):
        yield event
        if delay:
            time.sleep(delay)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache carbon-intensity history.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--mode", choices=("auto", "live", "csv"), default="auto")
    parser.add_argument("--path", type=Path, default=CACHE_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )

    dataset = get_data(args.days, args.mode, args.path)
    print(f"source: {dataset.source}")
    for key, value in quality_report(dataset.frame).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()