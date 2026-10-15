# -*- coding: utf-8 -*-
"""Before the company launchers (prepare.cmd) open Claude Code with the company account, copy the MCP servers
(user scope + this project's local scope) from the personal profile (~/.claude.json) into the company
profile (%CLAUDE_CONFIG_DIR%\\.claude.json). Personal wins for a server of the same name; servers only the
company profile has are kept. Folder trust comes over too when the personal profile trusts this folder or
a folder above it (trust is only ever added here, never removed). Login and history are left alone.

  python sync_mcp.py <project dir>        (CLAUDE_CONFIG_DIR must be set)
  python sync_mcp.py --trust <project dir>   mark the folder trusted in the company profile (the user said so)
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


def trusted(cfg, project):
    """Claude Code trusts a folder when it, or a folder above it, has the trust dialog accepted."""
    for key, p in (cfg.get("projects") or {}).items():
        k = norm(key).rstrip(os.sep)
        if (p or {}).get("hasTrustDialogAccepted") and (project == k or project.startswith(k + os.sep)):
            return True
    return False


def main():
    dst_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    force_trust = "--trust" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--trust"]
    if not dst_dir or not args:
        raise SystemExit("usage: set CLAUDE_CONFIG_DIR, then sync_mcp.py [--trust] <project dir>")
    raw = os.path.abspath(args[0])
    project = norm(raw)
    src = load(os.path.join(os.path.expanduser("~"), ".claude.json"))
    dst_path = os.path.join(dst_dir, ".claude.json")
    dst = load(dst_path)

    dst["mcpServers"] = {**(dst.get("mcpServers") or {}), **(src.get("mcpServers") or {})}
    servers = {}
    for key, p in (src.get("projects") or {}).items():
        if norm(key) == project:
            servers.update(p.get("mcpServers") or {})
    trust = not trusted(dst, project) and (force_trust or trusted(src, project))
    if servers or trust:
        projects = dst.setdefault("projects", {})
        # one folder can sit under several keys ("C:/x" and "C:/x/."): Claude Code reads the plain one
        keys = [k for k in projects if norm(k) == project] or [raw.replace("\\", "/")]
        for key in keys:
            entry = projects.setdefault(key, {})
            if servers:
                entry["mcpServers"] = {**(entry.get("mcpServers") or {}), **servers}
            if trust:
                entry["hasTrustDialogAccepted"] = True

    os.makedirs(dst_dir, exist_ok=True)
    tmp = dst_path + ".sync"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dst, f, ensure_ascii=False, indent=2)
    os.replace(tmp, dst_path)
    print(f"MCP: {len(dst['mcpServers'])} user, {len(servers)} project, trusted: {trusted(dst, project)}")


if __name__ == "__main__":
    main()
