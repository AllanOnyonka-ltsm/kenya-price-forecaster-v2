import tempfile
import unittest
from pathlib import Path

import pandas as pd

from support import ROOT  # noqa: F401
from data.data_loader import DataLoader, DataUnavailableError, normalise_timestamps
from src.main import load_market_data


def mixed_offset_frame():
    """Daily bars across the 2023 DST change, so offsets are -05:00 and then -04:00."""
    days = pd.date_range("2023-03-08", "2023-03-16", freq="D")
    days = days[days.day_of_week < 5]
    stamps = days.tz_localize("America/New_York")
    closes = [100.0 + i for i in range(len(stamps))]
    return pd.DataFrame({
        "timestamp": stamps,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1000 + i for i in range(len(stamps))],
    }), [d.strftime("%Y-%m-%d") for d in days]


class CacheRoundTripTests(unittest.TestCase):
    def test_mixed_offsets_parse_and_round_trip(self):
        frame, expected_days = mixed_offset_frame()
        with tempfile.TemporaryDirectory() as tmp:
            loader = DataLoader(historical_dir=Path(tmp))
            loader.save_historical_data("AAA", frame)
            loaded = loader.load_historical_data("AAA")

        self.assertEqual([str(d) for d in loaded["date"]], expected_days)
        self.assertEqual(loaded["timestamp"].dt.tz, None)
        self.assertEqual(len(loaded), len(frame))
        for got, want in zip(loaded["Close"], frame["Close"]):
            self.assertAlmostEqual(got, want)

    def test_normalise_handles_mixed_offsets_without_error(self):
        values = pd.Series(["2023-03-10 00:00:00-05:00", "2023-03-13 00:00:00-04:00"])
        out = normalise_timestamps(values)
        self.assertEqual([d.date().isoformat() for d in out],
                         ["2023-03-10", "2023-03-13"])

    def test_offline_with_no_cache_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DataUnavailableError) as ctx:
                load_market_data(["AAA"], "2023-01-01", "2023-06-01", Path(tmp), offline=True)
        self.assertIn("Offline mode", str(ctx.exception))
        self.assertIn("--data-dir", str(ctx.exception))

    def test_date_filter_is_end_exclusive(self):
        frame, _ = mixed_offset_frame()
        with tempfile.TemporaryDirectory() as tmp:
            loader = DataLoader(historical_dir=Path(tmp))
            loader.save_historical_data("AAA", frame)
            loaded = loader.load_historical_data("AAA", start="2023-03-08", end="2023-03-10")
        self.assertEqual([str(d) for d in loaded["date"]], ["2023-03-08", "2023-03-09"])


if __name__ == "__main__":
    unittest.main()
