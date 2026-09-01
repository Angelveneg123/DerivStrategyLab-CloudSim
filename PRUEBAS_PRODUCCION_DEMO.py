"""Pruebas locales del paquete de trading DEMO.

No abre conexión de red y nunca compra contratos. Para probar el token y una
propuesta real sin comprar, usa: ``python run_demo_trader.py --check``.
"""

import tempfile
from pathlib import Path

import database.database as db
from api.deriv import DerivClient, DerivConnectionError
from config import (
    DERIV_PAT_TOKEN,
    LIVE_ALLOCATED_CAPITAL_USD,
    REAL_TRADING_ENABLED,
    validate_trading_mode,
)
from database.database import get_candles
from live_trading.engine import LiveTradingEngine
from live_trading.risk import build_order_plans
from strategy.registry import run_registered_backtest
from strategy.strategy_engine import BUY


def test_real_lock():
    assert DERIV_PAT_TOKEN
    if not REAL_TRADING_ENABLED:
        try:
            validate_trading_mode("real")
        except RuntimeError:
            return
        raise AssertionError("El modo REAL debería permanecer bloqueado")


def test_symbol_resolution():
    symbols = [
        {"underlying_symbol": "CRASH500", "underlying_symbol_name": "Crash 500 Index"},
        {"underlying_symbol": "BOOM500", "underlying_symbol_name": "Boom 500 Index"},
    ]
    assert DerivClient.resolve_symbol_from_list("Crash 500 Index", symbols)["underlying_symbol"] == "CRASH500"
    assert DerivClient.resolve_symbol_from_list("CRASH500", symbols)["underlying_symbol"] == "CRASH500"


def test_risk_plan():
    plans = build_order_plans(
        direction=BUY,
        entry_price=1000,
        atr=1,
        virtual_balance=100,
        allocation_capital=100,
        reinvest_profits=False,
        risk_pct=2,
        multipliers=[100, 50, 20, 10],
        min_stake=1,
        max_stake=10,
        max_stake_pct=10,
        stop_atr_mult=1.5,
        reward_ratio=2,
    )
    assert plans
    assert all(plan.stake <= 10 for plan in plans)
    assert all(plan.stop_loss_amount <= 2.01 for plan in plans)



def test_multiplier_payloads():
    client = DerivClient(
        app_id="TEST_APP",
        access_token="TEST_TOKEN",
        api_base="https://example.invalid",
    )
    captured = []
    client.request = lambda payload, timeout=None: captured.append(payload) or payload
    buy_payload = client.request_proposal(
        symbol="CRASH500",
        direction="BUY",
        amount=2,
        currency="USD",
        multiplier=50,
        stop_loss=1,
        take_profit=2,
    )
    sell_payload = client.request_proposal(
        symbol="CRASH500",
        direction="SELL",
        amount=2,
        currency="USD",
        multiplier=50,
        stop_loss=1,
        take_profit=2,
    )
    assert buy_payload["contract_type"] == "MULTUP"
    assert sell_payload["contract_type"] == "MULTDOWN"
    assert buy_payload["underlying_symbol"] == "CRASH500"
    assert buy_payload["limit_order"] == {"stop_loss": 1.0, "take_profit": 2.0}
    assert len(captured) == 2

def test_crash500_backtest_smoke():
    rows = get_candles("CRASH500", 60, limit=5000)
    assert len(rows) >= 3200, "Falta el histórico CRASH500 incluido"
    result = run_registered_backtest(
        "hybrid",
        [row[0] for row in rows],
        [row[2] for row in rows],
        [row[3] for row in rows],
        [row[4] for row in rows],
        source_granularity=60,
    )
    assert "metrics" in result and "trades" in result


def test_fake_broker_lifecycle():
    real_db_path = db.DB_PATH
    rows = get_candles("CRASH500", 60, limit=4000)
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "test_live.db"
        try:
            db.create_live_runtime_tables()
            db.reset_bot_allocation("demo", LIVE_ALLOCATED_CAPITAL_USD)
            state = db.start_bot_session(
                "demo",
                account_id="DEMO_TEST",
                symbol_query="Crash 500 Index",
                symbol_code="CRASH500",
                symbol_name="Crash 500 Index",
                strategy_id="hybrid",
                granularity=60,
                currency="USD",
                allocation_capital=100,
                account_balance=10000,
            )

            class FakeClient:
                def get_balance(self):
                    return {"balance": {"balance": 10000, "currency": "USD"}}

                def request_proposal(self, **kwargs):
                    return {
                        "proposal": {
                            "id": "P" * 32,
                            "ask_price": kwargs["amount"],
                            "spot": rows[-1][4],
                        }
                    }

                def buy_contract(self, proposal_id, max_price):
                    return {
                        "buy": {
                            "contract_id": 987654321,
                            "buy_price": 10,
                            "transaction_id": 12345,
                            "start_spot": rows[-1][4],
                        }
                    }

                def subscribe_open_contract(self, contract_id):
                    return {
                        "proposal_open_contract": {
                            "contract_id": contract_id,
                            "profit": 0,
                            "is_sold": 0,
                            "status": "open",
                        }
                    }

            engine = LiveTradingEngine(mode="demo", symbol_query="CRASH500")
            engine.client = FakeClient()
            engine.symbol_code = "CRASH500"
            engine.symbol_name = "Crash 500 Index"
            engine.account_id = "DEMO_TEST"
            engine.session_id = state["session_id"]
            engine.epochs = [row[0] for row in rows]
            engine.highs = [row[2] for row in rows]
            engine.lows = [row[3] for row in rows]
            engine.closes = [row[4] for row in rows]

            engine._execute_signal(BUY, rows[-1][0], rows[-1][4])
            assert db.get_open_broker_trade("demo")
            engine._handle_contract_update(
                {
                    "contract_id": 987654321,
                    "profit": 1.25,
                    "is_sold": 1,
                    "status": "sold",
                    "buy_price": 10,
                    "sell_price": 11.25,
                    "entry_spot": rows[-1][4],
                    "exit_spot": rows[-1][4] + 1,
                    "sell_type": "take_profit",
                }
            )
            assert db.get_open_broker_trade("demo") is None
            final = db.get_bot_state("demo")
            assert abs(final["virtual_balance"] - 101.25) < 1e-9
        finally:
            db.DB_PATH = real_db_path



def test_purchase_reconciliation_after_disconnect():
    """Una caída durante buy no puede permitir una segunda compra duplicada."""
    real_db_path = db.DB_PATH
    rows = get_candles("CRASH500", 60, limit=4000)
    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "test_recovery.db"
        try:
            db.create_live_runtime_tables()
            db.reset_bot_allocation("demo", LIVE_ALLOCATED_CAPITAL_USD)
            state = db.start_bot_session(
                "demo",
                account_id="DEMO_TEST",
                symbol_query="Crash 500 Index",
                symbol_code="CRASH500",
                symbol_name="Crash 500 Index",
                strategy_id="hybrid",
                granularity=60,
                currency="USD",
                allocation_capital=100,
                account_balance=10000,
            )

            class FailingBuyClient:
                def get_balance(self):
                    return {"balance": {"balance": 10000, "currency": "USD"}}

                def request_proposal(self, **kwargs):
                    return {
                        "proposal": {
                            "id": "Q" * 32,
                            "ask_price": kwargs["amount"],
                            "spot": rows[-1][4],
                        }
                    }

                def buy_contract(self, proposal_id, max_price):
                    raise DerivConnectionError("conexión interrumpida durante buy")

            engine = LiveTradingEngine(mode="demo", symbol_query="CRASH500")
            engine.client = FailingBuyClient()
            engine.symbol_code = "CRASH500"
            engine.symbol_name = "Crash 500 Index"
            engine.account_id = "DEMO_TEST"
            engine.session_id = state["session_id"]
            engine.epochs = [row[0] for row in rows]
            engine.highs = [row[2] for row in rows]
            engine.lows = [row[3] for row in rows]
            engine.closes = [row[4] for row in rows]

            try:
                engine._execute_signal(BUY, rows[-1][0], rows[-1][4])
            except DerivConnectionError:
                pass
            else:
                raise AssertionError("La desconexión simulada debía propagarse")

            pending = db.get_latest_pending_trade("demo")
            assert pending and pending["status"] == "purchase_unknown"
            assert db.get_bot_state("demo")["circuit_breaker"] == 1

            class RecoveryClient:
                def get_portfolio(self):
                    return {
                        "portfolio": {
                            "contracts": [
                                {
                                    "contract_id": 777888999,
                                    "buy_price": 10,
                                    "entry_spot": rows[-1][4],
                                    "buy_transaction_id": 555,
                                }
                            ]
                        }
                    }

                def subscribe_open_contract(self, contract_id):
                    return {
                        "proposal_open_contract": {
                            "contract_id": contract_id,
                            "profit": 0,
                            "is_sold": 0,
                            "status": "open",
                        }
                    }

            recovered_engine = LiveTradingEngine(mode="demo", symbol_query="CRASH500")
            recovered_engine.client = RecoveryClient()
            recovered_engine._recover_or_validate_portfolio()
            recovered = db.get_open_broker_trade("demo")
            assert recovered and recovered["contract_id"] == 777888999
            recovered_state = db.get_bot_state("demo")
            assert recovered_state["current_contract_id"] == 777888999
            assert recovered_state["circuit_breaker"] == 0
        finally:
            db.DB_PATH = real_db_path

def test_dashboard_files():
    root = Path(__file__).resolve().parent
    live_template = root / "templates" / "live.html"
    index_template = root / "templates" / "index.html"
    assert live_template.exists() and index_template.exists()
    assert "https://" not in live_template.read_text(encoding="utf-8")
    assert "https://" not in index_template.read_text(encoding="utf-8")
    source = (root / "app_web.py").read_text(encoding="utf-8")
    for route in ("/live", "/api/live/status", "/api/live/trades", "/api/live/equity"):
        assert route in source


def main():
    tests = [
        test_real_lock,
        test_symbol_resolution,
        test_risk_plan,
        test_multiplier_payloads,
        test_crash500_backtest_smoke,
        test_fake_broker_lifecycle,
        test_purchase_reconciliation_after_disconnect,
        test_dashboard_files,
    ]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print("\nTodas las pruebas locales de producción DEMO pasaron.")
    print("La prueba de red/propuesta se ejecuta aparte con --check y no compra.")


if __name__ == "__main__":
    main()
