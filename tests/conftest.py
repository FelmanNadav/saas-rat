import csv
import io
import json

import pytest

# Minimal env that satisfies all os.environ[] lookups in common.py
DUMMY_ENV = {
    "SPREADSHEET_ID": "test_sheet_id",
    "INBOX_GID": "111",
    "OUTBOX_GID": "222",
    "FORMS_URL": "https://docs.google.com/forms/test/formResponse",
    "FORMS_FIELD_MAP": json.dumps({
        "command_id": "entry.1",
        "client_id":  "entry.2",
        "status":     "entry.3",
        "result":     "entry.4",
        "timestamp":  "entry.5",
    }),
    "INBOX_FORMS_URL": "https://docs.google.com/forms/inbox_test/formResponse",
    "INBOX_FORMS_FIELD_MAP": json.dumps({
        "command_id": "entry.10",
        "command":    "entry.11",
        "payload":    "entry.12",
        "target":     "entry.13",
        "status":     "entry.14",
        "created_at": "entry.15",
    }),
    "ENCRYPTION_METHOD": "plaintext",
    "FRAGMENT_METHOD":   "passthrough",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Set required env vars for every test."""
    for k, v in DUMMY_ENV.items():
        monkeypatch.setenv(k, v)


def make_csv(rows):
    """Convert list of dicts to a Google-Sheets-style CSV string."""
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
