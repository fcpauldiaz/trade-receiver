from datetime import datetime

from app.models.tables import TradeExecution
from app.services.performance import performance_summary, trade_cashflow


def _trade(**kwargs) -> TradeExecution:
    defaults = dict(
        user_id="u1",
        broker="tradier",
        mode="paper",
        status="filled",
        underlying="SPX",
        option_type="call",
        strike=5000.0,
        expiration="2026-07-28",
        quantity=1,
        contract_symbol="SPXW...",
        fill_price=14.0,
        pnl=None,
        intent_json='{"action":"buy_to_open"}',
        created_at=datetime(2026, 7, 28, 15, 5, 20),
    )
    defaults.update(kwargs)
    return TradeExecution(**defaults)


def test_trade_cashflow_open_has_no_realized_pnl():
    row = _trade(fill_price=14.0, quantity=1, pnl=None)
    assert trade_cashflow(row) is None


def test_trade_cashflow_uses_realized_pnl():
    row = _trade(pnl=250.5, fill_price=14.0)
    assert trade_cashflow(row) == 250.5


def test_trade_cashflow_ignores_non_filled():
    row = _trade(status="submitted", fill_price=18.2, quantity=1, pnl=None)
    assert trade_cashflow(row) is None


def test_performance_summary_open_not_counted_as_loss():
    rows = [
        _trade(id="a", fill_price=14.0, quantity=1, pnl=None),
    ]

    class FakeScalars:
        def __init__(self, items):
            self._items = items

        def __iter__(self):
            return iter(self._items)

    class FakeSession:
        def scalars(self, _stmt):
            return FakeScalars(rows)

    summary = performance_summary(FakeSession(), "u1")  # type: ignore[arg-type]
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] == 0.0
    assert summary["mtd_pnl"] == 0.0
    assert summary["win_rate"] == 0.0
