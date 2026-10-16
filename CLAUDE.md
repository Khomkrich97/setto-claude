# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Setto Claude: a Windows desktop chat window over the Claude Agent SDK that runs Claude Code with a **company** account (`CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-company`) next to the personal Claude app. Public repo `Khomkrich97/setto-claude`; colleagues install from the ZIP (see `README.md`, Thai, user-facing: write it like the public copy rules in `../STYLE.md`).

## Files

- `app.py`: everything server-side: HTTP server, one `Chat` per session (wraps `ClaudeSDKClient`), session list, usage, panel, pywebview window, self-update, shortcuts.
- `ui/`: `index.html` + `app.js` + `app.css`, plain JS, no build step. Served by `app.py` (re-read from disk on every request, `Cache-Control: no-store`).
- `Setto Claude.bat`: installer/launcher (Python 3.12, Claude Code, venv with the pinned `DEPS` line). `prepare.cmd`: company profile setup (junctions `projects` `skills` `plugins` to `%USERPROFILE%\.claude`, copies `settings.json`).
- `sync_mcp.py`: copies MCP servers and folder trust from `~/.claude.json` into the company `.claude.json` (`--trust` marks a folder trusted on request). Trust is only ever added; one folder can sit under several keys (`C:/x` and `C:/x/.`), set all of them.

## Checks (no tests, no linter)

```bash
node --check ui/app.js
"$LOCALAPPDATA/Setto/claude/venv/Scripts/python.exe" -W error -m py_compile app.py sync_mcp.py
```

Use the app venv (`%LOCALAPPDATA%\Setto\claude\venv`, Python 3.14 here): the system Python has no `claude_agent_sdk`.

## Trying a change without touching the running app

Only one instance runs per PC (`instance.json`), and Master's copy runs straight from this folder. Test on a separate server: point `LOCALAPPDATA` at a scratch folder before `import app`, then serve without a window:

```python
os.environ["LOCALAPPDATA"] = "<scratch>/testapp"      # own instance.json / config / sessions.json
sys.path.insert(0, "<this repo>"); import app
httpd = app.Server(("127.0.0.1", 8765), app.Handler)
app.Handler.KEY, app.Handler.PORT, app.Handler.CWD = "testkey123", 8765, r"<project folder>"
httpd.serve_forever()                                   # open http://127.0.0.1:8765/?k=testkey123
```

It still shares the company login, transcripts and `.claude.json` with the real app: open sessions read-only, and do not rename/delete real sessions or send messages (costs quota) while testing.

## Releasing

- Bump **both** `VERSION` (file) and `VERSION = "x.y"` in `app.py`. Pushing `main` makes every installed copy (no `.git` next to `app.py`) update itself on its next start.
- Master's copy has `.git`, so it never self-updates: restart it after a change. Check `/api/state` first that no chat is `busy`/`waiting`, kill the `python.exe ...setto-claude\app.py` processes and their `msedgewebview2.exe --webview-exe-name=python.exe` children, delete `instance.json`, then start it **through Explorer**: `Start-Process explorer.exe '"%USERPROFILE%\Desktop\Setto Claude.lnk"'`. Match processes on `setto-claude\app.py`, not `app.py` (NISIT BillCheck also runs an `app.py`).

## Architecture

- **Server**: `ThreadingHTTPServer` on 127.0.0.1, random port + random key. Every `/api/*` call needs `X-Key`; Host/Origin are checked because this server can run tools. A strict CSP is sent with every response.
- **Chat events**: each `Chat` appends to `self.events`; the window long-polls `/api/events?id=&after=&gen=`. Events are the single source the UI renders from. `gen` bumps when history is reloaded from the transcript and the UI redraws from event 0.
- **Claude Code process**: one persistent `ClaudeSDKClient` per chat, connected on the first send. Mode and model change live through the client; effort and fast mode (`settings={"fastMode": ...}`) are fixed per process, so they set `reconnect` and the next message reconnects on the same conversation.
- **Session list** (`history()`): the Claude app's own records (`desk_sessions()`) come first. `cliSessionId` is the transcript in use, `priorCliSessionIds` are the older ones; no `cliSessionId` means the session was cleared and opens empty. `desk_cleared()` refuses to resume a pre-Clear transcript (it would bring the whole old context back), forks excepted. Other titled transcripts are grouped by (folder, title); untitled ones are scripts and tests and stay hidden. Renames/archive of Claude-app sessions live in Setto's `sessions.json`, never in the app's files (the app rewrites them).
- **Shared transcripts**: the company profile's `projects` is a junction to the personal one, so the Claude app, terminals and Setto use the same files. A chat that has only been reading (no client of its own) reloads when the transcript grows (`Chat.follow`). Delete here deletes for the Claude app too.
- **Context**: `get_context_usage()` after connect and every ~20 s during a turn. A session only opened (not connected) estimates from the last assistant message's `usage` in the transcript over the model window (`context-max.json` remembers windows Claude Code reported).
- **Usage**: `GET https://api.anthropic.com/api/oauth/usage` with the company OAuth token (`anthropic-beta: oauth-2025-04-20`), every 5 min, doubling on 429 up to 30 min. CLI `RateLimitEvent`s carry no percentage: never let them blank a known number.

## Traps already hit

- **GIL deadlock in the window**: setting .NET / pywebview properties (`w.native.Icon`, `w.on_top`) from a worker thread froze the whole app (grey "Not Responding" window, server dead). The icon goes through `webview.start(icon=...)`; anything else from a worker thread uses Win32 through `ctypes`, which releases the GIL. Diagnose a hang with `py-spy dump --pid <pid>`.
- **The Claude app is an MSIX (Store) app**: its files really live in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`. Processes the Claude app starts (Claude Code, and so every command run from it) see them at `%APPDATA%\Claude`; a Setto started from its shortcut does not. So a check run from Claude Code can pass while the real app fails: reproduce by launching through Explorer/a `.lnk`.
- **CSP blocks `style="..."` attributes** in rendered HTML: put the value in `data-w` and apply it through the CSSOM (`widths()` in `app.js`).
- **Class names collide** easily in `app.css` (one file for the whole window): grep a new class name before using it.
- The Claude app rewrites its session files while Setto reads them: a half-written file falls back to the last good copy (`DESK_CACHE`).
