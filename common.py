import csv
import io
import json
import os
import requests


def load_env(path=".env"):
    """Parse .env file into os.environ."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip()


def get_encryptor():
    """Return the configured Encryptor instance based on ENCRYPTION_METHOD env var."""
    method = os.environ.get("ENCRYPTION_METHOD", "plaintext").strip().lower()
    if method == "fernet":
        from crypto.fernet import FernetEncryptor
        return FernetEncryptor()
    from crypto.plaintext import PlaintextEncryptor
    return PlaintextEncryptor()


def _get_column_map(env_key):
    """Load {logical_name: random_col_name} from env var.
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
    """Translate random column header keys → logical names using reverse of column_map.
    Keys not present in the map (e.g. form_timestamp) pass through unchanged.
    """
    if not column_map:
        return row
    reverse = {v: k for k, v in column_map.items()}
    return {reverse.get(k, k): v for k, v in row.items()}


def _encrypt_row(row, enc):
    """Encrypt all non-empty values in a row dict."""
    out = {}
    for k, v in row.items():
        if v:
            try:
                out[k] = enc.encrypt(str(v))
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


def _decrypt_row(row, enc):
    """Decrypt all non-empty values in a row dict.
    Silently passes through values that fail decryption (e.g. legacy plaintext rows).
    """
    out = {}
    for k, v in row.items():
        if v:
            try:
                out[k] = enc.decrypt(v)
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


def sheet_url(gid):
    """Build CSV export URL for a tab."""
    sid = os.environ["SPREADSHEET_ID"]
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


def read_tab(gid, timeout=15):
    """Fetch a tab as list of dicts via CSV export."""
    url = sheet_url(gid)
    resp = requests.get(url, timeout=timeout)
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
    """Read config tab, translate column names, decrypt values, return dict of key→value."""
    gid = os.environ["CONFIG_GID"]
    rows = read_tab(gid)
    column_map = _get_column_map("CONFIG_COLUMN_MAP")
    enc = get_encryptor()
    result = {}
    for row in rows:
        row = _translate_row(row, column_map)
        row = _decrypt_row(row, enc)
        if "key" in row and "value" in row:
            result[row["key"]] = row["value"]
    return result


def read_inbox():
    """Read inbox tab, translate column names, decrypt all fields, return list of task dicts."""
    gid = os.environ["INBOX_GID"]
    rows = read_tab(gid)
    column_map = _get_column_map("INBOX_COLUMN_MAP")
    enc = get_encryptor()
    result = []
    for row in rows:
        row = _translate_row(row, column_map)
        row = _decrypt_row(row, enc)
        result.append(row)
    return result


def read_outbox():
    """Read outbox tab, translate column names, decrypt all fields, return list of result dicts."""
    gid = os.environ["OUTBOX_GID"]
    rows = read_tab(gid)
    column_map = _get_column_map("OUTBOX_COLUMN_MAP")
    enc = get_encryptor()
    result = []
    for row in rows:
        row = _translate_row(row, column_map)
        row = _decrypt_row(row, enc)
        result.append(row)
    return result


def write_form(data):
    """Encrypt all fields, then POST to Google Forms (outbox)."""
    enc = get_encryptor()
    encrypted = _encrypt_row(data, enc)

    url = os.environ["FORMS_URL"]
    field_map = json.loads(os.environ["FORMS_FIELD_MAP"])
    payload = {entry_id: encrypted.get(field, "") for field, entry_id in field_map.items()}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        return resp.ok or resp.status_code in (301, 302, 303)
    except Exception as e:
        print(f"[error] write_form failed: {e}")
        return False


def write_inbox_form(data):
    """Encrypt all fields, then POST to Google Forms (inbox)."""
    enc = get_encryptor()
    encrypted = _encrypt_row(data, enc)

    url = os.environ["INBOX_FORMS_URL"]
    field_map = json.loads(os.environ["INBOX_FORMS_FIELD_MAP"])
    payload = {entry_id: encrypted.get(field, "") for field, entry_id in field_map.items()}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        return resp.ok or resp.status_code in (301, 302, 303)
    except Exception as e:
        print(f"[error] write_inbox_form failed: {e}")
        return False
