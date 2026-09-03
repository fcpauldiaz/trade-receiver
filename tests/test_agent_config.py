from app.agents.defaults import PARSE_SYSTEM_PROMPT, PARSE_USER_PROMPT_TEMPLATE
from app.config import settings
from app.models.tables import AgentConfig
from app.services.agent_config import (
    CACHE_TTL_SECONDS,
    get_agent_config,
    invalidate_agent_config_cache,
    render_user_prompt,
    update_agent_config,
    validate_user_prompt_template,
)


def test_resolve_falls_back_to_defaults(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ai_model", "openai/gpt-4o-mini")
    invalidate_agent_config_cache()
    resolved = get_agent_config(db_session, "parse")
    assert resolved.model == "openai/gpt-4o-mini"
    assert resolved.system_prompt == PARSE_SYSTEM_PROMPT
    assert resolved.user_prompt_template == PARSE_USER_PROMPT_TEMPLATE
    assert resolved.model_overridden is False
    assert resolved.system_prompt_overridden is False
    assert resolved.user_prompt_template_overridden is False


def test_resolve_uses_overrides_and_invalidates_cache(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ai_model", "openai/gpt-4o-mini")
    invalidate_agent_config_cache()
    update_agent_config(
        db_session,
        "parse",
        model="openai/gpt-4o",
        system_prompt="custom system",
        user_prompt_template="Alert: {alert_text}",
        updated_by="admin-1",
    )
    resolved = get_agent_config(db_session, "parse")
    assert resolved.model == "openai/gpt-4o"
    assert resolved.system_prompt == "custom system"
    assert "{alert_text}" in resolved.user_prompt_template
    assert resolved.model_overridden is True

    db_session.query(AgentConfig).filter(AgentConfig.agent_key == "parse").update(
        {"model": "openai/gpt-4.1-mini"}
    )
    db_session.commit()
    cached = get_agent_config(db_session, "parse")
    assert cached.model == "openai/gpt-4o"

    invalidate_agent_config_cache("parse")
    fresh = get_agent_config(db_session, "parse")
    assert fresh.model == "openai/gpt-4.1-mini"
    assert CACHE_TTL_SECONDS > 0


def test_validate_and_render_placeholders():
    assert validate_user_prompt_template("parse", "no placeholder") == [
        "user_prompt_template must include {alert_text}"
    ]
    assert validate_user_prompt_template("filter", "{user_rules}") == [
        "user_prompt_template must include {intent_json}"
    ]
    rendered = render_user_prompt("A {alert_text} Z", alert_text="BTO SPY")
    assert rendered == "A BTO SPY Z"
