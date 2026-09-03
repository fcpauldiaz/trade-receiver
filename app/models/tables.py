import uuid
from datetime import datetime

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


TzDateTime = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_mode: Mapped[str] = mapped_column(String(16), default="paper")
    max_contracts: Mapped[int] = mapped_column(Integer, default=1)
    allowed_tickers: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_filter_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sizing_mode: Mapped[str] = mapped_column(String(32), default="alert_inferred")
    fixed_contracts: Mapped[int] = mapped_column(Integer, default=1)
    risk_percent: Mapped[float] = mapped_column(Float, default=1.0)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    default_broker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", uselist=False)
    broker_connections: Mapped[list["BrokerConnection"]] = relationship(back_populates="user")
    devices: Mapped[list["UserDevice"]] = relationship(back_populates="user")
    inbound_webhooks: Mapped[list["InboundWebhook"]] = relationship(back_populates="user")
    alerts: Mapped[list["InboundAlert"]] = relationship(back_populates="user")
    trades: Mapped[list["TradeExecution"]] = relationship(back_populates="user")
    ai_evaluations: Mapped[list["AiEvaluation"]] = relationship(back_populates="user")
    review: Mapped["Review | None"] = relationship(back_populates="user", uselist=False)


class AuthSession(Base):
    __tablename__ = "session"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(TzDateTime)
    token: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class Account(Base):
    __tablename__ = "account"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text)
    provider_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())


class Verification(Base):
    __tablename__ = "verification"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    identifier: Mapped[str] = mapped_column(Text, index=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TzDateTime)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())


class Jwks(Base):
    __tablename__ = "jwks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    public_key: Mapped[str] = mapped_column(Text)
    private_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TzDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    author_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="review")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    variant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_name: Mapped[str] = mapped_column(String(64), default="free")
    status: Mapped[str] = mapped_column(String(32), default="none")
    renews_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="subscription")


class BrokerConnection(Base):
    __tablename__ = "broker_connections"
    __table_args__ = (
        Index("uq_broker_connections_user_broker", "user_id", "broker", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    broker: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="broker_connections")


class UserDevice(Base):
    __tablename__ = "user_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="devices")


class InboundWebhook(Base):
    __tablename__ = "inbound_webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="inbound_webhooks")


class InboundAlert(Base):
    __tablename__ = "inbound_alerts"
    __table_args__ = (
        Index("uq_inbound_alerts_user_idempotency", "user_id", "idempotency_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    inbound_webhook_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbound_webhooks.id"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    raw_payload: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    subscription_active: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="alerts")


class WebhookIngestEvent(Base):
    __tablename__ = "webhook_ingest_events"
    __table_args__ = (Index("ix_webhook_ingest_events_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    inbound_webhook_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inbound_webhooks.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32))
    request_payload: Mapped[str] = mapped_column(Text)
    alert_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbound_alerts.id"), nullable=True
    )
    trade_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())


class AiEvaluation(Base):
    __tablename__ = "ai_evaluations"
    __table_args__ = (
        Index("ix_ai_evaluations_created_at", "created_at"),
        Index("ix_ai_evaluations_user_created", "user_id", "created_at"),
        Index("ix_ai_evaluations_kind_created", "kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    alert_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbound_alerts.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="ai_evaluations")


class TradeExecution(Base):
    __tablename__ = "trade_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    alert_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbound_alerts.id"), nullable=True
    )
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
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="trades")
