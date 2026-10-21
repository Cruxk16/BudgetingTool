import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'budget.db'}")
FRONTEND_DIR = ROOT / "frontend"
BASELINE_FILE = Path(os.getenv("BASELINE_FILE", DATA_DIR / "baseline.json"))
