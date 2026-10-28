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
Ask questions about your budget in plain English. Pick a **provider** and **model** on the Ask AI page (each
provider remembers its own model):

| Provider | Needs | Notes |
|---|---|---|
| Gemini | a free API key (aistudio.google.com/apikey, or set `GEMINI_API_KEY`) | model list comes from your key; `GEMINI_MODEL` sets the default |
| Claude | the [Claude Code](https://claude.com/claude-code) CLI, signed in (run `claude` once) | uses your Claude subscription, no API key; models: `fable`/`opus`/`sonnet`/`haiku` (always newest) or a pinned id |
| OpenAI | the Codex CLI, signed in (run `codex` once) | uses your ChatGPT subscription, no API key; models come from your Codex install |
| Gemini (Antigravity) | the Antigravity CLI `agy`, signed in (run `agy` once) | models from `agy models`; needs a one-time allow rule, see below |

Each model has an **Effort** dropdown where it applies: Claude offers low to max on every model, Codex shows the
levels each model supports (pick a specific model, not `default`), and Gemini offers low/medium/high thinking (sent
only if you choose one, and a model may reject a level). Antigravity builds the level into the model name, e.g.
`gemini-3.8-flash-high`, so it has no separate dropdown.

The AI never sees the database. It gets read-only tools (budget, spending, transactions, subscriptions, goals) and
only what a tool returns is sent to the provider you picked. The CLI providers run the official tools headless in an
empty temp directory with every built-in tool (shell, files, web) turned off, talking only to the read-only
tool server in `backend/app/mcp_server.py`. They can't use your Plaid keys or Gemini key (those are removed from
their environment).

**Antigravity setup and caveat.** Add this to `~/.gemini/antigravity-cli/settings.json` once so headless runs may call
the budget tools (everything else stays denied):
`{"permissions": {"allow": ["mcp(budget/*)"]}}`. Unlike the other two, Antigravity's web search can't be turned off
(it is on the CLI's built-in always-safe list), so it is told in the prompt not to use it, and any search it makes
is flagged under the answer. That is a request, not a lock: tested against a planted instruction in a transaction
name, four models ignored it, but that is not a guarantee. Use Claude or Codex if you want a hard guarantee.

## Tests
`cd backend && ../.venv/bin/python -m pytest` (uses a temporary database)

## Notes
- Server binds to 127.0.0.1 only; data lives in `data/budget.db` (gitignored). No bank passwords
  are stored; Plaid keys (`data/plaid.json`) and Plaid access tokens (in the database) stay in `data/`.
- The frontend is plain JS + Chart.js served by FastAPI (no Node needed). 
