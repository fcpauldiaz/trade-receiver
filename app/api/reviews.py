from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import Review, User
from app.schemas.review import ReviewCreate, ReviewPublic
from app.services.entitlements import can_process_trades

router = APIRouter(tags=["reviews"])

MAX_BODY_LENGTH = 2000


def _author_name(user: User) -> str:
    if user.name and user.name.strip():
        return user.name.strip()
    local = user.email.split("@", 1)[0]
    return local[:1].upper() + local[1:] if local else "Customer"


def _to_public(review: Review) -> ReviewPublic:
    return ReviewPublic(
        id=review.id,
        author_name=review.author_name,
        rating=review.rating,
        body=review.body,
        created_at=review.created_at,
        verified_customer=True,
    )


@router.get("/v1/reviews", response_model=list[ReviewPublic])
def list_reviews(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Review)
        .order_by(Review.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_to_public(r) for r in rows]


@router.get("/v1/me/review", response_model=ReviewPublic | None)
def get_my_review(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    review = db.query(Review).filter_by(user_id=user.id).first()
    if review is None:
        return None
    return _to_public(review)


@router.post("/v1/me/reviews", response_model=ReviewPublic)
def submit_review(
    body: ReviewCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required to leave a review")

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Review body cannot be empty")

    review = db.query(Review).filter_by(user_id=user.id).first()
    now = datetime.now(timezone.utc)
    if review is None:
        review = Review(
            user_id=user.id,
            rating=body.rating,
            body=text[:MAX_BODY_LENGTH],
            author_name=_author_name(user),
            updated_at=now,
        )
        db.add(review)
    else:
        review.rating = body.rating
        review.body = text[:MAX_BODY_LENGTH]
        review.author_name = _author_name(user)
        review.updated_at = now
    db.commit()
    db.refresh(review)
    return _to_public(review)


@router.delete("/v1/me/reviews")
def delete_my_review(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    review = db.query(Review).filter_by(user_id=user.id).first()
    if review:
        db.delete(review)
        db.commit()
    return {"status": "deleted"}
