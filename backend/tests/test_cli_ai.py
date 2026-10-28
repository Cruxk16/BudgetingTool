import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app import mcp_server
from app.db import Account, Transaction, engine
from app.main import app
from app.services import ai, cli_ai
from datetime import date


@pytest.fixture()
def client(tmp_path, monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ai, "KEY_FILE", tmp_path / "gemini.key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli_ai, "_codex_catalog", {"gpt-x": {"label": "GPT X", "levels": ["low", "high"], "default": "low"}})
    monkeypatch.setattr(cli_ai, "executable", lambda p: f"/fake/{p}")
    monkeypatch.setattr(cli_ai, "agy_ready", lambda: True)
    monkeypatch.setattr(cli_ai, "_agy_catalog", {"gemini-x": "Gemini X (High)", "claude-y": "Claude Y"})
    with Session(engine) as s:
        a = Account(institution="WF", name="Checking", type="checking", role="income_bills")
        s.add(a); s.commit(); s.refresh(a)
        s.add(Transaction(account_id=a.id, dedupe_key="1", date=date.today(), amount=42.5, name="TARGET T-1",
                          category="Shopping"))
        s.commit()
    with TestClient(app) as c:
        yield c


# ---------- MCP server ----------

def test_mcp_serves_exactly_the_read_only_budget_tools(client):
    init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["capabilities"] == {"tools": {}} and init["result"]["protocolVersion"] == "2025-06-18"
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == [d["name"] for d in ai.DECLS]
    assert all(t["inputSchema"]["type"] == "object" and t["annotations"]["readOnlyHint"] for t in tools)
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"] == -32601


def test_mcp_tool_calls(client):
    ok = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "search_transactions", "arguments": {"text": "target"}}})["result"]
    assert not ok["isError"] and json.loads(ok["content"][0]["text"])["matches"] == 1
    bad = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "drop_tables", "arguments": {}}})["result"]
    assert bad["isError"]


# ---------- locked-down command lines ----------

def test_claude_gets_no_builtin_tools_and_only_our_server():
    cmd = cli_ai.claude_command("/fake/claude", "sonnet", "SYSTEM")
    assert cmd[cmd.index("--tools") + 1] == ""  # every built-in tool (shell, files, web) off
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--allowedTools") + 1] == "mcp__budget"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--system-prompt") + 1] == "SYSTEM"
    server = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["budget"]
    assert server["args"] == ["-m", "app.mcp_server"] and "DATABASE_URL" in server["env"]


def test_codex_is_sandboxed_and_stripped():
    cmd = cli_ai.codex_command("/fake/codex", "gpt-x", "/tmp/empty")
    assert cmd[cmd.index("-s") + 1] == "read-only" and cmd[cmd.index("-C") + 1] == "/tmp/empty"
    assert cmd[cmd.index("-m") + 1] == "gpt-x" and cmd[-1] == "-"  # prompt comes over stdin
    off = {cmd[i + 1] for i, c in enumerate(cmd) if c == "--disable"}
    assert {"shell_tool", "unified_exec", "multi_agent", "plugins", "computer_use"} <= off
    assert "code_mode_host" not in off  # MCP tools are only reachable through it
    assert 'web_search="disabled"' in cmd and "--ephemeral" in cmd and "--ignore-user-config" in cmd
    env_arg = next(c for c in cmd if c.startswith("mcp_servers.budget.env="))
    assert "PYTHONPATH" in env_arg and "DATABASE_URL" in env_arg
    assert "-m" not in cli_ai.codex_command("/fake/codex", "default", "/tmp/empty")


def test_secrets_are_not_passed_to_clis(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("PLAID_SECRET", "p")
    assert not {"GEMINI_API_KEY", "PLAID_SECRET"} & set(cli_ai._env())


# ---------- running them ----------

def stream(*events):
    return "\n".join(json.dumps(e) for e in events)


def test_claude_answer_and_tools_parsed(monkeypatch):
    out = stream({"type": "system", "subtype": "init"},
                 {"type": "assistant", "message": {"content": [
                     {"type": "tool_use", "name": "mcp__budget__monthly_trend", "input": {}}]}},
                 {"type": "result", "subtype": "success", "is_error": False, "result": "You spent $42.50."})
    seen = {}
    monkeypatch.setattr(cli_ai, "_run", lambda cmd, stdin, cwd, provider: seen.update(stdin=stdin, cwd=cwd) or out)
    r = cli_ai.ask("claude", "SYS", "How much?", [{"role": "user", "text": "hi"}, {"role": "ai", "text": "hello"}], "opus")
    assert r == {"answer": "You spent $42.50.", "tools_used": ["monthly_trend"], "model": "opus"}
    assert "User: hi" in seen["stdin"] and "Assistant: hello" in seen["stdin"] and seen["stdin"].endswith("How much?")


def test_codex_answer_and_tools_parsed(monkeypatch):
    out = stream({"type": "thread.started"},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "Looking."}},
                 {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "budget", "tool": "get_goals"}},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "You have 2 goals."}})
    seen = {}
    monkeypatch.setattr(cli_ai, "_run", lambda cmd, stdin, cwd, provider: seen.update(stdin=stdin) or out)
    r = cli_ai.ask("codex", "SYS", "Goals?", [], "default")
    assert r["answer"] == "You have 2 goals." and r["tools_used"] == ["get_goals"]
    assert seen["stdin"].startswith("SYS")  # Codex has no system-prompt flag, so it leads the prompt


def test_cli_errors_are_readable(monkeypatch):
    monkeypatch.setattr(cli_ai, "_run", lambda *a: stream(
        {"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}))
    with pytest.raises(ai.AIError) as e:
        cli_ai.ask("claude", "SYS", "hi", [], "sonnet")
    assert "Not logged in" in str(e.value) and "Run `claude` in a terminal" in str(e.value)
    monkeypatch.setattr(cli_ai, "_run", lambda *a: stream({"type": "error", "message": "401 unauthorized"}))
    with pytest.raises(ai.AIError) as e:
        cli_ai.ask("codex", "SYS", "hi", [], "default")
    assert "401" in str(e.value) and "Run `codex`" in str(e.value)


def test_missing_cli_is_a_clear_400(client, monkeypatch):
    monkeypatch.setattr(cli_ai, "executable", lambda p: None)
    client.put("/api/ai/provider", json={"provider": "claude"})
    r = client.post("/api/ai/ask", json={"question": "hi"})
    assert r.status_code == 400 and "isn't installed" in r.json()["detail"]


# ---------- provider + model selection ----------

def test_provider_and_per_provider_models(client, monkeypatch):
    st = client.get("/api/ai/status").json()
    assert st["provider"] == "gemini" and not st["configured"]
    assert [p["id"] for p in st["providers"]] == ["gemini", "claude", "codex", "antigravity"]
    assert [p["available"] for p in st["providers"]] == [False, True, True, True]

    st = client.put("/api/ai/provider", json={"provider": "claude"}).json()
    assert st["provider"] == "claude" and st["configured"] and st["model"] == "sonnet"
    m = client.get("/api/ai/models").json()
    assert m["provider"] == "claude" and m["current"] == "sonnet" and {"opus", "haiku", "fable"} <= set(m["models"])
    assert client.put("/api/ai/model", json={"model": "opus"}).json()["model"] == "opus"

    st = client.put("/api/ai/provider", json={"provider": "codex"}).json()
    assert st["model"] == "default" and client.get("/api/ai/models").json()["models"] == ["default", "gpt-x"]
    client.put("/api/ai/model", json={"model": "gpt-x"})
    # each provider remembers its own model; Gemini's is untouched
    assert client.get("/api/ai/models?provider=claude").json()["current"] == "opus"
    assert client.get("/api/ai/models?provider=gemini").json()["current"] == ai.DEFAULT_MODEL

    seen = []
    monkeypatch.setattr(cli_ai, "_run", lambda cmd, stdin, cwd, provider: seen.append(cmd) or stream(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}))
    r = client.post("/api/ai/ask", json={"question": "hi"}).json()
    assert r["provider"] == "codex" and r["model"] == "gpt-x" and "gpt-x" in seen[0]


def test_unknown_provider_rejected(client):
    assert client.put("/api/ai/provider", json={"provider": "grok"}).status_code == 400
    assert client.get("/api/ai/models?provider=grok").status_code == 400
    assert client.put("/api/ai/model", json={"model": "x", "provider": "grok"}).status_code == 400


# ---------- Antigravity ----------

def agy_stream(*steps, response="ok"):
    events = [{"event": "init"}]
    for name, params in steps:
        events.append({"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": name,
                                                              "tool_info": {"name": name, "parameters": params}}})
        events.append({"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE", "tool_name": name}})
    events.append({"event": "result", "result": {"status": "SUCCESS", "response": response}})
    return stream(*events)


def test_agy_command_never_skips_permissions():
    cmd = cli_ai.agy_command("/fake/agy", "gemini-x", "PROMPT")
    assert cmd[:3] == ["/fake/agy", "-p", "PROMPT"] and cmd[cmd.index("--model") + 1] == "gemini-x"
    assert "--dangerously-skip-permissions" not in cmd and "--output-format" in cmd


def test_agy_answer_tools_and_web_search_warning(monkeypatch):
    out = agy_stream(("read_file", {"Path": "/x/.gemini/antigravity-cli/mcp/budget/get_goals.json"}),
                     ("call_mcp_tool", {"ServerName": "budget", "ToolName": "get_goals", "Arguments": {}}),
                     ("search_web", {"query": "leak test"}), response="Two goals.")
    seen = {}

    def fake_run(cmd, stdin, cwd, provider):
        cfg = json.loads((cli_ai.Path(cwd) / ".agents" / "mcp_config.json").read_text())
        seen.update(cmd=cmd, server=cfg["mcpServers"]["budget"])
        return out

    monkeypatch.setattr(cli_ai, "_run", fake_run)
    r = cli_ai.ask("antigravity", "SYS", "Goals?", [{"role": "user", "text": "hi"}], "gemini-x")
    assert r["answer"] == "Two goals." and r["tools_used"] == ["get_goals"]
    assert r["warnings"] == ["Antigravity ran a web search: “leak test”"]
    assert seen["server"]["args"] == ["-m", "app.mcp_server"]
    prompt = seen["cmd"][2]
    assert prompt.startswith("SYS") and "Do not use web search" in prompt and "User: hi" in prompt


def test_agy_needs_the_one_time_allow_rule(client, monkeypatch):
    monkeypatch.setattr(cli_ai, "agy_ready", lambda: False)
    ag = next(p for p in client.get("/api/ai/status").json()["providers"] if p["id"] == "antigravity")
    assert not ag["available"] and "mcp(budget/*)" in ag["note"]
    client.put("/api/ai/provider", json={"provider": "antigravity"})
    r = client.post("/api/ai/ask", json={"question": "hi"})
    assert r.status_code == 400 and "mcp(budget/*)" in r.json()["detail"]


def test_agy_denied_tool_is_explained(monkeypatch):
    monkeypatch.setattr(cli_ai, "_run", lambda *a: 'jetski: no output produced — a tool required the "read_file" permission '
                        "that headless mode cannot prompt for, so it was auto-denied.\n" + stream(
                            {"event": "result", "result": {"status": "SUCCESS", "response": ""}}))
    with pytest.raises(ai.AIError) as e:
        cli_ai.ask("antigravity", "SYS", "hi", [], "gemini-x")
    assert "isn't allowed" in str(e.value)


def test_agy_catalog_and_ready_check(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_ai, "_agy_catalog", None)
    monkeypatch.setattr(cli_ai.subprocess, "run", lambda *a, **k: type("R", (), {
        "stdout": "gemini-a\tGemini A (High)\nclaude-b\tClaude B\nFetching available models...\n"})())
    assert cli_ai.agy_catalog() == {"gemini-a": "Gemini A (High)", "claude-b": "Claude B"}
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli_ai, "AGY_SETTINGS", settings)
    assert not cli_ai.agy_ready()
    settings.write_text(json.dumps({"permissions": {"allow": ["mcp(budget/*)"]}}))
    assert cli_ai.agy_ready()
    settings.write_text(json.dumps({"permissions": {"allow": ["mcp(*)"]}}))
    assert not cli_ai.agy_ready()  # we only trust the narrow rule


def test_signed_out_agy_is_not_cached(monkeypatch):
    monkeypatch.setattr(cli_ai, "_agy_catalog", None)
    monkeypatch.setattr(cli_ai.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "Error: Please sign in\n"})())
    assert list(cli_ai.agy_catalog()) == [cli_ai.DEFAULT_MODEL["antigravity"]] and cli_ai._agy_catalog is None


# ---------- effort levels ----------

def test_effort_levels_per_provider(client, monkeypatch):
    # Claude: the same five levels on every model, sent as --effort
    client.put("/api/ai/provider", json={"provider": "claude"})
    m = client.get("/api/ai/models").json()
    assert m["efforts"] == ["low", "medium", "high", "xhigh", "max"] and m["effort"] == "default"
    assert "opus[1m]" in m["models"] and m["labels"]["sonnet"] == "Sonnet (latest)"
    assert client.put("/api/ai/effort", json={"effort": "high"}).json()["effort"] == "high"
    assert client.put("/api/ai/effort", json={"effort": "turbo"}).status_code == 400
    seen = []
    monkeypatch.setattr(cli_ai, "_run", lambda cmd, stdin, cwd, provider: seen.append(cmd) or stream(
        {"type": "result", "is_error": False, "result": "ok"}))
    r = client.post("/api/ai/ask", json={"question": "hi"}).json()
    assert r["effort"] == "high" and seen[0][seen[0].index("--effort") + 1] == "high"
    client.put("/api/ai/effort", json={"effort": "default"})
    client.post("/api/ai/ask", json={"question": "hi"})
    assert "--effort" not in seen[1]

    # Codex: levels come from the model's catalog entry, sent as model_reasoning_effort
    client.put("/api/ai/provider", json={"provider": "codex"})
    assert client.get("/api/ai/models").json()["efforts"] == []  # "default" model: nothing to choose from
    client.put("/api/ai/model", json={"model": "gpt-x"})
    m = client.get("/api/ai/models").json()
    assert m["efforts"] == ["low", "high"] and m["default_effort"] == "low" and m["labels"]["gpt-x"] == "GPT X"
    assert client.put("/api/ai/effort", json={"effort": "max"}).status_code == 400  # not offered by this model
    client.put("/api/ai/effort", json={"effort": "high"})
    assert 'model_reasoning_effort="high"' in cli_ai.codex_command("/fake/codex", "gpt-x", "/tmp/x", "high")
    assert not any(c.startswith("model_reasoning_effort") for c in cli_ai.codex_command("/fake/codex", "gpt-x", "/tmp/x"))
    client.put("/api/ai/model", json={"model": "default"})
    assert client.get("/api/ai/status").json()["effort"] == "default"  # unsupported on the new model: falls back

    # Antigravity: effort is baked into the model id, so there is nothing to set
    client.put("/api/ai/provider", json={"provider": "antigravity"})
    m = client.get("/api/ai/models").json()
    assert m["efforts"] == [] and m["labels"]["gemini-x"] == "Gemini X (High)"
    assert client.put("/api/ai/effort", json={"effort": "high"}).status_code == 400


def test_gemini_effort_is_sent_as_thinking_level(client, monkeypatch):
    client.put("/api/ai/key", json={"key": "test"})
    payloads = []
    monkeypatch.setattr(ai, "call_gemini", lambda payload, model: payloads.append(payload) or
                        {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]})
    m = client.get("/api/ai/models").json()
    assert m["efforts"] == ["low", "medium", "high"] and m["provider"] == "gemini"
    client.post("/api/ai/ask", json={"question": "hi"})
    assert "generationConfig" not in payloads[0]  # nothing extra unless you pick a level
    client.put("/api/ai/effort", json={"effort": "low"})
    r = client.post("/api/ai/ask", json={"question": "hi"}).json()
    assert payloads[1]["generationConfig"] == {"thinkingConfig": {"thinkingLevel": "low"}} and r["effort"] == "low"
