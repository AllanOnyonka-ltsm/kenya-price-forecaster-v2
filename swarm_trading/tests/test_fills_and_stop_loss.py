import datetime
import unittest

from support import build_simulation, make_frame, random_walk_data, run_async
from core.market_environment import MarketEnvironment
from core.message_bus import LocalMessageBus


D0 = datetime.date(2023, 1, 2)
D1 = datetime.date(2023, 1, 3)
D2 = datetime.date(2023, 1, 4)
D3 = datetime.date(2023, 1, 5)


class NoSameBarFillTests(unittest.TestCase):
    def test_order_from_day_t_fills_at_open_of_t_plus_one(self):
        async def scenario():
            bus = LocalMessageBus()
            trades = []

            async def record(msg):
                trades.append(msg)

            await bus.subscribe("trades", record)
            frame = make_frame("AAA", [
                ("2023-01-02", 100.0, 101.0, 99.0, 100.0, 1000),  # close 100
                ("2023-01-03", 105.0, 106.0, 104.0, 107.0, 1000),  # open 105
            ])
            market = MarketEnvironment(bus, {"AAA": frame})
            await market.publish_bars(D0)
            await market.submit_order("o1", "t", "AAA", "BUY", 10, None)
            await market.execute_open(D0)  # same bar as the signal: must not fill
            await bus.drain()
            before = list(trades)
            await market.publish_bars(D1)
            await market.execute_open(D1)
            await bus.drain()
            return before, trades

        before, trades = run_async(scenario())
        self.assertEqual(before, [], "order filled on the bar that generated it")
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["execution_price"], 105.0)  # open of t+1, not close of t
        self.assertEqual(trades[0]["submitted_on"], "2023-01-02")
        self.assertEqual(trades[0]["timestamp"], "2023-01-03T00:00:00")

    def test_limit_not_reached_expires_instead_of_filling(self):
        async def scenario():
            bus = LocalMessageBus()
            events = []

            async def record(msg):
                events.append(msg)

            await bus.subscribe("trades", record)
            await bus.subscribe("order_expired", record)
            frame = make_frame("AAA", [
                ("2023-01-02", 100.0, 101.0, 99.0, 100.0, 1000),
                ("2023-01-03", 105.0, 106.0, 104.0, 107.0, 1000),
            ])
            market = MarketEnvironment(bus, {"AAA": frame})
            await market.publish_bars(D0)
            await market.submit_order("o1", "t", "AAA", "BUY", 10, 101.0)  # open 105 > limit 101
            await market.execute_open(D0)
            await market.publish_bars(D1)
            await market.execute_open(D1)
            await bus.drain()
            return events, market

        events, market = run_async(scenario())
        self.assertEqual([e["type"] for e in events], ["order_expired"])
        self.assertEqual(market.pending_orders, [])

    def test_missing_open_falls_back_to_close_and_is_labelled(self):
        async def scenario():
            bus = LocalMessageBus()
            trades = []

            async def record(msg):
                trades.append(msg)

            await bus.subscribe("trades", record)
            frame = make_frame("AAA", [
                ("2023-01-02", 100.0, 101.0, 99.0, 100.0, 1000),
                ("2023-01-03", None, 106.0, 104.0, 107.0, 1000),  # open missing
            ])
            market = MarketEnvironment(bus, {"AAA": frame})
            await market.publish_bars(D0)
            await market.submit_order("o1", "t", "AAA", "BUY", 1, None)
            await market.publish_bars(D1)
            await market.execute_open(D1)
            await bus.drain()
            return trades

        trades = run_async(scenario())
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["execution_price"], 107.0)
        self.assertEqual(trades[0]["price_source"], "close_fallback")


class StopLossLiquidationTests(unittest.TestCase):
    def test_stop_loss_alert_sells_whole_position(self):
        async def scenario():
            data = {"AAA": make_frame("AAA", [
                ("2023-01-02", 100.0, 100.5, 99.5, 100.0, 1000),   # D0 close 100
                ("2023-01-03", 100.0, 100.5, 99.5, 100.0, 1000),   # D1 open 100 (fill)
                ("2023-01-04", 80.0, 80.5, 79.5, 80.0, 1000),      # D2 close 80: -20%
                ("2023-01-05", 78.0, 78.5, 77.5, 78.0, 1000),      # D3 open 78 (liquidation fill)
            ])}
            sim = await build_simulation(data, ["AAA"], seed=5)
            for analyst in sim.analysts:
                analyst.symbols = []  # isolate the stop-loss path from random signals
            trader = sim.traders[0]

            await sim.step(D0)
            await trader._place_buy("AAA", 100.0)   # budget 6000 -> 60 shares
            await sim.bus.drain()
            await sim.step(D1)
            held_after_buy = trader.portfolio.quantity("AAA")
            await sim.step(D2)
            await sim.step(D3)
            return sim, trader, held_after_buy

        sim, trader, held_after_buy = run_async(scenario())
        self.assertEqual(held_after_buy, 60)
        self.assertEqual(trader.portfolio.quantity("AAA"), 0, "stop-loss did not liquidate")
        self.assertLess(trader.portfolio.realized_pnl, 0.0)
        self.assertEqual(len(sim.reporter.stop_losses), 1, "one liquidation, despite two risk managers")
        self.assertEqual(trader.liquidating, set())


class SeededRunDeterminismTests(unittest.TestCase):
    def test_same_seed_gives_identical_trades_and_report(self):
        async def one_run():
            symbols = ["AAA", "BBB"]
            data = random_walk_data(symbols, days=80, seed=11)
            sim = await build_simulation(data, symbols, seed=7)
            report = await sim.run()
            return report

        first = run_async(one_run())
        second = run_async(one_run())
        self.assertGreater(first["summary"]["total_trades"], 0)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
