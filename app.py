# -*- coding: utf-8 -*-
"""Setto Claude (company account): a desktop-style chat window over the Claude Agent SDK.

Started by "Setto Claude.bat" (pass the project folder as its argument). Claude Code runs with CLAUDE_CONFIG_DIR=%USERPROFILE%\\.claude-company,
so it uses the company login while the personal account in the Claude app stays as it is. Memory,
transcripts, skills and plugins are shared with the personal profile through junctions prepare.cmd makes.

Serves ui/ on 127.0.0.1 (random port + random key, Host/Origin checked: this server can run tools,
so nothing without the key may reach it) and shows it in its own window (pywebview = Edge WebView2) with
its own taskbar identity and icon; falls back to an Edge --app window. One instance per PC.
Installed copies (no .git next to this file) update themselves from GitHub on start.

  pythonw app.py [--cwd <project folder>]
"""
import asyncio, base64, copy, ctypes, datetime as dt, http.server, json, os, re, secrets, shutil, subprocess, sys, \
    threading, time, traceback, urllib.request, uuid, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("CLAUDE_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".claude-company"))
CONFIG_DIR = os.environ["CLAUDE_CONFIG_DIR"]

from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, UserMessage,  # noqa: E402
                              SystemMessage, ResultMessage, TextBlock, ToolUseBlock, ToolResultBlock,
                              list_sessions, get_session_messages, rename_session, delete_session,
                              fork_session, tag_session, get_session_info)
from claude_agent_sdk.types import (PermissionResultAllow, PermissionResultDeny, HookMatcher,  # noqa: E402
                                    StreamEvent, RateLimitEvent, TaskStartedMessage, TaskProgressMessage,
                                    TaskNotificationMessage, TaskUpdatedMessage)

VERSION = "1.9"
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Setto", "claude")
UPLOADS = os.path.join(APP_DIR, "uploads")
INSTANCE = os.path.join(APP_DIR, "instance.json")
LOG_FILE = os.path.join(APP_DIR, "app.log")
UI_PROFILE = os.path.join(APP_DIR, "ui-profile")
CONFIG = os.path.join(APP_DIR, "config.json")
USAGE_FILE = os.path.join(APP_DIR, "usage.json")
ICON = os.path.join(HERE, "icon.ico")
AUMID = "Setto.Claude"
REPO = "Khomkrich97/setto-claude"
DEFAULT_CWD = os.path.join(os.path.expanduser("~"), "Documents")
WINDOW = {"w": None}   # the pywebview window once it exists
IDLE_EXIT = 60
MODES = ("auto", "default", "acceptEdits", "plan", "bypassPermissions")
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL, DEFAULT_EFFORT = "claude-opus-5-5", "medium"
NEXT_CHAT_OPTS = {}   # model/effort picked on the start screen for the next session
OUTPUT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MAX_IMAGE_B64 = 7_000_000      # ~5 MB per image after the window's own downscale
MAX_FILE = 30_000_000


def log_file(msg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


MIN_CLI = (2, 1, 280)   # older Claude Code rejects the current models (Opus 5.5 ...)


def _cli_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
        return tuple(int(x) for x in m.groups()) if m else None
    except Exception:
        return None


def cli_path():
    """The newest installed Claude Code (the Windows SDK wheel ships without one). A PC can have several
    (native installer in ~/.local/bin, npm global): an old one there made Opus 5.5 fail (15/10)."""
    home = os.path.expanduser("~")
    cands = [os.path.join(home, ".local", "bin", "claude.exe"),
             os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "@anthropic-ai", "claude-code",
                          "bin", "claude.exe"),
             shutil.which("claude.exe")]
    best, best_v = None, None
    for p in dict.fromkeys(c for c in cands if c and os.path.isfile(c)):
        v = _cli_version(p)
        if v and (best_v is None or v > best_v):
            best, best_v = p, v
    if best and best_v < MIN_CLI:
        log_file(f"Claude Code {best_v} is too old, updating")
        try:   # native installs update themselves; npm ones need npm (left as is, the error says so)
            subprocess.run([best, "update"], capture_output=True, text=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log_file(f"claude update failed: {e}")
    return best


CLI = cli_path()


def trim(v, n=4000):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"\n... (ตัดเหลือ {n} ตัวอักษรจาก {len(s)})"


def clip(v, n):
    """Cut long strings but keep lists and dicts as they are, so the window can still walk them."""
    if isinstance(v, str):
        return trim(v, n)
    if isinstance(v, list):
        return [clip(x, n) for x in v[:300]]
    if isinstance(v, dict):
        return {str(k): clip(x, n) for k, x in list(v.items())[:300]}
    return v


def short_input(inp, n=1500):
    """Tool input for display. Permission cards pass a large n: what the user approves must be what
    they can read (astra 15/10: a harmless first screen with a destructive tail)."""
    return clip(inp or {}, n)


def result_text(content):
    if isinstance(content, str):
        return trim(content)
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, dict) and c.get("type") == "image":
                parts.append("[ภาพ]")
        return trim("\n".join(parts))
    return ""


# ── asyncio loop for every SDK client ─────────────────────────────────────────────────────────
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True).start()


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, LOOP)


async def _keep_stream_open(input_data, tool_use_id, context):
    # documented workaround: a PreToolUse hook keeps the control stream open so can_use_tool fires
    return {"continue_": True}


CTX_MAX_FILE = os.path.join(APP_DIR, "context-max.json")   # model -> context window, as Claude Code reported it


def ctx_max(model):
    known = load_json(CTX_MAX_FILE, {}).get(model or "")
    if known:
        return int(known)
    return 1_000_000 if re.match(r"claude-(opus|sonnet|fable)-5", model or "") else 200_000


class Chat:
    def __init__(self, cwd, resume=None, title=""):
        self.id = uuid.uuid4().hex[:12]
        self.cwd, self.resume, self.title = cwd, resume, title
        self.sdk_id = resume
        self.events, self.cond = [], threading.Condition()
        self.busy, self.client, self.perms = False, None, {}
        prefs = load_json(CONFIG, {}).get("prefs") or {}   # last choices carry over to new sessions
        self.mode = prefs.get("mode") if prefs.get("mode") in MODES else "default"
        # every new session starts on Opus 5.5 + medium (owner's choice); a pick made on the start screen
        # before the session existed applies to that one session only
        self.model = str(NEXT_CHAT_OPTS.pop("model", DEFAULT_MODEL))
        self.effort = NEXT_CHAT_OPTS.pop("effort", DEFAULT_EFFORT)
        self.fast = bool(NEXT_CHAT_OPTS.pop("fast", False))   # Claude Code fast mode (Opus, billed as extra usage)
        self.reconnect = False
        self.ctx, self.outputs = None, []
        self.tasks = {}            # background tasks (shell / agents) by task id
        self.reader = None         # one reader per client: background tasks report after the turn ended
        self.turn_done = None
        self.auto = False          # a turn Claude started by itself (a background task finished)
        self.connecting = False
        self.allow_tools = set()
        self.live = False
        self.stop_req = False
        self.closed = False
        self.titled_id = None
        self.ctx_at = 0
        self.gen = 0               # bumps when the history is reloaded from the transcript
        self.hist_mtime = 0        # transcript time when the history shown here was read
        self.watch_at = 0

    def save_title(self):
        sid, title = self.sdk_id, (self.title or "").strip()
        if not sid or not title or sid == self.titled_id:
            return
        self.titled_id = sid
        try:
            rename_session(sid, title[:100], directory=self.cwd)
            STATE["hist"] = None
        except Exception as e:
            log_file(f"auto title failed: {e}")

    # events are the single source the window renders from (long-polled by index)
    def emit(self, kind, **kw):
        with self.cond:
            self.events.append({"n": len(self.events), "kind": kind, "t": time.strftime("%H:%M"), **kw})
            self.cond.notify_all()

    def info(self):
        return {"id": self.id, "title": self.title or "เซสชันใหม่", "busy": self.busy, "sdk_id": self.sdk_id,
                "mode": self.mode, "model": self.model, "effort": self.effort, "fast": self.fast, "cwd": self.cwd,
                "waiting": bool(self.perms), "ctx": self.ctx, "outputs": self.outputs[-30:],
                "tasks": list(self.tasks.values())[-20:], "gen": self.gen}

    def _mtime(self):
        try:
            info = get_session_info(self.sdk_id, directory=self.cwd)
            return (info.last_modified or 0) if info else 0
        except Exception:
            return 0

    def follow(self):
        """The same transcript can be continued in the Claude app or a terminal. While this window has only
        been reading it (no Claude Code process of its own on it), show what was added elsewhere."""
        if not self.sdk_id or self.client or self.busy or self.connecting or time.time() - self.watch_at < 4:
            return
        self.watch_at = time.time()
        m = self._mtime()
        if m and self.hist_mtime and m > self.hist_mtime + 1000:
            with self.cond:
                self.events = []
                self.gen += 1
            self.resume = self.sdk_id
            self.load_history()
            self.emit("note", text="มีข้อความใหม่จากแอป Claude หรือเทอร์มินัล โหลดใหม่แล้ว")

    def load_history(self):
        self.hist_mtime = self._mtime() if self.sdk_id else 0
        try:
            msgs = get_session_messages(self.resume, directory=self.cwd)
        except Exception as e:
            self.emit("error", text=f"อ่านประวัติเซสชันไม่ได้ ({e})")
            return
        for m in reversed(msgs):
            msg = m.message if isinstance(m.message, dict) else {}
            u = msg.get("usage") if m.type == "assistant" else None
            if isinstance(u, dict):
                total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                          "cache_read_input_tokens", "output_tokens"))
                mx = ctx_max(str(msg.get("model") or ""))
                self.ctx = {"pct": round(100 * total / mx, 1), "total": total, "max": mx,
                            "model": str(msg.get("model") or ""), "cats": [], "est": True}
                self.emit("context", **self.ctx)
                break
        for m in msgs[-400:]:
            msg = m.message if isinstance(m.message, dict) else {}
            content = msg.get("content")
            if m.type == "user":
                if isinstance(content, str):
                    if not self.title:
                        self.title = content[:60]
                    self.emit("user", text=content, images=0, hist=True)
                elif isinstance(content, list):
                    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                    imgs = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image")
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            self.emit("tool_result", id=c.get("tool_use_id"), text=result_text(c.get("content")),
                                      error=bool(c.get("is_error")), hist=True)
                    if texts or imgs:
                        self.emit("user", text="\n".join(texts), images=imgs, hist=True)
            elif m.type == "assistant" and isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and c.get("text"):
                        self.emit("text", text=c["text"], hist=True)
                    elif c.get("type") == "tool_use":
                        self.emit("tool", id=c.get("id"), name=c.get("name"), input=short_input(c.get("input")),
                                  hist=True)
        self.emit("hist_end")

    async def _perm(self, tool, inp, ctx):
        if tool != "AskUserQuestion" and tool in self.allow_tools:
            return PermissionResultAllow(updated_input=inp)
        pid = uuid.uuid4().hex[:10]
        fut = LOOP.create_future()
        self.perms[pid] = fut
        if tool == "AskUserQuestion":
            self.emit("ask", pid=pid, questions=inp.get("questions", []))
        else:
            full = json.dumps(inp, ensure_ascii=False)
            cut = len(full) > 400_000
            # what the user approves is exactly what the card shows: never clip a request that fits
            self.emit("perm", pid=pid, tool=tool, input=short_input(inp, 4000) if cut else inp, size=len(full),
                      cut=cut)
        try:
            ans = await fut
        finally:
            self.perms.pop(pid, None)
        self.emit("perm_done", pid=pid, allow=bool(ans.get("allow")))
        if tool == "AskUserQuestion":
            if ans.get("allow"):
                return PermissionResultAllow(updated_input={"questions": inp.get("questions", []),
                                                            "answers": ans.get("answers") or {}})
            return PermissionResultDeny(message="ผู้ใช้ปิดคำถามโดยไม่ตอบ")
        if ans.get("allow"):
            if ans.get("always"):
                self.allow_tools.add(tool)
            return PermissionResultAllow(updated_input=inp)
        return PermissionResultDeny(message=ans.get("note") or "ผู้ใช้ไม่อนุญาตให้ทำขั้นนี้")

    async def _ensure_client(self):
        if self.client:
            return
        opts = ClaudeAgentOptions(
            cwd=self.cwd, resume=self.resume, permission_mode=self.mode, can_use_tool=self._perm,
            include_partial_messages=True, cli_path=CLI, model=self.model or None, effort=self.effort or None,
            settings=json.dumps({"fastMode": bool(self.fast)}),
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_keep_stream_open])]},
            # lets the mode menu switch to Bypass later; it does not bypass anything by itself
            extra_args={"allow-dangerously-skip-permissions": None},
            stderr=lambda line: log_file(f"[cli {self.id}] {line}"))
        client = ClaudeSDKClient(opts)
        self.connecting = True
        try:
            await client.connect()
        finally:
            self.connecting = False
        self.client = client   # reachable for close/stop from here on, even if the next step fails
        self.reader = asyncio.ensure_future(self._read_loop(client))
        asyncio.ensure_future(self._context())
        applied, model = opts.permission_mode, opts.model or ""
        while self.mode != applied or self.model != model:   # changed while connecting (or applying)
            if self.mode != applied:
                applied = self.mode
                await client.set_permission_mode(applied)
            if self.model != model:
                model = self.model
                await client.set_model(model or None)
        if (self.effort or None) != opts.effort:   # effort is fixed per process: reconnect after this turn
            self.reconnect = True

    async def _read_loop(self, client):
        """Every message from this client, also between turns (background task results, and the turns
        Claude starts by itself when one finishes)."""
        try:
            async for msg in client.receive_messages():
                top = not getattr(msg, "parent_tool_use_id", None)
                if not self.busy and top and isinstance(msg, (AssistantMessage, StreamEvent)):
                    self.busy, self.auto = True, True
                    self.emit("auto_turn")
                self._handle(msg)
                if isinstance(msg, AssistantMessage) and time.time() - self.ctx_at > 20:
                    self.ctx_at = time.time()
                    asyncio.ensure_future(self._context())
                if isinstance(msg, ResultMessage):
                    asyncio.ensure_future(self._context())
                    if self.turn_done is not None:
                        self.turn_done.set()
                    if self.auto:
                        self.auto, self.busy, self.live = False, False, False
                        if self.reconnect:   # effort changed during a turn Claude started itself
                            self.reconnect = False
                            asyncio.ensure_future(self._drop_client())
                        self.emit("idle")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_file(f"reader {self.id} failed\n" + traceback.format_exc())
            self.emit("error", text=f"การเชื่อมต่อกับ Claude หลุด ({type(e).__name__}: {str(e)[:200]}) พิมพ์ต่อได้ จะเชื่อมใหม่ให้")
        finally:
            if self.turn_done is not None:
                self.turn_done.set()
            if self.auto:   # the process ended inside a turn Claude started itself: nobody else clears busy
                self.auto, self.busy, self.live = False, False, False
                self.emit("idle")
            if self.client is client:   # the process ended: next message starts a fresh one on this conversation
                self.client = None
                self.resume = self.sdk_id or self.resume

    async def send(self, text, images, files):
        if not self.title and (text or "").strip():
            self.title = text.strip().splitlines()[0][:60]
        content = []
        for img in images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": img["media_type"],
                                                       "data": img["data"]}})
        body = text
        if files:
            body += "\n\n" + "\n".join(f"ไฟล์แนบ: {p}" for p in files)
        content.append({"type": "text", "text": body or "ดูภาพนี้"})

        async def stream():
            yield {"type": "user", "message": {"role": "user", "content": content}}

        try:
            if self.closed:
                return
            await self._ensure_client()
            if self.stop_req or self.closed:   # Stop pressed while the client was still connecting
                self.emit("result", ok=False, subtype="stopped", ms=0, turns=0, text="หยุดก่อนเริ่ม", errors=[])
                return
            self.turn_done = asyncio.Event()
            await self.client.query(stream())
            await self.turn_done.wait()
        except Exception as e:
            log_file(f"chat {self.id} failed\n" + traceback.format_exc())
            self.emit("error", text=f"{type(e).__name__}: {str(e)[:400]}")
            await self._drop_client()
        finally:
            self.busy = False
            self.live = False
            if self.closed or self.reconnect:   # closed, or effort changed: the next message reconnects
                self.reconnect = False
                await self._drop_client()
            self.emit("idle")

    async def _context(self):
        """Same numbers as /context: how full this session's context window is."""
        try:
            u = await self.client.get_context_usage()
        except Exception as e:
            log_file(f"context usage failed: {e}")
            return
        u = u if isinstance(u, dict) else getattr(u, "__dict__", {}) or {}
        cats = sorted(({"name": str(c.get("name", "")), "tokens": int(c.get("tokens") or 0)}
                       for c in (u.get("categories") or []) if isinstance(c, dict)),
                      key=lambda c: -c["tokens"])
        self.ctx = {"pct": float(u.get("percentage") or 0), "total": int(u.get("totalTokens") or 0),
                    "max": int(u.get("maxTokens") or 0), "model": str(u.get("model") or ""), "cats": cats[:10]}
        if self.ctx["model"] and self.ctx["max"]:
            known = load_json(CTX_MAX_FILE, {})
            if known.get(self.ctx["model"]) != self.ctx["max"]:
                save_json(CTX_MAX_FILE, {**known, self.ctx["model"]: self.ctx["max"]})
        self.emit("context", **self.ctx)

    async def _drop_client(self):
        # remember the conversation before the client goes, so a message sent meanwhile resumes it
        self.resume = self.sdk_id or self.resume
        c, self.client = self.client, None
        r, self.reader = self.reader, None
        if r and not r.done():
            r.cancel()
        if c:
            try:
                await c.disconnect()
            except Exception:
                pass
        # the next message resumes the same conversation in a fresh process
        self.resume = self.sdk_id or self.resume

    def _handle(self, msg):
        if isinstance(msg, RateLimitEvent):
            note_usage(msg.rate_limit_info)
            return
        if isinstance(msg, StreamEvent):
            if msg.parent_tool_use_id:
                return
            ev = msg.event or {}
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                if not self.live:
                    self.live = True
                    self.emit("live_start")
                self.emit("delta", text=ev["delta"].get("text", ""))
            return
        if isinstance(msg, (TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage, TaskUpdatedMessage)):
            self._task(msg)
            return
        if isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                self.sdk_id = (msg.data or {}).get("session_id") or self.sdk_id
                self.emit("init", model=(msg.data or {}).get("model"))
            return
        if isinstance(msg, AssistantMessage):
            if msg.parent_tool_use_id:
                for b in msg.content:
                    if isinstance(b, ToolUseBlock):
                        self.emit("sub", parent=msg.parent_tool_use_id, name=b.name, input=short_input(b.input))
                return
            if self.live:
                self.live = False
                self.emit("live_end")
            for b in msg.content:
                if isinstance(b, TextBlock) and b.text:
                    self.emit("text", text=b.text)
                elif isinstance(b, ToolUseBlock):
                    self.emit("tool", id=b.id, name=b.name, input=short_input(b.input))
                    path = (b.input or {}).get("file_path") or (b.input or {}).get("notebook_path")
                    if b.name in OUTPUT_TOOLS and isinstance(path, str):
                        path = os.path.normpath(path if os.path.isabs(path) else os.path.join(self.cwd, path))
                        if path in self.outputs:
                            self.outputs.remove(path)
                        self.outputs.append(path)
            if msg.error:
                self.emit("error", text=str(msg.error), auth=str(msg.error) == "authentication_failed")
            return
        if isinstance(msg, UserMessage):
            if msg.parent_tool_use_id or not isinstance(msg.content, list):
                return
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    self.emit("tool_result", id=b.tool_use_id, text=result_text(b.content), error=bool(b.is_error))
            return
        if isinstance(msg, ResultMessage):
            self.sdk_id = msg.session_id or self.sdk_id
            threading.Thread(target=self.save_title, daemon=True).start()
            self.emit("result", ok=not msg.is_error, subtype=msg.subtype, ms=msg.duration_ms, turns=msg.num_turns,
                      text=(msg.result or "")[:500] if msg.is_error else "",
                      errors=[str(e)[:300] for e in (msg.errors or [])][:5])

    def _task(self, msg):
        t = self.tasks.setdefault(msg.task_id, {"id": msg.task_id, "desc": "", "type": "", "status": "running",
                                               "tool": "", "summary": "", "started": time.strftime("%H:%M")})
        if isinstance(msg, TaskStartedMessage):
            t.update(desc=msg.description or t["desc"], type=msg.task_type or "", status="running")
        elif isinstance(msg, TaskProgressMessage):
            t.update(desc=msg.description or t["desc"], tool=msg.last_tool_name or t["tool"])
        elif isinstance(msg, TaskNotificationMessage):
            t.update(status=msg.status or t["status"], summary=(msg.summary or "")[:300])
        elif isinstance(msg, TaskUpdatedMessage) and msg.status:
            t["status"] = msg.status
        self.emit("task", **t)

    async def stop_task(self, task_id):
        if self.client and task_id in self.tasks:
            try:
                await self.client.stop_task(task_id)
            except Exception as e:
                self.emit("error", text=f"หยุดงานเบื้องหลังไม่ได้ ({e})")

    async def interrupt(self):
        self.stop_req = True
        if self.client:
            try:
                await self.client.interrupt()
            except Exception as e:
                self.emit("error", text=f"หยุดไม่ได้ ({e})")
        for fut in list(self.perms.values()):
            if not fut.done():
                fut.set_result({"allow": False, "note": "ผู้ใช้กดหยุด"})

    async def set_mode(self, mode):
        if self.client:
            try:
                await self.client.set_permission_mode(mode)
            except Exception as e:
                # keep showing the mode the running client really has
                self.emit("error", text=f"เปลี่ยนโหมดไม่ได้ ({e}) ยังเป็นโหมดเดิม")
                return
        self.mode = mode
        save_prefs(mode=mode)

    async def set_model(self, model):
        if self.client:
            try:
                await self.client.set_model(model or None)
            except Exception as e:
                self.emit("error", text=f"เปลี่ยนโมเดลไม่ได้ ({e}) ยังเป็นโมเดลเดิม")
                return
        self.model = model

    async def set_effort(self, effort):
        # effort is fixed per CLI process: reconnect (same conversation) before the next message
        self.effort = effort
        if self.connecting:
            self.reconnect = True
        elif self.client:
            if self.busy:
                self.reconnect = True
            else:
                await self._drop_client()

    async def set_fast(self, on):
        # a setting the CLI reads at start: reconnect (same conversation) before the next message
        self.fast = bool(on)
        if self.connecting or (self.client and self.busy):
            self.reconnect = True
        elif self.client:
            await self._drop_client()

    async def mcp_status(self):
        if not self.client:
            return None
        try:
            return await self.client.get_mcp_status()
        except Exception as e:
            log_file(f"mcp status failed: {e}")
            return None

    async def close(self):
        self.closed = True
        await self.interrupt()
        await self._drop_client()


CHATS = {}
CHATS_LOCK = threading.Lock()
STATE = {"last_poll": time.time(), "polled": False, "auth": None, "auth_at": 0, "hist": None, "hist_at": 0,
         "usage_at": 0, "usage_busy": False, "usage_gap": 300}
USAGE = load_json(USAGE_FILE, {})


def note_usage(info):
    """Plan usage windows (5 hours, week ...) as the CLI reports them during a run."""
    try:
        kind = str(getattr(info, "rate_limit_type", "") or "limit")
        util = getattr(info, "utilization", None)
        if util is not None:
            util = float(util)
            util = util * 100 if util <= 1 else util
        if util is None:
            util = (USAGE.get(kind) or {}).get("util")
        USAGE[kind] = {"util": util, "resets_at": getattr(info, "resets_at", None) or (USAGE.get(kind) or {}).get("resets_at"),
                       "status": str(getattr(info, "status", "") or ""), "seen": time.time()}
        save_json(USAGE_FILE, USAGE)
    except Exception as e:
        log_file(f"usage note failed: {e}")


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_KEYS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")
USAGE_GAP = 300   # seconds between usage checks; doubles on 429 up to 30 minutes


def _iso_epoch(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fetch_usage():
    """Session (5 hours) + weekly limits of the signed-in account. The token stays on this PC and only
    goes to api.anthropic.com, the same place Claude Code sends it; an expired token just waits for
    Claude Code to refresh it on the next run."""
    try:
        cred = (load_json(os.path.join(CONFIG_DIR, ".credentials.json"), {}).get("claudeAiOauth") or {})
        tok = cred.get("accessToken")
        if not tok or (cred.get("expiresAt") or 0) / 1000 < time.time() + 30:
            return
        req = urllib.request.Request(USAGE_URL, headers={"Authorization": "Bearer " + tok,
                                                         "anthropic-beta": "oauth-2025-04-20",
                                                         "User-Agent": "setto-claude/" + VERSION})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = int(e.headers.get("Retry-After") or 0)
                except ValueError:
                    wait = 0
                STATE["usage_gap"] = min(1800, max(wait, STATE["usage_gap"] * 2))
                log_file(f"usage fetch 429, next try in {STATE['usage_gap']}s")
                return
            raise
        STATE["usage_gap"] = USAGE_GAP
        got = {}
        for k in USAGE_KEYS:
            v = d.get(k)
            if isinstance(v, dict) and v.get("utilization") is not None:
                got[k] = {"util": float(v["utilization"]), "resets_at": _iso_epoch(v.get("resets_at")),
                          "status": "", "seen": time.time()}
        if got:
            for k in USAGE_KEYS:   # windows the plan no longer reports must not linger
                USAGE.pop(k, None)
            USAGE.update(got)
            save_json(USAGE_FILE, USAGE)
    except Exception as e:
        log_file(f"usage fetch failed: {e}")
    finally:
        STATE["usage_busy"] = False


def usage_now(force=False):
    # force = the Usage popover was opened: still at most once a minute, and never inside a 429 back-off
    age = time.time() - STATE["usage_at"]
    due = age > STATE["usage_gap"] or (force and age > 60 and STATE["usage_gap"] <= USAGE_GAP)
    if not STATE["usage_busy"] and due:
        STATE["usage_busy"], STATE["usage_at"] = True, time.time()
        threading.Thread(target=_fetch_usage, daemon=True).start()
    return USAGE


COMMANDS = {}   # cwd -> {"at": time, "list": [...], "models": [...]}


def save_prefs(**kw):
    cfg = load_json(CONFIG, {})
    cfg["prefs"] = {**(cfg.get("prefs") or {}), **kw}
    save_json(CONFIG, cfg)


async def _fetch_commands(cwd):
    """Slash commands Claude Code offers in this folder (built-ins, project/user commands, skills).
    A client that connects and asks costs no tokens."""
    c = ClaudeSDKClient(ClaudeAgentOptions(cwd=cwd, cli_path=CLI,
                                           stderr=lambda line: log_file(f"[cli cmds] {line}")))
    try:
        await c.connect()
        info = await c.get_server_info() or {}
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass
    rows = [{"name": str(x.get("name", "")), "desc": str(x.get("description", ""))[:220],
             "hint": str(x.get("argumentHint") or "")} for x in (info.get("commands") or []) if isinstance(x, dict)]
    models = [{"value": str(m.get("value", "")), "name": str(m.get("displayName", "")),
               "desc": str(m.get("description", "")), "efforts": list(m.get("supportedEffortLevels") or []),
               "auto": bool(m.get("supportsAutoMode"))} for m in (info.get("models") or []) if isinstance(m, dict)]
    known = {m["value"] for m in models}
    full = ["low", "medium", "high", "xhigh", "max"]
    for value, name, desc in (("claude-opus-5-5", "Opus 5.5", "รุ่นล่าสุด เก่งสุดสำหรับงานยาก"),
                              ("claude-fable-5-1", "Fable 5.1", "รุ่น Fable"),
                              ("claude-sonnet-5-5", "Sonnet 5.5", "เร็วและเก่ง งานทั่วไป"),
                              ("claude-haiku-5-5", "Haiku 5.5", "เร็วสุด งานเล็ก")):
        if value not in known:   # the CLI's own list can lag behind the current models
            models.append({"value": value, "name": name, "desc": desc, "efforts": full, "auto": True})
    COMMANDS[cwd] = {"at": time.time(), "list": sorted(rows, key=lambda r: r["name"]), "models": models}


def commands(cwd, refresh=False):
    got = COMMANDS.get(cwd)
    if CLI and (refresh or not got or time.time() - got["at"] > 600):
        COMMANDS.setdefault(cwd, {"at": time.time(), "list": (got or {}).get("list", [])})["at"] = time.time()
        run_async(_fetch_commands(cwd))
    return (COMMANDS.get(cwd) or {}).get("list", [])


def account_name():
    try:
        acc = load_json(os.path.join(CONFIG_DIR, ".claude.json"), {}).get("oauthAccount") or {}
        return acc.get("displayName") or ""
    except Exception:
        return ""


def auth_status(force=False):
    if not force and STATE["auth"] is not None and time.time() - STATE["auth_at"] < 30:
        return STATE["auth"]
    exe = CLI
    if not exe:
        res = {"loggedIn": None, "noCli": True}
        STATE["auth"], STATE["auth_at"] = res, time.time()
        return res
    try:
        out = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                             encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        d = json.loads(out.stdout or "{}")
        res = {"loggedIn": bool(d.get("loggedIn")), "email": d.get("email"), "org": d.get("orgName"),
               "plan": d.get("subscriptionType"), "method": d.get("authMethod"), "name": account_name()}
    except Exception as e:
        res = {"loggedIn": None, "error": str(e)[:200]}
    STATE["auth"], STATE["auth_at"] = res, time.time()
    return res


def _desk_dir():
    """Where the Claude app keeps its session list. The Store (MSIX) app's files really live in its package
    folder; only programs the Claude app starts see them under %APPDATA%, so a Setto started from
    its own shortcut found nothing there and fell back to pre-Clear transcripts."""
    import glob
    pk = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache", "Roaming",
                                "Claude", "claude-code-sessions"))
    return pk[0] if pk else os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")


DESK_DIR = _desk_dir()
LOCAL_META = os.path.join(APP_DIR, "sessions.json")   # renames / archive done here for Claude-app sessions


DESK_CACHE = {}   # path -> last record that parsed (the app rewrites these files while we read them)


def desk_sessions():
    """The Claude app's own session list: title, folder, the transcript in use (cliSessionId; none right
    after Clear) and the older transcripts of the same session (priorCliSessionIds)."""
    out = []
    for dp, _, files in os.walk(DESK_DIR):
        for f in files:
            if f.startswith("local_") and f.endswith(".json"):
                path = os.path.join(dp, f)
                d = load_json(path, None)
                if isinstance(d, dict) and d.get("sessionId") and d.get("cwd"):
                    DESK_CACHE[path] = d
                else:   # half-written right now: the last good copy, so its old transcripts stay hidden
                    d = DESK_CACHE.get(path)
                if d:
                    out.append(d)
    return out


def desk_cleared(sid):
    """True when sid is a transcript from before Clear of a session the Claude app lists (not the one it
    uses now). Opening it would bring the whole old conversation back into context."""
    try:
        for d in desk_sessions():
            if sid in (d.get("priorCliSessionIds") or []) and d.get("cliSessionId") != sid:
                return True
    except Exception as e:
        log_file(f"desk check failed: {e}")
    return False


def history(force=False):
    """One row per session the way the Claude app lists them. A session the app knows opens its current
    transcript, or starts empty when it was cleared (never the transcript from before Clear). Other
    sessions (made here or by Claude Code itself) are grouped by title; untitled ones are scripts/tests."""
    if not force and STATE["hist"] is not None and time.time() - STATE["hist_at"] < 15:
        return STATE["hist"]
    try:
        rows = list_sessions(limit=400)
    except Exception as e:
        log_file(f"list_sessions failed: {e}")
        rows = []
    by_id = {s.session_id: s for s in rows}
    meta = load_json(LOCAL_META, {})
    hidden, titles = set(meta.get("hidden") or []), meta.get("titles") or {}
    norm = lambda p: os.path.normcase(os.path.normpath(p or ""))
    stamp = lambda ms: dt.datetime.fromtimestamp(ms / 1000).strftime("%d/%m %H:%M") if ms else ""
    out, taken, cleared, desk_keys = [], set(), {}, set()
    try:
        desk = desk_sessions()
    except Exception as e:
        log_file(f"reading Claude app sessions failed: {e}")
        desk = []
    if not desk and not STATE.get("desk_warned"):
        STATE["desk_warned"] = True
        log_file(f"no Claude app sessions found in {DESK_DIR}")
    for d in desk:
        cli = d.get("cliSessionId") or None
        ids = [x for x in [cli, *(d.get("priorCliSessionIds") or [])] if x]
        taken.update(ids)
        key = "desk:" + d["sessionId"]
        if d.get("isArchived") or key in hidden:
            continue
        title = titles.get(key) or (d.get("title") or "").strip() or "ไม่มีชื่อ"
        desk_keys.add((norm(d["cwd"]), (d.get("title") or "").strip()))
        ts = max(int(d.get("lastActivityAt") or 0), (by_id[cli].last_modified or 0) if cli in by_id else 0)
        row = {"key": key, "sdk_id": cli, "ids": ids, "title": title[:90], "cwd": d["cwd"], "ts": ts, "at": stamp(ts),
               "desk": True}
        out.append(row)
        if not cli:   # cleared in the app: a session started here afterwards continues it
            newest = max((by_id[x].created_at or 0 for x in ids if x in by_id), default=0)
            cleared[(norm(d["cwd"]), title.strip())] = (row, newest)
    seen = {}
    for s in sorted(rows, key=lambda r: r.last_modified or 0, reverse=True):
        title = (s.custom_title or "").strip()
        if s.session_id in taken or s.tag == "archived" or not title or ("id:" + s.session_id) in hidden:
            continue
        k = (norm(s.cwd), title)
        hit = cleared.get(k)
        if hit and (s.created_at or 0) > hit[1]:
            row = hit[0]
            row["ids"].insert(0, s.session_id)
            if not row["sdk_id"]:
                row["sdk_id"], row["ts"] = s.session_id, max(row["ts"], s.last_modified or 0)
                row["at"] = stamp(row["ts"])
            continue
        if k in desk_keys:   # an older transcript of a session the Claude app lists
            continue
        if k in seen:
            seen[k]["ids"].append(s.session_id)
            continue
        row = {"key": "id:" + s.session_id, "sdk_id": s.session_id, "ids": [s.session_id], "title": title[:90],
               "cwd": s.cwd or "", "ts": s.last_modified or 0, "at": stamp(s.last_modified)}
        seen[k] = row
        out.append(row)
    out.sort(key=lambda r: r["ts"], reverse=True)
    STATE["hist"], STATE["hist_at"] = out, time.time()
    return out


def prune_uploads():
    cut = time.time() - 7 * 86400
    try:
        for name in os.listdir(UPLOADS):
            p = os.path.join(UPLOADS, name)
            if os.path.getmtime(p) < cut:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


PANEL_CACHE = {}


def _git(cwd, *args):
    # a folder's own .git/config must not run programs just because the panel looked at it
    safe = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "diff.external=",
            "-c", "core.hooksPath=NUL"]
    if args and args[0] == "diff":
        args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
    r = subprocess.run(["git", *safe, "-C", cwd, *args], capture_output=True, text=True, timeout=8, encoding="utf-8",
                       errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() if r.returncode == 0 else None


def panel_info(cwd, chat=None):
    """Right panel: folder + branch, working-tree changes, connectors, context, files written."""
    key = cwd
    hit = PANEL_CACHE.get(key)
    if hit and time.time() - hit[0] < 5:
        base = hit[1]
    else:
        base = {"cwd": cwd, "folder": os.path.basename(os.path.normpath(cwd)) or cwd, "branch": None, "changes": None}
        try:
            base["branch"] = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
            if base["branch"] is not None:
                stat = _git(cwd, "diff", "HEAD", "--numstat") or ""
                add = dele = files = 0
                for line in stat.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        files += 1
                        add += int(parts[0]) if parts[0].isdigit() else 0
                        dele += int(parts[1]) if parts[1].isdigit() else 0
                untracked = _git(cwd, "ls-files", "--others", "--exclude-standard") or ""
                base["changes"] = {"files": files, "add": add, "del": dele,
                                   "new": len([x for x in untracked.splitlines() if x][:5000])}
        except Exception as e:
            log_file(f"git panel failed: {e}")
        PANEL_CACHE[key] = (time.time(), base)
    mcp = None
    if chat is not None and chat.client:
        try:
            st = run_async(chat.mcp_status()).result(timeout=6)
            servers = (st or {}).get("mcpServers") if isinstance(st, dict) else st
            mcp = [{"name": str(x.get("name", "")), "status": str(x.get("status", ""))}
                   for x in (servers or []) if isinstance(x, dict)]
        except Exception as e:
            log_file(f"panel mcp failed: {e}")
    if mcp is None:   # not connected yet: what the profile has configured
        cfg = load_json(os.path.join(CONFIG_DIR, ".claude.json"), {})
        names = list((cfg.get("mcpServers") or {}).keys())
        for k, p in (cfg.get("projects") or {}).items():
            if os.path.normcase(os.path.normpath(k.replace("/", os.sep))) == os.path.normcase(os.path.normpath(cwd)):
                names += list((p.get("mcpServers") or {}).keys())
        mcp = [{"name": n, "status": ""} for n in dict.fromkeys(names)]
    return {**base, "mcp": mcp, "ctx": chat.ctx if chat else None, "outputs": chat.outputs[-30:] if chat else []}


def pick_folder(current):
    """Native folder dialog (the app window's own when it exists)."""
    w = WINDOW["w"]
    if w is not None:
        import webview
        got = w.create_file_dialog(webview.FileDialog.FOLDER, directory=current)
        return got[0] if got else None
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(initialdir=current, title="เลือกโฟลเดอร์งาน") or None
    finally:
        root.destroy()


def set_folder(path):
    path = os.path.abspath(path)
    Handler.CWD = path
    save_json(CONFIG, {**load_json(CONFIG, {}), "cwd": path})
    STATE["hist"] = None
    sync_profile(path)
    return path


SYNCED = set()


def sync_profile(path, trust=False):
    """This folder's MCP servers and trust from the personal profile (same as prepare.cmd does at start);
    trust=True marks the folder trusted because the user pressed the button for it."""
    key = os.path.normcase(os.path.normpath(path))
    if key in SYNCED and not trust:
        return
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "sync_mcp.py"), *(["--trust"] if trust else []), path],
                           timeout=30, env={**os.environ, "CLAUDE_CONFIG_DIR": CONFIG_DIR},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            SYNCED.add(key)
        else:
            log_file(f"sync_mcp failed: {(r.stderr or '')[-300:]}")
    except Exception as e:
        log_file(f"sync_mcp failed: {e}")


def folder_trusted(path):
    """Same rule as Claude Code: the folder or one above it has the trust dialog accepted."""
    want = os.path.normcase(os.path.normpath(path))
    for k, p in (load_json(os.path.join(CONFIG_DIR, ".claude.json"), {}).get("projects") or {}).items():
        k = os.path.normcase(os.path.normpath(k.replace("/", os.sep))).rstrip(os.sep)
        if (p or {}).get("hasTrustDialogAccepted") and (want == k or want.startswith(k + os.sep)):
            return True
    return False


def open_login_terminal():
    exe = CLI or "claude"   # without Claude Code the console says so; the installer .bat puts it in
    env = {**os.environ, "CLAUDE_CONFIG_DIR": CONFIG_DIR}
    subprocess.Popen(["cmd.exe", "/c", "start", "Claude login", "cmd.exe", "/k", exe, "auth", "login"],
                     env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    STATE["auth"] = None


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"), "/icon.png": ("icon.png", "image/png")}
CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
SAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "setto-claude"
    KEY = PORT = None
    CWD = DEFAULT_CWD

    def log_message(self, *a):
        pass

    def _host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.PORT}"

    def _api_ok(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Key", ""), self.KEY)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _query(self):
        q = {}
        if "?" in self.path:
            for part in self.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    q[k] = urllib.request.unquote(v)
        return q

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"ok": False})
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(HERE, "ui", name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            except OSError:
                return self._send(404, b"missing", "text/plain")
        if not self._api_ok():
            return self._send(403, {"ok": False})
        STATE["last_poll"], STATE["polled"] = time.time(), True
        q = self._query()
        if path == "/api/ping":
            return self._send(200, {"ok": True})
        if path == "/api/state":
            with CHATS_LOCK:
                chats = [c.info() for c in CHATS.values()]
            return self._send(200, {"ok": True, "version": VERSION, "cwd": self.CWD, "chats": chats,
                                    "trusted": folder_trusted(self.CWD),
                                    "history": history(q.get("force") == "1"),
                                    "auth": auth_status(q.get("auth") == "1"),
                                    "usage": usage_now(q.get("force") == "1"),
                                    "prefs": {**(load_json(CONFIG, {}).get("prefs") or {}),
                                              "model": NEXT_CHAT_OPTS.get("model", DEFAULT_MODEL),
                                              "effort": NEXT_CHAT_OPTS.get("effort", DEFAULT_EFFORT),
                                              "fast": bool(NEXT_CHAT_OPTS.get("fast", False))}})
        if path == "/api/commands":
            lst = commands(self.CWD, q.get("refresh") == "1")
            return self._send(200, {"ok": True, "commands": lst,
                                    "models": (COMMANDS.get(self.CWD) or {}).get("models", [])})
        if path == "/api/panel":
            c = CHATS.get(q.get("id", ""))
            return self._send(200, {"ok": True, **panel_info(c.cwd if c else self.CWD, c)})
        if path == "/api/events":
            c = CHATS.get(q.get("id", ""))
            if not c:
                return self._send(200, {"ok": False, "error": "gone"})
            after = int(q.get("after", "0") or 0)
            if int(q.get("gen", "0") or 0) != c.gen:   # reloaded: the window starts over from event 0
                return self._send(200, {"ok": True, "events": [], "chat": c.info()})
            c.follow()
            with c.cond:
                if len(c.events) <= after:
                    c.cond.wait(timeout=6)
                evs = copy.deepcopy(c.events[after:after + 500])
            return self._send(200, {"ok": True, "events": evs, "chat": c.info()})
        self._send(404, {"ok": False})

    def do_POST(self):
        if not self._api_ok() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._send(403, {"ok": False})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://127.0.0.1:{self.PORT}":
            return self._send(403, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 60_000_000:
                return self._send(413, {"ok": False, "error": "ไฟล์ใหญ่เกิน"})
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"ok": False})
        path = self.path.split("?", 1)[0]
        try:
            self._send(200, self._post(path, body))
        except Exception as e:
            log_file(f"POST {path} failed\n" + traceback.format_exc())
            self._send(200, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})

    def _post(self, path, body):
        if path == "/api/login":
            open_login_terminal()
            return {"ok": True}
        if path == "/api/logout":
            if not CLI:
                return {"ok": False, "error": "ยังไม่ได้ติดตั้ง Claude Code"}
            r = subprocess.run([CLI, "auth", "logout"], capture_output=True, text=True, timeout=30, encoding="utf-8",
                               errors="replace", env={**os.environ, "CLAUDE_CONFIG_DIR": CONFIG_DIR},
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            STATE["auth"] = None
            for k in USAGE_KEYS:
                USAGE.pop(k, None)
            save_json(USAGE_FILE, USAGE)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout or "ล็อกเอาท์ไม่สำเร็จ").strip()[:200]}
            return {"ok": True}
        if path == "/api/folder":   # {path} = a folder picked from the list, {} = the folder dialog
            want = str(body.get("path") or "")
            got = (want if os.path.isdir(want) else None) if want else pick_folder(Handler.CWD)
            if not got:
                return {"ok": True, "cwd": Handler.CWD, "same": True}
            cwd = set_folder(got)
            commands(cwd, refresh=True)
            return {"ok": True, "cwd": cwd}
        if path == "/api/prefs":   # choices made before any session exists (used by the next new one)
            kw = {}
            if body.get("mode") in MODES:
                kw["mode"] = body["mode"]
            if "model" in body and re.fullmatch(r"[\w.\[\]:-]{0,60}", str(body["model"] or "")):
                NEXT_CHAT_OPTS["model"] = str(body["model"] or "")
            if body.get("effort") in EFFORTS:
                NEXT_CHAT_OPTS["effort"] = body["effort"]
            if "fast" in body:
                NEXT_CHAT_OPTS["fast"] = bool(body["fast"])
            if kw:
                save_prefs(**kw)
            return {"ok": True}
        if path == "/api/focus":
            w = WINDOW["w"]
            if w is None:
                return {"ok": False}
            w.restore()
            w.show()
            return {"ok": True}
        if path == "/api/trust":   # the user trusts the working folder (Claude Code's trust dialog)
            sync_profile(Handler.CWD, trust=True)
            commands(Handler.CWD, refresh=True)
            return {"ok": folder_trusted(Handler.CWD)}
        if path == "/api/session":   # title menu / sidebar menu: rename, fork, archive, delete
            return self._session_op(body)
        if path == "/api/new":
            resume = body.get("resume")
            if resume and not re.fullmatch(r"[0-9a-f-]{36}", str(resume)):
                return {"ok": False, "error": "bad session id"}
            if resume and not body.get("fork_of") and desk_cleared(resume):
                resume = None   # cleared in the Claude app: start empty there too
                STATE["hist"] = None
            with CHATS_LOCK:
                for c in CHATS.values():
                    if resume and c.sdk_id == resume:
                        return {"ok": True, "id": c.id}
            cwd = self.CWD
            want = str(body.get("cwd") or "")
            if want and os.path.isdir(want):
                cwd = os.path.abspath(want)
                if not resume and os.path.normcase(cwd) != os.path.normcase(self.CWD):
                    set_folder(cwd)   # "+" on a project: that project becomes the working folder
                    commands(cwd, refresh=True)
            sync_profile(cwd)
            c = Chat(cwd, resume=resume, title=body.get("title") or "")
            with CHATS_LOCK:
                CHATS[c.id] = c
            if resume:
                c.load_history()
                if any(resume == d.get("cliSessionId") for d in desk_sessions()):
                    c.emit("note", text="เซสชันนี้ใช้ไฟล์เดียวกับแอป Claude เดสก์ท็อป ถ้าคุยที่นั่นต่อ หน้านี้จะโหลดตามให้ "
                                        "แต่อย่าพิมพ์สั่งงานทั้งสองที่พร้อมกัน งานจะแยกเป็นสองทาง")
            return {"ok": True, "id": c.id, "fresh": not resume and bool(body.get("resume"))}
        c = CHATS.get(str(body.get("id", "")))
        if not c:
            return {"ok": False, "error": "ไม่พบเซสชันนี้"}
        if path == "/api/send":
            with CHATS_LOCK:   # check and claim in one step: two windows must not start two queries
                if c.busy:
                    return {"ok": False, "error": "เซสชันนี้กำลังทำงานอยู่ กดหยุดก่อน หรือรอให้เสร็จ"}
                c.busy = True
                c.stop_req = False   # cleared at claim time: a Stop while files upload still counts
            try:
                return self._send_msg(c, body)
            except Exception:
                c.busy = False
                raise
        return self._post_chat(path, body, c)

    def _session_op(self, body):
        op, title = body.get("op"), str(body.get("title") or "").strip()[:120]
        sid = str(body.get("sdk_id") or "")
        c = CHATS.get(str(body.get("id") or ""))
        if c and c.sdk_id:
            sid = c.sdk_id
        if sid and not re.fullmatch(r"[0-9a-f-]{36}", sid):
            return {"ok": False, "error": "bad session id"}
        if not c and sid:
            with CHATS_LOCK:
                c = next((x for x in CHATS.values() if x.sdk_id == sid), None)
        where = c.cwd if c else (str(body.get("cwd") or "") or None)
        if where and not os.path.isdir(where):
            where = None

        ids = [str(x) for x in (body.get("ids") or []) if re.fullmatch(r"[0-9a-f-]{36}", str(x))][:50]
        if sid and sid not in ids:
            ids.insert(0, sid)

        def close_open():
            if c:
                with CHATS_LOCK:
                    CHATS.pop(c.id, None)
                run_async(c.close()).result(timeout=15)

        key = str(body.get("key") or "")
        if key and not re.fullmatch(r"(desk:local_|id:)[0-9a-f-]{36}", key):
            return {"ok": False, "error": "bad key"}
        if op in ("rename", "archive", "delete") and key:
            meta = load_json(LOCAL_META, {})
            if op == "rename" and title:
                meta.setdefault("titles", {})[key] = title
            elif op in ("archive", "delete"):
                meta["hidden"] = sorted(set(meta.get("hidden") or []) | {key})
            save_json(LOCAL_META, meta)
        if op == "rename":
            if not title:
                return {"ok": False, "error": "ชื่อว่าง"}
            if c:
                c.title, c.titled_id = title, c.sdk_id
            for x in ids:
                rename_session(x, title, directory=where)
        elif op == "fork":
            if not sid:
                return {"ok": False, "error": "เซสชันนี้ยังไม่มีข้อความ"}
            if c and c.busy:
                return {"ok": False, "error": "รอให้ Claude ทำงานเสร็จก่อน"}
            got = fork_session(sid, directory=where, title=(title or (c.title if c else "") or "เซสชัน")[:100] + " (fork)")
            STATE["hist"] = None
            return {"ok": True, "sdk_id": got.session_id, "cwd": where or ""}
        elif op == "archive":
            close_open()
            for x in ids:
                tag_session(x, "archived", directory=where)
        elif op == "delete":
            close_open()
            for x in ids:
                delete_session(x, directory=where)
        else:
            return {"ok": False, "error": "unknown op"}
        STATE["hist"] = None
        return {"ok": True}

    def _send_msg(self, c, body):
        def fail(msg):
            c.busy = False
            return {"ok": False, "error": msg}
        text = str(body.get("text") or "")
        if len(text) > 200_000:
            return fail("ข้อความยาวเกิน 200,000 ตัวอักษร แนบเป็นไฟล์แทน")
        if len(body.get("images") or []) > 20 or len(body.get("files") or []) > 20:
            return fail("แนบได้ครั้งละไม่เกิน 20 ภาพ และ 20 ไฟล์")
        images = []
        for img in (body.get("images") or []):
            mt, data = img.get("media_type"), img.get("data") or ""
            if mt not in ("image/png", "image/jpeg", "image/gif", "image/webp") or len(data) > MAX_IMAGE_B64:
                return fail("ภาพต้องเป็น png jpg gif webp และไม่ใหญ่เกิน 5 MB")
            images.append({"media_type": mt, "data": data})
        files = []
        if body.get("files"):
            folder = os.path.join(UPLOADS, time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
            os.makedirs(folder, exist_ok=True)
            for i, fobj in enumerate(body["files"], 1):
                raw = base64.b64decode(fobj.get("data") or "")
                if len(raw) > MAX_FILE:
                    return fail("ไฟล์แนบใหญ่เกิน 30 MB")
                name = SAFE_NAME.sub("_", os.path.basename(str(fobj.get("name") or "file")))[:120].strip(". ")
                p = os.path.join(folder, f"{i:02d}-{name or 'file'}")   # same names must not overwrite
                with open(p, "wb") as f:
                    f.write(raw)
                files.append(p)
        if not text.strip() and not images and not files:
            return fail("พิมพ์ข้อความ หรือแนบภาพก่อน")
        if not c.title:
            c.title = (text.strip().splitlines() or ["ภาพ"])[0][:60]
        c.emit("user", text=text, images=len(images), files=[os.path.basename(p) for p in files],
               thumbs=[f"data:{i['media_type']};base64,{i['data']}" for i in images
                       if len(i["data"]) < 400_000][:6])
        run_async(c.send(text, images, files))
        return {"ok": True}

    def _post_chat(self, path, body, c):
        if path == "/api/perm":
            fut = c.perms.get(str(body.get("pid", "")))
            if not fut:
                return {"ok": False, "error": "คำขอนี้หมดอายุแล้ว"}
            ans = {"allow": bool(body.get("allow")), "always": bool(body.get("always")),
                   "note": str(body.get("note") or "")[:500], "answers": body.get("answers") or {}}
            LOOP.call_soon_threadsafe(lambda: fut.done() or fut.set_result(ans))
            return {"ok": True}
        if path == "/api/stoptask":
            run_async(c.stop_task(str(body.get("task") or "")))
            return {"ok": True}
        if path == "/api/openfile":
            p = str(body.get("path") or "")
            if p not in c.outputs or not os.path.isfile(p):   # only files this session wrote
                return {"ok": False, "error": "เปิดไฟล์นี้ไม่ได้"}
            os.startfile(p)
            return {"ok": True}
        if path == "/api/stop":
            run_async(c.interrupt())
            return {"ok": True}
        if path in ("/api/mode", "/api/opts"):
            if "mode" in body:
                if body["mode"] not in MODES:
                    return {"ok": False, "error": "bad mode"}
                run_async(c.set_mode(body["mode"]))
            if "model" in body:
                model = str(body["model"] or "")
                if model and not re.fullmatch(r"[\w.\[\]:-]{1,60}", model):
                    return {"ok": False, "error": "bad model"}
                run_async(c.set_model(model))
            if "effort" in body:
                if body["effort"] not in EFFORTS:
                    return {"ok": False, "error": "bad effort"}
                run_async(c.set_effort(body["effort"]))
            if "fast" in body:
                run_async(c.set_fast(bool(body["fast"])))
            return {"ok": True}
        if path == "/api/close":
            with CHATS_LOCK:
                CHATS.pop(c.id, None)
            run_async(c.close())
            return {"ok": True}
        return {"ok": False, "error": "unknown"}


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def local_version():
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def self_update():
    """Installed copies follow GitHub main: when VERSION there differs, replace this folder with the
    published files and restart. A clone (.git) is a developer copy and is left alone. Any failure
    just keeps the current version."""
    if os.path.isdir(os.path.join(HERE, ".git")) or os.environ.get("SETTO_NO_UPDATE"):
        return False
    try:
        with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/main/VERSION", timeout=5) as r:
            remote = r.read().decode("utf-8").strip()
        if not remote or remote == local_version() or len(remote) > 40:
            return False
        import io, zipfile
        with urllib.request.urlopen(f"https://codeload.github.com/{REPO}/zip/refs/heads/main", timeout=60) as r:
            data = r.read()
        z = zipfile.ZipFile(io.BytesIO(data))
        root = z.namelist()[0].split("/")[0] + "/"
        staged = []
        for name in z.namelist():
            rel = name[len(root):] if name.startswith(root) else ""
            if not rel or name.endswith("/"):
                continue
            dest = os.path.normpath(os.path.join(HERE, rel))
            if not dest.startswith(os.path.normpath(HERE) + os.sep):   # no path tricks out of the folder
                raise ValueError(f"bad path in update: {name}")
            staged.append((dest, z.read(name)))
        # write every file beside its target first; only when all are on disk swap them in, VERSION last,
        # so a failure halfway leaves the old version intact and the next start tries again
        staged.sort(key=lambda x: os.path.basename(x[0]) == "VERSION")
        written = []
        try:
            for dest, blob in staged:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest + ".new", "wb") as f:
                    f.write(blob)
                written.append(dest)
        except Exception:
            for dest in written:
                try:
                    os.remove(dest + ".new")
                except OSError:
                    pass
            raise
        new_bat = next((blob for dest, blob in staged if os.path.basename(dest) == "Setto Claude.bat"), None)
        if new_bat is not None and not ensure_deps(new_bat.decode("utf-8", "replace")):
            for dest in written:   # libraries for the new version could not be installed: keep the old one
                try:
                    os.remove(dest + ".new")
                except OSError:
                    pass
            return False
        for dest in written:
            os.replace(dest + ".new", dest)
        log_file(f"updated to {local_version()} from GitHub")
        return True
    except Exception as e:
        log_file(f"update check failed: {e}")
        return False


def ensure_deps(bat_text):
    """The launcher pins the libraries (DEPS line). Before an update is swapped in, install a changed
    list; -> False when that failed (the update is then skipped and retried next start)."""
    try:
        m = re.search(r'set "DEPS=([^"]+)"', bat_text)
        if not m:
            return True
        deps = m.group(1).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.=\- ]{1,300}", deps):   # pins only, nothing else reaches pip
            log_file(f"bad DEPS line in update: {deps[:100]}")
            return False
        ready = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "ready.txt")
        try:
            with open(ready, encoding="utf-8") as f:
                if f.read().strip() == deps:
                    return True
        except OSError:
            pass
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", *deps.split()],
                           capture_output=True, text=True, timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            with open(ready, "w", encoding="utf-8") as f:
                f.write(deps + "\n")
            return True
        log_file(f"dependency update failed: {(r.stderr or '')[-500:]}")
        return False
    except Exception as e:
        log_file(f"dependency update failed: {e}")
        return False


SHORTCUT_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellLinkW { void GetPath(IntPtr a, int b, IntPtr c, uint d); void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
 void GetDescription(IntPtr a, int b); void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s); void GetWorkingDirectory(IntPtr a, int b);
 void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s); void GetArguments(IntPtr a, int b); void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
 void GetHotkey(out short h); void SetHotkey(short h); void GetShowCmd(out int c); void SetShowCmd(int c); void GetIconLocation(IntPtr a, int b, out int i);
 void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i); void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, uint r);
 void Resolve(IntPtr h, uint f); void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s); }
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PropertyKey k); void GetValue(ref PropertyKey k, out PropVariant v);
 void SetValue(ref PropertyKey k, ref PropVariant v); void Commit(); }
[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropertyKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)] public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPersistFile { void GetClassID(out Guid g); [PreserveSig] int IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint m);
 void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool r); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f); void GetCurFile(out IntPtr f); }
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] public class ShellLink {}
public static class SettoLink {
 public static void Make(string lnk, string target, string args, string dir, string icon, string aumid) {
  var link = (IShellLinkW)new ShellLink(); link.SetPath(target); link.SetArguments(args); link.SetWorkingDirectory(dir);
  link.SetIconLocation(icon, 0); link.SetDescription("Setto Claude");
  var store = (IPropertyStore)link; var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
  var v = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(aumid) }; store.SetValue(ref key, ref v); store.Commit();
  ((IPersistFile)link).Save(lnk, true); Marshal.FreeCoTaskMem(v.p); } }
'@
$a = $env:SETTO_LNK_ARGS
foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
  [SettoLink]::Make((Join-Path $dir 'Setto Claude.lnk'), $env:SETTO_LNK_TARGET, $a, $env:SETTO_LNK_DIR, $env:SETTO_LNK_ICON, $env:SETTO_AUMID)
}
"""


def make_shortcuts():
    """Start menu + desktop shortcut with the Setto icon and the same AppUserModelID as the running
    window, so pinning to the taskbar shows one Setto Claude button (not Edge, not Python)."""
    if os.name != "nt":
        return
    stamp = os.path.join(APP_DIR, "shortcut.json")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    want = {"target": pyw, "app": os.path.abspath(__file__), "v": 1}
    desktop_lnk = os.path.join(os.path.expanduser("~"), "Desktop", "Setto Claude.lnk")
    if load_json(stamp, {}) == want and os.path.exists(desktop_lnk):
        return
    env = {**os.environ, "SETTO_LNK_TARGET": pyw, "SETTO_LNK_ARGS": f'"{os.path.abspath(__file__)}"',
           "SETTO_LNK_DIR": HERE, "SETTO_LNK_ICON": ICON, "SETTO_AUMID": AUMID}
    import base64 as b64
    enc = b64.b64encode(SHORTCUT_PS.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       env=env, capture_output=True, text=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0:
        save_json(stamp, want)
    else:
        log_file(f"shortcut failed: {(r.stderr or r.stdout)[:500]}")


def run_window(url):
    """Own window over WebView2. -> False when pywebview is unavailable (caller falls back to Edge)."""
    try:
        import webview
    except Exception as e:
        log_file(f"pywebview unavailable: {e}")
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass
    w = webview.create_window("Setto Claude", url, width=1320, height=900, min_size=(900, 600),
                              background_color="#0e1520", text_select=True)
    WINDOW["w"] = w

    def on_shown():
        # Windows may open a new window behind the current one: lift it once. Plain Win32 through ctypes
        # (releases the GIL); setting .NET properties such as w.native.Icon or w.on_top from this worker
        # thread held the GIL while WinForms waited for the UI thread, which froze the whole app.
        try:
            u = ctypes.windll.user32
            hwnd = u.FindWindowW(None, "Setto Claude")
            if hwnd:
                flags = 0x0001 | 0x0002 | 0x0040   # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
                u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)   # HWND_TOPMOST
                time.sleep(0.4)
                u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)   # HWND_NOTOPMOST
                u.SetForegroundWindow(hwnd)
        except Exception as e:
            log_file(f"window lift failed: {e}")
    w.events.shown += on_shown
    try:
        # the icon is set by pywebview itself on the UI thread while it builds the window
        webview.start(private_mode=False, storage_path=os.path.join(APP_DIR, "webview"), icon=ICON)
    except Exception:
        log_file("pywebview start failed, using Edge\n" + traceback.format_exc())
        WINDOW["w"] = None
        return False
    WINDOW["w"] = None
    return True


def edge_path():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        p = base and os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
        if p and os.path.exists(p):
            return p
    return None


def open_window(url):
    if os.environ.get("SETTO_CLAUDE_NOWINDOW"):   # tests drive the page from another browser
        return
    exe = edge_path()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={UI_PROFILE}", "--no-first-run",
                              "--no-default-browser-check", "--disable-features=Translate,msEdgeSidebarV2",
                              "--disable-sync", "--lang=th", "--window-size=1320,900"], close_fds=True)
            return
        except OSError:
            log_file("app window failed\n" + traceback.format_exc())
    webbrowser.open(url)


def existing_instance():
    inst = load_json(INSTANCE, None)
    if not inst:
        return None
    url = f"http://127.0.0.1:{inst['port']}"
    try:
        req = urllib.request.Request(url + "/api/ping", headers={"X-Key": inst["key"],
                                                                 "Host": f"127.0.0.1:{inst['port']}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            if json.loads(r.read().decode("utf-8")).get("ok"):
                return f"{url}/?k={inst['key']}"
    except Exception:
        pass
    return None


def watchdog(httpd):
    started = time.time()
    while True:
        time.sleep(5)
        with CHATS_LOCK:
            busy = any(c.busy for c in CHATS.values())
        if busy:
            continue
        idle = time.time() - STATE["last_poll"]
        if (STATE["polled"] and idle > IDLE_EXIT) or (not STATE["polled"] and time.time() - started > 180):
            httpd.shutdown()
            return


def relaunch_hidden_console():
    """pythonw has no console, so every console program Claude Code starts (Bash, hooks, git) got a new
    console window: the CMD flashes. Restart under python.exe with a console that has no window; children
    share it and stay invisible. -> True when this process should just exit."""
    if os.name != "nt" or os.environ.get("SETTO_HIDDEN_CONSOLE") or \
            os.path.basename(sys.executable).lower() != "pythonw.exe":
        return False
    py = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.exists(py):
        return False
    subprocess.Popen([py, os.path.abspath(__file__), *sys.argv[1:]], close_fds=True,
                     env={**os.environ, "SETTO_HIDDEN_CONSOLE": "1"},
                     creationflags=subprocess.CREATE_NO_WINDOW)
    return True


def main():
    if relaunch_hidden_console():
        return
    if "--cwd" in sys.argv:
        cwd = os.path.abspath(sys.argv[sys.argv.index("--cwd") + 1])
        save_json(CONFIG, {**load_json(CONFIG, {}), "cwd": cwd})
    else:
        cwd = load_json(CONFIG, {}).get("cwd") or DEFAULT_CWD
    if not os.path.isdir(cwd):
        cwd = DEFAULT_CWD
    url = existing_instance()
    if url:
        try:   # bring the running window forward instead of opening a second one
            inst = load_json(INSTANCE, {})
            req = urllib.request.Request(f"http://127.0.0.1:{inst['port']}/api/focus", data=b"{}", method="POST",
                                         headers={"X-Key": inst["key"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                if not json.loads(r.read().decode("utf-8")).get("ok"):
                    raise RuntimeError("no app window")
        except Exception:
            open_window(url)
        return
    if self_update():
        subprocess.Popen([sys.executable, os.path.abspath(__file__), *sys.argv[1:]], close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    threading.Thread(target=make_shortcuts, daemon=True).start()
    prune_uploads()
    key = secrets.token_urlsafe(24)
    httpd = Server(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    Handler.KEY, Handler.PORT, Handler.CWD = key, port, cwd
    save_json(INSTANCE, {"port": port, "key": key, "pid": os.getpid()})
    threading.Thread(target=auth_status, daemon=True).start()
    sync_profile(cwd)
    commands(cwd)
    url = f"http://127.0.0.1:{port}/?k={key}"
    try:
        if not os.environ.get("SETTO_CLAUDE_NOWINDOW"):
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            if run_window(url):
                return   # window closed = app closed
        threading.Thread(target=watchdog, args=(httpd,), daemon=True).start()
        open_window(url)
        httpd.serve_forever()
    finally:
        try:
            if load_json(INSTANCE, {}).get("pid") == os.getpid():
                os.remove(INSTANCE)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_file("fatal\n" + traceback.format_exc())
        try:
            ctypes.windll.user32.MessageBoxW(None, f"เปิดหน้าต่าง Setto Claude ไม่ได้\nส่งไฟล์ {LOG_FILE} ให้ผู้ดูแล",
                                             "Setto Claude", 0x10)
        except Exception:
            pass
