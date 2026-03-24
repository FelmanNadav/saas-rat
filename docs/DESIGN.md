# Sheets C2 — Design Document

## Overview

A command-and-control system that uses Google Sheets as its communication channel and Google Forms as its write channel. A client process runs on a target machine and polls a Google Sheet for commands. Results are written back through a Google Form. An operator console on the attacker machine uses GPT-4o to translate natural language into C2 commands and interpret results.

The channel is entirely HTTPS to Google's infrastructure — no listener, no port, no inbound connection to the operator.

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
                                     │  │  (poll settings) │    │
  ┌─────────────────────┐            │  ├──────────────────┤    │
  │  Outbox Google Form │◀──appends──│  │  inbox tab       │    │
  │  (write results)    │            │  │  (commands)      │    │
  └─────────────────────┘            │  ├──────────────────┤    │
           │                         │  │  outbox tab      │    │
           │  HTTPS GET (CSV)        │  │  (results)       │    │
           ▼                         │  └──────────────────┘    │
  server.py reads outbox             └──────────────────────────┘
           │
           ▼
  ┌─────────────────────┐
  │     OpenAI API      │
  │     (GPT-4o)        │
  └─────────────────────┘
```

**Write path (commands):** `server.py` → Inbox Google Form → inbox tab of spreadsheet

**Write path (results):** `client.py` → Outbox Google Form → outbox tab of spreadsheet

**Read path (commands):** `client.py` fetches the inbox tab as CSV

**Read path (results):** `server.py` fetches the outbox tab as CSV

Google Forms is used for all writes because the spreadsheet itself requires OAuth for direct edits. Forms bypass this — they accept anonymous POST submissions and append rows to a linked sheet.

---

## Files

| File | Role |
|------|------|
| `common.py` | Shared I/O: env loading, CSV reading, Forms writing |
| `client.py` | Runs on target. Poll loop, command execution, heartbeat |
| `server.py` | Runs on operator machine. AI CLI, background polling, session state |
| `.env` | Spreadsheet IDs, GIDs, form URLs, field maps, OpenAI key |

---

## common.py

Shared utilities used by both `client.py` and `server.py`. No state, no classes.

### Functions

| Function | Description |
|----------|-------------|
| `load_env(path=".env")` | Parses the `.env` file and injects each `KEY=value` pair into `os.environ`. Ignores blank lines and comments. |
| `sheet_url(gid)` | Builds the CSV export URL for a given spreadsheet tab GID. Reads `SPREADSHEET_ID` from env. |
| `read_tab(gid)` | GETs a tab as CSV and returns a list of dicts (one per row) via `csv.DictReader`. |
| `read_config()` | Reads the `config` tab, returns a flat `{key: value}` dict. |
| `read_inbox()` | Reads the `inbox` tab, returns list of command dicts. |
| `read_outbox()` | Reads the `outbox` tab, returns list of result dicts. |
| `write_form(data)` | POSTs a dict to the outbox Google Form. Maps logical field names to `entry.XXXXXXX` IDs via `FORMS_FIELD_MAP`. |
| `write_inbox_form(data)` | POSTs a dict to the inbox Google Form. Maps via `INBOX_FORMS_FIELD_MAP`. |

**Note on write_form / write_inbox_form:** Google Forms returns HTTP 200 with a redirect (301/302/303) on success. Both functions treat any of these as success to avoid false failures.

---

## client.py

Runs continuously on the target machine. Polls the inbox for pending commands, executes them, and writes results to the outbox. Also sends periodic heartbeats so the operator knows the client is alive and can identify the target OS.

### Top-level functions

| Function | Description |
|----------|-------------|
| `_system_info()` | Collects host metadata: OS name, OS version, hostname, Python version, architecture, and username (via `getpass`). Returns a dict. |
| `handle_system_info(payload)` | Command handler. Calls `_system_info()` and returns the dict as the result. |
| `send_heartbeat(config)` | Writes a heartbeat row to the outbox via `write_form`. Uses `command_id="heartbeat-<uuid>"`, `status="heartbeat"`, and `result=json.dumps(_system_info())`. Does not wait for a response. |
| `now_iso()` | Returns the current UTC time as an ISO 8601 string. |
| `handle_echo(payload)` | Returns the payload unchanged. |
| `handle_shell(payload)` | Runs an arbitrary bash command via `subprocess.run(shell=True)`. Accepts `cmd` (the shell command string) and `stdin` (optional string piped to the process). **Sudo conflict guard:** if the command contains `| sudo -S` and `stdin` is also set, `stdin` is silently discarded to prevent subprocess receiving conflicting input. Returns `{stdout, stderr, returncode}`. Timeout: 30 seconds. |

### Handler registry

```python
HANDLERS = {
    "system_info": handle_system_info,
    "echo": handle_echo,
    "shell": handle_shell,
}
```

### Poll loop (`main`)

1. Calls `common.load_env()`.
2. Initialises `processed` (set of already-handled command IDs), `poll_cycle=0`, `last_heartbeat_cycle=-10`.
3. Loops forever:
   - Reads config from the sheet (interval, jitter, client_id).
   - **Heartbeat check:** if `poll_cycle - last_heartbeat_cycle >= 10`, calls `send_heartbeat()`. This fires on cycle 0 (startup) and every 10 cycles thereafter.
   - Reads inbox and outbox.
   - **First-run deduplication:** if `processed` is empty, pre-populates it from existing outbox rows so commands sent before the client started are not re-executed.
   - Filters inbox for rows where `status == "pending"` and `command_id` not in `processed`.
   - For each pending command: looks up the handler, parses the JSON payload, runs the handler, writes the result to the outbox form, adds `command_id` to `processed`.
   - Sleeps for `poll_interval_sec + random(jitter_min, jitter_max)` seconds.
4. Errors in the poll body are caught and logged; the loop always continues.

---

## server.py

The operator-facing process. Three top-level CLI modes (`send`, `collect`, `ai`). The `ai` mode is the primary interface — an interactive terminal driven by GPT-4o.

### Module-level functions

| Function | Description |
|----------|-------------|
| `now_iso()` | Returns current UTC time as ISO 8601 string. |
| `send_command(command, payload, target)` | Generates a UUID, POSTs to the inbox form via `write_inbox_form`. Returns the command ID. |
| `collect(filter_id)` | Reads the full outbox tab. Optionally filters to a single command ID. |
| `_wants_raw(text)` | Returns True if the user input contains keywords indicating they want raw output: `raw`, `json`, `dump`, `exact`, `full output`, `complete output`. |
| `_wants_summary(text)` | Returns True if the user input contains keywords requesting an AI summary: `summarize`, `summary`, `interpret`, `what does it mean`, etc. |
| `_strip_markdown(text)` | Strips markdown formatting (bold, italic, fenced code, inline code, headings, bullets) for clean terminal display. |
| `_sanitize_for_gpt(text)` | Removes null bytes and stray control characters (0x01–0x08, 0x0B, 0x0C, 0x0E–0x1F, 0x7F) that cause OpenAI API 400 errors. Preserves tab, newline, carriage return. |
| `_print_results(results)` | Renders a list of outbox rows directly to the terminal without GPT. Shell results print `stdout`/`stderr` with ANSI color; `system_info`/`echo` results print `key: value` pairs. Returns `True` if any result had an error. |
| `collect_new(seen_ids, filter_id)` | Reads the outbox, returns only rows not yet in `seen_ids`. Heartbeat rows are silently added to `seen_ids` and never returned. Updates `seen_ids` in place. |
| `_result_summary(row)` | Produces a ≤120-char one-line summary of an outbox row for the command log (shown in the session log injected into GPT context). |
| `_cmd_desc(action)` | Produces a short description of a command action for the session log. For `shell`, returns the raw `cmd` string. |
| `_build_api_messages(messages, cmd_log, client_os_info, output_mode, session_facts)` | Assembles the final messages list for an OpenAI API call. Injects four optional system messages immediately before the last user turn: (1) SESSION FACTS, (2) connected client info, (3) output mode hint, (4) session command log. Returns the unmodified list if no extras apply. |
| `_ask_mode()` | Interactive prompt at session start: asks the operator to choose auto or confirm mode. Returns `"auto"` or `"confirm"`. |
| `_ask_output_mode()` | Interactive prompt: asks for raw or interpreted output mode. Returns `"raw"` or `"interpreted"`. (Currently not called at startup; output_mode is hardcoded to `"interpreted"` with runtime switching available.) |
| `_print_command_preview(action)` | Prints command/payload/target in yellow for the confirm-mode preview. |
| `_dispatch_send(action, send_mode)` | Central send gate. Checks for `warning` field (always prompts), then respects `send_mode`. In `auto` mode with no warning, sends immediately. In `confirm` mode, shows preview and waits for `s`/`c`. Returns `command_id` or `None` if cancelled. |

### `ai_chat()` — session state

All session state is local to `ai_chat()`. No global state.

| Variable | Type | Purpose |
|----------|------|---------|
| `messages` | `list[dict]` | OpenAI conversation history. `messages[0]` is always the system prompt. |
| `arrived_ids` | `set` | Command IDs whose results the background poll has observed in the outbox. |
| `seen_ids` | `set` | Command IDs the user has actually viewed (superset of `arrived_ids` after display). |
| `pending_results` | `dict[str, dict]` | Maps `cmd_id` → outbox row. Populated by background poll threads. Used by the raw bypass. |
| `client_os_info` | `dict[str, dict]` | Maps `client_id` → latest heartbeat info dict (OS, hostname, arch, user). |
| `cmd_log` | `list[dict]` | Ordered list of all commands sent this session. Each entry: `{desc, cmd_id, result, command, payload}`. |
| `cmd_id_to_idx` | `dict[str, int]` | Fast index from `cmd_id` to its position in `cmd_log`. |
| `session_facts` | `list[str]` | Confirmed facts auto-detected from results (sudo creds, root access, OS). Injected into every GPT call. |
| `pending_suggestion` | `list[None\|dict]` | Single-element mutable list. Background threads write a `send_command` action dict here. Main loop reads it when the operator types "do it". |
| `pending_suggestion_lock` | `threading.Lock` | Guards access to `pending_suggestion[0]`. |
| `send_mode` | `str` | `"auto"` or `"confirm"`. Set at startup, changeable mid-session. |
| `output_mode` | `str` | `"interpreted"` (default) or `"raw"`. Changeable mid-session. |

### `ai_chat()` — inner functions (closures)

| Function | Description |
|----------|-------------|
| `_interpret(results)` | Secondary GPT-4o call: asks the model to summarize a list of outbox rows in 1–3 sentences. Raises `BadRequestError` to the caller on 400 errors so the caller can fall back to `_print_results`. |
| `_analyze_result(row)` | GPT-4o call triggered immediately when a background poll result arrives. Asks the model to return `{summary, suggestion, action}` as JSON. Returns `(None, None, None)` on any error. The `action` is stored as `pending_suggestion[0]` for "do it". |
| `_start_poll_thread(cmd_id, command_desc)` | Spawns a daemon `threading.Thread` that polls the outbox every `poll_interval_sec + jitter_max` seconds for up to 5 minutes. When the target row arrives: adds to `arrived_ids`/`seen_ids`, stores in `pending_results`, calls `_update_session_facts`, displays result per `output_mode`, calls `_analyze_result`, prints `[Suggestion]`, reprints the prompt. Heartbeats encountered during polling are silently absorbed into `client_os_info`. |
| `_update_session_facts(cmd_id, row)` | Inspects a newly arrived result and appends facts to `session_facts` when confirmed: (1) sudo credentials — extracts the password from the `echo 'X' \| sudo -S` pipe pattern when the command succeeds; (2) root access — `whoami` output is `"root"`; (3) OS — populated from a `system_info` result; (4) uname OS — from `uname` command stdout. Facts are deduplicated; OS facts replace previous OS entries. Prints `[Fact] ...` in dim text when a new fact is added. |
| `_record_send(action, cmd_id)` | Adds a command to `cmd_log` (storing `desc`, `cmd_id`, `result=None`, `command`, `payload`), updates `cmd_id_to_idx`, and calls `_start_poll_thread`. |
| `_update_log_from_results(results)` | Updates `cmd_log[n]["result"]` with a one-line summary for any command ID that appears in the results list. Also calls `_update_session_facts` for each result. |
| `_fetch_results(fid, show_all)` | Returns `collect(fid)` (all rows) if `show_all` is True, otherwise `collect_new(seen_ids, fid)` (unseen rows only). |

### `ai_chat()` — main input loop

Each iteration:

1. **Read input** — `input()` with cyan `>` prompt. `EOFError`/`KeyboardInterrupt` exits.

2. **Client-side commands** (never touch GPT-4o):
   - `mode auto` / `mode confirm` — switch send mode.
   - `mode` — print current send mode.
   - `output raw` / `output r` — switch to raw output mode.
   - `output interpreted` / `output i` — switch to interpreted output mode.
   - `output` — print current output mode.
   - `do it` / `yes` / `go` / `y` — execute `pending_suggestion[0]` if it is a `send_command` action.
   - `exit` / `quit` — break the loop.

3. **Raw output bypass** — before any GPT call, checks if user input contains: `raw`, `exact`, `terminal output`, `full output`, `like a terminal`. If matched: walks `cmd_log` in reverse to find the most recent `cmd_id` in `pending_results`, prints its `stdout` field directly, and `continue`s. GPT-4o is never called.

4. **Per-turn output override** — `_wants_raw()` and `_wants_summary()` set `effective_output`, `show_raw`, and `show_all` for this turn without changing the session default.

5. **GPT-4o call** — appends the user message to `messages`, calls `_build_api_messages` to inject context, calls `client.chat.completions.create`.

6. **Message trimming** — after the assistant reply is appended, if `len(messages) > 21`, `messages[1:]` is replaced with `messages[-20:]`, keeping the system prompt and the 10 most recent user/assistant pairs.

7. **Action dispatch** based on `action["action"]`:
   - `explain` — strips markdown, prints text.
   - `send_command` — calls `_dispatch_send` then `_record_send`.
   - `read_outbox` — fetches results, calls `_update_log_from_results`, prints via `_print_results`, optionally calls `_interpret`.
   - `read_and_act` — fetches results, prints, prints explanation, calls `_dispatch_send` + `_record_send`.

8. **Error handling** — `KeyboardInterrupt` prints `[Interrupted]` and continues. `BadRequestError` falls back to `_print_results` on unseen results. `openai.APIError` prints the error. All other exceptions print the error. The loop never crashes.

### `main()` — CLI entry point

Parses `sys.argv[1]` for mode:
- `send` — calls `send_command()` from CLI args `--command` and `--payload`.
- `collect` — calls `collect()` and prints each row as JSON. Accepts `--id` to filter.
- `ai` — calls `ai_chat()`.

---

## GPT-4o System Prompt

The system prompt defines the AI's behaviour in `SYSTEM_PROMPT`. Key sections:

- **Available commands** — `system_info`, `echo`, `shell` with exact payload schemas.
- **Client context** — instructs the model to use injected OS info for platform-appropriate commands.
- **Output format** — JSON only, no markdown outside the JSON, no preamble.
- **Interpreted output style** — key:value format, full directory listings, offensive-only suggestions.
- **Offensive mindset** — suggestions must be enumeration, exploitation, privesc, lateral movement, persistence, or exfiltration. Never defensive.
- **READ vs SEND** — explicit keyword lists for when to read existing results vs send new commands.
- **Raw output rule** — when asked for exact/raw/full output, return the complete untruncated stdout in an `explain` action.
- **Multi-step planning** — use `read_and_act` to chain result reading with the next command.
- **Red team reasoning** — OS fingerprinting, default credentials, privesc paths, sudo misconfigurations.
- **Sudo rules** — always use `echo 'password' | sudo -S`; never bare sudo (no TTY); SESSION FACTS override everything.
- **SESSION FACTS** — once sudo creds or root access appear in SESSION FACTS, use them in every subsequent privileged command without re-testing.
- **Destructive command flag** — add `warning` field for irreversible commands.
- **Action schemas** — `send_command`, `read_outbox`, `read_and_act`, `explain`.

---

## Data Flow: Operator Types a Command

```
Operator types: "get me a shell"

1. Input loop — not a client-side command, not a raw bypass keyword.

2. Per-turn mode check — no raw/summary keywords, effective_output = session default ("interpreted").

3. messages.append({"role": "user", "content": "get me a shell"})

4. _build_api_messages() assembles:
     [system_prompt]
     [SESSION FACTS system message]       ← if any facts exist
     [Connected client info system msg]   ← if heartbeat seen
     [output mode hint system message]
     [session command log system message] ← if any commands sent
     [user: "get me a shell"]

5. GPT-4o call → returns:
     {"action": "send_command", "command": "shell", "payload": {"cmd": "bash -i"}}

6. messages.append({"role": "assistant", ...})
   Trim: if len(messages) > 21, keep system_prompt + last 20.

7. action["action"] == "send_command" → _dispatch_send():
     - No warning field.
     - send_mode == "confirm": prints preview, waits for "s".
     - Operator presses s → send_command() called → write_inbox_form() POSTs to Google Form.
     - Inbox form appends row to inbox tab.

8. _record_send():
     - Adds to cmd_log: {desc: "bash -i", cmd_id: "abc-123", result: None, command: "shell", payload: {...}}
     - _start_poll_thread("abc-123", "bash -i") spawns daemon thread.

9. Background thread polls outbox every ~30s.

10. client.py (on target) polls inbox tab:
     - Sees row with command_id="abc-123", status="pending".
     - handle_shell({"cmd": "bash -i"}) → subprocess.run().
     - write_form() POSTs result to outbox form → row appended to outbox tab.

11. Background thread polls outbox, finds command_id="abc-123":
     - arrived_ids.add("abc-123"), seen_ids.add("abc-123")
     - pending_results["abc-123"] = row
     - cmd_log entry result updated
     - _update_session_facts() checks for sudo creds, root, OS facts
     - Prints "[Result arrived] abc-123: bash -i → success"
     - _analyze_result() → GPT returns summary + suggestion + action
     - Prints summary in cyan, [Suggestion] in dim
     - Stores suggestion in pending_suggestion[0]
     - Reprints "> " prompt

12. Operator sees result. Types "do it" → executes pending_suggestion[0].
```

---

## Google Sheet Schema

### Tab: `config`

Read by `client.py` every poll cycle. Two columns: `key`, `value`.

| key | example value | description |
|-----|---------------|-------------|
| `poll_interval_sec` | `30` | Base sleep time between poll cycles |
| `poll_jitter_min` | `5` | Minimum random jitter added to sleep |
| `poll_jitter_max` | `15` | Maximum random jitter added to sleep |
| `client_id` | `client-01` | Identifier included in every outbox row |

Sleep formula: `poll_interval_sec + random.uniform(jitter_min, jitter_max)`

### Tab: `inbox`

Commands written by `server.py` (via inbox form). Read by `client.py`.

| column | type | description |
|--------|------|-------------|
| `command_id` | UUID string | Unique identifier for this command |
| `command` | string | Handler name: `system_info`, `echo`, or `shell` |
| `payload` | JSON string | Arguments for the handler (e.g. `{"cmd": "whoami"}`) |
| `target` | string | Reserved: always `"outbox"` in current code |
| `status` | string | Always `"pending"` when written; client does not update this field |
| `created_at` | ISO 8601 | Timestamp when the command was sent |

### Tab: `outbox`

Results written by `client.py` (via outbox form) and heartbeats. Read by `server.py`.

| column | type | description |
|--------|------|-------------|
| `command_id` | string | UUID for regular results; `"heartbeat-<uuid>"` for heartbeats |
| `client_id` | string | Value from config tab's `client_id` key |
| `status` | string | `"success"`, `"error"`, or `"heartbeat"` |
| `result` | JSON string | For success/error: `{stdout, stderr, returncode}` or `{error: ...}` or system_info dict. For heartbeat: system_info dict. |
| `timestamp` | ISO 8601 | Time the result was written |

---

## .env Fields

| Variable | Description |
|----------|-------------|
| `SPREADSHEET_ID` | The Google Sheets document ID (from the sheet URL) |
| `CONFIG_GID` | Numeric GID of the `config` tab (from the URL `?gid=N`) |
| `INBOX_GID` | Numeric GID of the `inbox` tab |
| `OUTBOX_GID` | Numeric GID of the `outbox` tab |
| `FORMS_URL` | Full submission URL for the outbox Google Form (`/formResponse` endpoint) |
| `FORMS_FIELD_MAP` | JSON object mapping logical field names to `entry.XXXXXXX` IDs for the outbox form. Fields: `command_id`, `client_id`, `status`, `result`, `timestamp` |
| `INBOX_FORMS_URL` | Full submission URL for the inbox Google Form |
| `INBOX_FORMS_FIELD_MAP` | JSON object mapping logical field names to entry IDs for the inbox form. Fields: `command_id`, `command`, `payload`, `target`, `status`, `created_at` |
| `OPENAI_API_KEY` | OpenAI API key used by `server.py` in `ai` mode |

Both `client.py` and `server.py` call `common.load_env()` at startup to populate `os.environ` from this file.

---

## Session Features

### Auto / Confirm Mode

Set at startup via the `[A]uto / [C]onfirm` prompt. Switchable mid-session with `mode auto` or `mode confirm`.

- **auto**: commands are sent immediately after GPT generates them, with a single line confirmation printed.
- **confirm**: every command shows a preview (command name, payload, target) and waits for `s` (send) or `c` (cancel).

Destructive commands (those with a `warning` field) always prompt for confirmation regardless of mode.

### Output Mode

Session default is `interpreted`. Switchable with `output raw` / `output interpreted`.

- **interpreted**: results are displayed via `_print_results` then passed to GPT-4o for a concise summary. Background results are auto-analyzed and displayed with a summary.
- **raw**: results are displayed via `_print_results` only. No secondary GPT call.

Per-turn overrides: `_wants_raw()` and `_wants_summary()` can flip the effective mode for a single turn without changing the session default.

### Raw Output Bypass

When the user input contains `raw`, `exact`, `terminal output`, `full output`, or `like a terminal`, the main loop bypasses GPT-4o entirely and prints the `stdout` field from the most recent result in `pending_results` directly to the terminal.

### Dangerous Command Warning

If GPT-4o includes a `warning` field in a `send_command` or `read_and_act` action, `_dispatch_send` always interrupts to show the warning in bold red and requires explicit `s` confirmation before sending, regardless of send mode.

### Session Facts

`session_facts` is a list of strings auto-detected from results as the session progresses. Facts are injected into every GPT-4o request as a `SESSION FACTS` system message, ensuring the model remembers confirmed context even after message history is trimmed.

Facts detected automatically:

| Trigger | Fact added |
|---------|------------|
| Shell command with `echo 'X' \| sudo -S` returns success | `sudo credentials confirmed: password='X' — always use echo 'X' \| sudo -S <cmd>` |
| `whoami` returns `root` | `root access available (whoami=root)` |
| `system_info` result contains `os` field | `OS: <name> \| version: ... \| arch: ... \| hostname: ... \| user: ...` |
| `uname` command returns output | `uname: <stdout>` |

New facts are printed inline as `[Fact] ...` in dim text when discovered.

### Message Trimming

After each assistant reply is appended to `messages`, if `len(messages) > 21` (system prompt + more than 20 turns), `messages[1:]` is replaced with `messages[-20:]`. This preserves the system prompt and the 10 most recent user/assistant pairs. SESSION FACTS injected per-request preserve critical discovered context across the trim boundary.

### Background Result Polling

Each call to `_record_send` spawns a daemon thread via `_start_poll_thread`. The thread:

- Reads the config sheet to determine the poll interval.
- Polls the outbox every `interval + jitter_max` seconds.
- On any heartbeat rows encountered: silently updates `client_os_info`, does not surface them.
- On the target command ID: stores the row, calls `_analyze_result` for a summary and suggestion, displays the result inline, stores the suggested next command in `pending_suggestion[0]`, reprints the prompt.
- Times out after 5 minutes if no result arrives.

Multiple threads can run concurrently (one per in-flight command).

### "Do It" Suggestion Execution

`_analyze_result` always returns a suggested next `send_command` action alongside its summary. This is stored in `pending_suggestion[0]`. When the operator types `do it`, `yes`, `go`, or `y`, `_dispatch_send` is called with the stored action, respecting the current send mode and any warning field.

---

## Limitations

- **No inbox management**: the server cannot update or delete inbox rows. Google Forms is append-only. Inbox grows indefinitely until manually cleared from the spreadsheet.
- **No outbox management**: same constraint. Old results persist forever. `seen_ids` prevents re-display in a session, but a fresh session sees everything again.
- **No retry logic**: `write_form` and `read_tab` do not retry on failure. A single network error drops the write or read silently (logged by the poll loop).
- **No authentication on reads**: the spreadsheet is shared as "anyone with the link can view". Anyone with the spreadsheet ID can read all commands and results.
- **No encryption**: commands and results travel in plaintext inside HTTPS to Google.
- **No multi-client routing**: the `target` field in the inbox exists but is unused. All clients execute all pending commands. If multiple clients are running, they all respond.
- **Poll interval governs latency**: minimum round-trip time is one full client poll cycle. Default config is 30–45 seconds.
- **Background poll timeout**: a background poll thread gives up after 5 minutes. Commands that take longer (e.g. a slow shell command) will not be auto-displayed; the operator must manually call `show results`.
- **Session history in-memory only**: `session_facts`, `cmd_log`, and `messages` are lost when `server.py` exits.
- **OpenAI API key in `.env`**: the key is stored in plaintext on disk.
- **Sudo conflict guard is client-side**: the server's system prompt discourages conflicting sudo usage, but enforcement happens in `handle_shell`. If a custom payload bypasses the guard, subprocess behavior is undefined.

---

## Dependencies

```
requests   # HTTP client for sheet reads and form writes
openai     # GPT-4o API (server.py only)
```

Standard library: `csv`, `io`, `json`, `os`, `platform`, `getpass`, `random`, `re`, `subprocess`, `sys`, `threading`, `time`, `uuid`, `datetime`.

Install:
```bash
pip install requests openai
```
