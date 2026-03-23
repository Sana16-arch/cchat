#!/usr/bin/env python3
# ┌──────────▄▄▄▄▄▄▄─┐
# │  ▄▄▄▄▄▄  █cchat█ │
# │ ▄█~██~█▄ ▀█▀▀▀▀▀ │
# │  ▀█▀▀█▀  ▀       │
# └──────────────────┘
# claude.ai chat for the terminal
# author: xero (https://x-e.ro)
# repo: https://github.com/xero/cchat
# required deps: pip install prompt_toolkit

import argparse
import base64
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.cursor_shapes import ModalCursorShapeConfig
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import has_completions, vi_navigation_mode
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import clear_title, set_title
from prompt_toolkit.styles import Style

# --- config ---
SUBMIT_KEY = "c-g"  # ctrl+g to send
MAX_FILE_KB = 100  # skip files larger than this
WARN_TOKENS = 100_000
URGENT_TOKENS = 150_000

DIR = Path.home() / ".claude_chat"
CFG = DIR / "config.json"


def _claude_bin():
    return shutil.which("claude") or "/usr/local/bin/claude"


def _default_cfg():
    b = _claude_bin()
    return {
        "profiles": {
            "personal": {"bin": b, "env": {}},
            "work": {
                "bin": b,
                "env": {"CLAUDE_CONFIG_DIR": str(Path.home() / ".claudework")},
            },
        },
        "default_profile": "personal",
    }


# --- 16colors ---
Y = "\033[33m"  # yellow
B = "\033[34m"  # blue
C = "\033[36m"  # cyan
DW = "\033[37m"  # dim white
G = "\033[32m"  # green
RE = "\033[31m"  # red
R = "\033[0m"  # reset

# --- system prompts ---
CHAT_SYSTEM = (
    "You are a terse, expert developer assistant. Be direct and concise. "
    "Prefer short answers unless depth is clearly needed. "
    "Output is raw markdown in a terminal — use it sparingly."
)

PLAN_SYSTEM = """\
You are a technical planning assistant with full access to the project
codebase and a working development environment. You are designing tasks
for an autonomous AI coding agent to execute independently.

## How You Work

You have a full development environment. Use it to validate your plans:
- Read the actual source files under discussion. Grep for usage patterns,
  imports, and call sites. Do not assume file contents from memory.
- Install packages and check version numbers when they matter.
- Write small test scripts in /tmp to verify your approach works before
  recommending it. Run them.
- Run the project's existing tests to understand current behavior and
  confirm nothing is broken before you start planning changes.
- Check CLI help, API docs, and man pages firsthand. Do not plan from
  memory when a tool can give you the real answer.
- Fetch documentation, changelogs, and API references from the web when
  local sources are insufficient. Verify third-party claims firsthand.

Do not modify project files. Your scratch work goes in /tmp. The project
is the executor's workspace — yours is /tmp and your tools.

## Working With the User

Plan iteratively:
- Ask up to 4 clarifying questions at a time. The user may defer some —
  return to deferred items before declaring ready.
- Surface tradeoffs, edge cases, and likely failure modes before they
  become the executor's problem.
- Push back on vague requirements until they are concrete decisions.
- Identify gate tests for each major step — the minimal verifiable check
  proving the step is correct. When possible, run the gate test yourself
  in /tmp to confirm it works.

Share your research findings concisely. When you read a file and discover
its actual structure contradicts an assumption, say so. Don't narrate
every tool call — share conclusions.

## What You Are Building Toward

The task file you will produce has this structure:

- **Goal** — what and why, one paragraph
- **Orientation** — files the agent must read first, with exact paths
  you discovered during research
- **Steps** — numbered, imperative, each with a gate test. Commands and
  paths should be verified, not guessed.
- **Definition of Done** — checkbox list, every item verifiable by
  command or inspection
- **What Not To Do** — specific prohibitions from the planning conversation
- **Orchestration** (when needed) — if the task has independent workstreams,
  the agent can spawn sub-agents for parallel execution
- **Raising an Issue** — instructions for the executor to stop and report
  rather than guess when blocked

Keep this structure in mind as you plan. Gather the information each
section needs. You will write the full formatted task when /handoff
is called.

## You Are Ready When

All of these are true:
- You have read every file the agent will need to modify
- Every step is ordered, concrete, and unambiguous
- Every major step has a gate test (ideally one you have verified)
- Every design decision is made — nothing left to the agent's judgment
- You know what "done" looks like, concretely
- You know what the agent must NOT do

When ready:
"Ready to write the task. Run /handoff when you are."\
"""

PLAN_TRANSITION = (
    "The user has switched to planning mode. You now have full access to "
    "the project codebase and development tools. Review our conversation "
    "so far and identify what needs to be planned or built. Start by "
    "reading any files already discussed, then summarize what you "
    "understand the task to be and ask the user to confirm or redirect."
)

# --- help ---
_HELP_ART_Z = (
    "eJzNmL1u2zAQx/e+QpZDJgcFErtKa6BuOxYomq2jkYGVaIkNj1JEKk46BUGGDB0SwDA6dPDQR+gT"
    "5FH8JCUpyZX8bdlKmhCwQ/GO/5/ujh/Z6zZxr9tCgL2u8wbHgx/jwfWSpke1cTy8nW2Z/TLjee3e"
    "ejTTNztOEx9H2/xqJ/hC43ScYy3lZjx8yFTd6E/HOp8VnhparDuj4rXW0Tluo+sGRJmOlnliRrmc"
    "JB4F05/PN/GuJxw+2K9PwWHfdy7Vwe54OLJKO/qpFlsiMT2j08Lj2wnvtR17XWyWM0QkwgPOBH1W"
    "TANa1HiXCczlTztkQtG4R1xanGOV9kzABY0lCwU4h83D5ozOsoGefQXBr80roUL7syM/93PCuQ5l"
    "/iptvunHr/CIKEXcwFiADeK7iKjgg/7anCSm8xpTF7ru0+HQY5w25AGEMXxnEagQBL1UgFRK4tPc"
    "2s6xMpqVZMszxvkS1TPCmfhGXQUEvnz+dHJyiJ7uWSA7N69P/JVUFP+p76pLdbpMvAzCvnnXkipI"
    "jSGKQ4xU2Wh5WweogLQJUMSJKPYaJo8In8anCzPJ2AgmfMBQr9G6r4WN1MaaWCUtfJ+pTvebFvYS"
    "zoG4ro7YQTauXrR8U1nnPU8Frc+ULhWdZMaHxdwkXFUCWMjHzSgDvX2Evd50DAVBOolgma4fM0VB"
    "EXlmaqkfUGEjCkxCTIl3tQC1Rga9C0bEXRWsMkVMbUnpbDJbiTlYNQhnRL6FI5dTEqc5Bit91gaV"
    "SZPLFJSROJOaiFxQL+eSsEXe1ciWpIvuGrU1VVhZuKQiSm5XUk9bZJRHFZcSs/6rQNfW3K3qvyM9"
    "z/a27K+Eqdkx9JKVa3UKWWcwmEOtsYZtMrgm7Kc5Me6q/d5tjB10Vcxf+pWqV3hmjzhPpJrs9Npl"
    "Gxv7frgPKCiGgrkHRozVt9R/XVnsIDWXk2r1Kmjf3MXMke0ou5wVdtE2yuQrsvUPb/UxfjxeF3AB"
    "qQp9n1O4YDs432xMXTVt3Qppa6+qcRIp+TiKE3tyLYV0KszPSOdVKUq92kqY91MzUPpvLBgPBs+9"
    "Qm7UfkJ+e5sH9xehS5sc"
)


def _help_art():
    return zlib.decompress(base64.b64decode(_HELP_ART_Z)).decode("utf-8")


# --- toolbar ---
_TOOLBAR_ICONS = {
    "chat": "\033[42m \033[34m≡\033[30m \033[35m░▒",
    "plan": "\033[1;36m█\033[5;30;46m*\033[0;1;36m█\033[0;5;35;46m░▒",
    "danger": "\033[1;33;41m *\033[0;35;41m ░▓",
}


def make_toolbar(s, prof_name, sc, ctx_count_fn):
    def toolbar():
        mode = s.get("mode", "chat")
        danger = s.get("plan_danger", False) and mode == "plan"
        icon = _TOOLBAR_ICONS["danger" if danger else mode]
        name = s["name"]
        sid = s.get("session_id", "")[:8] if s.get("session_id") else "new"
        turns = s.get("turns", 0)
        nc = ctx_count_fn()
        ctx = f" ctx:{nc}" if nc else ""
        return ANSI(
            f"\n{icon}"
            f"\033[1;36;45m \033[30m{name}\033[36m   {prof_name}   \033[33m{sid}\033[30m "
            f"\033[0;5;35;44m▒░\033[1;32m t:{turns}  s:{sc}{ctx} "
            f"\033[0;5;35;44m░▒\033[0;1;36;45m \033[30m ^g send   /help\033[36m  "
            f"\033[0;30;45m░▒▓\033[0m\n"
        )
    return toolbar


# --- config / session ---
def load_cfg():
    if not CFG.exists():
        DIR.mkdir(exist_ok=True)
        CFG.write_text(json.dumps(_default_cfg(), indent=2))
    return json.loads(CFG.read_text())


def load_session(name):
    p = DIR / f"{name}.json"
    return (
        json.loads(p.read_text())
        if p.exists()
        else {
            "name": name,
            "session_id": None,
            "mode": "chat",
            "system": CHAT_SYSTEM,
            "turns": 0,
            "total_input": 0,
            "total_output": 0,
            "handoff_attempted": False,
            "plan_danger": False,
        }
    )


def save_session(s):
    DIR.mkdir(exist_ok=True)
    (DIR / f"{s['name']}.json").write_text(json.dumps(s, indent=2))


def skill_count(profile):
    d = (
        Path(
            profile.get("env", {}).get(
                "CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")
            )
        )
        / "skills"
    )
    return len(list(d.iterdir())) if d.exists() else 0


def find_claude_md():
    for p in [Path("CLAUDE.md"), Path(".claude/CLAUDE.md")]:
        if p.exists():
            return p.read_text().strip()
    return None


def token_warning(total_in):
    if total_in >= URGENT_TOKENS:
        return f"  {RE}⚠ context at {total_in:,} tokens — consider /compact{R}\n"
    if total_in >= WARN_TOKENS:
        return f"  {Y}⚠ context at {total_in:,} tokens{R}\n"
    return ""


# --- file injection ---
TEXT_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".sh",
    ".bash",
    ".zsh",
    ".env",
    ".css",
    ".html",
    ".htm",
    ".xml",
    ".sql",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".swift",
    ".kt",
    ".csv",
    ".lock",
    ".env.example",
}


def read_text_file(p):
    if p.stat().st_size > MAX_FILE_KB * 1024:
        return None, f"skipped {p.name} (>{MAX_FILE_KB}kb)"
    if p.suffix.lower() not in TEXT_EXTS:
        return None, None
    try:
        return p.read_text(errors="replace"), None
    except Exception as e:
        return None, f"skipped {p.name} ({e})"


def build_file_context(paths):
    blocks, warns = [], []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            warns.append(f"not found: {p}")
            continue
        if p.suffix.lower() == ".zip":
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(p) as z:
                    z.extractall(tmp)
                for fp in sorted(Path(tmp).rglob("*")):
                    if not fp.is_file():
                        continue
                    content, w = read_text_file(fp)
                    if w:
                        warns.append(w)
                    if content:
                        blocks.append(
                            f'<file path="{fp.relative_to(tmp)}">\n{content}\n</file>'
                        )
        else:
            content, w = read_text_file(p)
            if w:
                warns.append(w)
            if content:
                blocks.append(f'<file path="{p.name}">\n{content}\n</file>')
    return "\n\n".join(blocks), warns, len(blocks)


# --- spinner ---
class Spinner:
    def __init__(self, msg="thinking"):
        self._msg = msg
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for c in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if self._stop.is_set():
                break
            print(f"\r{c} {self._msg}...", end="", flush=True)
            time.sleep(0.08)
        print("\r" + " " * (len(self._msg) + 16) + "\r", end="", flush=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()


# --- claude call ---
class StaleSessionError(Exception):
    pass


def run_claude(
    profile, s, message, claude_md=None, spinner_msg="thinking", skip_perms=False
):
    env = {**os.environ, **profile.get("env", {})}
    cmd = [profile["bin"], "--print", "--output-format", "json"]
    if not skip_perms and s.get("mode") == "plan":
        if s.get("plan_danger"):
            cmd += ["--dangerously-skip-permissions"]
        else:
            cmd += ["--permission-mode", "plan"]
    if s.get("session_id"):
        cmd += ["--resume", s["session_id"]]
    else:
        cmd += ["--system-prompt", s["system"]]
    if claude_md:
        cmd += ["--append-system-prompt", claude_md]
    cmd.append(message)

    proc = None
    try:
        with Spinner(spinner_msg):
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            stdout, stderr = proc.communicate()
        print("\a", end="", flush=True)
    except KeyboardInterrupt:
        if proc:
            proc.send_signal(signal.SIGTERM)
            proc.wait()
        return None, 0, 0, s.get("session_id")

    if proc.returncode != 0:
        err = stderr.strip() or stdout.strip() or f"exited {proc.returncode}"
        err_low = err.lower()
        if (
            "valid session" in err_low
            or "not found" in err_low
            or "no conversation" in err_low
            or "session id" in err_low
        ):
            raise StaleSessionError(err)
        raise RuntimeError(err)

    try:
        data = json.loads(stdout)
        u = data.get("usage", {})
        i = (
            u.get("input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
        )
        o = u.get("output_tokens", 0)
        return (
            data.get("result", "").strip(),
            i,
            o,
            data.get("session_id", s.get("session_id")),
        )
    except json.JSONDecodeError:
        return stdout.strip(), 0, 0, s.get("session_id")


# --- reply helper ---
def apply_reply(s, reply, i, o, sid):
    s["session_id"] = sid
    s["turns"] = s.get("turns", 0) + 1
    s["total_input"] = s.get("total_input", 0) + i
    s["total_output"] = s.get("total_output", 0) + o
    warn = token_warning(s["total_input"])
    print(f"  [in: {i:,}  out: {o:,}  total in: {s['total_input']:,}]")
    if warn:
        print(warn, end="")
    print()
    save_session(s)


def run_claude_safe(
    profile, s, message, claude_md=None, spinner_msg="thinking", skip_perms=False
):
    """run_claude with automatic stale-session retry."""
    try:
        return run_claude(profile, s, message, claude_md, spinner_msg, skip_perms)
    except StaleSessionError:
        print(f"  {Y}stale session — starting fresh{R}\n")
        s["session_id"] = None
        return run_claude(profile, s, message, claude_md, spinner_msg, skip_perms)


# --- handoff ---
_TASK_FORMAT = """\

## Task Format

### Goal
One paragraph. What is being built or changed and why.

### Orientation
Files and directories the agent must read before starting, in order.
One line per item with its exact path and why it matters.

### Steps
Numbered. Imperative mood. Each step:
- What to do — precise and unambiguous
- Exact commands where applicable (use paths and commands you verified
  during planning, not guesses)
- A gate test before proceeding to the next step:

  ```
  # verify: [what this proves]
  <command>
  # expect: <exact output or condition>
  ```

If a gate fails the agent must stop and raise an issue — never proceed.

### Definition of Done
Checkbox list. Every item verifiable by command or file inspection.
No subjective criteria.

### What Not To Do
Specific prohibitions derived from the planning conversation.
Concrete failure modes the agent might plausibly hit — "do not modify
X" not "be careful."

### Orchestration
If the task has independent workstreams that benefit from parallelism:
- Identify which steps can run concurrently
- Instruct the agent to spawn sub-agents for parallel steps:
  `claude -p "..." --dangerously-skip-permissions`
- Define sync points where parallel work must merge before continuing
- The executing agent acts as project manager: dispatch, monitor gate
  tests, handle failures, merge results

Omit this section for straightforward sequential tasks.

### Raising an Issue
If the agent encounters any of the following, it must stop and create
ISSUE.md rather than guess:
- Ambiguity requiring a judgment call
- A gate test failing for unknown reasons
- Contradictory instructions or sources
- The same fix attempted twice without success

ISSUE.md format:
```
# Issue — [short title]
## Blocked at: [step / file]
## What I tried: [each attempt with results]
## What I need: [specific question or decision]
## Relevant files: [paths]
```"""

HANDOFF_CHECK = (
    "Review our planning conversation and your research findings.\n\n"
    "Can an agent complete this task without asking a single question?\n\n"
    "If yes — write the full task.md now. Follow the task format below\n"
    "exactly. Output only the raw markdown.\n\n"
    "If no — state exactly what is missing (specific questions, not vague\n"
    "concerns) and ask the user to run /handoff again to proceed anyway." + _TASK_FORMAT
)

HANDOFF_REQ = (
    "Write the task.md now with the best information available.\n\n"
    "If there are unresolved items, open with:\n\n"
    "> ⚠ Incomplete — the following items were unresolved at writing time\n"
    "> and may require agent judgment or raise an issue:\n"
    "> - [item]\n\n"
    "Then write the full task following the format below. Output only raw\n"
    "markdown." + _TASK_FORMAT
)


def _write_task(content, filename):
    p = Path.cwd() / filename
    p.write_text(content)
    print(f"  {G}wrote → {p}{R}\n")


def do_handoff(profile, s, filename, claude_md):
    if not s.get("session_id"):
        print(f"  {Y}nothing to plan yet{R} — describe what you want to build first\n")
        return

    attempted = s.get("handoff_attempted", False)
    msg = HANDOFF_REQ if attempted else HANDOFF_CHECK

    print()
    reply, i, o, sid = run_claude_safe(
        profile, s, msg, claude_md, spinner_msg="writing task", skip_perms=True
    )
    if reply is None:
        print("cancelled.\n")
        return

    print(reply + "\n")

    if attempted:
        # second attempt — write unconditionally, reset flag before save
        s["handoff_attempted"] = False
        apply_reply(s, reply, i, o, sid)
        _write_task(reply, filename)
    else:
        # first attempt — just flip the flag, don't write
        s["handoff_attempted"] = True
        apply_reply(s, reply, i, o, sid)


# --- completer ---
class CchatCompleter(Completer):
    _cmds = {
        "/attach": "attach file(s) or zip",
        "/skill": "inject a SKILL.md",
        "/system": "show or set system prompt",
        "/sessions": "list saved sessions",
        "/plan": "planning mode (read-only)",
        "/plan danger": "planning mode (full tools, sandbox only)",
        "/chat": "switch to chat mode",
        "/handoff": "write task.md",
        "/compact": "reset session",
        "/clear": "reset session (alias)",
        "/help": "show help",
        "/usage": "session stats",
        "/quit": "save and quit",
        "/exit": "save and quit",
    }
    _path = PathCompleter(expanduser=True)
    _path_cmds = ("/attach ", "/skill ")

    def get_completions(self, document, complete_event):
        txt = document.text_before_cursor
        # path completion after /attach or /skill + space
        for cmd in self._path_cmds:
            if txt.startswith(cmd):
                sub = Document(txt[len(cmd):], len(txt[len(cmd):]))
                yield from self._path.get_completions(sub, complete_event)
                return
        # command completion: only when line starts with /
        if txt.startswith("/"):
            for cmd, desc in self._cmds.items():
                if cmd.startswith(txt):
                    yield Completion(cmd, start_position=-len(txt), display_meta=desc)


# --- key bindings ---
def make_bindings():
    kb = KeyBindings()

    # ctrl+g always submits (any vi mode)
    @kb.add(SUBMIT_KEY)
    def _(event):
        event.current_buffer.validate_and_handle()

    # enter + completion menu open → accept completion, don't submit
    @kb.add("enter", filter=has_completions, eager=True)
    def _(event):
        event.current_buffer.complete_state = None

    # enter in insert mode (vi or emacs) → submit if /command, else newline
    @kb.add("enter", filter=~vi_navigation_mode & ~has_completions)
    def _(event):
        buf = event.current_buffer
        if buf.text.lstrip().startswith("/"):
            buf.validate_and_handle()
        else:
            buf.insert_text("\n")

    # F4 toggles vi/emacs
    @kb.add("f4")
    def _(event):
        app = event.app
        if app.editing_mode == EditingMode.VI:
            app.editing_mode = EditingMode.EMACS
        else:
            app.editing_mode = EditingMode.VI

    return kb


QUIT = {"/exit", "/quit", "/q"}


# --- main ---
def run():
    ap = argparse.ArgumentParser(prog="cchat", add_help=False)
    ap.add_argument("session", nargs="?", default="default")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--plan-danger", action="store_true")
    ap.add_argument("-a", "--ask", nargs="?", const="-")
    ap.add_argument("--file", nargs="+", default=[])
    ap.add_argument("-h", "--help", action="store_true")
    args = ap.parse_args()

    # help before anything else
    if args.help and args.ask is None:
        s = load_session(args.session)
        cfg = load_cfg()
        prof_name = os.environ.get("CCHAT_PROFILE", cfg["default_profile"])
        profile = cfg["profiles"].get(prof_name, {})
        print(_help_art())
        return

    cfg = load_cfg()
    prof_name = os.environ.get("CCHAT_PROFILE", cfg["default_profile"])
    profile = cfg["profiles"].get(prof_name)
    if not profile:
        print(f"unknown profile '{prof_name}'. available: {', '.join(cfg['profiles'])}")
        sys.exit(1)

    # one-off ask mode
    if args.ask is not None:
        q = sys.stdin.read().strip() if args.ask == "-" else args.ask
        if not q:
            print("no question provided")
            return
        s = {
            "name": "_ask",
            "session_id": None,
            "system": CHAT_SYSTEM,
            "mode": "chat",
            "turns": 0,
            "total_input": 0,
            "total_output": 0,
        }
        ctx = ""
        if args.file:
            c, warns, _ = build_file_context(args.file)
            for w in warns:
                print(f"  warn: {w}")
            ctx = c
        msg = f"{ctx}\n\n{q}" if ctx else q
        print()
        reply, i, o, _ = run_claude(profile, s, msg)
        if reply is None:
            print("cancelled.")
            return
        print(reply + "\n")
        print(f"  [in: {i:,}  out: {o:,}]\n")
        return

    # interactive session
    sess_name = args.session
    s = load_session(sess_name)
    claude_md = find_claude_md()
    DIR.mkdir(exist_ok=True)

    # apply --plan / --plan-danger flags
    if args.plan_danger:
        s["mode"] = "plan"
        s["system"] = PLAN_SYSTEM
        s["plan_danger"] = True
        save_session(s)
    elif args.plan and s.get("mode") != "plan":
        s["mode"] = "plan"
        s["system"] = PLAN_SYSTEM
        s["plan_danger"] = False
        save_session(s)

    sc = skill_count(profile)
    pending_skill = None
    pending_ctx = ""  # pre-rendered file context for next message
    pending_files = [0]  # mutable for closure access

    def ctx_count():
        return pending_files[0] + (1 if pending_skill else 0)

    ps = PromptSession(
        history=FileHistory(str(DIR / f"{sess_name}.history")),
        key_bindings=make_bindings(),
        multiline=True,
        vi_mode=True,
        cursor=ModalCursorShapeConfig(),
        completer=CchatCompleter(),
        complete_while_typing=False,
        bottom_toolbar=make_toolbar(s, prof_name, sc, ctx_count),
        style=Style.from_dict({"bottom-toolbar": "noreverse"}),
    )

    print(_help_art())
    set_title("cchat")  # shell
    try:
        subprocess.run(["tmux", "set-window-option", "automatic-rename", "off"], check=True)
        subprocess.run(["tmux", "rename-window", "cchat"], check=True)
    except subprocess.CalledProcessError:
        pass
    if claude_md:
        print(f"  {DW}CLAUDE.md loaded from {Path.cwd()}{R}")
    print()

    def vi_prompt():
        try:
            app = get_app()
            if app.editing_mode == EditingMode.VI:
                if app.vi_state.input_mode.value == "vi-insert":
                    return ANSI(f"{G}❯{R} ")
                return ANSI(f"{G}❮{R} ")
        except Exception:
            pass
        return ANSI(f"{G}❯{R} ")

    while True:
        try:
            line = ps.prompt(vi_prompt).strip()
        except (EOFError, KeyboardInterrupt):
            save_session(s)
            clear_title()
            print("\033]2;\033\\", end="", flush=True)
            try:
                subprocess.run(["tmux", "set-window-option", "automatic-rename", "on"], check=True)
            except subprocess.CalledProcessError:
                pass
            print("\nsaved.")
            break

        if not line:
            pass
        elif line in QUIT:
            save_session(s)
            clear_title()
            try:
                subprocess.run(["tmux", "set-window-option", "automatic-rename", "on"], check=True)
            except subprocess.CalledProcessError:
                pass
            break
        elif line == "/help":
            print(_help_art())

        elif line == "/plan" or line == "/plan danger":
            danger = line.endswith("danger")
            prev_mode = s.get("mode", "chat")
            prev_danger = s.get("plan_danger", False)
            if prev_mode == "plan" and prev_danger == danger:
                label = f"{RE}plan (danger){R}" if danger else f"{Y}plan{R}"
                print(f"  already in {label} mode\n")
            else:
                # switching permission tier requires a fresh session
                if prev_mode == "plan" and prev_danger != danger:
                    s["session_id"] = None
                s["mode"] = "plan"
                s["system"] = PLAN_SYSTEM
                s["plan_danger"] = danger
                label = f"{RE}plan (danger){R}" if danger else f"{Y}plan{R}"
                if s.get("session_id") and prev_mode != "plan":
                    # has history from chat — send transition message
                    print()
                    try:
                        reply, i, o, sid = run_claude_safe(
                            profile,
                            s,
                            PLAN_TRANSITION,
                            claude_md,
                            spinner_msg="switching to plan mode",
                        )
                        if reply is None:
                            print("cancelled.\n")
                        else:
                            print(reply + "\n")
                            apply_reply(s, reply, i, o, sid)
                    except Exception as e:
                        print(f"\nerror switching: {e}\n")
                        s["session_id"] = None
                        save_session(s)
                else:
                    if danger:
                        print(f"  {label} — full tool access, use in sandbox only\n")
                    else:
                        print(
                            f"  {label} — read-only, describe what you want to build\n"
                        )
                    save_session(s)

        elif line == "/chat":
            s["mode"] = "chat"
            s["system"] = CHAT_SYSTEM
            s["plan_danger"] = False
            print(f"  {DW}chat mode{R}\n")
            save_session(s)

        elif line.startswith("/handoff"):
            parts = line.split(None, 1)
            filename = (parts[1].strip() if len(parts) > 1 else "task") + ".md"
            if s.get("mode") != "plan":
                s["mode"] = "plan"
                s["system"] = PLAN_SYSTEM
                print(f"  {Y}switching to plan mode{R} — what are we planning?\n")
                save_session(s)
            else:
                do_handoff(profile, s, filename, claude_md)

        elif line in ("/clear", "/compact"):
            s["session_id"] = None
            s["turns"] = 0
            s["total_input"] = 0
            s["total_output"] = 0
            s["handoff_attempted"] = False
            s["plan_danger"] = False
            s["mode"] = "chat"
            s["system"] = CHAT_SYSTEM
            pending_skill = None
            pending_ctx = ""
            pending_files[0] = 0
            save_session(s)
            print("compacted — fresh session started\n")

        elif line == "/sessions":
            rows = []
            for p in sorted(DIR.glob("*.json")):
                if p.stem == "config":
                    continue
                d = json.loads(p.read_text())
                sid = (d.get("session_id") or "")[:8]
                rows.append(
                    f"  {C}{p.stem}{R}  mode={d.get('mode', 'chat')}  turns={d.get('turns', 0)}  id={sid}…"
                )
            print("\n".join(rows) or "none")

        elif line == "/system":
            print(s["system"])
        elif line == "/usage":
            ti, to = s.get("total_input", 0), s.get("total_output", 0)
            warn = token_warning(ti).strip()
            print(
                f"session:      {s['name']}\nprofile:      {prof_name}\nmode:         {s.get('mode', 'chat')}\nturns:        {s.get('turns', 0)}\nskills:       {sc}\ncode-session: {s.get('session_id', 'none')}\ntokens in:    {ti:,}\ntokens out:   {to:,}"
            )
            if warn:
                print(warn)
            print()

        elif line.startswith("/system "):
            s["system"] = line[8:].strip()
            print("updated\n")

        elif line.startswith("/skill "):
            p = Path(line[7:].strip()).expanduser()
            if p.exists():
                pending_skill = p.read_text().strip()
                print(f"  skill armed: {p.name}\n")
            else:
                print(f"not found: {p}\n")

        elif line.startswith("/attach "):
            paths = line[8:].strip().split()
            ctx, warns, n = build_file_context(paths)
            for w in warns:
                print(f"  warn: {w}")
            if ctx:
                pending_files[0] += n
                pending_ctx += ("\n\n" if pending_ctx else "") + ctx
                print(f"  {n} added, {pending_files[0]} file(s) armed\n")

        elif line.startswith("/"):
            print("unknown — /help for commands\n")

        else:
            parts = []
            if pending_ctx:
                parts.append(pending_ctx)
                pending_ctx = ""
            if pending_skill:
                parts.append(f"<skill>\n{pending_skill}\n</skill>")
                pending_skill = None
            pending_files[0] = 0
            parts.append(line)
            msg = "\n\n".join(parts)
            print()
            try:
                reply, i, o, sid = run_claude_safe(profile, s, msg, claude_md)
                if reply is None:
                    print("cancelled.\n")
                    continue
                print(reply + "\n")
                apply_reply(s, reply, i, o, sid)
            except Exception as e:
                print(f"\nerror: {e}\n")


if __name__ == "__main__":
    run()
