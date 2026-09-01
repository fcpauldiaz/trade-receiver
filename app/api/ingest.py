from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import User
from app.services.ingest_gate import ingest_processing_slot
from app.services.ingest_pipeline import duplicate_response, process_inbound_alert

router = APIRouter(tags=["ingest"])


@router.post("/v1/ingest")
async def ingest_alert(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    async with ingest_processing_slot(user.id):
        try:
            return await process_inbound_alert(db, user, body)
        except IntegrityError:
            db.rollback()
            return duplicate_response(db, user, body)
