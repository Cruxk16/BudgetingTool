"""Read-only MCP server exposing the budget tools to command-line AI agents (Claude Code, Codex).

Speaks newline-delimited JSON-RPC over stdio and is started by those CLIs as a child process:
    python -m app.mcp_server
It offers exactly the tools in ai.DECLS, which only read the local database.
"""
import json
import sys

from sqlmodel import Session

from .db import engine
from .services import ai

PROTOCOL = "2025-06-18"
EMPTY_SCHEMA = {"type": "object", "properties": {}}


def tool_list() -> list[dict]:
    return [{"name": d["name"], "description": d["description"], "inputSchema": d.get("parameters") or EMPTY_SCHEMA,
             "annotations": {"readOnlyHint": True}} for d in ai.DECLS]


def call_tool(name: str, args: dict) -> dict:
    with Session(engine) as session:
        out = ai.Tools(session).run(name, args)
    return {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
            "isError": set(out) == {"error"} if isinstance(out, dict) else False}


def handle(msg: dict) -> dict | None:
    """Reply to one JSON-RPC message; None for notifications."""
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None  # notifications/initialized etc. need no answer
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "moneytool-budget", "version": "1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except Exception as e:  # never die on one bad message; stdout is reserved for protocol traffic
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
