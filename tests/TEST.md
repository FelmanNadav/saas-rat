# Test Guide

## Automated Tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

90 tests, no external dependencies, no network calls.

### Coverage

| Area | Tests |
|------|-------|
| PassthroughFragmenter | always single chunk, empty, large input |
| FixedFragmenter | under/at/over threshold, chunk count, reassembly recovery |
| `_reassemble_fragments` | in-order, out-of-order, incomplete dropped, two interleaved IDs, malformed status, mixed rows, done_status applied |
| `build_outbox/inbox_fragments` | status format, metadata preserved, command field preserved |
| PlaintextEncryptor | encrypt/decrypt identity, special chars |
| FernetEncryptor | roundtrip, ciphertext ≠ plaintext, non-deterministic, wrong key raises, missing key raises |
| `_encrypt_row` / `_decrypt_row` | empty fields skipped, bad ciphertext passes through silently |
| `_translate_row` | with/without map, unknown keys pass through, partial map |
| `_get_column_map` | valid JSON, invalid JSON, missing env, empty string |
| `get_encryptor` / `get_fragmenter` | all methods, unknown method fallback, case insensitive |
| `handle_echo` | identity, empty payload |
| `handle_system_info` | required keys, string values |
| `handle_shell` | basic, no cmd, nonzero returncode, stderr, timeout (mocked), stdin ignored with sudo -S, stdin used otherwise |
| `handle_config` | known/unknown/mixed keys, disk persistence, type coercion, result shape |
| `dispatch` | routing, unknown command, command_id preserved, small/large result fragmentation, fragment status format |
| `read_tab` | CSV parse, empty sheet |
| `read_inbox` | normal rows, fragment reassembly, column obfuscation |
| `read_outbox` | normal rows, out-of-order fragments, Fernet decryption |
| `write_form` | URL, field→entry mapping, Fernet encrypts fields, return codes (ok/redirect/error) |
| Full roundtrip | plaintext fragments write→read→reassemble, Fernet+fragments write→read→reassemble |

### Not covered by automation

| Gap | Reason |
|-----|--------|
| Multi-cycle send queue (one fragment per poll) | Requires channel abstraction — see `ideas/pluggable_channels.md` |
| `send_heartbeat` | Real network call |
| AI console | GPT-4o — excluded by design |
| `server.py collect` / `collect_new` | Thin wrappers over `read_outbox` (tested) |
| `server.py send_command` fragmentation | Thin wrapper over tested primitives |
| Column obfuscation with real sheet headers | Requires Google Forms rename |
| Client restart with persisted send queue | Requires running process |

---

## Manual Test Guide

### Prerequisites

```bash
source venv/bin/activate
# Confirm .env is populated:
#   SPREADSHEET_ID, INBOX_GID, OUTBOX_GID
#   FORMS_URL, FORMS_FIELD_MAP
#   INBOX_FORMS_URL, INBOX_FORMS_FIELD_MAP
```

Open two terminals — Terminal A for the client, Terminal B for the server.

---

### Test 1 — Heartbeat and system_info

```bash
# Terminal A
python client.py

# Terminal B
python server.py send --command system_info
python server.py collect
```

**Expected:**
- Terminal A shows `[client] Heartbeat sent` on startup
- `collect` returns a row with `os`, `hostname`, `username`, `python_version`
- Sheet outbox has one `heartbeat` row and one `success` row

---

### Test 2 — Shell command round-trip

```bash
python server.py send --command shell --payload '{"cmd": "whoami"}'
# wait one poll cycle
python server.py collect
```

**Expected:**
- Result contains the client machine username
- Single row written to outbox (no fragments)

---

### Test 3 — Config command

```bash
python server.py send --command config --payload '{"poll_interval_sec": "10", "client_id": "agent-99"}'
# wait one poll cycle
python server.py collect
```

**Expected:**
- Terminal A shows `[client] Config updated: {'poll_interval_sec': '10', 'client_id': 'agent-99'}`
- `.client_config.json` exists on client machine with new values
- Client polls at ~10s intervals from next cycle onward
- Subsequent heartbeats use `client_id: agent-99`

**Restart persistence:**
- Kill the client (`Ctrl+C`), restart it
- Terminal A shows `[client] Loaded config from .client_config.json`
- Poll interval and client_id are retained without re-sending the config command

---

### Test 4 — Fragmentation (plaintext)

Set in `.env`:
```
FRAGMENT_METHOD=fixed
FRAGMENT_CHUNK_SIZE=100
```
Restart both client and server.

```bash
python server.py send --command shell --payload '{"cmd": "cat /etc/passwd"}'
# watch Terminal A across several poll cycles
python server.py collect   # run after Terminal A shows queue empty
```

**Expected:**
- Terminal A shows `fragment 0/N sent, N queued` after first cycle
- Each subsequent cycle shows one more fragment sent and queue shrinking
- Sheet outbox contains multiple rows with `frag:N:T` in the status column
- `collect` returns the full `/etc/passwd` content after all fragments are sent

---

### Test 5 — Fernet encryption + fragmentation

Set in `.env`:
```
ENCRYPTION_METHOD=fernet
ENCRYPTION_KEY=<generated key>
FRAGMENT_METHOD=fixed
FRAGMENT_CHUNK_SIZE=100
```
Restart both sides.

```bash
python server.py send --command shell --payload '{"cmd": "find / -maxdepth 3 2>/dev/null"}'
# wait for all cycles to complete
python server.py collect
```

**Expected:**
- Sheet cells contain only ciphertext — no readable values
- `collect` reassembles fragments and decrypts, returning full plaintext output

---

### Test 6 — Column obfuscation

> Requires manual Google Forms and sheet setup.

1. Rename sheet column headers to match `INBOX_COLUMN_MAP` / `OUTBOX_COLUMN_MAP` values
2. Rename Google Form field labels to match
3. Set both maps in `.env` on both client and server machines
4. Restart both sides

```bash
python server.py send --command shell --payload '{"cmd": "id"}'
# wait one cycle
python server.py collect
```

**Expected:**
- Sheet shows only random short strings as column headers (`f3a7k`, `x9m2p`, etc.)
- Values are encrypted ciphertext (if Fernet also enabled)
- `collect` returns plaintext `id` output — both sides resolve maps correctly

---

### Test 7 — Client restart with queue mid-flight

Set `FRAGMENT_CHUNK_SIZE=50`, run a large command:

```bash
python server.py send --command shell --payload '{"cmd": "find / -maxdepth 2 2>/dev/null"}'
```

- Watch Terminal A until fragment 0 is sent (queue has items)
- Kill the client with `Ctrl+C`
- Restart the client

**Expected:**
- Terminal A shows `[client] Resumed send queue: N fragment(s) pending`
- Remaining fragments delivered over subsequent cycles
- `python server.py collect` returns full output

---

### Test 8 — AI console smoke test

```bash
python server.py ai
```

Run through this sequence:

| Input | Expected |
|-------|----------|
| `get system info` | GPT-4o dispatches `system_info`, result arrives, session facts populated |
| `run whoami` | Dispatches `shell` with `{"cmd": "whoami"}` |
| `show results` | Displays arrived results without sending a new command |
| `mode raw` | Switches to raw output mode |
| `show results` | Displays unformatted stdout |
| `mode confirm` | Switches to confirm mode |
| `run ls /tmp` | Shows suggested command, waits for `yes` / `do it` |
| `yes` | Dispatches the pending command |
| `exit` | Exits cleanly |

**Also verify:**
- Session facts (OS, sudo creds if confirmed) are auto-populated from results
- Destructive commands (`rm -rf`, `kill -9`) show a warning and require confirmation regardless of mode
