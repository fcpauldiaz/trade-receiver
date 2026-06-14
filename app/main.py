from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import billing, brokers, settings, trades, users, webhooks
from app.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Trade Receiver", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router)
app.include_router(users.router)
app.include_router(billing.router)
app.include_router(brokers.router)
app.include_router(settings.router)
app.include_router(trades.router)


@app.get("/health")
def health():
    return {"status": "ok"}
