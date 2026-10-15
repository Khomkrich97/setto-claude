# -*- coding: utf-8 -*-
"""Before the company launchers (prepare.cmd) open Claude Code with the company account, copy the MCP servers
(user scope + this project's local scope) from the personal profile (~/.claude.json) into the company
profile (%CLAUDE_CONFIG_DIR%\\.claude.json). Login, trust and history in the company profile are left
alone. Personal wins for a server of the same name; servers only the company profile has are kept.

  python sync_mcp.py <project dir>     (CLAUDE_CONFIG_DIR must be set)
"""
import json, os, sys


def norm(p):
    return os.path.normcase(os.path.normpath(p.replace("/", os.sep)))


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    dst_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not dst_dir or len(sys.argv) < 2:
        raise SystemExit("usage: set CLAUDE_CONFIG_DIR, then sync_mcp.py <project dir>")
    project = norm(sys.argv[1])
    src = load(os.path.join(os.path.expanduser("~"), ".claude.json"))
    dst_path = os.path.join(dst_dir, ".claude.json")
    dst = load(dst_path)

    dst["mcpServers"] = {**(dst.get("mcpServers") or {}), **(src.get("mcpServers") or {})}
    servers = {}
    for key, p in (src.get("projects") or {}).items():
        if norm(key) == project:
            servers.update(p.get("mcpServers") or {})
    if servers:
        projects = dst.setdefault("projects", {})
        key = next((k for k in projects if norm(k) == project), sys.argv[1].replace("\\", "/"))
        entry = projects.setdefault(key, {})
        entry["mcpServers"] = {**(entry.get("mcpServers") or {}), **servers}

    os.makedirs(dst_dir, exist_ok=True)
    tmp = dst_path + ".sync"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dst, f, ensure_ascii=False, indent=2)
    os.replace(tmp, dst_path)
    print(f"MCP: {len(dst['mcpServers'])} user, {len(servers)} project")


if __name__ == "__main__":
    main()
