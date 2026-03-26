# Plan: `switch_sheet` — Mid-Operation Spreadsheet Rotation

## Goal

Allow the operator to pivot the client to a different Google Sheet mid-operation without restarting the client. The existing channel type (Sheets) stays the same — only the underlying spreadsheet credentials change.

---

## Motivation

`switch_channel` already handles transport-level pivots (Sheets → Firebase). This extends the same idea within the Sheets channel: if a sheet is flagged, an account is suspended, or the operator wants a clean operational artifact, they issue one command and all subsequent traffic moves to a fresh sheet. No client restart, no redeployment.

---

## What Changes

### New command: `switch_sheet`

Payload:

```json
{
  "spreadsheet_id": "...",
  "inbox_gid": "...",
  "outbox_gid": "...",
  "forms_url": "...",
  "forms_field_map": "{...}",
  "inbox_forms_url": "...",
  "inbox_forms_field_map": "{...}",
  "inbox_column_map": "{...}",
  "outbox_column_map": "{...}"
}
```

Only `spreadsheet_id`, `forms_url`, `forms_field_map`, `inbox_forms_url`, and `inbox_forms_field_map` are required. Column maps and GIDs default to current values if omitted — allows partial updates when only the form URLs change.

---

## Implementation

### 1. `client.py` — `handle_switch_sheet(payload)`

Mirror the pattern from `handle_switch_channel`:

- Validate that required keys are present in the payload. Return `{"error": ...}` if not.
- Return `{"_deferred_switch": {"type": "sheet", ...new_credentials}, "switched_to": "new_sheet"}`.
- The `_deferred_switch` dict is hoisted out of the result JSON in `dispatch()` exactly as it is for `switch_channel` — the ACK is written to the **old** sheet before credentials are swapped.

The deferred switch key already has special handling in `dispatch()` — extend it to accept a dict (sheet rotation) in addition to a string (channel name).

### 2. `client.py` — `_apply_sheet_switch(credentials)`

Called by the main loop after the result is confirmed written on the old sheet:

```python
def _apply_sheet_switch(credentials):
    for key, value in credentials.items():
        os.environ[key.upper()] = str(value)
    common.set_channel(SheetsChannel())  # re-init with new env vars
    print("[client] Sheet rotated →", credentials.get("spreadsheet_id", "?")[:8])
```

### 3. `client.py` — register handler

```python
HANDLERS = {
    ...
    "switch_sheet": handle_switch_sheet,
}
```

### 4. `server.py` — detect `switch_sheet` result and mirror

In the result-processing loop alongside the existing `switch_channel` block:

```python
if entry.get("command") == "switch_sheet":
    result_data = json.loads(row.get("result", "{}"))
    creds = result_data.get("new_credentials", {})
    if creds:
        for key, value in creds.items():
            os.environ[key.upper()] = str(value)
        common.set_channel(SheetsChannel())
        print(f"\n[Channel] Server sheet rotated → {creds.get('spreadsheet_id', '?')[:8]}")
```

### 5. `server.py` — add to help text and command dispatch

Add `switch_sheet` to the REPL help string and to the AI system prompt in `system_prompt.txt`.

---

## The Deferred Switch — Key Detail

The flow is identical to `switch_channel`:

```
1. Server writes switch_sheet command to OLD sheet
2. Client reads it, executes handle_switch_sheet
3. dispatch() hoists _deferred_switch out of result payload
4. Client writes ACK to OLD sheet (credentials not yet swapped)
5. Client main loop detects _deferred_switch, calls _apply_sheet_switch
6. Client now reads/writes to NEW sheet
7. Server reads ACK from OLD sheet, mirrors the credential swap
8. Both sides are now on the new sheet simultaneously
```

Without the deferred pattern, the client would swap credentials before writing the ACK — the server would never see the confirmation.

---

## Testing Plan

### Unit tests — `tests/unit/test_switch_sheet.py`

**`handle_switch_sheet` (client-side handler):**

| Test | What it checks |
|---|---|
| Missing required key returns error | `{"error": ...}` when `spreadsheet_id` absent |
| Valid payload returns `_deferred_switch` | Key present in result dict |
| `_deferred_switch` is a dict, not a string | Type check |
| `switched_to` field present | Confirmation field |
| Result sent over old channel | Env var not yet changed after handler returns |
| Already on same sheet (same spreadsheet_id) returns note | No unnecessary re-init |

**`dispatch()` hoisting (existing dispatch tests extended):**

| Test | What it checks |
|---|---|
| `_deferred_switch` dict hoisted out of result JSON | Not present in what gets written to outbox |
| `switched_to` remains in result JSON | Client confirmation still visible to server |

**`_apply_sheet_switch` (credential swap):**

| Test | What it checks |
|---|---|
| Env vars updated correctly | `os.environ["SPREADSHEET_ID"]` reflects new value |
| `SheetsChannel` re-initialized | `common.get_channel()` returns new instance |
| Partial payload: omitted keys retain existing env var | `INBOX_GID` unchanged if not in payload |

**Server-side mirroring:**

| Test | What it checks |
|---|---|
| Server updates env vars on `switch_sheet` result | Same credential swap logic |
| Server re-inits channel after swap | `get_channel()` returns new instance |
| Non-`switch_sheet` commands not affected | Existing result handling unchanged |

### Integration test (requires two live sheets)

1. Configure two full sheet setups (two spreadsheets, two form pairs).
2. Send a command on Sheet A, confirm result arrives.
3. Send `switch_sheet` with Sheet B credentials.
4. Send another command — confirm it arrives on Sheet B, not Sheet A.
5. Confirm Sheet A receives no new rows after the pivot.

---

## Files Touched

| File | Change |
|---|---|
| `client.py` | Add `handle_switch_sheet`, `_apply_sheet_switch`, extend `dispatch()` deferred logic, register handler |
| `server.py` | Add `switch_sheet` result detection block, help text |
| `system_prompt.txt` | Add `switch_sheet` to available commands |
| `tests/unit/test_switch_sheet.py` | New test file (all unit tests above) |

---

## What Is Not Changing

- `channel/sheets.py` — `SheetsChannel` already reads all credentials from env vars on every call. Re-initializing it after an env var swap is sufficient; no changes to the class itself.
- `channel/base.py` — no interface changes.
- `common.py` — `set_channel()` already exists; no changes needed.
- Encryption, fragmentation, column obfuscation — all carry over transparently to the new sheet.
