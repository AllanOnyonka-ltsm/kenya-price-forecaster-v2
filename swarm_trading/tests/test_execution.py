import datetime
import unittest

from support import build_simulation, make_frame, random_walk_data, run_async
from core.market_environment import MarketEnvironment
from core.message_bus import LocalMessageBus
from agents.trader_agent import TraderAgent, OrderState


class OneExecutionPerOrderTests(unittest.TestCase):
    def test_full_run_each_order_fills_exactly_once(self):
        async def scenario():
            symbols = ["AAA", "BBB", "CCC"]
            data = random_walk_data(symbols, days=120, seed=3)
            sim = await build_simulation(data, symbols, seed=3)
            report = await sim.run()
            return sim, report

        sim, report = run_async(scenario())
        trades = sim.reporter.trades
        order_ids = [t["order_id"] for t in trades]
        self.assertGreater(len(trades), 0, "scenario must produce trades to be meaningful")
        self.assertEqual(len(order_ids), len(set(order_ids)), "an order was filled twice")
        self.assertEqual(len(trades), sim.market.filled_count)
        self.assertEqual(sim.market.duplicate_submissions, 0)
        self.assertEqual(report["risk_metrics"]["duplicate_trades_detected"], 0)
        # Two risk managers approved each order, but each order is counted once.
        self.assertEqual(sim.reporter.approved_orders, sim.market.submitted_count)

    def test_duplicate_approvals_publish_and_forward_once(self):
        async def scenario():
            bus = LocalMessageBus()
            approved, decisions = [], []

            async def record_approved(msg):
                approved.append(msg)

            async def record_decision(msg):
                decisions.append(msg)

            await bus.subscribe("approved_orders", record_approved)
            await bus.subscribe("order_decisions", record_decision)
            trader = TraderAgent("trader_1", bus, ["AAA"], initial_cash=100_000.0,
                                 required_approvals=2)
            await trader.start()
            trader.current_prices["AAA"] = 100.0
            await trader._place_buy("AAA", 100.0)
            await bus.drain()
            order_id = "trader_1_order_1"
            for rm, ok in [("rm1", True), ("rm2", True), ("rm2", True), ("rm1", False)]:
                await bus.publish("risk_decisions", {
                    "type": "risk_decision", "order_id": order_id,
                    "risk_manager_id": rm, "approved": ok, "reason": "x"})
                await bus.drain()
            return trader, approved, decisions, order_id

        trader, approved, decisions, order_id = run_async(scenario())
        self.assertEqual(len(approved), 1)
        self.assertEqual([d["decision"] for d in decisions], ["APPROVED"])
        self.assertEqual(trader.orders[order_id].state, OrderState.APPROVED)

    def test_market_ignores_resubmission_of_same_order(self):
        async def scenario():
            bus = LocalMessageBus()
            frame = make_frame("AAA", [("2023-01-02", 100, 101, 99, 100, 1000),
                                       ("2023-01-03", 100, 101, 99, 100, 1000)])
            market = MarketEnvironment(bus, {"AAA": frame})
            await market.publish_bars(datetime.date(2023, 1, 2))
            first = await market.submit_order("o1", "t", "AAA", "BUY", 1, None)
            second = await market.submit_order("o1", "t", "AAA", "BUY", 1, None)
            return first, second, market

        first, second, market = run_async(scenario())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(market.duplicate_submissions, 1)
        self.assertEqual(len(market.pending_orders), 1)


if __name__ == "__main__":
    unittest.main()
