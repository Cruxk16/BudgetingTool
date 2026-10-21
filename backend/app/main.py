from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from . import baseline, config, demo
from .services import categorize
from .db import engine, get_session, init_db
from .routes import accounts, ai, budget, data, goals

demo_router = APIRouter(prefix="/api")


@demo_router.post("/demo/seed")
def seed_demo(session: Session = Depends(get_session)):
    return {"transactions": demo.seed(session)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        baseline.autoload(session)
        categorize.apply_all(session)  # picks up categorization changes for existing data
    yield


app = FastAPI(title="MoneyTool", lifespan=lifespan)
for r in (accounts.router, ai.router, data.router, budget.router, goals.router, demo_router):
    app.include_router(r)


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(__import__("os").getenv("PORT", "8765")))
