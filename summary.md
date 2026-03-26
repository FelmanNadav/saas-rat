# SaaS RAT — Design & Methodology Summary

---

## Personal Note

I had a great time building this. After the first working version I kept going — not because the assignment required it, but because the problem kept pulling me forward. What started as an MVP turned into a full framework with two channels, an AI console, a packaging pipeline, and test coverage. The assignment carried me away and I lost track of time for those couple of days. I hope that shows in the result.

This was built almost entirely with Claude Code. A project of this scope in a two-day window wouldn't have been possible otherwise — Claude handled implementation while I controlled direction, architecture decisions, and every call about what to build next. The main friction was Claude's reluctance around security research tasks — the caution is understandable, but navigating it was a constant part of the process.

---

## Core Concept

Google Sheets as a C2 channel. The read side is a public CSV export — unauthenticated, works from any network. The write side was the harder problem: the Sheets API requires OAuth for every write endpoint, with no anonymous write option. The workaround is Google Forms — submitting a form response appends a row to a linked sheet with no authentication required on the submitter's side. Both client and server communicate exclusively through `docs.google.com`, a domain whitelisted by virtually every enterprise security stack.

*[screenshot — Google Sheet inbox tab in plaintext mode: readable column names, command rows visible]*

*[screenshot — same sheet in production mode: random column names, Fernet-encrypted field values — an observer sees noise]*

---

## Architecture

```
server.py → Google Forms POST → inbox tab
                                      ↓
                               client.py polls CSV
                                      ↓
                               executes command
                                      ↓
client.py → Google Forms POST → outbox tab
                                      ↓
                               server.py reads result
```

The client polls on a configurable interval with random jitter. The server auto-syncs its refresh interval to the client's cycle timing via heartbeat messages — no manual coordination required.

---

## Why a Multi-Channel Architecture

Google Sheets has a structural limitation: Google Forms is append-only. There is no delete endpoint. Rows accumulate indefinitely, and all C2 traffic is tied to a single document — if that document is discovered, the channel is gone.

The response was to treat the transport layer as a pluggable component from the start, so a second channel could be added without touching the core. Firebase Realtime Database was the natural choice: a different domain (`firebaseio.com`), a different Google product, a proper REST API with DELETE as a first-class operation. An operator pivoting from Sheets to Firebase doesn't just change the URL — they change the entire detection surface. Detection rules, domain blocklists, and traffic patterns built around one channel have no visibility into the other.

---

## Design Decisions

**Channel is pluggable — others are not yet**

The `Channel` abstract base class is the only component where multiple implementations are fully shipped (Sheets, Firebase). `Encryptor` and `Fragmenter` have abstract base classes and two implementations each, but switching them at runtime is not yet wired to a command — it requires a config change and restart. The architecture is designed to support runtime switching; the operator surface isn't built yet.

**Encryptor**

All field values pass through an encryptor before being written to the channel and after being read. The motivation is dual: the Google Sheet is publicly readable by anyone who knows the spreadsheet ID, and TLS-inspecting proxies can expose payload content. Fernet (AES-128-CBC + HMAC-SHA256) makes both irrelevant — ciphertext at rest and in transit. Plaintext mode exists for debugging.

**Fragmenter**

Large command outputs cannot be reliably sent in a single write — both for size constraints and because a single request carrying a complete result is easier to pattern-match. Fixed-size fragmentation splits the payload into chunks, each submitted as a separate write. The server reassembles them in order before presenting the result. The operator sees nothing of the chunking.

**Packager**

The client needs to run on target machines without a Python installation and without exposing source code. `packager.py` builds standalone binaries through four obfuscation profiles (Basic, UPX, PyArmor, Nuitka), each adding a layer that raises the bar for static analysis. Silent mode, which strips all stdout at build time, was added because sandbox analysis watches console output — a binary that prints its own behavior identifies itself.

**Setup Wizard**

Configuring the framework requires a Google Sheet, two Google Forms, entry IDs for each form field, encryption keys, and optional column obfuscation maps — all of which need to be consistent between client and server. Manual configuration is error-prone and slow. The wizard walks through each step, validates inputs, generates obfuscation maps, and writes a complete `.env` file. It also handles the cleanup strategy decision (service account vs Apps Script trigger) inline, with setup instructions for each.

---

## Channels

| Channel | Transport | Write mechanism | Cleanup |
|---|---|---|---|
| **Sheets** | `docs.google.com` | Google Forms POST | Service account (per-message) or Apps Script trigger (scheduled) |
| **Firebase** | `firebaseio.com` | REST PUT | REST DELETE after result confirmed — automatic |

The `switch_channel` command pivots the client mid-operation. The ACK for the switch is sent on the old channel before the client moves — both sides synchronize simultaneously with no coordination gap.

**Channel pivot example:**

```
> give me the current user and hostname
```
*[screenshot — result arrives via Sheets, outbox tab shows a new row]*

```
> switch to firebase
```
*[screenshot — server log showing "Channel switched → firebase"]*

```
> list the current directory
```
*[screenshot — result arrives — no new row in the Google Sheet. Firebase console shows the command and result nodes.]*

---

## Cleanup & Append-Only Constraints

Google Forms is append-only — there is no delete endpoint. Every command and result written to the sheet stays there until manually removed. Left unmanaged, the inbox and outbox grow indefinitely, accumulating operational history that is a liability if the sheet is discovered.

Two solutions are implemented for the Sheets channel, both configurable via the setup wizard:

- **Service account** — a Google service account with the Sheets API enabled deletes each row individually via gspread immediately after the result is confirmed. Per-message, automatic. Adds ~1-2s latency per delete since each deletion is an HTTPS API call from the operator's machine.
- **Apps Script trigger** — a generated script (`sheets_c2_cleanup.gs`) runs scheduled batch cleanup inside Google's infrastructure on a configurable interval. Faster than the service account approach (runs server-side at Google), no GCP setup required, but not per-message.

Firebase has no equivalent problem — REST DELETE is a first-class operation. The server removes each inbox entry the moment the corresponding result is confirmed. Nothing accumulates.

Heartbeat rows are a separate case: they are ephemeral by nature and are deleted from the outbox immediately after the server reads them, regardless of cleanup strategy.

---

## AI Operator Console

The original design was a fully autonomous AI pentester — something that would chain findings, suggest next steps, and operate with minimal operator input. After building several versions of that, the scope was deliberately narrowed: the AI should augment the operator, not replace them. An autonomous agent making offensive decisions without confirmation is a different product with a different risk profile.

The result is a natural language interface on top of the existing command protocol. The operator types intent, GPT-4o translates it to a structured command, dispatches it, and interprets the result back in plain English. Two send modes: `confirm` (operator approves before dispatch) and `auto` (immediate). Destructive command patterns are always intercepted regardless of mode.

The AI behavior is entirely driven by `system_prompt.txt` — the model is given the full command protocol, available commands, payload formats, and config keys. Pushing it further toward autonomous operation is a prompt edit, not a code change.

**Example session:**

```
> who am I running as and what machine is this

  Command: system_info
  Confirm? yes

  [✓] Running as root on ubuntu-victim (Linux 5.15.0, x86_64). Python 3.11.4.
```

*[screenshot — full AI console session: prompt → proposed command → confirmation → interpreted result]*

---

## Defense Evasion

### Binary — escalating obfuscation ladder

| Profile | Method | What an analyst sees |
|---|---|---|
| **Basic** | PyInstaller `--onefile` | Raw Python bytecode — extractable with pyinstxtractor |
| **UPX** | PyInstaller + UPX | Must unpack before reaching the Python layer |
| **PyArmor** | Encrypted bytecode + PyInstaller | Encrypted blobs — decryption key in a native `.so`, without it you get noise |
| **Nuitka** | Python → C → native binary | No bytecode anywhere — requires a disassembler |

**Silent mode** (all profiles): `print = lambda *_a, **_kw: None` prepended at build time. Every `print()` call becomes a no-op — sandbox stdout monitoring sees nothing.

*[screenshot — `strings` output on basic binary vs Nuitka binary: Python strings present vs absent]*

### Network — data in motion

- **Header normalization** — Chrome-accurate headers on every request (`User-Agent`, `Origin`, `Referer`, `Sec-Fetch-*`). Traffic is indistinguishable from a browser submitting a real Google Form.
- **Encryption** — Fernet encrypts all field values before they hit the wire. DLP and EDR pattern matching sees only ciphertext.
- **Fragmentation** — payloads split into fixed-size chunks, each a separate write. No single request carries a complete result.
- **Jitter** — beacon interval randomized at runtime. Configurable base + min/max jitter produces an irregular pattern that defeats frequency-based beaconing detection.

### Channel rotation

`switch_channel` moves all traffic from `docs.google.com` to `firebaseio.com` mid-operation. Different domain, different Google product, different request model — detection rules built around one channel do not apply to the other.

### Designed, not yet implemented

- **Sandbox detection** — on startup, check for sandbox indicators before first contact: uptime < 5 min, single CPU, <2GB RAM, known sandbox usernames, VM MAC address prefixes. If indicators match, exit cleanly — the sandbox reports no suspicious behavior.
- **Sleep before first contact** — most automated sandboxes have a fixed analysis window (2-3 minutes). A configurable startup delay outlasts it with no other changes required.
- **String obfuscation** — even Nuitka-compiled binaries carry string literals in the data segment. Build-time encryption replaces protocol strings (`heartbeat`, `shell`, `command_id`) with decrypt calls — strings exist only transiently in memory, never in the binary's data section.
- **Dropper pattern** — a thin first-stage binary sets environment variables and launches the client, then exits immediately. Operational config (spreadsheet ID, form URLs, encryption key) never lives in the client binary. Persistence entries carry the dropper, not the client.
- **Memory-only operation** — execute the client payload entirely in process memory with no disk artifact (`memfd_create` + `fexecve` on Linux, process hollowing on Windows). The highest-complexity item on the list.
- **Persistence** — survive reboots without manual redeployment. Platform-specific mechanisms: cron or systemd on Linux, registry run key or scheduled task on Windows. The persistence entry carries the dropper (see above), which re-sets environment variables on each execution. The client binary itself contains no config.
- **Spreadsheet rotation** — `switch_channel` currently pivots between channel types (Sheets → Firebase). A natural extension is rotating between different spreadsheets within the same channel — if a sheet is flagged or the account suspended, the operator issues one command and traffic moves to a fresh document with no client restart.

---

## Delivery

The client is distributed as a standalone binary via `packager.py` — no Python installation required on the target. Config is passed entirely through environment variables at runtime; nothing operational is baked into the binary.
