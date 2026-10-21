import os
import tempfile

# Must run before app.config is imported so tests never touch the real database.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["BASELINE_FILE"] = str(__import__("pathlib").Path(tempfile.mkdtemp()) / "none.json")
