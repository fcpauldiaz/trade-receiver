from sqlalchemy import text
from sqlalchemy.orm import Session

BETTER_AUTH_CREATED_AT_MS = 1786843742238


def insert_better_auth_user(
    db: Session,
    *,
    user_id: str,
    email: str,
    name: str = "Ms",
    created_at_ms: int = BETTER_AUTH_CREATED_AT_MS,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO users (
              id, email, name, email_verified, created_at, updated_at,
              default_mode, max_contracts, live_trading_enabled, sizing_mode,
              fixed_contracts, risk_percent, onboarding_completed
            ) VALUES (
              :id, :email, :name, 0, :created_at, :updated_at,
              'paper', 1, 0, 'alert_inferred', 1, 1.0, 0
            )
            """
        ),
        {
            "id": user_id,
            "email": email,
            "name": name,
            "created_at": created_at_ms,
            "updated_at": created_at_ms,
        },
    )
