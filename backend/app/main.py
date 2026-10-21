import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from . import baseline, config, demo
from .services import categorize, plaid
from .db import engine, get_session, init_db
from .routes import accounts, ai, budget, data, goals
from .routes import plaid as plaid_routes

demo_router = APIRouter(prefix="/api")
SYNC_HOURS = float(os.getenv("PLAID_SYNC_HOURS", "4"))  # 0 turns off background syncing


def _sync_once():
    if not plaid.credentials():
        return  # keys may be added from Settings later; the next round picks them up
    with Session(engine) as session:
        plaid.sync_all(session)  # per-bank errors are recorded on the item and shown in Settings


async def _sync_forever():
    while True:
        with suppress(Exception):
            await asyncio.to_thread(_sync_once)
        await asyncio.sleep(SYNC_HOURS * 3600)


@demo_router.post("/demo/seed")
def seed_demo(session: Session = Depends(get_session)):
    return {"transactions": demo.seed(session)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        baseline.autoload(session)
        categorize.apply_all(session)  # picks up categorization changes for existing data
    task = asyncio.create_task(_sync_forever()) if SYNC_HOURS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="MoneyTool", lifespan=lifespan)
for r in (accounts.router, ai.router, data.router, budget.router, goals.router, plaid_routes.router, demo_router):
    app.include_router(r)


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(__import__("os").getenv("PORT", "8765")))
