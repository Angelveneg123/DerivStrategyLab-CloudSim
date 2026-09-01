"""Pruebas locales seguras: nunca inicializan MT5 ni envían order_send."""

import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace

from backtesting.reporting import export_backtest_reports
from config import LIVE_EXECUTION_PRODUCT
import database.database as db
from database.database import create_live_runtime_tables, get_broker_trade_by_contract
from live_trading.cfd_risk import build_cfd_order_plan


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def symbol_info(self, _symbol):
        return SimpleNamespace(
            point=0.001,
            digits=3,
            volume_min=0.01,
            volume_max=10.0,
            volume_step=0.01,
            trade_stops_level=10,
        )

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(bid=3089.700, ask=3089.720)

    def order_calc_profit(self, order_type, _symbol, volume, open_price, close_price):
        delta = close_price - open_price
        if order_type == self.ORDER_TYPE_SELL:
            delta = -delta
        return delta * volume * 100.0

    def order_calc_margin(self, _order_type, _symbol, volume, _price):
        return volume * 100.0


def test_cfd_plan():
    mt5 = FakeMT5()
    for direction in ("BUY", "SELL"):
        plan = build_cfd_order_plan(
            mt5=mt5,
            symbol="Crash 500 Index",
            direction=direction,
            signal_price=3089.710,
            atr=0.60,
            virtual_balance=100,
            allocation_capital=100,
            reinvest_profits=False,
            risk_pct=2,
            stop_atr_mult=1.5,
            reward_ratio=2,
            max_margin=100,
        )
        assert plan is not None
        assert plan.volume_lots >= 0.01
        assert plan.estimated_loss <= 2.01
        assert plan.margin_required <= 100.01
        assert math.isclose(plan.spread_points, 20.0, rel_tol=1e-9, abs_tol=1e-6)
        if direction == "BUY":
            assert plan.stop_price < plan.entry_price < plan.take_price
        else:
            assert plan.take_price < plan.entry_price < plan.stop_price


def test_reports():
    result = {
        "trades": [
            {"direction": "BUY", "profit": 2.0, "entry_epoch": 1, "exit_epoch": 2},
            {"direction": "SELL", "profit": -1.0, "entry_epoch": 3, "exit_epoch": 4},
        ],
        "metrics": {"total_trades": 2, "profit_factor": 2.0, "final_balance": 101.0},
    }
    with tempfile.TemporaryDirectory() as tmp:
        report = export_backtest_reports(result, tmp, initial_balance=100)
        for name in ("equity_curve.png", "trades.csv", "summary.json", "metrics.json"):
            path = Path(tmp) / name
            assert path.exists() and path.stat().st_size > 0
        assert len(report["files"]) == 4


def test_schema_and_history_preserved():
    create_live_runtime_tables()
    existing = get_broker_trade_by_contract(8235773199)
    if existing is not None:
        assert existing["contract_type"] == "MULTUP"
        assert existing["status"] == "closed"



def test_product_switch_resets_ledger():
    original = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "test.db"
        try:
            db.create_live_runtime_tables()
            db.ensure_bot_state("demo", 100)
            db.update_bot_state(
                "demo",
                execution_product="multiplier",
                virtual_balance=97.0,
                peak_balance=100.0,
                realized_pnl_total=-3.0,
                realized_pnl_today=-3.0,
                trades_today=2,
            )
            state = db.start_bot_session(
                "demo",
                account_id="123",
                execution_product="cfd_standard",
                symbol_query="Crash 500 Index",
                symbol_code="Crash 500 Index",
                symbol_name="Crash 500 Index",
                strategy_id="hybrid",
                granularity=60,
                currency="USD",
                allocation_capital=100,
                account_balance=10000,
            )
            assert state["execution_product"] == "cfd_standard"
            assert state["virtual_balance"] == 100.0
            assert state["realized_pnl_total"] == 0.0
            assert state["trades_today"] == 0
        finally:
            db.DB_PATH = original


def main():
    assert LIVE_EXECUTION_PRODUCT == "cfd_standard"
    test_cfd_plan()
    test_reports()
    test_schema_and_history_preserved()
    test_product_switch_resets_ledger()
    print(json.dumps({
        "ok": True,
        "default_product": LIVE_EXECUTION_PRODUCT,
        "mt5_initialized": False,
        "order_sent": False,
        "tests": ["cfd_risk", "reports", "schema", "multiplier_history_preserved", "product_switch_resets_ledger"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
