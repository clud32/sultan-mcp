"""Small stdio client for the integration and fixture tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "2025-06-18"


def rpc(method, params=None, request_id=None):
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    if request_id is not None:
        message["id"] = request_id
    return message


def run_session(work, env, calls, version=PROTOCOL):
    requests = [
        rpc("initialize", {"protocolVersion": version, "capabilities": {},
                           "clientInfo": {"name": "release-test", "version": "1"}}, 1),
        rpc("notifications/initialized"),
        rpc("ping", request_id=2),
        rpc("tools/list", request_id=3),
    ]
    for i, (name, args) in enumerate(calls, 4):
        requests.append(rpc("tools/call", {"name": name, "arguments": args}, i))
    payload = "".join(json.dumps(message) + "\n" for message in requests)
    proc = subprocess.run([sys.executable, str(ROOT / "server.py")], input=payload, cwd=work,
                          encoding="utf-8", capture_output=True, env={**os.environ, **env},
                          timeout=240, check=True)
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    if [r.get("id") for r in replies] != [r["id"] for r in requests if "id" in r]:
        raise AssertionError("MCP 回复序列不匹配")
    if any(r.get("jsonrpc") != "2.0" or "result" not in r for r in replies):
        raise AssertionError("MCP 回复缺少 JSON-RPC 结果")
    if proc.stderr.strip():
        raise AssertionError(proc.stderr)
    return [r["result"] for r in replies]


def content(reply):
    if reply.get("isError"):
        raise AssertionError(reply["content"][0]["text"])
    return json.loads(reply["content"][0]["text"])
