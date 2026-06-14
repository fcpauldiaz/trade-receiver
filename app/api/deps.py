import hashlib

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tables import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth.removeprefix("Bearer ").strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = db.query(User).filter(User.api_key_hash == token_hash).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user
