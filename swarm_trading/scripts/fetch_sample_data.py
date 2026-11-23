"""Download a small real OHLCV sample from Yahoo Finance into data/sample/.

Run once, with network access: python scripts/fetch_sample_data.py
The committed CSVs are real historical data. Tests do not use this script
and do not need network access.
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data.data_loader import DataLoader, SAMPLE_DATA_DIR  # noqa: E402

SYMBOLS = ["AAPL", "MSFT", "JNJ"]
START, END = "2023-01-01", "2023-07-01"


def main() -> int:
    loader = DataLoader(historical_dir=SAMPLE_DATA_DIR)
    data = loader.download_historical_data(SYMBOLS, START, END)
    if loader.failed_downloads:
        print(f"Failed: {loader.failed_downloads}")
    print(f"Wrote {len(data)} symbol files to {SAMPLE_DATA_DIR}")
    return 0 if len(data) == len(SYMBOLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
