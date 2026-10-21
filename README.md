# MoneyTool

Local budget app. Download CSVs from Wells Fargo / Chase / Capital One, upload them, and it builds a budget
from your real spending, analyzes where the money goes, and plans savings goals.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && ../.venv/bin/python -m app.main
```
Open http://127.0.0.1:8765 → Settings → add your accounts → upload CSVs (or **load demo data** to explore).

## Importing statements
Settings → add each account → upload its **PDF statement** (or CSV). You get a preview first: check that
"money out / money in" match your statement, flip the signs if they're backwards, drop bad rows, then Import.
Re-uploading overlapping statements is safe — duplicates are skipped.

- **PDFs** must be the text-based ones downloaded from the bank's site (not scans). Layouts vary by bank, so
  parsing is heuristic; that's why the preview step exists.
- **CSVs**: Chase, Capital One, Wells Fargo (headerless) and generic Date/Description/Amount files.

## Connect banks with Plaid (optional)
Instead of uploading statements, link accounts once and transactions sync automatically.
1. Sign up at dashboard.plaid.com and copy your client ID and secret (Developers → Keys).
2. Settings → **Connect banks with Plaid** → paste them (or set `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`).
   Start with **sandbox** (fake banks, log in with `user_good` / `pass_good`); switch to **production** for real
   accounts once Plaid approves your production access.
3. **Connect a bank** → log in through Plaid Link. Checking, savings and credit cards are added with up to two
   years of history; pending transactions are skipped until they post.

The app syncs on startup and every 4 hours while running (`PLAID_SYNC_HOURS`, 0 = off), plus **Sync now**. If a
bank asks you to log in again, its row shows the error and a **Reconnect** button.

## Ask AI (optional)
Ask AI page → paste a Gemini API key (free at aistudio.google.com/apikey), or set `GEMINI_API_KEY`. Questions are
answered by Gemini calling read-only tools on your local data, so only what a question needs leaves your machine.
Pick the model from the dropdown on that page (it lists the models your key can use; your choice is saved).
`GEMINI_MODEL` sets the default.

## Tests
`cd backend && ../.venv/bin/python -m pytest` (uses a temporary database)

## Notes
- Server binds to 127.0.0.1 only; data lives in `data/budget.db` (gitignored). No bank passwords
  are stored; Plaid keys (`data/plaid.json`) and Plaid access tokens (in the database) stay in `data/`.
- The frontend is plain JS + Chart.js served by FastAPI (no Node needed). 
