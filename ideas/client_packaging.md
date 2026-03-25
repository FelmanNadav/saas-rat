# Idea: Client Packaging, Obfuscation, and Dynamic Loading

## Context

The client needs to be deployable as a self-contained artifact with no obvious
files or dependencies on the target machine. This document covers packaging
options, config handling for packed binaries, and the dynamic module loading
problem introduced by the pluggable channel architecture.

---

## What Actually Needs to Be Baked In

Only the bootstrap channel config. Everything else either arrives over the wire
(via `switch_channel`, `config` command) or is server-only.

**Must be present at launch:**
- `SPREADSHEET_ID`
- `INBOX_GID`, `OUTBOX_GID`
- `FORMS_URL` + `FORMS_FIELD_MAP`
- `INBOX_FORMS_URL` + `INBOX_FORMS_FIELD_MAP`
- `ENCRYPTION_METHOD` + `ENCRYPTION_KEY`
- `FRAGMENT_METHOD` + `FRAGMENT_CHUNK_SIZE`
- `INBOX_COLUMN_MAP` + `OUTBOX_COLUMN_MAP` (if using obfuscation)

**Not needed on client:**
- `OPENAI_API_KEY` — server-only

**Not needed after first contact:**
- Any of the above, if `switch_channel` burns the bootstrap channel

---

## Attack Chain

```
Dropper (first stage)
  └── sets env vars (bootstrap channel config)
  └── launches client binary as child process
  └── exits

Client binary (inherited env)
  └── reads os.environ, no .env file needed
  └── connects to bootstrap channel
  └── receives switch_channel → moves to new channel
  └── bootstrap channel is now dead / unreachable
```

The dropper can be a shell script, macro, memory-only loader, or anything that
can set environment variables and exec a process. It never writes config to disk.

**The wrapper con:** persistence mechanisms (cron, systemd, registry run key)
must carry the dropper, not just the binary — otherwise env vars are missing
on reboot. This means the dropper is an artifact on disk. Mitigation: embed the
vars directly into the persistence entry (e.g. systemd `Environment=` lines).

---

## Config Handling Options for Packed Binaries

**Option A — Bake at build time**
Read `.env` at pack time, embed as constants. Single artifact, no runtime file.
Reconfiguring requires a rebuild. Good for stable deployments.

**Option B — Alternate file path**
Read config from an innocuous location (`~/.config/.sysconf`, `/tmp/.rc`).
Same mechanic, different path. Reconfigurable without rebuild, but file exists on disk.

**Option C — Environment variables only (recommended)**
Drop `load_env()`. Binary reads `os.environ` directly. Dropper sets vars before launch.
Nothing written to disk. Clean fit with channel switching — bootstrap vars only need
to survive until first `switch_channel`.

**Option D — Encrypted config blob**
Config stored as an encrypted file or hardcoded blob. Single master key passed
at runtime (argument or one env var) decrypts everything else. Config on disk
is opaque. Still requires passing the master key somehow.

**Current recommendation: Option C.** `load_env()` in `common.py` is already the
single injection point — swapping it out is a one-function change.

---

## Cross-Platform Packaging

PyInstaller cannot cross-compile. A binary built on Linux only runs on Linux.
To target multiple OSes, run `packager.py` natively on each:

| Target OS | Build machine | Output |
|---|---|---|
| Linux (x86_64) | Any Linux box | ELF binary |
| Windows | Windows machine | .exe |
| macOS | macOS machine | Mach-O binary |

The client code itself is fully cross-platform — no OS-specific code. `packager.py`
works identically on all three. Maintain one build machine per target OS.

---

## Obfuscation Profiles

Layers can be stacked. Test each level against the defense product independently
to find the detection boundary.

| Profile | Build | Reversibility | Status |
|---|---|---|---|
| **basic** | PyInstaller `--onefile` | Extractable with pyinstxtractor + decompile-bytecode | **Implement first** |
| **upx** | PyInstaller + UPX compression | Extra unpack step before Python layer is accessible | Planned |
| **pyarmor** | PyArmor source encryption → PyInstaller | pyinstxtractor yields encrypted bytecode, not readable source | Planned |
| **nuitka** | Nuitka → native ELF/.exe | No Python bytecode — C-level binary, hardest to reverse | Planned |

**Recommended test sequence:** build basic → run against defense product → if caught,
stop. If not caught, add upx → retest. Continue until detection fires or all profiles
exhausted. The boundary tells you exactly how strong the obfuscation needs to be.

---

## Packaging Options

**PyInstaller**
Bundles Python + all imports into a single binary. No Python required on target.
- Fast to build
- `get_encryptor()` / `get_fragmenter()` do runtime imports — must declare as
  `--hidden-import fragmenter.fixed`, `--hidden-import crypto.fernet` etc.
- Everything must be known at build time (see Dynamic Loading problem below)
- Does not cross-compile — build on target OS

**PyArmor**
Encrypts Python source before PyInstaller bundles it. Bytecode inside the binary
is encrypted — pyinstxtractor yields gibberish instead of readable code.
Works as a pre-processing step on top of PyInstaller.

**UPX**
Compresses the PyInstaller binary. PyInstaller has `--upx-dir` built in.
Adds a binary unpacking step before the Python layer is accessible.

**Nuitka**
Compiles Python to C, produces a native binary. No Python bytecode to extract.
Hardest to reverse. Slower build. Better for long-term operational security.
Does not cross-compile — build on target OS.

---

## The Dynamic Loading Problem

The pluggable channel architecture (`get_channel()` factory, `channel/sheets.py`,
`channel/firebase.py`) assumes new backends can be added as Python files. PyInstaller
and Nuitka break this — everything must be in the bundle at build time.

`switch_channel` sending to a Firebase backend fails if `channel/firebase.py`
was not compiled into the binary.

### Solutions

**Option A — Compile all known backends upfront (recommended for now)**
Bundle every channel backend at build time even if not active. `switch_channel`
can activate any of them. Limited to what was compiled in — no post-deployment
new backends, but covers all planned transports.

**Option B — `exec`/`eval` over the wire ("bake by request")**
Server sends raw Python source for a new module. Client `exec`s it into a live
module object, registers it as the active channel.
- No compile-time constraints — push any new backend post-deployment
- Maximum flexibility: new channel types, new crypto, new handlers
- Risk: if the channel is compromised, attacker gets arbitrary code execution
  on the client. Requires tight encryption + authentication (Fernet already
  provides this — only someone with the key can send valid commands)
- This is how most mature C2 frameworks implement plugins
- Natural fit with the existing encrypted channel design

**Option C — Thin loader + dynamic module fetch**
Binary is a minimal loader. Channel backends are fetched at runtime via HTTP
or the C2 channel itself and loaded with `importlib`. Loader is compiled,
plugins are not. Middle ground between A and B.

### Recommendation

**Ship Option A first** — compile all known backends, covers immediate needs.
**Design Option B properly when ready** — it is the right long-term answer and
fits the architecture cleanly. The encrypted channel already provides the
authentication boundary that makes `exec`-over-the-wire safe in practice.
Do not bolt it on — design it as a first-class `load_module` command with
its own handler, signature verification, and rollback on failure.

---

## Implementation Order

1. `packager.py` — interactive script, selects modules, runs PyInstaller (basic profile)
   - Asks which crypto to include (plaintext / fernet / both)
   - Asks which fragmenter to include (passthrough / fixed / both)
   - Declares all required `--hidden-import` flags
   - Outputs single binary to `dist/client`
2. Test basic profile against defense product
3. Add UPX profile to `packager.py` if basic is caught
4. Add PyArmor profile if UPX is caught
5. Nuitka as final escalation
6. Design `load_module` command as a separate workstream (deferred)
