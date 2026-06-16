import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    better_auth_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True, nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_mode: Mapped[str] = mapped_column(String(16), default="paper")
    max_contracts: Mapped[int] = mapped_column(Integer, default=1)
    allowed_tickers: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sizing_mode: Mapped[str] = mapped_column(String(32), default="alert_inferred")
    fixed_contracts: Mapped[int] = mapped_column(Integer, default=1)
    risk_percent: Mapped[float] = mapped_column(Float, default=1.0)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    default_broker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", uselist=False)
    broker_connections: Mapped[list["BrokerConnection"]] = relationship(back_populates="user")
    alerts: Mapped[list["InboundAlert"]] = relationship(back_populates="user")
    trades: Mapped[list["TradeExecution"]] = relationship(back_populates="user")
    review: Mapped["Review | None"] = relationship(back_populates="user", uselist=False)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    author_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="review")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    lemon_squeezy_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lemon_squeezy_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    variant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_name: Mapped[str] = mapped_column(String(64), default="free")
    status: Mapped[str] = mapped_column(String(32), default="none")
    renews_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="subscription")


class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    broker: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="broker_connections")


class InboundAlert(Base):
    __tablename__ = "inbound_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    raw_payload: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    subscription_active: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="alerts")


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TradeExecution(Base):
    __tablename__ = "trade_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    alert_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("inbound_alerts.id"), nullable=True)
    broker: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    underlying: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str] = mapped_column(String(8))
    strike: Mapped[float] = mapped_column(Float)
    expiration: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    contract_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="trades")
