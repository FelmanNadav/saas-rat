# Setup Wizard

`setup_wizard.py` is the recommended way to configure the framework. It walks through every setting interactively and writes a complete `.env` file at the end. It can also auto-create the Google Sheet and Forms via an Apps Script.

```bash
python setup_wizard.py
```

---

## Wizard Steps

### 1. Encryption

Choose the encryption method applied to all values written to the sheet.

| Option | Description |
|--------|-------------|
| `plaintext` | No encryption — values appear in cleartext. Use for debugging. |
| `fernet` | AES-128-CBC + HMAC-SHA256. Recommended for ops. |

If you choose Fernet, the wizard generates a key automatically and writes it to `.env`. Copy the key to every machine running the client or server — both must use the same key.

### 2. Column Obfuscation

Optionally replace logical column names (`command_id`, `status`, etc.) with short random strings. The sheet looks like meaningless data to a casual observer. Values can still be encrypted independently — the two features compose.

| Mode | Sheet appearance |
|------|-----------------|
| Off | Readable column names |
| On | Random strings (e.g. `f3a7k`, `x9m2p`) |

If enabled, the wizard generates the maps and shows you the names to use when creating the sheet and forms.

### 3. Channel Setup

Choose a channel — the transport layer for all C2 traffic.

| Channel | Traffic destination | Cleanup |
|---|---|---|
| `sheets` | `docs.google.com` | Auto per-message (service account) or manual (`sheets_c2_cleanup.gs`) |
| `firebase` | `firebaseio.com` | Automatic after result confirmed (inbox + outbox) |

**Sheets — two sub-modes:**

*Auto (Apps Script) — recommended*

The wizard writes `sheets_c2_setup.gs`. You paste it into [script.google.com](https://script.google.com), run `setup()`, copy the JSON from the execution log, and paste it back. The wizard fills in all sheet/form IDs automatically.

The wizard also writes `sheets_c2_cleanup.gs` — run `cleanupAll()` to delete all data rows from inbox and outbox (keeps headers). Use `installTrigger()` to automate this on a schedule.

*Manual*

Step-by-step browser instructions. The wizard asks for each value individually. See [Manual Google Sheets Setup](#manual-google-sheets-setup) below.

**Firebase:**

The wizard walks through creating a Firebase Realtime Database in the Firebase console, setting public read/write rules (encryption handles content security), and runs a live connection test before writing env vars. No Apps Script required.

The Firebase wizard generates field name obfuscation maps (`FIREBASE_INBOX_COLUMN_MAP` / `FIREBASE_OUTBOX_COLUMN_MAP`) automatically — enabled by default. If Sheets column obfuscation was enabled in Step 2, the same maps are reused for Firebase. Otherwise the wizard generates new maps and writes them to `.env`. Both client and server must use the same maps.

See [docs/firebase.md](firebase.md) for the full Firebase channel reference.

### 4. Fragmentation

Choose how large results are handled.

| Option | Description |
|--------|-------------|
| `passthrough` | Send results in a single write. Default. |
| `fixed` | Split into fixed-size chunks (default 2000 bytes). Required when results exceed Google Forms' ~4000 character field limit. |

### 5. Extras

- **OpenAI API key** — required for `server.py ai` mode only. Skip if using CLI mode.
- **Client ID** — identifier reported in results. Defaults to `NADAV`. Useful when running multiple clients.

### 5b. Sheets Cleanup (Sheets channel only)

Choose how inbox and outbox rows are cleaned up:

| Option | How it works | GCP required |
|--------|-------------|-------------|
| **Service account** | Deletes each row immediately after the result is confirmed — per-message, automatic | Yes |
| **Apps Script trigger** | Scheduled batch cleanup on a configurable interval (default 6h) — generates `sheets_c2_cleanup.gs` | No |
| **Skip** | No automatic cleanup — run `cleanupAll()` in script.google.com manually | No |

**Service account setup (inline in wizard):**
1. Go to [console.cloud.google.com](https://console.cloud.google.com) → same project as your sheet
2. IAM & Admin → Service Accounts → Create Service Account → give it a name → Done
3. Click the service account → Keys tab → Add Key → JSON → Create → save the file
4. Copy `client_email` from the JSON → open your sheet → Share → paste email → Editor → Send
5. Enter the path to the JSON file when the wizard prompts

**Apps Script trigger setup:**
The wizard generates `sheets_c2_cleanup.gs` with your spreadsheet IDs and chosen interval baked in.
1. Go to [script.google.com](https://script.google.com) → create a new project
2. Paste the contents of `sheets_c2_cleanup.gs`
3. Run `installTrigger()` once — cleanup runs automatically on the chosen schedule
4. Run `cleanupAll()` at any time for an immediate manual sweep
5. Run `removeTrigger()` to disable the schedule

### 6. Summary and Write

The wizard shows a full summary of all settings before writing. Confirm to write `.env`.

---

## Manual Google Sheets Setup

### Create the Spreadsheet

Create one spreadsheet with **two tabs**: `inbox` and `outbox`.

Share it as **"Anyone with the link can view"** — required for unauthenticated CSV export.

### Tab: `inbox`

Default column headers:

```
command_id, command, payload, target, status, created_at
```

If using column obfuscation, use the random names generated by the wizard instead.

### Tab: `outbox`

Default column headers:

```
command_id, client_id, status, result, timestamp
```

> When linking a Google Form to a sheet, Forms auto-creates a new tab. Re-check `?gid=` values after linking — the linked tab has a different GID than your original empty tab. Rename the auto-added `Timestamp` column to `form_timestamp` to avoid collision.

---

## Manual Google Forms Setup

Two forms required — one per tab.

### Outbox Form (client → server, links to `outbox` tab)

Fields: `command_id`, `client_id`, `status`, `result`, `timestamp`

### Inbox Form (server → client, links to `inbox` tab)

Fields: `command_id`, `command`, `payload`, `target`, `status`, `created_at`

**Getting entry IDs:** Open the form preview → right-click → View Page Source → search `entry.`. Each field has a unique `entry.XXXXXXXXX` ID. Required for `FORMS_FIELD_MAP`. Entry IDs never change even if you rename field labels.

---

## After Setup

The wizard writes `.env` to the project root. Both machines (server and client) need a copy of this file with the same `ENCRYPTION_KEY`.

```bash
# Start the client
python client.py

# Or via Docker
docker compose up --build

# Start the server
python server.py ai        # AI console
python server.py --help    # CLI reference
```
