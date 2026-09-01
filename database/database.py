"""Persistencia SQLite para histórico, simulación y operaciones del broker."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "historical" / "market_data.db"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def get_connection(row_factory=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------
def create_tables():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            granularity INTEGER NOT NULL,
            epoch INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            UNIQUE(symbol, granularity, epoch)
        )
        """
    )
    conn.commit()
    conn.close()


def save_candles(symbol, granularity, candles):
    conn = get_connection()
    conn.executemany(
        """
        INSERT OR IGNORE INTO candles
            (symbol, granularity, epoch, open, high, low, close)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                symbol,
                int(granularity),
                int(c["epoch"]),
                float(c["open"]),
                float(c["high"]),
                float(c["low"]),
                float(c["close"]),
            )
            for c in candles
        ],
    )
    conn.commit()
    conn.close()


def get_candles(symbol, granularity, limit=None):
    conn = get_connection()
    if limit:
        # Para obtener las últimas N velas y devolverlas en orden ascendente.
        rows = conn.execute(
            """
            SELECT epoch, open, high, low, close FROM (
                SELECT epoch, open, high, low, close FROM candles
                WHERE symbol = ? AND granularity = ?
                ORDER BY epoch DESC LIMIT ?
            ) ORDER BY epoch ASC
            """,
            (symbol, int(granularity), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT epoch, open, high, low, close FROM candles
            WHERE symbol = ? AND granularity = ? ORDER BY epoch ASC
            """,
            (symbol, int(granularity)),
        ).fetchall()
    conn.close()
    return rows


def count_candles(symbol, granularity):
    conn = get_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM candles WHERE symbol = ? AND granularity = ?",
        (symbol, int(granularity)),
    ).fetchone()[0]
    conn.close()
    return total


def get_available_datasets():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT symbol, granularity, COUNT(*) AS total
        FROM candles GROUP BY symbol, granularity ORDER BY symbol, granularity
        """
    ).fetchall()
    conn.close()
    return [{"symbol": s, "granularity": g, "total": t} for s, g, t in rows]


# ---------------------------------------------------------------------------
# Simulación live heredada
# ---------------------------------------------------------------------------
def create_live_trades_table():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            granularity INTEGER NOT NULL,
            entry_epoch INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_epoch INTEGER NOT NULL,
            exit_price REAL NOT NULL,
            profit REAL NOT NULL,
            profit_pct REAL NOT NULL,
            direction TEXT NOT NULL DEFAULT 'BUY',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_trades)")}
    if "direction" not in columns:
        conn.execute(
            "ALTER TABLE live_trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'BUY'"
        )
    conn.commit()
    conn.close()


def save_live_trade(
    symbol,
    granularity,
    entry_epoch,
    entry_price,
    exit_epoch,
    exit_price,
    profit,
    profit_pct,
    direction="BUY",
):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO live_trades
            (symbol, granularity, entry_epoch, entry_price, exit_epoch,
             exit_price, profit, profit_pct, direction)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            int(granularity),
            int(entry_epoch),
            float(entry_price),
            int(exit_epoch),
            float(exit_price),
            float(profit),
            float(profit_pct),
            direction,
        ),
    )
    conn.commit()
    conn.close()


def get_live_trades(symbol, granularity):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT entry_epoch, entry_price, exit_epoch, exit_price,
               profit, profit_pct, direction
        FROM live_trades WHERE symbol = ? AND granularity = ?
        ORDER BY exit_epoch ASC
        """,
        (symbol, int(granularity)),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Trading conectado al broker
# ---------------------------------------------------------------------------
BOT_STATE_FIELDS = {
    "account_id",
    "execution_product",
    "status",
    "symbol_query",
    "symbol_code",
    "symbol_name",
    "strategy_id",
    "granularity",
    "currency",
    "allocation_capital",
    "allocation_reset_at",
    "virtual_balance",
    "peak_balance",
    "account_balance",
    "realized_pnl_total",
    "realized_pnl_today",
    "stats_day",
    "trades_today",
    "consecutive_losses",
    "circuit_breaker",
    "circuit_breaker_reason",
    "current_contract_id",
    "current_direction",
    "current_unrealized_pnl",
    "last_price",
    "last_signal",
    "last_signal_epoch",
    "last_candle_epoch",
    "cooldown_until_epoch",
    "session_id",
    "started_at",
    "last_heartbeat",
    "stopped_at",
    "last_error",
    "reconnect_attempts",
}


def create_live_runtime_tables():
    create_tables()
    create_live_trades_table()
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS live_bot_state (
            mode TEXT PRIMARY KEY,
            account_id TEXT,
            execution_product TEXT NOT NULL DEFAULT 'multiplier',
            status TEXT NOT NULL DEFAULT 'stopped',
            symbol_query TEXT,
            symbol_code TEXT,
            symbol_name TEXT,
            strategy_id TEXT,
            granularity INTEGER,
            currency TEXT DEFAULT 'USD',
            allocation_capital REAL NOT NULL DEFAULT 100,
            allocation_reset_at TEXT,
            virtual_balance REAL NOT NULL DEFAULT 100,
            peak_balance REAL NOT NULL DEFAULT 100,
            account_balance REAL,
            realized_pnl_total REAL NOT NULL DEFAULT 0,
            realized_pnl_today REAL NOT NULL DEFAULT 0,
            stats_day TEXT,
            trades_today INTEGER NOT NULL DEFAULT 0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            circuit_breaker INTEGER NOT NULL DEFAULT 0,
            circuit_breaker_reason TEXT,
            current_contract_id INTEGER,
            current_direction TEXT,
            current_unrealized_pnl REAL NOT NULL DEFAULT 0,
            last_price REAL,
            last_signal TEXT,
            last_signal_epoch INTEGER,
            last_candle_epoch INTEGER,
            cooldown_until_epoch INTEGER,
            session_id TEXT,
            started_at TEXT,
            last_heartbeat TEXT,
            stopped_at TEXT,
            last_error TEXT,
            reconnect_attempts INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS broker_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            execution_product TEXT NOT NULL DEFAULT 'multiplier',
            account_id TEXT,
            symbol_code TEXT NOT NULL,
            symbol_name TEXT,
            granularity INTEGER NOT NULL,
            strategy_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            contract_type TEXT NOT NULL,
            signal_epoch INTEGER NOT NULL,
            proposal_id TEXT,
            contract_id INTEGER UNIQUE,
            transaction_id TEXT,
            order_id INTEGER,
            stake REAL NOT NULL,
            multiplier INTEGER NOT NULL,
            volume_lots REAL,
            margin_required REAL,
            stop_loss_amount REAL NOT NULL,
            take_profit_amount REAL NOT NULL,
            stop_loss_price REAL,
            take_profit_price REAL,
            entry_price_signal REAL,
            entry_spot REAL,
            exit_spot REAL,
            buy_price REAL,
            sell_price REAL,
            profit REAL,
            profit_pct REAL,
            spread_points REAL,
            slippage_points REAL,
            commission REAL DEFAULT 0,
            swap REAL DEFAULT 0,
            fee REAL DEFAULT 0,
            net_profit REAL,
            magic INTEGER,
            account_server TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            exit_reason TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            raw_open_json TEXT,
            raw_close_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_broker_trades_mode_closed
            ON broker_trades(mode, closed_at);
        CREATE INDEX IF NOT EXISTS idx_broker_trades_status
            ON broker_trades(status);

        CREATE TABLE IF NOT EXISTS live_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            data_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_live_events_created
            ON live_events(created_at DESC);
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_bot_state)")}
    if "cooldown_until_epoch" not in columns:
        conn.execute("ALTER TABLE live_bot_state ADD COLUMN cooldown_until_epoch INTEGER")
    if "allocation_reset_at" not in columns:
        conn.execute("ALTER TABLE live_bot_state ADD COLUMN allocation_reset_at TEXT")
    if "execution_product" not in columns:
        conn.execute(
            "ALTER TABLE live_bot_state ADD COLUMN execution_product TEXT NOT NULL DEFAULT 'multiplier'"
        )

    trade_columns = {row[1] for row in conn.execute("PRAGMA table_info(broker_trades)")}
    migrations = {
        "execution_product": "TEXT NOT NULL DEFAULT 'multiplier'",
        "order_id": "INTEGER",
        "volume_lots": "REAL",
        "margin_required": "REAL",
        "stop_loss_price": "REAL",
        "take_profit_price": "REAL",
        "spread_points": "REAL",
        "slippage_points": "REAL",
        "commission": "REAL DEFAULT 0",
        "swap": "REAL DEFAULT 0",
        "fee": "REAL DEFAULT 0",
        "net_profit": "REAL",
        "magic": "INTEGER",
        "account_server": "TEXT",
    }
    for column, definition in migrations.items():
        if column not in trade_columns:
            conn.execute(f"ALTER TABLE broker_trades ADD COLUMN {column} {definition}")
    conn.commit()
    conn.close()


def ensure_bot_state(mode, allocation_capital=100):
    create_live_runtime_tables()
    day = utc_day()
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO live_bot_state
            (mode, allocation_capital, allocation_reset_at, virtual_balance, peak_balance, stats_day)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mode,
            float(allocation_capital),
            utc_now_iso(),
            float(allocation_capital),
            float(allocation_capital),
            day,
        ),
    )
    conn.commit()
    conn.close()
    return get_bot_state(mode)


def get_bot_state(mode="demo"):
    conn = get_connection(row_factory=True)
    try:
        row = conn.execute("SELECT * FROM live_bot_state WHERE mode = ?", (mode,)).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        create_live_runtime_tables()
        conn = get_connection(row_factory=True)
        row = conn.execute("SELECT * FROM live_bot_state WHERE mode = ?", (mode,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_bot_state(mode, **fields):
    invalid = set(fields) - BOT_STATE_FIELDS
    if invalid:
        raise ValueError(f"Campos de estado no permitidos: {sorted(invalid)}")
    if not fields:
        return get_bot_state(mode)
    fields.setdefault("last_heartbeat", utc_now_iso())
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [mode]
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO live_bot_state
            (mode, allocation_capital, allocation_reset_at, virtual_balance, peak_balance, stats_day)
        VALUES (?, 100, ?, 100, 100, ?)
        """,
        (mode, utc_now_iso(), utc_day()),
    )
    conn.execute(f"UPDATE live_bot_state SET {assignments} WHERE mode = ?", values)
    conn.commit()
    conn.close()
    return get_bot_state(mode)


def roll_bot_day_if_needed(mode):
    state = ensure_bot_state(mode)
    today = utc_day()
    if state.get("stats_day") != today:
        daily_reasons = {
            "límite de pérdida diaria alcanzado",
            "límite diario de operaciones alcanzado",
            "máximo de pérdidas consecutivas alcanzado",
        }
        clear_daily_breaker = state.get("circuit_breaker_reason") in daily_reasons
        fields = {
            "stats_day": today,
            "realized_pnl_today": 0.0,
            "trades_today": 0,
            "consecutive_losses": 0,
        }
        if clear_daily_breaker:
            fields.update(circuit_breaker=0, circuit_breaker_reason=None)
        state = update_bot_state(mode, **fields)
    return state


def reset_bot_allocation(mode, allocation_capital):
    open_trade = get_open_broker_trade(mode)
    if open_trade:
        raise RuntimeError("No se puede reiniciar la asignación con un contrato abierto")
    now = utc_now_iso()
    ensure_bot_state(mode, allocation_capital)
    return update_bot_state(
        mode,
        allocation_capital=float(allocation_capital),
        allocation_reset_at=now,
        virtual_balance=float(allocation_capital),
        peak_balance=float(allocation_capital),
        realized_pnl_total=0.0,
        realized_pnl_today=0.0,
        trades_today=0,
        consecutive_losses=0,
        circuit_breaker=0,
        circuit_breaker_reason=None,
        current_contract_id=None,
        current_direction=None,
        current_unrealized_pnl=0.0,
        status="stopped",
        stopped_at=now,
        last_error=None,
    )


def start_bot_session(
    mode,
    *,
    account_id,
    execution_product="multiplier",
    symbol_query,
    symbol_code,
    symbol_name,
    strategy_id,
    granularity,
    currency,
    allocation_capital,
    account_balance,
):
    ensure_bot_state(mode, allocation_capital)
    state = roll_bot_day_if_needed(mode)
    session_id = str(uuid.uuid4())
    now = utc_now_iso()

    # Cada producto mantiene un ciclo de riesgo limpio. Al cambiar de
    # Multiplier a CFD (o viceversa), no se arrastra el P&L del producto
    # anterior. El historial se conserva íntegro en broker_trades.
    previous_product = state.get("execution_product")
    if previous_product and previous_product != execution_product:
        open_trade = get_open_broker_trade(mode)
        if open_trade:
            raise RuntimeError(
                f"No se puede cambiar de {previous_product} a {execution_product} "
                "mientras existe una operación abierta"
            )
        state = reset_bot_allocation(mode, allocation_capital)
        state = update_bot_state(mode, execution_product=execution_product)

    # No aumenta una asignación existente por reiniciar el mismo producto.
    allocation = float(state.get("allocation_capital") or allocation_capital)
    virtual = float(state.get("virtual_balance") or allocation)
    return update_bot_state(
        mode,
        account_id=account_id,
        execution_product=execution_product,
        status="running",
        symbol_query=symbol_query,
        symbol_code=symbol_code,
        symbol_name=symbol_name,
        strategy_id=strategy_id,
        granularity=int(granularity),
        currency=currency,
        allocation_capital=allocation,
        allocation_reset_at=state.get("allocation_reset_at") or now,
        virtual_balance=virtual,
        peak_balance=max(float(state.get("peak_balance") or virtual), virtual),
        account_balance=float(account_balance),
        session_id=session_id,
        started_at=now,
        stopped_at=None,
        last_error=None,
        reconnect_attempts=0,
    )


def stop_bot_session(mode, status="stopped", error=None):
    return update_bot_state(
        mode,
        status=status,
        stopped_at=utc_now_iso(),
        last_error=error,
        current_unrealized_pnl=0.0,
    )


def save_broker_trade_open(trade):
    required = {
        "session_id",
        "mode",
        "symbol_code",
        "granularity",
        "strategy_id",
        "direction",
        "contract_type",
        "signal_epoch",
        "stake",
        "multiplier",
        "stop_loss_amount",
        "take_profit_amount",
        "opened_at",
    }
    missing = required - set(trade)
    if missing:
        raise ValueError(f"Faltan campos de operación: {sorted(missing)}")
    columns = [
        "session_id", "mode", "execution_product", "account_id",
        "symbol_code", "symbol_name", "granularity", "strategy_id",
        "direction", "contract_type", "signal_epoch", "proposal_id",
        "contract_id", "transaction_id", "order_id", "stake", "multiplier",
        "volume_lots", "margin_required", "stop_loss_amount",
        "take_profit_amount", "stop_loss_price", "take_profit_price",
        "entry_price_signal", "entry_spot", "buy_price", "spread_points",
        "slippage_points", "magic", "account_server", "status",
        "opened_at", "raw_open_json",
    ]
    values = [trade.get(column) for column in columns]
    conn = get_connection()
    cursor = conn.execute(
        f"INSERT INTO broker_trades ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        values,
    )
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_broker_trade(contract_id, **fields):
    allowed = {
        "transaction_id", "entry_spot", "exit_spot", "buy_price", "sell_price",
        "profit", "profit_pct", "spread_points", "slippage_points",
        "commission", "swap", "fee", "net_profit", "status",
        "exit_reason", "closed_at", "raw_close_json",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Campos de trade no permitidos: {sorted(invalid)}")
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE broker_trades SET {assignments} WHERE contract_id = ?",
        list(fields.values()) + [int(contract_id)],
    )
    conn.commit()
    conn.close()


def update_broker_trade_by_id(trade_id, **fields):
    allowed = {
        "proposal_id", "contract_id", "transaction_id", "order_id",
        "entry_spot", "exit_spot", "buy_price", "sell_price", "profit",
        "profit_pct", "spread_points", "slippage_points", "commission",
        "swap", "fee", "net_profit", "status", "exit_reason",
        "closed_at", "raw_open_json", "raw_close_json",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Campos de trade no permitidos: {sorted(invalid)}")
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE broker_trades SET {assignments} WHERE id = ?",
        list(fields.values()) + [int(trade_id)],
    )
    conn.commit()
    conn.close()


def get_latest_pending_trade(mode="demo"):
    conn = get_connection(row_factory=True)
    row = conn.execute(
        """
        SELECT * FROM broker_trades
        WHERE mode = ? AND status IN ('proposal_ready', 'buy_submitted', 'purchase_unknown', 'order_ready', 'order_submitted', 'execution_unknown')
        ORDER BY id DESC LIMIT 1
        """,
        (mode,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_broker_trade_by_contract(contract_id):
    conn = get_connection(row_factory=True)
    row = conn.execute(
        "SELECT * FROM broker_trades WHERE contract_id = ? LIMIT 1",
        (int(contract_id),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_broker_trade(mode="demo"):
    create_live_runtime_tables()
    conn = get_connection(row_factory=True)
    row = conn.execute(
        """
        SELECT * FROM broker_trades
        WHERE mode = ? AND status IN ('open', 'purchased', 'monitoring', 'position_open')
        ORDER BY id DESC LIMIT 1
        """,
        (mode,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_broker_trades(mode="demo", limit=200, execution_product=None):
    create_live_runtime_tables()
    conn = get_connection(row_factory=True)
    where = "WHERE mode = ?"
    params = [mode]
    if execution_product:
        where += " AND execution_product = ?"
        params.append(execution_product)
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM broker_trades {where} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def apply_closed_trade_to_state(mode, profit):
    state = roll_bot_day_if_needed(mode)
    profit = float(profit)
    virtual = float(state["virtual_balance"]) + profit
    peak = max(float(state["peak_balance"]), virtual)
    consecutive = 0 if profit > 0 else int(state["consecutive_losses"]) + 1
    return update_bot_state(
        mode,
        virtual_balance=virtual,
        peak_balance=peak,
        realized_pnl_total=float(state["realized_pnl_total"]) + profit,
        realized_pnl_today=float(state["realized_pnl_today"]) + profit,
        trades_today=int(state["trades_today"]) + 1,
        consecutive_losses=consecutive,
        current_contract_id=None,
        current_direction=None,
        current_unrealized_pnl=0.0,
    )


def get_broker_metrics(mode="demo", execution_product=None):
    state = ensure_bot_state(mode)
    conn = get_connection(row_factory=True)
    reset_at = state.get("allocation_reset_at") or "0000-01-01T00:00:00+00:00"
    product_clause = " AND execution_product = ?" if execution_product else ""
    params = [mode, reset_at]
    if execution_product:
        params.append(execution_product)
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(net_profit, profit) > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN COALESCE(net_profit, profit) <= 0 AND COALESCE(net_profit, profit) IS NOT NULL THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(COALESCE(net_profit, profit)), 0) AS pnl,
            COALESCE(SUM(CASE WHEN COALESCE(net_profit, profit) > 0 THEN COALESCE(net_profit, profit) ELSE 0 END), 0) AS gross_profit,
            ABS(COALESCE(SUM(CASE WHEN COALESCE(net_profit, profit) < 0 THEN COALESCE(net_profit, profit) ELSE 0 END), 0)) AS gross_loss
        FROM broker_trades
        WHERE mode = ? AND status = 'closed' AND closed_at >= ?{product_clause}
        """,
        params,
    ).fetchone()
    conn.close()
    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    gross_loss = float(row["gross_loss"] or 0)
    same_product = not execution_product or state.get("execution_product") == execution_product
    if same_product:
        peak = float(state.get("peak_balance") or state["allocation_capital"])
        virtual = float(state["virtual_balance"])
        daily_pnl = float(state["realized_pnl_today"] or 0)
    else:
        # Antes del primer arranque del producto nuevo, muestra un libro limpio
        # de US$100 en vez de arrastrar el P&L del producto anterior.
        virtual = float(state["allocation_capital"])
        peak = virtual
        daily_pnl = 0.0
    drawdown = ((peak - virtual) / peak * 100) if peak > 0 else 0.0
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total * 100) if total else 0.0,
        "profit": float(row["pnl"] or 0),
        "profit_factor": (
            float(row["gross_profit"] or 0) / gross_loss if gross_loss > 0 else 0.0
        ),
        "virtual_balance": virtual,
        "allocation_capital": float(state["allocation_capital"]),
        "drawdown_pct": drawdown,
        "daily_pnl": daily_pnl,
        "execution_product": execution_product or state.get("execution_product"),
    }

def get_equity_curve(mode="demo", limit=500, execution_product=None):
    state = ensure_bot_state(mode)
    conn = get_connection(row_factory=True)
    reset_at = state.get("allocation_reset_at") or "0000-01-01T00:00:00+00:00"
    product_clause = " AND execution_product = ?" if execution_product else ""
    params = [mode, reset_at]
    if execution_product:
        params.append(execution_product)
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT closed_at, COALESCE(net_profit, profit) AS profit FROM broker_trades
        WHERE mode = ? AND status = 'closed' AND closed_at IS NOT NULL
          AND closed_at >= ?{product_clause}
        ORDER BY closed_at ASC, id ASC LIMIT ?
        """,
        params,
    ).fetchall()
    conn.close()
    balance = float(state["allocation_capital"])
    curve = [{"time": state.get("started_at") or "inicio", "balance": balance}]
    for row in rows:
        balance += float(row["profit"] or 0)
        curve.append({"time": row["closed_at"], "balance": balance})
    return curve

def log_live_event(mode, level, event_type, message, data=None):
    create_live_runtime_tables()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO live_events
            (mode, level, event_type, message, data_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mode,
            level,
            event_type,
            message,
            json.dumps(data, ensure_ascii=False, default=str) if data is not None else None,
            utc_now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def get_live_events(mode="demo", limit=100):
    create_live_runtime_tables()
    conn = get_connection(row_factory=True)
    rows = conn.execute(
        """
        SELECT level, event_type, message, data_json, created_at
        FROM live_events WHERE mode = ? ORDER BY id DESC LIMIT ?
        """,
        (mode, int(limit)),
    ).fetchall()
    conn.close()
    output = []
    for row in rows:
        item = dict(row)
        if item.get("data_json"):
            try:
                item["data"] = json.loads(item["data_json"])
            except json.JSONDecodeError:
                item["data"] = None
        item.pop("data_json", None)
        output.append(item)
    return output
