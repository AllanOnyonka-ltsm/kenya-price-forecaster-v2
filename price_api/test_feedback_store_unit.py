"""Stdlib unit tests for feedback_store (SQLite persistence + real stats).

No model load, network, or third-party dependencies.
Run from price_api/: python -m unittest test_feedback_store_unit -v
"""

import os
import tempfile
import unittest
from unittest import mock

import feedback_store as fs


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "nested", "fb.db")  # nested dir must be auto-created

    def test_save_and_get_roundtrip_all_fields(self):
        r = fs.save_feedback(user_id="u1", prediction_id="p1", predicted_price=60.0, actual_price=67.0,
                             accuracy_rating=4, usefulness_rating=5, comments="good",
                             client_timestamp="2025-12-06T10:30:00", db_path=self.db)
        self.assertTrue(r["feedback_id"].startswith("FB-"))
        row = fs.get_feedback(r["feedback_id"], db_path=self.db)
        self.assertEqual(row["created_at"], r["created_at"])
        self.assertEqual(
            (row["user_id"], row["prediction_id"], row["predicted_price"], row["actual_price"],
             row["accuracy_rating"], row["usefulness_rating"], row["comments"], row["client_timestamp"]),
            ("u1", "p1", 60.0, 67.0, 4, 5, "good", "2025-12-06T10:30:00"))

    def test_all_optional_fields_none(self):
        r = fs.save_feedback(db_path=self.db)
        self.assertIsNone(fs.get_feedback(r["feedback_id"], db_path=self.db)["user_id"])

    def test_ids_unique(self):
        ids = {fs.save_feedback(db_path=self.db)["feedback_id"] for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_invalid_ratings_rejected_and_not_stored(self):
        for kw in ({"accuracy_rating": 0}, {"accuracy_rating": 6},
                   {"usefulness_rating": -1}, {"usefulness_rating": 9}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                fs.save_feedback(db_path=self.db, **kw)
        self.assertEqual(fs.compute_stats(self.db)["feedback_count"], 0)

    def test_env_var_controls_path(self):
        path = os.path.join(self.tmp.name, "env.db")
        with mock.patch.dict(os.environ, {"FEEDBACK_DB_PATH": path}):
            fs.save_feedback(user_id="x")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(fs.compute_stats()["feedback_count"], 1)

    def test_default_path_when_env_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FEEDBACK_DB_PATH", None)
            self.assertEqual(fs.get_db_path(), fs.DEFAULT_DB_PATH)
            self.assertTrue(fs.DEFAULT_DB_PATH.endswith(os.path.join("data", "feedback.db")))

    def test_stats_empty_are_none_not_fake(self):
        s = fs.compute_stats(self.db)
        self.assertEqual(s["feedback_count"], 0)
        self.assertEqual(s["distinct_users"], 0)
        for k in ("average_accuracy_rating", "average_usefulness_rating", "mape_pct",
                  "price_accuracy", "last_feedback_at"):
            self.assertIsNone(s[k], k)

    def test_stats_values(self):
        fs.save_feedback(user_id="a", accuracy_rating=4, usefulness_rating=5,
                         predicted_price=90, actual_price=100, db_path=self.db)
        fs.save_feedback(user_id="a", accuracy_rating=2, predicted_price=120, actual_price=100, db_path=self.db)
        fs.save_feedback(user_id="b", usefulness_rating=3, predicted_price=50, actual_price=0,
                         db_path=self.db)  # actual 0: excluded from MAPE
        fs.save_feedback(user_id=None, actual_price=80, db_path=self.db)  # no predicted: excluded
        s = fs.compute_stats(self.db)
        self.assertEqual(s["feedback_count"], 4)
        self.assertEqual(s["distinct_users"], 2)
        self.assertEqual(s["average_accuracy_rating"], 3.0)
        self.assertEqual(s["average_usefulness_rating"], 4.0)
        self.assertEqual(s["price_comparisons"], 2)
        self.assertEqual(s["mape_pct"], 15.0)
        self.assertEqual(s["price_accuracy"], 0.85)
        self.assertIsNotNone(s["last_feedback_at"])

    def test_price_accuracy_clamped_at_zero(self):
        fs.save_feedback(predicted_price=500, actual_price=100, db_path=self.db)
        s = fs.compute_stats(self.db)
        self.assertEqual(s["mape_pct"], 400.0)
        self.assertEqual(s["price_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
