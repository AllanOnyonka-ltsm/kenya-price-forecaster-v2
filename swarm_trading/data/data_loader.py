from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import logging

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
SAMPLE_DATA_DIR = PROJECT_ROOT / "data" / "sample"


class DataUnavailableError(RuntimeError):
    """No usable market data: no cache, and downloads were not possible or not allowed."""


def normalise_timestamps(values: pd.Series) -> pd.Series:
    """Parse timestamps that may carry mixed UTC offsets (e.g. -05:00 and -04:00 across DST).

    Converting everything to UTC first avoids the 'mixed timezones' error from
    pd.to_datetime. The result is naive midnight-UTC, one value per trading day.
    Yahoo stamps each bar at local midnight, which is still the same calendar day in UTC.
    """
    parsed = pd.to_datetime(values, utc=True)
    return parsed.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()


class DataLoader:
    def __init__(self, historical_dir: Path = DEFAULT_HISTORICAL_DIR):
        self.historical_dir = Path(historical_dir)
        self.failed_downloads: dict[str, str] = {}

    def save_historical_data(self, symbol: str, df: pd.DataFrame) -> Path:
        """Write one symbol's bars to the cache. The timestamp column keeps its original offsets."""
        self.historical_dir.mkdir(parents=True, exist_ok=True)
        path = self.historical_dir / f"{symbol}.csv"
        df.to_csv(path, index=False)
        return path

    def download_historical_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """Download from Yahoo Finance (yfinance). Failures are recorded, not hidden."""
        import yfinance as yf  # imported lazily so offline runs do not need network access

        logger.info(f"Downloading {len(symbols)} symbols from {start_date} to {end_date}")
        data: dict[str, pd.DataFrame] = {}
        self.failed_downloads = {}
        for symbol in symbols:
            try:
                raw = yf.Ticker(symbol).history(start=start_date, end=end_date, interval=interval)
            except Exception as exc:  # network or Yahoo error for this symbol only
                self.failed_downloads[symbol] = f"{type(exc).__name__}: {exc}"
                logger.error(f"Failed to download {symbol}: {exc}")
                continue
            if raw is None or raw.empty:
                self.failed_downloads[symbol] = "no rows returned"
                logger.warning(f"No data retrieved for {symbol}")
                continue
            df = raw.copy()
            df.index.name = "timestamp"
            df = df.reset_index()
            df["symbol"] = symbol
            self.save_historical_data(symbol, df)
            data[symbol] = self._prepare(df, symbol)
            logger.info(f"Downloaded {len(df)} rows for {symbol}")
        return data

    def load_historical_data(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """Read one symbol from the cache. Returns None if the file is absent.

        A file that exists but cannot be parsed raises, instead of being skipped.
        """
        path = self.historical_dir / f"{symbol}.csv"
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
            df = self._prepare(df, symbol)
        except (ValueError, KeyError) as exc:
            raise DataUnavailableError(f"cache file {path} is unreadable: {exc}") from exc
        return self._filter_dates(df, start, end)

    def load_all(
        self,
        symbols: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        """Load cached symbols. With symbols=None, load every CSV in the directory."""
        if symbols is None:
            names = sorted(p.stem for p in self.historical_dir.glob("*.csv")) if self.historical_dir.exists() else []
        else:
            names = list(symbols)
        data: dict[str, pd.DataFrame] = {}
        for symbol in names:
            df = self.load_historical_data(symbol, start, end)
            if df is not None and not df.empty:
                data[symbol] = df
        logger.info(f"Loaded cached data for {len(data)} symbols from {self.historical_dir}")
        return data

    @staticmethod
    def _prepare(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Return a new frame with normalised timestamps and a date column. Input is not changed."""
        out = df.copy()
        out["timestamp"] = normalise_timestamps(out["timestamp"])
        out = out.dropna(subset=["Close"])
        out["date"] = out["timestamp"].dt.date
        out["symbol"] = symbol
        return out.reset_index(drop=True)

    @staticmethod
    def _filter_dates(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
        mask = pd.Series(True, index=df.index)
        if start:
            mask &= df["timestamp"] >= pd.Timestamp(start)
        if end:
            mask &= df["timestamp"] < pd.Timestamp(end)  # end is exclusive, as in yfinance
        return df[mask].reset_index(drop=True)
