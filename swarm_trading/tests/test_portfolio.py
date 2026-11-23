import unittest

from support import ROOT  # noqa: F401  (sets sys.path)
from core.portfolio import Portfolio, PortfolioError


class PortfolioPnLTests(unittest.TestCase):
    def test_buy_then_partial_sell_realised_and_unrealised(self):
        p = Portfolio(10_000.0)
        p.apply_fill("AAA", "BUY", 10, 100.0, commission=1.0)
        # Commission is part of the cost basis: (10*100 + 1) / 10
        self.assertAlmostEqual(p.average_cost("AAA"), 100.1)
        self.assertAlmostEqual(p.cash, 10_000.0 - 1001.0)

        realised = p.apply_fill("AAA", "SELL", 4, 110.0, commission=0.5)
        expected_realised = (110.0 - 100.1) * 4 - 0.5
        self.assertAlmostEqual(realised, expected_realised)
        self.assertAlmostEqual(p.realized_pnl, expected_realised)
        self.assertEqual(p.quantity("AAA"), 6)

        unrealised = p.unrealized_pnl({"AAA": 105.0})
        self.assertAlmostEqual(unrealised, (105.0 - 100.1) * 6)

    def test_identity_total_value_minus_initial_equals_pnl(self):
        p = Portfolio(50_000.0)
        p.apply_fill("AAA", "BUY", 30, 50.0, commission=1.5)
        p.apply_fill("BBB", "BUY", 5, 200.0, commission=2.0)
        p.apply_fill("AAA", "SELL", 10, 55.0, commission=0.7)
        marks = {"AAA": 48.0, "BBB": 210.0}
        self.assertAlmostEqual(
            p.total_value(marks) - p.initial_cash,
            p.realized_pnl + p.unrealized_pnl(marks),
            places=6,
        )

    def test_full_close_resets_cost_and_realises_all(self):
        p = Portfolio(1_000.0)
        p.apply_fill("AAA", "BUY", 5, 10.0)
        p.apply_fill("AAA", "SELL", 5, 8.0)
        self.assertEqual(p.quantity("AAA"), 0)
        self.assertEqual(p.average_cost("AAA"), 0.0)
        self.assertAlmostEqual(p.realized_pnl, -10.0)
        self.assertAlmostEqual(p.unrealized_pnl({"AAA": 99.0}), 0.0)

    def test_oversell_and_overspend_raise(self):
        p = Portfolio(100.0)
        with self.assertRaises(PortfolioError):
            p.apply_fill("AAA", "BUY", 100, 2.0)
        p.apply_fill("AAA", "BUY", 10, 1.0)
        with self.assertRaises(PortfolioError):
            p.apply_fill("AAA", "SELL", 11, 1.0)


if __name__ == "__main__":
    unittest.main()
