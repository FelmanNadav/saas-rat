# Defense Evasion

## The Problem

A RAT can be detected at multiple layers:

1. **Static analysis** — the binary is scanned before execution (AV signature matching, file hash, string extraction, bytecode inspection)
2. **Dynamic analysis** — the binary is executed in a sandbox and observed (stdout, system calls, network connections, process behavior)
3. **Network detection** — traffic is inspected at the perimeter (domain, protocol, payload pattern)

Each layer requires a different evasion approach. No single technique defeats all three.

---

## The Approach: Escalation on Demand

Rather than applying maximum obfuscation from the start, the tool uses an **escalation ladder** — build the simplest version first, test it against the target defense product, and add complexity only if caught. This has two advantages:

- Each step that passes tells you exactly what the defense product is *not* doing
- Each step that fails tells you exactly what it *is* doing

The ladder:

```
basic → upx → pyarmor → nuitka + silent
```

---

## Layer 1 — Basic PyInstaller

**What it does:** Bundles Python interpreter + all dependencies + source code into a single ELF binary. No Python installation required on target.

**What it defeats:**
- Dependency scanning — no loose `.py` files on disk
- Python-specific file detection — no `.pyc` files visible
- Basic "is this a script" heuristics

**What it doesn't defeat:**
- PyInstaller signature detection — the binary has a known header (`MEI` bootstrap), recognized by most AV products
- Bytecode extraction — `pyinstxtractor` unpacks the bundle in seconds, `decompile-bytecode` recovers readable source

**Verdict:** Baseline. If this passes, the defense product has no PyInstaller signature rule.

<!-- SCREENSHOT: basic binary running, heartbeat visible in sheet — shows clean execution -->

---

## Layer 2 — UPX Compression

**What it does:** Applies UPX compression to the PyInstaller output. Wraps the binary in a decompression stub with a different header. Changes the file hash and binary signature.

**What it defeats:**
- Hash-based signature matching — the binary hash is completely different
- Header-based PyInstaller detection — the `MEI` bootstrap is no longer at the expected offset
- Simple string scanning — strings are compressed until runtime

**What it doesn't defeat:**
- Heuristic analysis — the binary still extracts itself to `/tmp` at runtime, a known PyInstaller behavior
- Behavioral detection — execution pattern is identical to the basic build
- UPX detection — some AV products flag UPX-packed binaries as suspicious regardless of content

**Note:** PyInstaller's built-in UPX integration is disabled on Linux due to known compatibility issues. UPX is applied directly to the output binary as a post-build step — the result is functionally equivalent.

**Verdict:** Changes the static signature. If this passes, the defense product is not doing UPX-aware unpacking before scanning.

<!-- SCREENSHOT: upx binary size vs basic binary size comparison in terminal -->

---

## Layer 3 — PyArmor Encryption

**What it does:** PyArmor encrypts the Python bytecode before PyInstaller bundles it. The source is never accessible in plaintext — only encrypted blobs exist in the bundle.

**What it defeats:**
- Bytecode extraction — `pyinstxtractor` yields encrypted blobs, not readable bytecode
- String-based detection — all Python string literals are inside the encrypted payload
- Source recovery — no decompiler can reconstruct the source without the decryption key

**How the encryption works:**
The decryption key is baked into a native `pyarmor_runtime.so` file generated at build time. At runtime, the `.so` decrypts the bytecode into memory — the plaintext bytecode never touches disk. Without the exact `.so` from your specific build, the encrypted blobs are noise.

**What it doesn't defeat:**
- Runtime memory inspection — the bytecode exists decrypted in process memory during execution
- Behavioral detection — the process still forks shells, writes to disk, makes network calls
- Network detection — traffic pattern to `docs.google.com` is unchanged

**Verdict:** Defeats Python-aware static analysis completely. If this passes, the defense product has no dynamic unpacking capability for PyArmor v9.

<!-- SCREENSHOT: pyinstxtractor output on pyarmor binary — showing encrypted blobs instead of readable .pyc files -->

---

## Layer 4 — Nuitka Native Compilation

**What it does:** Compiles Python → C → native ELF binary. There is no Python bytecode anywhere in the output — not encrypted, not hidden, simply absent.

**What it defeats:**
- All Python-specific analysis — no interpreter, no bytecode, no `.pyc` files, no PyInstaller bootstrap
- Bytecode decompilers — nothing to decompile
- PyArmor-style runtime memory inspection — there is no Python runtime to hook

**What an analyst sees:**
A native ELF binary. Reversing requires a disassembler (Ghidra, IDA Pro) and C-level analysis. Variable names, class structure, and logic flow are heavily mangled by the C compiler's optimization pass.

**What it doesn't defeat:**
- Behavioral detection — process behavior is identical: fork shell, write result, network call
- Network detection — traffic is unchanged
- String literals that survive compilation — error messages, format strings visible via `strings ./binary` (though operational config like spreadsheet ID is never baked in — it comes from environment variables at runtime)

**Verdict:** Eliminates the entire Python attack surface. Raises the bar from "script kiddie with pyinstxtractor" to "reverse engineer with a disassembler."

<!-- SCREENSHOT: strings output on nuitka binary — showing absence of Python-related strings vs basic binary -->

---

## Layer 5 — Silent Mode (Cross-cutting)

**What it does:** Suppresses all `print()` output before the binary is built by prepending:

```python
print = lambda *_a, **_kw: None
```

In Nuitka's case, the C compiler optimizes this to a native no-op — no function call overhead, no observable behavior.

**What it defeats:**
- Sandbox stdout monitoring — dynamic analysis sandboxes (Cuckoo, Any.run, enterprise EDR behavioral engines) watch stdout for suspicious strings. A binary that prints `[client] Heartbeat sent` and `[client] Executing: shell` every few seconds is self-identifying.
- String-based behavioral rules — `[client]` prefix patterns, command names in output

**What it doesn't defeat:**
- Syscall-level monitoring — `execve()`, `open()`, `connect()` are visible regardless of stdout
- Network traffic analysis — HTTP POSTs to `docs.google.com` are still observable

**Verdict:** Essential for sandbox evasion. The output strings are more identifying than the binary structure in many enterprise sandboxes.

<!-- SCREENSHOT: Process Monitor or strace showing clean execution with no stdout — vs non-silent build showing all print output -->

---

## The C2 Channel: The Honest Assessment

This is where static and binary evasion reach their limit.

**The channel cannot be hidden.** Every command and result travels as an HTTP POST to `docs.google.com/forms/...` or as a CSV read from `docs.google.com/spreadsheets/...`. This traffic:

- Is encrypted (HTTPS) — payload content is not inspectable without TLS inspection
- Blends with legitimate Google Workspace traffic — most organizations cannot block `docs.google.com` without breaking productivity tools
- Produces no unusual DNS queries — the domain is in every enterprise's whitelist

**What catches this at the network layer:**

| Detection method | Effectiveness |
|---|---|
| Domain blocking (`docs.google.com`) | Impractical — breaks Google Workspace |
| TLS inspection + content analysis | Would expose form field values — detectable if patterns are known |
| Volume/frequency anomaly detection | Partially mitigated. The operator has full runtime control over beacon timing via the `config` command: `cycle_interval_sec` (base sleep), `cycle_jitter_min` and `cycle_jitter_max` (random jitter added per cycle), and `heartbeat_every` (heartbeat frequency). A fixed 1s interval looks like beaconing. Configuring a 60s base with 30-90s jitter produces an irregular, human-like pattern that is significantly harder to fingerprint as automated traffic. The server auto-syncs its refresh interval to match, so result visibility is not sacrificed. |
| User agent / request header analysis | Currently a gap — the client uses `requests` defaults (user agent: `python-requests/2.x`). A TLS-inspecting proxy flags this immediately. **Planned fix: request header normalization** (see Planned Evasion Techniques below). |
| Behavioral correlation | A process that regularly POSTs to Google Forms and has no browser UI is anomalous |

**Column obfuscation and Fernet encryption** handle the TLS inspection case — even if an inspector decrypts the HTTPS payload, they see random column names and encrypted field values. Without the key, the content is opaque.

**The realistic detection boundary for a mature defense product:**

- **Immature product (signature-only):** Caught at basic or UPX
- **Intermediate product (behavioral + signatures):** Caught at PyArmor if it has Python-aware rules; possibly caught at nuitka via behavioral heuristics
- **Mature product (network + behavioral correlation):** Caught at the network layer via beaconing detection or Google Forms POST anomaly — binary obfuscation is irrelevant at this point

**The honest conclusion:** Binary obfuscation buys time against static analysis and low-sophistication sandboxes. Against a mature XDR with network visibility, the C2 channel is the real detection surface — and `docs.google.com` is both the strength (whitelisted everywhere) and the weakness (all traffic goes to one known domain with a recognizable request pattern).

<!-- SCREENSHOT: Wireshark or network capture showing HTTPS traffic to docs.google.com — clean, no obvious C2 pattern visible at the packet level -->

---

## Planned Evasion Techniques

The following techniques were designed but not yet implemented. They address detection vectors that the current build does not fully cover.

---

### Request Header Normalization *(planned, high priority)*

**Problem:** The client sends HTTP requests with Python's default headers. The user agent string alone (`python-requests/2.x`) is sufficient for a TLS-inspecting proxy to identify the traffic as non-browser. Combined with the absence of browser-specific headers (`Sec-Fetch-*`, `Origin`, `Referer`), the HTTP fingerprint is distinct from legitimate Google Forms usage.

**Fix:** Pass Chrome-accurate headers on every request in `channel/sheets.py`:

```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://docs.google.com",
    "Referer": "<form_url>",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
}
```

The `Referer` and `Origin` headers reference the actual form URL already present in config — no new configuration required. This makes every HTTP transaction indistinguishable from a Chrome browser submitting a real Google Form.

**What it defeats:** TLS inspection + user agent analysis, HTTP fingerprinting.

---

### Sandbox Detection *(planned)*

**Problem:** Dynamic analysis sandboxes execute the binary in a controlled VM environment and observe behavior. If the binary behaves identically in a sandbox and on a real target, it will be caught.

**Fix:** On startup, before first contact, check for sandbox indicators:

- Uptime < 5 minutes (sandboxes are freshly booted)
- Single CPU core
- Total RAM < 2 GB
- Known sandbox usernames: `admin`, `sandbox`, `malware`, `virus`, `test`
- Known VM MAC address prefixes (VMware, VirtualBox, QEMU)
- Absence of user activity artifacts (no browser history, no recent documents)

If indicators are present: exit cleanly with no network activity. The sandbox reports "no suspicious behavior."

**What it defeats:** Dynamic analysis sandboxes, automated behavioral detection pipelines.

---

### Sleep Before First Contact *(planned)*

**Problem:** Most automated sandboxes have a fixed analysis window (typically 2-3 minutes). A binary that does nothing for the first 5-10 minutes never shows suspicious behavior during the observation window.

**Fix:** On startup, sleep for a configurable duration before the first poll cycle. Configurable via environment variable so the deployed binary can be tuned per-engagement without a rebuild.

**What it defeats:** Time-bounded sandbox analysis. Complements sandbox detection — the sleep is the fallback when indicator-based detection is uncertain.

---

### String Obfuscation *(planned)*

**Problem:** Even with Nuitka compilation, string literals survive in the binary's data segment. `strings ./binary` reveals error messages, format strings, and protocol-level strings (`heartbeat`, `shell`, `fernet`, `command_id`) that fingerprint the tool even without bytecode analysis.

**Note:** Operational config (spreadsheet ID, form URLs, encryption key) is never hardcoded — it comes from environment variables at runtime. The strings that remain are structural protocol strings.

**Fix:** A build-time pre-processing step encrypts all targeted string literals with a hardcoded key, replacing them with decrypt calls. The key is baked into the binary — this is not cryptographic security, it is signature evasion. The strings are never visible in the binary's data segment; they exist only transiently in memory during execution.

**What it defeats:** `strings`-based static analysis, string-pattern AV signatures, string extraction from memory dumps (transient — strings exist briefly but are not resident).

---

### Dropper Pattern *(planned)*

**Problem:** The binary requires environment variables to be set before launch (spreadsheet ID, form URLs, encryption key). On a real target, something must set those variables — and that something is a disk artifact.

**Fix:** A thin first-stage dropper (shell script, PowerShell, or compiled binary) sets the environment variables and launches the client binary as a child process, then exits immediately. The dropper is the only artifact that contains config. The client inherits config from the environment and never reads from disk.

For persistence (cron, systemd, registry run key): the persistence entry carries the dropper, not just the binary. The dropper re-sets the environment on each execution. Alternatively, systemd `Environment=` lines or Windows registry `REG_EXPAND_SZ` values embed the vars directly in the persistence mechanism.

**What it defeats:** Config discovery on disk, forensic recovery of operational parameters from the client binary.

---

### Channel Rotation *(planned)*

**Problem:** All C2 traffic goes through a single Google Sheets document. If the document is discovered and deleted, or the account is suspended, the C2 channel is gone permanently.

**Fix:** The `switch_channel` command (planned) allows the operator to pivot the client to a completely different transport mid-operation — Firebase, a custom HTTP endpoint, or any other implemented channel backend. The current Sheets channel burns after the switch; it is no longer reachable or useful to a defender.

Combined with `switch_encryption` and `switch_fragmenter`, a full pivot sequence (encryption → fragmentation → channel) can be executed without restarting the client.

**What it defeats:** Single-channel dependency, document-level takedowns, account suspension.

---

### Memory-Only Operation *(planned)*

**Problem:** The client binary is written to disk on the target machine. Disk forensics can recover it, analyze it, and extract indicators of compromise.

**Fix:** A memory-only loader executes the client payload entirely in process memory without writing to disk. The loader itself may be a small compiled binary, but the C2 logic never touches the filesystem.

This is the highest complexity technique on this list and requires OS-specific implementation (Linux: `memfd_create` + `fexecve`; Windows: process hollowing or reflective DLL injection).

**What it defeats:** Disk forensics, file-based AV scanning, artifact recovery post-incident.

---

### Exec-Over-the-Wire / `load_module` *(planned)*

**Problem:** New capabilities require a new binary to be deployed on the target. Each deployment is a new risk surface.

**Fix:** The server sends raw Python source for a new module. The client `exec`s it into a live module object and registers it as the active handler, channel, or crypto implementation. New capabilities are pushed post-deployment with no new files on disk.

Requires tight encryption and authentication — only a holder of the Fernet key can send valid `load_module` commands. The existing encrypted channel already provides this boundary.

**What it defeats:** Static capability limitations, need for re-deployment when requirements change.
