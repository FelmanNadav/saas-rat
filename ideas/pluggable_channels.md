# Idea: Pluggable Channel Backends + Mid-Stream Channel Switch

## Concept

The current I/O layer in `common.py` is the natural abstraction boundary. All code above it calls four functions:

```
read_inbox()
read_outbox()
write_result()
write_task()
```

These can be backed by any transport. The same pattern used for encryption (`crypto/base.py` → factory in `common.py`) applies here: an abstract `Channel` base class, one concrete implementation per backend, a `get_channel()` factory.

```
channel/
├── base.py       # abstract Channel with read_inbox, read_outbox, write_result, write_task
├── sheets.py     # current Google Sheets implementation, moved here
└── firebase.py   # Firebase Realtime Database implementation
```

Nothing above the boundary changes — `client.py`, `server.py`, and the AI layer are channel-agnostic.

---

## Evasion Mechanic: switch_channel Command

A `switch_channel` command whose payload is the full config of the new channel. Flow:

1. Server sends `switch_channel` with new channel config (new sheet ID, or Firebase URL + table paths)
2. Client receives it on the current channel
3. Client instantiates new channel object from payload config
4. Client writes one final ACK on the **old** channel ("switched, going dark")
5. Client replaces its active channel, persists new config to `.client_config.json`
6. Old channel goes silent — any observer watching it sees activity stop, new channel has no history

Server mirrors the switch simultaneously and begins monitoring the new channel.

---

## Firebase as a Backend

Firebase Realtime Database supports **unauthenticated access on public-rule tables** — same principle as Google Sheets CSV export. No API keys or OAuth required on the client, just an HTTPS endpoint.

Read: `GET https://<project>.firebaseio.com/<path>.json`
Write: `POST https://<project>.firebaseio.com/<path>.json` with JSON body

Firebase is push-capable (Server-Sent Events on `.json?stream=true`) which means the polling loop could block until data arrives rather than sleeping — lower latency and less network noise than fixed-interval polling. The `Channel` abstraction needs to accommodate both polling and blocking-read semantics.

All traffic goes to `firebaseio.com` — different domain than `docs.google.com`, useful if one is blocked or flagged.

---

## Implementation Order

1. **Refactor `common.py` → `channel/sheets.py`** with abstract base class — no behavior change, just restructuring
2. **Add `switch_channel` command** with same-backend switching (new sheet ID) — immediately useful for evasion
3. **`channel/firebase.py`** — add when needed, no changes to anything else

---

## Payload Complexity

The Sheets channel config is large: `SPREADSHEET_ID`, `INBOX_GID`, `OUTBOX_GID`, `FORMS_URL`, `FORMS_FIELD_MAP`, `INBOX_FORMS_URL`, `INBOX_FORMS_FIELD_MAP`, optional column maps. All of this ships in the encrypted `switch_channel` payload — safe under Fernet, fits within the ~4000 char Forms field limit if column maps are omitted or compact.

Firebase config is smaller: base URL + inbox path + outbox path.

---

## Notes

- `switch_channel` payload contains live credentials (Forms URLs, Firebase endpoint). Acceptable by design — the channel is the trust boundary and the payload is encrypted.
- Multiple active channels (server aggregates across sheets) is a separate and more complex idea — separate from the switch mechanic.
- The `target` field + multi-client routing is independent of this and should be implemented first.

---

## Testing — implement alongside the channel abstraction

The channel abstraction unlocks proper automated testing of multi-cycle behavior (currently untested). When implementing, use **dependency injection** rather than extracting `run_cycle()` as a workaround:

- `client.py` main loop receives a `channel` object and a `clock` object instead of calling `common.*` and `time.sleep` directly
- Tests pass a `FakeChannel` (pre-loaded inbox data, records writes) and a `FakeClock` (sleep is a no-op)
- `FakeChannel` implements the same interface as `SheetsChannel` — the test drives N cycles by calling the loop N times with instant sleep

**What this enables:**
- Assert exactly one fragment written per cycle when send queue is non-empty
- Assert queue depth decreases by one per cycle
- Assert server-side reassembly returns original result after N cycles
- Assert failed write leaves queue unchanged and retries next cycle
- Assert `switch_channel` silences the old channel and activates the new one

This is the correct enterprise-grade pattern. Do not refactor `main()` for testability before the channel abstraction exists — the abstraction solves both problems at once.

---

## Server Poll Interval — current state and open idea

**Current state:** `_start_poll_thread` calls `common.get_channel().poll_interval()`. `SheetsChannel.poll_interval()` returns a fixed 5s. The `Channel` base class defaults to 30s for future backends. No config needed, no sync burden.

The fixed 5s works fine in practice — the server just polls until a result appears, and results arrive whenever the client writes them. Polling faster than necessary is harmless (cheap CSV read).

**The open idea: self-synchronising poll interval**

Ideally the server's poll interval would automatically track the client's actual cycle time. When the operator sends a `config` command that changes `poll_interval_sec` or `poll_jitter_max`, the server's background poller should adjust immediately without any manual change.

Approaches worth exploring:

- **Option A — Config command side-effect:** when the server sends a `config` command, it updates `common.get_channel().set_poll_interval(new_interval)` locally at the same time. Both sides stay in sync because the server is the one initiating the change. Simple, no new commands needed. Risk: server-side state can drift if the client's config is changed by another server instance.

- **Option B — Heartbeat carries client timing:** client includes `poll_interval_sec` and `poll_jitter_max` in heartbeat results. Server reads these on arrival and calls `set_poll_interval()` on the channel. Fully self-synchronising. Requires heartbeat schema change and server-side heartbeat processing.

- **Option C — `switch_channel` carries timing:** when implementing `switch_channel`, include the new poll interval in the channel config payload. Both sides update their interval as part of the switch. Natural fit — channel config already travels together.

Option B is the cleanest long-term answer and pairs well with heartbeat improvements. Option A is a quick win once `switch_channel` is built. Option C falls out naturally from `switch_channel` design.
