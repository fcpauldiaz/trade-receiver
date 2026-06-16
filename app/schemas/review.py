from datetime import datetime

from pydantic import BaseModel, Field


class ReviewPublic(BaseModel):
    id: str
    author_name: str
    rating: int
    body: str
    created_at: datetime
    verified_customer: bool = True


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=2000)
