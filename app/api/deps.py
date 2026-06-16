import hashlib

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tables import User
from app.services.jwt_auth import hash_api_key, verify_better_auth_jwt


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        if token.count(".") == 2:
            try:
                claims = verify_better_auth_jwt(token)
            except jwt.PyJWTError:
                raise HTTPException(status_code=401, detail="Invalid token") from None
            user = db.query(User).filter(User.better_auth_id == claims.sub).first()
            if user is None and claims.email:
                user = db.query(User).filter(User.email == claims.email).first()
            if user is not None:
                return user
            raise HTTPException(status_code=401, detail="User not provisioned")
        token_hash = hash_api_key(token)
        user = db.query(User).filter(User.api_key_hash == token_hash).first()
        if user is not None:
            return user

    raise HTTPException(status_code=401, detail="Not authenticated")
