from __future__ import annotations

from typing import Literal

AgentKey = Literal["parse", "filter"]

AGENT_KEYS: tuple[AgentKey, ...] = ("parse", "filter")

PARSE_SYSTEM_PROMPT = "Return only valid JSON matching the trade intent schema."

PARSE_USER_PROMPT_TEMPLATE = (
    "Parse this trade alert into structured JSON. "
    "Use asset_class=future for futures symbols like ES, MES, NQ, MNQ, YM, RTY with BUY/SELL. "
    "Use asset_class=option for options alerts with strikes and expirations. "
    "Use action skip if not a trade alert. "
    "Include quantity; default to 1 if not specified. "
    "For futures, map BUY to buy_to_open and SELL to sell_to_close.\n\n"
    "{alert_text}"
)

FILTER_SYSTEM_PROMPT = (
    "Follow the user's trading rules and return only JSON. "
    "Set take=true to execute the trade as parsed. "
    "Set take=false to skip it. "
    "Do not invent fills or change strike, quantity, or side."
)

FILTER_USER_PROMPT_TEMPLATE = (
    "User rules:\n"
    "{user_rules}\n\n"
    "Parsed trade intent (do not change strike, quantity, or side; only decide take or skip):\n"
    "{intent_json}"
)

REQUIRED_PLACEHOLDERS: dict[AgentKey, tuple[str, ...]] = {
    "parse": ("alert_text",),
    "filter": ("user_rules", "intent_json"),
}


def default_system_prompt(agent_key: AgentKey) -> str:
    if agent_key == "parse":
        return PARSE_SYSTEM_PROMPT
    return FILTER_SYSTEM_PROMPT


def default_user_prompt_template(agent_key: AgentKey) -> str:
    if agent_key == "parse":
        return PARSE_USER_PROMPT_TEMPLATE
    return FILTER_USER_PROMPT_TEMPLATE
