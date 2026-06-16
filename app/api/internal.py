from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.tables import Subscription, User
from app.services.jwt_auth import generate_api_key, hash_api_key

router = APIRouter(prefix="/v1/internal", tags=["internal"])


class ProvisionRequest(BaseModel):
    auth_id: str
    email: EmailStr
    name: str | None = None


class ProvisionResponse(BaseModel):
    user_id: str
    created: bool
    linked: bool


def _verify_internal_secret(x_internal_secret: str = Header(..., alias="X-Internal-Secret")) -> None:
    if not settings.internal_api_secret or x_internal_secret != settings.internal_api_secret:
        raise HTTPException(status_code=401, detail="Invalid internal secret")


@router.post("/provision", response_model=ProvisionResponse, dependencies=[Depends(_verify_internal_secret)])
def provision_user(body: ProvisionRequest, db: Session = Depends(get_db)):
    by_auth = db.query(User).filter(User.better_auth_id == body.auth_id).first()
    if by_auth:
        return ProvisionResponse(user_id=by_auth.id, created=False, linked=False)

    by_email = db.query(User).filter(User.email == body.email).first()
    if by_email:
        if by_email.better_auth_id and by_email.better_auth_id != body.auth_id:
            raise HTTPException(status_code=409, detail="Email linked to another auth account")
        by_email.better_auth_id = body.auth_id
        if body.name and not by_email.name:
            by_email.name = body.name
        db.commit()
        return ProvisionResponse(user_id=by_email.id, created=False, linked=True)

    user = User(
        email=body.email,
        name=body.name,
        better_auth_id=body.auth_id,
        api_key_hash=hash_api_key(generate_api_key()),
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db.commit()
    db.refresh(user)
    return ProvisionResponse(user_id=user.id, created=True, linked=False)
