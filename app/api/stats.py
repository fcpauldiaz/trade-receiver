from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tables import User

router = APIRouter(tags=["stats"])


class PublicStatsResponse(BaseModel):
    user_count: int


@router.get("/v1/stats/public", response_model=PublicStatsResponse)
def public_stats(db: Session = Depends(get_db)):
    return PublicStatsResponse(user_count=db.query(User).count())
