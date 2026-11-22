"""SQLite persistence for /feedback and real aggregates for /impact-stats.

Stdlib only; no model/app imports so it can be unit-tested in isolation.

DB path: env FEEDBACK_DB_PATH, else <this dir>/data/feedback.db (dir is created).
On Render the filesystem is ephemeral unless a persistent disk is mounted, so
point FEEDBACK_DB_PATH at the disk (e.g. /var/data/feedback.db).

A new connection is opened per call (safe for FastAPI sync endpoints running in
a threadpool and for several gunicorn workers sharing one file; WAL + busy
timeout handle concurrent writers).
"""

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feedback.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id       TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,   -- server UTC timestamp (ISO 8601)
    client_timestamp  TEXT,            -- timestamp supplied by the client, if any
    user_id           TEXT,
    prediction_id     TEXT,
    predicted_price   REAL,
    actual_price      REAL,
    accuracy_rating   INTEGER CHECK (accuracy_rating IS NULL OR accuracy_rating BETWEEN 1 AND 5),
    usefulness_rating INTEGER CHECK (usefulness_rating IS NULL OR usefulness_rating BETWEEN 1 AND 5),
    comments          TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback (created_at);
"""


def get_db_path() -> str:
    return os.environ.get("FEEDBACK_DB_PATH") or DEFAULT_DB_PATH


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.executescript(_SCHEMA)
    return conn


def _check_rating(name: str, value: Optional[int]) -> None:
    if value is not None and not (1 <= value <= 5):
        raise ValueError(f"{name} must be 1-5")


def save_feedback(
    *,
    user_id: Optional[str] = None,
    prediction_id: Optional[str] = None,
    predicted_price: Optional[float] = None,
    actual_price: Optional[float] = None,
    accuracy_rating: Optional[int] = None,
    usefulness_rating: Optional[int] = None,
    comments: Optional[str] = None,
    client_timestamp: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, str]:
    """Persist one submission. Returns {"feedback_id", "created_at"}. Raises ValueError on bad ratings."""
    _check_rating("accuracy_rating", accuracy_rating)
    _check_rating("usefulness_rating", usefulness_rating)
    feedback_id = f"FB-{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as conn, conn:  # inner `with conn` commits/rolls back
        conn.execute(
            "INSERT INTO feedback (feedback_id, created_at, client_timestamp, user_id, prediction_id,"
            " predicted_price, actual_price, accuracy_rating, usefulness_rating, comments)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (feedback_id, created_at, client_timestamp, user_id, prediction_id,
             predicted_price, actual_price, accuracy_rating, usefulness_rating, comments),
        )
    return {"feedback_id": feedback_id, "created_at": created_at}


def get_feedback(feedback_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
    return dict(row) if row else None


def compute_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Aggregates over stored feedback. Metrics with no supporting data are None (never invented).

    mape_pct        mean(|predicted-actual|/actual)*100 over rows with predicted_price and actual_price > 0
    price_accuracy  max(0, 1 - MAPE) on a 0..1 scale (same scale as the old hardcoded average_accuracy)
    """
    with closing(_connect(db_path)) as conn:
        agg = conn.execute(
            "SELECT COUNT(*) AS n,"
            " COUNT(DISTINCT user_id) AS users,"
            " AVG(accuracy_rating) AS avg_acc, COUNT(accuracy_rating) AS n_acc,"
            " AVG(usefulness_rating) AS avg_use, COUNT(usefulness_rating) AS n_use,"
            " MAX(created_at) AS last_at FROM feedback"
        ).fetchone()
        mape = conn.execute(
            "SELECT AVG(ABS(predicted_price - actual_price) / actual_price) AS m, COUNT(*) AS n"
            " FROM feedback WHERE predicted_price IS NOT NULL AND actual_price IS NOT NULL AND actual_price > 0"
        ).fetchone()
    mape_pct = round(mape["m"] * 100, 2) if mape["n"] else None
    return {
        "feedback_count": agg["n"],
        "distinct_users": agg["users"],
        "average_accuracy_rating": round(agg["avg_acc"], 2) if agg["n_acc"] else None,
        "average_usefulness_rating": round(agg["avg_use"], 2) if agg["n_use"] else None,
        "price_comparisons": mape["n"],
        "mape_pct": mape_pct,
        "price_accuracy": round(max(0.0, 1 - mape["m"]), 4) if mape["n"] else None,
        "last_feedback_at": agg["last_at"],
    }
