# Sheets C2 Lite — Design Document

## Overview

Minimal command-and-control system over Google Sheets. A client polls the sheet for commands and writes results back. A server sends commands and reads results, with an optional AI chat mode (GPT-4o) for natural language operation.

Four files. No abstraction layers. Google Sheets hardcoded. Build time: ~2 hours.

---

## Files

```
c2-lite/
├── common.py      # Shared: CSV reader, Forms writer, config parser
├── client.py      # Poll loop — reads inbox, executes, writes to outbox
├── server.py      # Send commands + collect results + AI chat mode
└── .env           # Spreadsheet ID, GIDs, form URL, form field map, OpenAI key
```

---

## Google Sheet Layout

One spreadsheet, three tabs.

### Tab: `config`

Two columns: `key`, `value`. Read by the client every poll cycle.

| key                 | value         |
|---------------------|---------------|
| `poll_interval_sec` | `30`          |
| `poll_jitter_min`   | `5`           |
| `poll_jitter_max`   | `15`          |
| `client_id`         | `client-01`   |

Polling behavior: `sleep(poll_interval_sec + random(poll_jitter_min, poll_jitter_max))`

### Tab: `inbox`

Commands from server → client. Headers in row 1:

| command_id | command      | payload              | target | status  | created_at               |
|------------|-------------|----------------------|--------|---------|--------------------------|
| uuid-1     | system_info | {}                   | outbox | pending | 2026-03-24T10:00:00Z     |
| uuid-2     | echo        | {"msg": "hello"}     | outbox | pending | 2026-03-24T10:01:00Z     |

### Tab: `outbox`

Results from client → server. Append-only via Google Forms. Headers in row 1:

| command_id | client_id  | status  | result                          | timestamp                |
|------------|-----------|---------|----------------------------------|--------------------------|
| uuid-1     | client-01 | success | {"os": "Linux", ...}            | 2026-03-24T10:00:45Z     |

---

## .env File

```
SPREADSHEET_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
CONFIG_GID=0
INBOX_GID=123456789
OUTBOX_GID=987654321
FORMS_URL=https://docs.google.com/forms/d/e/1FAIp.../formResponse
FORMS_FIELD_MAP={"command_id":"entry.111111","client_id":"entry.222222","status":"entry.333333","result":"entry.444444","timestamp":"entry.555555"}
OPENAI_API_KEY=sk-...
```

Both `client.py` and `server.py` load this via `common.load_env()`.

---

## common.py (~80 lines)

Shared functions used by both client and server.

```python
import csv
import io
import json
import os
import requests

def load_env(path=".env"):
    """Parse .env file into os.environ."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

def sheet_url(gid):
    """Build CSV export URL for a tab."""
    sid = os.environ["SPREADSHEET_ID"]
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

def read_tab(gid):
    """Fetch a tab as list of dicts."""
    resp = requests.get(sheet_url(gid), timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)

def read_config():
    """Read config tab, return dict of key→value."""
    rows = read_tab(os.environ["CONFIG_GID"])
    return {row["key"]: row["value"] for row in rows}

def read_inbox():
    """Read inbox tab, return list of command dicts."""
    return read_tab(os.environ["INBOX_GID"])

def read_outbox():
    """Read outbox tab, return list of result dicts."""
    return read_tab(os.environ["OUTBOX_GID"])

def write_form(data):
    """POST a dict to Google Forms. Keys are logical field names."""
    field_map = json.loads(os.environ["FORMS_FIELD_MAP"])
    form_data = {}
    for key, value in data.items():
        if key in field_map:
            form_data[field_map[key]] = value
    resp = requests.post(os.environ["FORMS_URL"], data=form_data, timeout=30)
    # Forms returns 200 with redirect on success — don't raise on redirect
    return resp.ok or resp.status_code in (301, 302, 303)
```

---

## client.py (~80 lines)

```python
import json
import platform
import random
import time
from datetime import datetime, timezone

import common

def handle_system_info(payload):
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
    }

def handle_echo(payload):
    return payload

HANDLERS = {
    "system_info": handle_system_info,
    "echo": handle_echo,
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    common.load_env()
    processed = set()
    config = None

    print("[client] Starting poll loop...")

    while True:
        try:
            config = common.read_config()
            inbox = common.read_inbox()
            outbox = common.read_outbox()

            # Rebuild state from outbox on first run
            if not processed:
                processed = {r["command_id"] for r in outbox if r.get("command_id")}

            pending = [cmd for cmd in inbox
                       if cmd.get("status") == "pending"
                       and cmd["command_id"] not in processed]

            for cmd in pending:
                handler = HANDLERS.get(cmd["command"])
                if handler:
                    try:
                        payload = json.loads(cmd["payload"]) if cmd["payload"] else {}
                        result = handler(payload)
                        status = "success"
                    except Exception as e:
                        result = {"error": str(e)}
                        status = "error"
                else:
                    result = {"error": f"unknown command: {cmd['command']}"}
                    status = "error"

                common.write_form({
                    "command_id": cmd["command_id"],
                    "client_id": config.get("client_id", "unknown"),
                    "status": status,
                    "result": json.dumps(result),
                    "timestamp": now_iso(),
                })
                processed.add(cmd["command_id"])
                print(f"[client] Processed {cmd['command_id']}: {cmd['command']} → {status}")

        except Exception as e:
            print(f"[client] Poll error: {e}")

        interval = int(config.get("poll_interval_sec", 30)) if config else 30
        jitter_min = float(config.get("poll_jitter_min", 0)) if config else 0
        jitter_max = float(config.get("poll_jitter_max", 0)) if config else 0
        sleep_time = interval + random.uniform(jitter_min, jitter_max)
        print(f"[client] Sleeping {sleep_time:.1f}s...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
```

---

## server.py (~120 lines)

Three modes: `send`, `collect`, `ai`.

```python
import json
import sys
import uuid
from datetime import datetime, timezone

import common

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ── Send ──

def send_command(command, payload=None, target="outbox"):
    command_id = str(uuid.uuid4())
    common.write_form({
        "command_id": command_id,
        "command": command,
        "payload": json.dumps(payload or {}),
        "target": target,
        "status": "pending",
        "created_at": now_iso(),
    })
    print(f"[server] Sent {command_id}: {command}")
    return command_id

# ── Collect ──

def collect(filter_id=None):
    outbox = common.read_outbox()
    if filter_id:
        outbox = [r for r in outbox if r["command_id"] == filter_id]
    return outbox

# ── AI Chat ──

SYSTEM_PROMPT = """You are an operator assistant for a C2 system that communicates through Google Sheets.

Available commands you can send to remote clients:
- system_info: Returns OS, hostname, Python version, architecture. No payload needed.
- echo: Echoes back the payload. Expects {"msg": "some string"}.

Respond with a JSON action. No markdown, no preamble, no explanation outside the JSON.

To send a command:
{"action": "send_command", "command": "<name>", "payload": {<args>}}

To read results from outbox:
{"action": "read_outbox"}

To read results for a specific command:
{"action": "read_outbox", "filter_command_id": "<uuid>"}

To explain something (no backend action):
{"action": "explain", "text": "<your explanation>"}
"""

def ai_chat():
    import openai
    client = openai.OpenAI()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("[ai] Interactive mode. Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0,
            )
            reply = resp.choices[0].message.content
            messages.append({"role": "assistant", "content": reply})

            try:
                action = json.loads(reply)
            except json.JSONDecodeError:
                print(f"[ai] Raw response: {reply}")
                continue

            if action["action"] == "explain":
                print(f"\n{action['text']}\n")

            elif action["action"] == "send_command":
                print(f"\n  Command: {action['command']}")
                print(f"  Payload: {json.dumps(action.get('payload', {}))}")
                choice = input("\n  [S]end / [P]review only / [C]ancel: ").strip().lower()
                if choice == "s":
                    cmd_id = send_command(action["command"], action.get("payload"))
                    print(f"  Sent. ID: {cmd_id}\n")
                else:
                    print("  Skipped.\n")

            elif action["action"] == "read_outbox":
                fid = action.get("filter_command_id")
                results = collect(fid)
                if not results:
                    print("\n  No results found.\n")
                    continue

                results_json = json.dumps(results, indent=2)
                print(f"\n  Found {len(results)} result(s). Summarizing...\n")

                messages.append({
                    "role": "user",
                    "content": f"Here are the results:\n{results_json}\nSummarize in plain language."
                })
                summary_resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    temperature=0,
                )
                summary = summary_resp.choices[0].message.content
                messages.append({"role": "assistant", "content": summary})
                print(f"{summary}\n")

        except Exception as e:
            print(f"[ai] Error: {e}\n")

# ── CLI ──

def main():
    common.load_env()

    if len(sys.argv) < 2:
        print("Usage: python server.py <send|collect|ai>")
        print("  send --command <name> [--payload '<json>']")
        print("  collect [--id <command_id>]")
        print("  ai")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "send":
        command = None
        payload = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--command" and i + 1 < len(args):
                command = args[i + 1]; i += 2
            elif args[i] == "--payload" and i + 1 < len(args):
                payload = json.loads(args[i + 1]); i += 2
            else:
                i += 1
        if not command:
            print("Error: --command required")
            sys.exit(1)
        send_command(command, payload)

    elif mode == "collect":
        filter_id = None
        if "--id" in sys.argv:
            idx = sys.argv.index("--id")
            filter_id = sys.argv[idx + 1]
        results = collect(filter_id)
        for r in results:
            print(json.dumps(r, indent=2))

    elif mode == "ai":
        ai_chat()

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

---

## Setup (Manual, ~10 minutes)

### 1. Create the spreadsheet
- New Google Sheet → rename to "C2"
- Create three tabs: `config`, `inbox`, `outbox`
- Add headers:
  - config: `key`, `value`
  - inbox: `command_id`, `command`, `payload`, `target`, `status`, `created_at`
  - outbox: `command_id`, `client_id`, `status`, `result`, `timestamp`
- Populate config tab with default values (see schema above)
- Share → "Anyone with the link" → Viewer

### 2. Create the Google Form
- New Google Form
- Add 5 short-answer questions titled exactly: `command_id`, `client_id`, `status`, `result`, `timestamp`
- Responses → Link to Sheets → select your spreadsheet → select `outbox` tab
- Get entry IDs: open form preview URL → browser dev tools → Elements tab → search `entry.` → note each field's `entry.XXXXXXX` ID

### 3. Create .env
```
SPREADSHEET_ID=<from sheet URL>
CONFIG_GID=<from tab URL parameter>
INBOX_GID=<from tab URL parameter>
OUTBOX_GID=<from tab URL parameter>
FORMS_URL=https://docs.google.com/forms/d/e/<FORM_ID>/formResponse
FORMS_FIELD_MAP={"command_id":"entry.XXX","client_id":"entry.XXX","status":"entry.XXX","result":"entry.XXX","timestamp":"entry.XXX"}
OPENAI_API_KEY=sk-...
```

### 4. Install dependencies
```bash
pip install requests openai
```

### 5. Test
```bash
# Terminal 1 — start client
python client.py

# Terminal 2 — send a command
python server.py send --command system_info

# Wait for client to poll and process, then:
python server.py collect

# Or use AI mode:
python server.py ai
> get system info from the client
> show me the results
```

---

## Limitations (by design — keep it lite)

- No retry/backoff on failures — just logs and continues
- No backend abstraction — Google Sheets hardcoded everywhere
- No handler registry pattern — just a dict in client.py
- Config lives in the sheet but .env holds connection details (can't bootstrap without it)
- Server can't update/delete inbox rows (Forms = append only)
- Inbox grows forever until manually cleaned
- AI session history is in-memory only, lost on exit

---

## Growth Path

When this version works and you want more:
- Add retry logic to `common.write_form` and `common.read_tab`
- Extract handlers into separate files
- Add the backend abstraction from DESIGN.md (heavy version)
- Add gspread driver for server-side inbox management
- Add setup automation
- Persist AI conversation history
