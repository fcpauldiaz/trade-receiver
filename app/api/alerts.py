from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import User
from app.services.alert_audit import AlertAuditItem, list_alert_audit

router = APIRouter(prefix="/v1/me", tags=["alerts"])


@router.get("/alerts", response_model=list[AlertAuditItem])
def get_alerts(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_alert_audit(db, user.id, limit=limit)
