import os

import requests

from wizard.channel.base import WizardChannel
from wizard import core


def _validate_firebase_url(v):
    if not v.startswith("https://"):
        return "Must start with https://"
    if ".firebaseio.com" not in v:
        return "Expected a Firebase Realtime Database URL (*.firebaseio.com)"


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
        core.info("Note: column name obfuscation does not apply to Firebase.")
        core.info("Fernet encryption (if enabled) handles operational security.")
        core.info()
        core.info("Create your database:")
        core.info("  1. Go to https://console.firebase.google.com")
        core.info("  2. Create a new project (or select an existing one)")
        core.info("  3. In the left sidebar: Build → Realtime Database")
        core.info("  4. Click 'Create Database'")
        core.info("  5. Choose a region, then select 'Start in test mode'")
        core.info("     (rules: public read/write — encryption keeps content secure)")
        core.info("  6. Your database URL appears at the top of the page:")
        core.info("     https://<project-id>-default-rtdb.firebaseio.com")
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

        return {
            "CHANNEL":              "firebase",
            "FIREBASE_URL":         firebase_url,
            "FIREBASE_INBOX_PATH":  inbox_path,
            "FIREBASE_OUTBOX_PATH": outbox_path,
        }
