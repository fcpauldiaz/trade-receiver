from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.defaults import (
    AGENT_KEYS,
    AgentKey,
    REQUIRED_PLACEHOLDERS,
    default_system_prompt,
    default_user_prompt_template,
)
from app.config import settings
from app.models.tables import AgentConfig

CACHE_TTL_SECONDS = 30.0
MODEL_MAX_LEN = 200
PROMPT_MAX_LEN = 20_000

_cache: dict[str, tuple[float, "ResolvedAgentConfig"]] = {}


@dataclass(frozen=True)
class ResolvedAgentConfig:
    agent_key: AgentKey
    model: str
    system_prompt: str
    user_prompt_template: str
    model_overridden: bool
    system_prompt_overridden: bool
    user_prompt_template_overridden: bool
    updated_at: datetime | None
    updated_by: str | None


def invalidate_agent_config_cache(agent_key: str | None = None) -> None:
    if agent_key is None:
        _cache.clear()
        return
    _cache.pop(agent_key, None)


def render_user_prompt(template: str, **placeholders: str) -> str:
    result = template
    for key, value in placeholders.items():
        result = result.replace("{" + key + "}", value)
    return result


def validate_user_prompt_template(agent_key: AgentKey, template: str) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_PLACEHOLDERS[agent_key]:
        token = "{" + name + "}"
        if token not in template:
            errors.append(f"user_prompt_template must include {token}")
    return errors


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_from_row(agent_key: AgentKey, row: AgentConfig | None) -> ResolvedAgentConfig:
    model_raw = _blank_to_none(row.model if row else None)
    system_raw = _blank_to_none(row.system_prompt if row else None)
    user_raw = _blank_to_none(row.user_prompt_template if row else None)
    return ResolvedAgentConfig(
        agent_key=agent_key,
        model=model_raw or settings.ai_model,
        system_prompt=system_raw or default_system_prompt(agent_key),
        user_prompt_template=user_raw or default_user_prompt_template(agent_key),
        model_overridden=model_raw is not None,
        system_prompt_overridden=system_raw is not None,
        user_prompt_template_overridden=user_raw is not None,
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


def get_agent_config(db: Session | None, agent_key: AgentKey) -> ResolvedAgentConfig:
    now = time.monotonic()
    cached = _cache.get(agent_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    row: AgentConfig | None = None
    if db is not None:
        row = db.get(AgentConfig, agent_key)
    resolved = _resolve_from_row(agent_key, row)
    _cache[agent_key] = (now, resolved)
    return resolved


def list_agent_configs(db: Session) -> list[ResolvedAgentConfig]:
    return [get_agent_config(db, key) for key in AGENT_KEYS]


def serialize_agent_config(resolved: ResolvedAgentConfig) -> dict[str, Any]:
    return {
        "agent_key": resolved.agent_key,
        "model": resolved.model,
        "system_prompt": resolved.system_prompt,
        "user_prompt_template": resolved.user_prompt_template,
        "model_overridden": resolved.model_overridden,
        "system_prompt_overridden": resolved.system_prompt_overridden,
        "user_prompt_template_overridden": resolved.user_prompt_template_overridden,
        "default_model": settings.ai_model,
        "default_system_prompt": default_system_prompt(resolved.agent_key),
        "default_user_prompt_template": default_user_prompt_template(resolved.agent_key),
        "updated_at": resolved.updated_at,
        "updated_by": resolved.updated_by,
    }


def update_agent_config(
    db: Session,
    agent_key: AgentKey,
    *,
    model: str | None,
    system_prompt: str | None,
    user_prompt_template: str | None,
    updated_by: str | None,
    clear_model: bool = False,
    clear_system_prompt: bool = False,
    clear_user_prompt_template: bool = False,
) -> ResolvedAgentConfig:
    row = db.get(AgentConfig, agent_key)
    if row is None:
        row = AgentConfig(agent_key=agent_key)
        db.add(row)

    if clear_model:
        row.model = None
    elif model is not None:
        cleaned = model.strip()
        if not cleaned:
            row.model = None
        else:
            row.model = cleaned[:MODEL_MAX_LEN]

    if clear_system_prompt:
        row.system_prompt = None
    elif system_prompt is not None:
        cleaned = system_prompt.strip()
        row.system_prompt = cleaned or None

    if clear_user_prompt_template:
        row.user_prompt_template = None
    elif user_prompt_template is not None:
        cleaned = user_prompt_template.strip()
        row.user_prompt_template = cleaned or None

    row.updated_by = updated_by
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_agent_config_cache(agent_key)
    return _resolve_from_row(agent_key, row)


def reset_agent_config(
    db: Session,
    agent_key: AgentKey,
    *,
    updated_by: str | None,
) -> ResolvedAgentConfig:
    return update_agent_config(
        db,
        agent_key,
        model=None,
        system_prompt=None,
        user_prompt_template=None,
        updated_by=updated_by,
        clear_model=True,
        clear_system_prompt=True,
        clear_user_prompt_template=True,
    )
