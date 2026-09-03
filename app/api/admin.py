from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.agents.defaults import AGENT_KEYS, AgentKey
from app.database import get_db
from app.models.tables import AiEvaluation, InboundAlert, Subscription, User
from app.services.agent_config import (
    MODEL_MAX_LEN,
    PROMPT_MAX_LEN,
    list_agent_configs,
    reset_agent_config,
    serialize_agent_config,
    update_agent_config,
    validate_user_prompt_template,
)
from app.services.alert_audit import AlertAuditItem, list_alert_audit_admin
from app.services.entitlements import can_process_trades, grant_subscription
from app.services.jwt_auth import verify_better_auth_jwt
import jwt

router = APIRouter(prefix="/v1/admin", tags=["admin"])

ADMIN_ROLE = "admin"


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth.removeprefix("Bearer ").strip()
    if token.count(".") != 2:
        raise HTTPException(status_code=401, detail="Admin requires Better Auth JWT")
    try:
        claims = verify_better_auth_jwt(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    user = db.get(User, claims.sub)
    if user is None and claims.email:
        user = db.query(User).filter(User.email == claims.email).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not provisioned")
    if user.role != ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class AdminOverview(BaseModel):
    user_count: int
    active_subscription_count: int
    alerts_today: int
    ai_calls_today: int
    tokens_today: int
    tokens_mtd: int
    cost_usd_today: float
    cost_usd_mtd: float
    latest_evaluations: list[AdminAiEvaluation]


class AdminAiEvaluation(BaseModel):
    id: str
    created_at: datetime
    user_id: str
    user_email: str
    alert_id: str | None
    kind: str
    decision: str
    rationale: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    generation_id: str | None = None
    output_json: str | None = None


class AdminAiEvaluationPage(BaseModel):
    items: list[AdminAiEvaluation]
    total: int
    cost_usd_sum: float
    limit: int
    offset: int


class AdminUser(BaseModel):
    id: str
    email: str
    name: str | None
    created_at: datetime
    plan_name: str
    status: str
    can_process_trades: bool
    role: str


class AdminSubscriptionUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=32)
    plan_name: str = Field(default="pro", min_length=1, max_length=64)


class AdminSubscriptionResult(BaseModel):
    user_id: str
    status: str
    plan_name: str
    can_process_trades: bool


class AdminRoleUpdate(BaseModel):
    role: Literal["user", "admin"]


class AdminRoleResult(BaseModel):
    user_id: str
    role: str


class AdminAgentConfig(BaseModel):
    agent_key: AgentKey
    model: str
    system_prompt: str
    user_prompt_template: str
    model_overridden: bool
    system_prompt_overridden: bool
    user_prompt_template_overridden: bool
    default_model: str
    default_system_prompt: str
    default_user_prompt_template: str
    updated_at: datetime | None = None
    updated_by: str | None = None


class AdminAgentUpdate(BaseModel):
    model: str | None = Field(default=None, max_length=MODEL_MAX_LEN)
    system_prompt: str | None = Field(default=None, max_length=PROMPT_MAX_LEN)
    user_prompt_template: str | None = Field(default=None, max_length=PROMPT_MAX_LEN)
    reset: bool = False


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_month_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _cost_float(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _serialize_evaluation(row: AiEvaluation, email: str) -> AdminAiEvaluation:
    return AdminAiEvaluation(
        id=row.id,
        created_at=row.created_at,
        user_id=row.user_id,
        user_email=email,
        alert_id=row.alert_id,
        kind=row.kind,
        decision=row.decision,
        rationale=row.rationale,
        model=row.model,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        cost_usd=_cost_float(row.cost_usd) if row.cost_usd is not None else None,
        latency_ms=row.latency_ms,
        generation_id=row.generation_id,
        output_json=row.output_json,
    )


@router.get("/overview", response_model=AdminOverview)
def admin_overview(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    today = _start_of_today_utc()
    month = _start_of_month_utc()

    user_count = db.query(func.count(User.id)).scalar() or 0
    active_subscription_count = (
        db.query(func.count(Subscription.id))
        .filter(Subscription.status.in_(["active", "trialing"]))
        .scalar()
        or 0
    )
    alerts_today = (
        db.query(func.count(InboundAlert.id)).filter(InboundAlert.created_at >= today).scalar() or 0
    )
    ai_calls_today = (
        db.query(func.count(AiEvaluation.id)).filter(AiEvaluation.created_at >= today).scalar() or 0
    )

    tokens_today = (
        db.query(func.coalesce(func.sum(AiEvaluation.total_tokens), 0))
        .filter(AiEvaluation.created_at >= today)
        .scalar()
        or 0
    )
    tokens_mtd = (
        db.query(func.coalesce(func.sum(AiEvaluation.total_tokens), 0))
        .filter(AiEvaluation.created_at >= month)
        .scalar()
        or 0
    )
    cost_today = (
        db.query(func.coalesce(func.sum(AiEvaluation.cost_usd), 0))
        .filter(AiEvaluation.created_at >= today)
        .scalar()
        or 0
    )
    cost_mtd = (
        db.query(func.coalesce(func.sum(AiEvaluation.cost_usd), 0))
        .filter(AiEvaluation.created_at >= month)
        .scalar()
        or 0
    )

    latest_rows = (
        db.query(AiEvaluation, User.email)
        .join(User, AiEvaluation.user_id == User.id)
        .order_by(AiEvaluation.created_at.desc())
        .limit(8)
        .all()
    )
    latest = [_serialize_evaluation(row, email) for row, email in latest_rows]

    return AdminOverview(
        user_count=int(user_count),
        active_subscription_count=int(active_subscription_count),
        alerts_today=int(alerts_today),
        ai_calls_today=int(ai_calls_today),
        tokens_today=int(tokens_today),
        tokens_mtd=int(tokens_mtd),
        cost_usd_today=_cost_float(cost_today),
        cost_usd_mtd=_cost_float(cost_mtd),
        latest_evaluations=latest,
    )


@router.get("/ai-evaluations", response_model=AdminAiEvaluationPage)
def admin_ai_evaluations(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    kind: Literal["parse", "filter"] | None = None,
    decision: Literal["take", "skip", "error"] | None = None,
    email: str | None = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    q = db.query(AiEvaluation, User.email).join(User, AiEvaluation.user_id == User.id)
    if kind:
        q = q.filter(AiEvaluation.kind == kind)
    if decision:
        q = q.filter(AiEvaluation.decision == decision)
    if email:
        q = q.filter(User.email.ilike(f"%{email.strip()}%"))
    if from_dt is not None:
        q = q.filter(AiEvaluation.created_at >= from_dt)
    if to_dt is not None:
        q = q.filter(AiEvaluation.created_at <= to_dt)

    total = q.count()
    cost_sum_q = db.query(func.coalesce(func.sum(AiEvaluation.cost_usd), 0)).select_from(AiEvaluation).join(
        User, AiEvaluation.user_id == User.id
    )
    if kind:
        cost_sum_q = cost_sum_q.filter(AiEvaluation.kind == kind)
    if decision:
        cost_sum_q = cost_sum_q.filter(AiEvaluation.decision == decision)
    if email:
        cost_sum_q = cost_sum_q.filter(User.email.ilike(f"%{email.strip()}%"))
    if from_dt is not None:
        cost_sum_q = cost_sum_q.filter(AiEvaluation.created_at >= from_dt)
    if to_dt is not None:
        cost_sum_q = cost_sum_q.filter(AiEvaluation.created_at <= to_dt)
    cost_sum = cost_sum_q.scalar() or 0
    rows = (
        q.order_by(AiEvaluation.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AdminAiEvaluationPage(
        items=[_serialize_evaluation(row, email) for row, email in rows],
        total=int(total),
        cost_usd_sum=_cost_float(cost_sum),
        limit=limit,
        offset=offset,
    )


@router.get("/users", response_model=list[AdminUser])
def admin_users(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    email: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    q = db.query(User).options(joinedload(User.subscription)).order_by(User.created_at.desc())
    if email:
        q = q.filter(User.email.ilike(f"%{email.strip()}%"))
    users = q.limit(limit).all()
    items: list[AdminUser] = []
    for user in users:
        sub = user.subscription
        items.append(
            AdminUser(
                id=user.id,
                email=user.email,
                name=user.name,
                created_at=user.created_at,
                plan_name=sub.plan_name if sub else "free",
                status=sub.status if sub else "none",
                can_process_trades=can_process_trades(user),
                role=user.role or "user",
            )
        )
    return items


@router.post("/users/{user_id}/subscription", response_model=AdminSubscriptionResult)
def admin_update_subscription(
    user_id: str,
    body: AdminSubscriptionUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    sub = grant_subscription(
        db,
        user,
        status=body.status,
        plan_name=body.plan_name,
        revoke_device=body.status not in {"active", "trialing"},
    )
    db.refresh(user)
    return AdminSubscriptionResult(
        user_id=user.id,
        status=sub.status,
        plan_name=sub.plan_name,
        can_process_trades=can_process_trades(user),
    )


@router.post("/users/{user_id}/role", response_model=AdminRoleResult)
def admin_update_role(
    user_id: str,
    body: AdminRoleUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and body.role != ADMIN_ROLE:
        raise HTTPException(status_code=400, detail="Cannot remove your own admin role")
    user.role = body.role
    db.commit()
    db.refresh(user)
    return AdminRoleResult(user_id=user.id, role=user.role)


@router.get("/alerts", response_model=list[AlertAuditItem])
def admin_alerts(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    email: str | None = None,
    outcome: Literal["executed", "skipped", "pending"] | None = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=500),
):
    return list_alert_audit_admin(
        db,
        limit=limit,
        email=email,
        outcome=outcome,
        from_dt=from_dt,
        to_dt=to_dt,
    )


@router.get("/agents", response_model=list[AdminAgentConfig])
def admin_list_agents(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [AdminAgentConfig.model_validate(serialize_agent_config(item)) for item in list_agent_configs(db)]


@router.put("/agents/{agent_key}", response_model=AdminAgentConfig)
def admin_update_agent(
    agent_key: str,
    body: AdminAgentUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=404, detail="Unknown agent")
    key: AgentKey = agent_key  # type: ignore[assignment]

    if body.reset:
        resolved = reset_agent_config(db, key, updated_by=admin.id)
        return AdminAgentConfig.model_validate(serialize_agent_config(resolved))

    fields_set = body.model_fields_set
    update_model = "model" in fields_set
    update_system = "system_prompt" in fields_set
    update_user = "user_prompt_template" in fields_set

    if not (update_model or update_system or update_user):
        raise HTTPException(status_code=400, detail="No fields to update")

    template = body.user_prompt_template if update_user else None
    if template is not None and template.strip():
        errors = validate_user_prompt_template(key, template)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))

    clear_model = update_model and (body.model is None or not body.model.strip())
    clear_system = update_system and (body.system_prompt is None or not body.system_prompt.strip())
    clear_user = update_user and (
        body.user_prompt_template is None or not body.user_prompt_template.strip()
    )

    resolved = update_agent_config(
        db,
        key,
        model=body.model if update_model and not clear_model else None,
        system_prompt=body.system_prompt if update_system and not clear_system else None,
        user_prompt_template=(
            body.user_prompt_template if update_user and not clear_user else None
        ),
        updated_by=admin.id,
        clear_model=clear_model,
        clear_system_prompt=clear_system,
        clear_user_prompt_template=clear_user,
    )
    return AdminAgentConfig.model_validate(serialize_agent_config(resolved))
