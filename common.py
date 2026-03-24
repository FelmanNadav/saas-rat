import csv
import io
import json
import os
import requests

def load_env(path=".env"):
    """Parse .env file into os.environ."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

def sheet_url(gid):
    """Build CSV export URL for a tab."""
    sid = os.environ["SPREADSHEET_ID"]
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

def read_tab(gid):
    """Fetch a tab as list of dicts."""
    resp = requests.get(sheet_url(gid), timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)

def read_config():
    """Read config tab, return dict of key→value."""
    rows = read_tab(os.environ["CONFIG_GID"])
    return {row["key"]: row["value"] for row in rows}

def read_inbox():
    """Read inbox tab, return list of command dicts."""
    return read_tab(os.environ["INBOX_GID"])

def read_outbox():
    """Read outbox tab, return list of result dicts."""
    return read_tab(os.environ["OUTBOX_GID"])

def write_form(data):
    """POST a dict to Google Forms (outbox). Keys are logical field names."""
    field_map = json.loads(os.environ["FORMS_FIELD_MAP"])
    form_data = {}
    for key, value in data.items():
        if key in field_map:
            form_data[field_map[key]] = value
    resp = requests.post(os.environ["FORMS_URL"], data=form_data, timeout=30)
    # Forms returns 200 with redirect on success — don't raise on redirect
    return resp.ok or resp.status_code in (301, 302, 303)

def write_inbox_form(data):
    """POST a dict to Google Forms (inbox). Keys are logical field names."""
    field_map = json.loads(os.environ["INBOX_FORMS_FIELD_MAP"])
    form_data = {}
    for key, value in data.items():
        if key in field_map:
            form_data[field_map[key]] = value
    resp = requests.post(os.environ["INBOX_FORMS_URL"], data=form_data, timeout=30)
    return resp.ok or resp.status_code in (301, 302, 303)
