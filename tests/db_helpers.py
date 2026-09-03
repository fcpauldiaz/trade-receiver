from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

BETTER_AUTH_CREATED_AT = datetime(2026, 8, 16, 4, 9, 2, 238000, tzinfo=timezone.utc)


def insert_better_auth_user(
    db: Session,
    *,
    user_id: str,
    email: str,
    name: str = "Ms",
    created_at: datetime = BETTER_AUTH_CREATED_AT,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO users (
              id, email, name, email_verified, created_at, updated_at,
              default_mode, max_contracts, live_trading_enabled, sizing_mode,
              fixed_contracts, risk_percent, onboarding_completed
            ) VALUES (
              :id, :email, :name, false, :created_at, :updated_at,
              'paper', 1, false, 'alert_inferred', 1, 1.0, false
            )
            """
        ),
        {
            "id": user_id,
            "email": email,
            "name": name,
            "created_at": created_at,
            "updated_at": created_at,
        },
    )
