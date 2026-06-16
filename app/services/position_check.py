from app.brokers.base import BrokerAdapter


async def validate_close_position(adapter: BrokerAdapter, contract_symbol: str, quantity: int) -> str | None:
    positions = await adapter.get_positions()
    held = 0.0
    for pos in positions:
        if pos.get("symbol") == contract_symbol:
            held = float(pos.get("quantity") or 0)
            break
    if held <= 0:
        return f"no open position for {contract_symbol}"
    if quantity > int(held):
        return f"close quantity {quantity} exceeds position {int(held)}"
    return None
