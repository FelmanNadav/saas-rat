"""Tests for Firebase column obfuscation — _get_column_map, _translate_row,
_obfuscate_row, and their integration with _read/_write/read_inbox/
read_outbox/write_task/write_result.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

FIREBASE_ENV = {
    "CHANNEL":               "firebase",
    "FIREBASE_URL":          "https://test-default-rtdb.firebaseio.com",
    "FIREBASE_INBOX_PATH":   "c2/inbox",
    "FIREBASE_OUTBOX_PATH":  "c2/outbox",
    "ENCRYPTION_METHOD":     "plaintext",
    "FRAGMENT_METHOD":       "passthrough",
}

INBOX_MAP  = {"command_id": "f3a7k", "command": "x9m2p", "status": "h6v3j",
              "payload": "b4r8w", "target": "d1n5q", "created_at": "k2y9t"}
OUTBOX_MAP = {"command_id": "p7c4s", "client_id": "m1z8e", "status": "w5g2u",
              "result": "a9b3l", "timestamp": "r6q7n"}


@pytest.fixture
def firebase_env(monkeypatch):
    for k, v in FIREBASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FIREBASE_INBOX_COLUMN_MAP",  raising=False)
    monkeypatch.delenv("FIREBASE_OUTBOX_COLUMN_MAP", raising=False)
    import common
    common._active_channel = None
    yield
    common._active_channel = None


@pytest.fixture
def firebase_env_with_maps(monkeypatch):
    for k, v in FIREBASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FIREBASE_INBOX_COLUMN_MAP",  json.dumps(INBOX_MAP))
    monkeypatch.setenv("FIREBASE_OUTBOX_COLUMN_MAP", json.dumps(OUTBOX_MAP))
    import common
    common._active_channel = None
    yield
    common._active_channel = None


def ok_response(json_data=None):
    m = MagicMock()
    m.ok = True
    m.json.return_value = json_data
    m.raise_for_status = MagicMock()
    return m


# ---------------------------------------------------------------------------
# _get_column_map
# ---------------------------------------------------------------------------

class TestGetColumnMap:
    def test_returns_empty_when_env_unset(self, firebase_env):
        from channel.firebase import _get_column_map
        assert _get_column_map("FIREBASE_INBOX_COLUMN_MAP") == {}

    def test_returns_parsed_dict(self, firebase_env, monkeypatch):
        from channel.firebase import _get_column_map
        monkeypatch.setenv("FIREBASE_INBOX_COLUMN_MAP", json.dumps(INBOX_MAP))
        assert _get_column_map("FIREBASE_INBOX_COLUMN_MAP") == INBOX_MAP

    def test_returns_empty_on_invalid_json(self, firebase_env, monkeypatch):
        from channel.firebase import _get_column_map
        monkeypatch.setenv("FIREBASE_INBOX_COLUMN_MAP", "not-json")
        assert _get_column_map("FIREBASE_INBOX_COLUMN_MAP") == {}

    def test_returns_empty_on_whitespace_only(self, firebase_env, monkeypatch):
        from channel.firebase import _get_column_map
        monkeypatch.setenv("FIREBASE_INBOX_COLUMN_MAP", "   ")
        assert _get_column_map("FIREBASE_INBOX_COLUMN_MAP") == {}


# ---------------------------------------------------------------------------
# _translate_row (obfuscated → logical)
# ---------------------------------------------------------------------------

class TestTranslateRow:
    def test_passthrough_when_no_map(self):
        from channel.firebase import _translate_row
        row = {"command_id": "abc", "status": "pending"}
        assert _translate_row(row, {}) == row

    def test_translates_obfuscated_keys_to_logical(self):
        from channel.firebase import _translate_row
        row = {"f3a7k": "abc123", "x9m2p": "shell", "h6v3j": "pending"}
        result = _translate_row(row, INBOX_MAP)
        assert result["command_id"] == "abc123"
        assert result["command"]    == "shell"
        assert result["status"]     == "pending"

    def test_unknown_keys_pass_through(self):
        from channel.firebase import _translate_row
        row = {"f3a7k": "abc123", "unknown_key": "val"}
        result = _translate_row(row, INBOX_MAP)
        assert result["command_id"] == "abc123"
        assert result["unknown_key"] == "val"

    def test_empty_row_returns_empty(self):
        from channel.firebase import _translate_row
        assert _translate_row({}, INBOX_MAP) == {}


# ---------------------------------------------------------------------------
# _obfuscate_row (logical → obfuscated)
# ---------------------------------------------------------------------------

class TestObfuscateRow:
    def test_passthrough_when_no_map(self):
        from channel.firebase import _obfuscate_row
        row = {"command_id": "abc", "status": "pending"}
        assert _obfuscate_row(row, {}) == row

    def test_translates_logical_keys_to_obfuscated(self):
        from channel.firebase import _obfuscate_row
        row = {"command_id": "abc123", "command": "shell", "status": "pending"}
        result = _obfuscate_row(row, INBOX_MAP)
        assert result["f3a7k"] == "abc123"
        assert result["x9m2p"] == "shell"
        assert result["h6v3j"] == "pending"
        assert "command_id" not in result
        assert "command"    not in result

    def test_unknown_keys_pass_through(self):
        from channel.firebase import _obfuscate_row
        row = {"command_id": "abc123", "extra": "val"}
        result = _obfuscate_row(row, INBOX_MAP)
        assert result["f3a7k"] == "abc123"
        assert result["extra"] == "val"

    def test_roundtrip_with_translate(self):
        from channel.firebase import _obfuscate_row, _translate_row
        original = {"command_id": "abc123", "command": "shell", "status": "pending"}
        obfuscated = _obfuscate_row(original, INBOX_MAP)
        restored = _translate_row(obfuscated, INBOX_MAP)
        assert restored == original


# ---------------------------------------------------------------------------
# _read with column_map
# ---------------------------------------------------------------------------

class TestReadWithColumnMap:
    def test_de_obfuscates_keys_on_read(self, firebase_env):
        from channel.firebase import FirebaseChannel
        raw = {"entry1": {"f3a7k": "abc123", "x9m2p": "shell", "h6v3j": "pending",
                          "b4r8w": "", "d1n5q": "", "k2y9t": ""}}
        with patch("requests.get", return_value=ok_response(raw)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/p.json", INBOX_MAP)
        assert rows[0]["command_id"] == "abc123"
        assert rows[0]["command"]    == "shell"
        assert "f3a7k" not in rows[0]

    def test_no_map_leaves_keys_unchanged(self, firebase_env):
        from channel.firebase import FirebaseChannel
        raw = {"entry1": {"command_id": "abc123", "command": "shell"}}
        with patch("requests.get", return_value=ok_response(raw)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/p.json")
        assert rows[0]["command_id"] == "abc123"


# ---------------------------------------------------------------------------
# _write with column_map
# ---------------------------------------------------------------------------

class TestWriteWithColumnMap:
    def test_obfuscates_keys_on_write(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "command": "shell", "status": "pending",
                "payload": "", "target": "", "created_at": ""}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel()._write("https://x.firebaseio.com/p.json", data, INBOX_MAP)
        body = mock_put.call_args[1]["json"]
        assert "f3a7k" in body
        assert "x9m2p" in body
        assert "command_id" not in body
        assert "command"    not in body

    def test_no_map_sends_logical_keys(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "command": "shell"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel()._write("https://x.firebaseio.com/p.json", data)
        body = mock_put.call_args[1]["json"]
        assert "command_id" in body
        assert "command"    in body

    def test_values_encrypted_before_keys_obfuscated(self, firebase_env, monkeypatch):
        """Encryption operates on logical keys; obfuscation renames after."""
        from channel.firebase import FirebaseChannel
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key.decode())
        import common; common._active_channel = None

        data = {"command_id": "abc123", "command": "shell"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel()._write("https://x.firebaseio.com/p.json", data, INBOX_MAP)
        body = mock_put.call_args[1]["json"]
        # Keys should be obfuscated
        assert "f3a7k" in body
        # Values should be encrypted (not plaintext)
        assert body["f3a7k"] != "abc123"
        common._active_channel = None


# ---------------------------------------------------------------------------
# read_inbox / read_outbox integration
# ---------------------------------------------------------------------------

class TestReadInboxWithColumnMap:
    def test_uses_firebase_inbox_column_map(self, firebase_env_with_maps):
        from channel.firebase import FirebaseChannel
        raw = {"e1": {"f3a7k": "abc123", "x9m2p": "shell", "h6v3j": "pending",
                      "b4r8w": "", "d1n5q": "", "k2y9t": ""}}
        with patch("requests.get", return_value=ok_response(raw)):
            rows = FirebaseChannel().read_inbox()
        assert len(rows) == 1
        assert rows[0]["command_id"] == "abc123"
        assert rows[0]["command"]    == "shell"

    def test_no_map_reads_logical_keys(self, firebase_env):
        from channel.firebase import FirebaseChannel
        raw = {"e1": {"command_id": "abc123", "command": "shell",
                      "status": "pending", "payload": "", "target": "", "created_at": ""}}
        with patch("requests.get", return_value=ok_response(raw)):
            rows = FirebaseChannel().read_inbox()
        assert rows[0]["command_id"] == "abc123"


class TestReadOutboxWithColumnMap:
    def test_uses_firebase_outbox_column_map(self, firebase_env_with_maps):
        from channel.firebase import FirebaseChannel
        raw = {"e1": {"p7c4s": "abc123", "m1z8e": "client1",
                      "w5g2u": "success", "a9b3l": "ok", "r6q7n": "ts"}}
        with patch("requests.get", return_value=ok_response(raw)):
            rows = FirebaseChannel().read_outbox()
        assert rows[0]["command_id"] == "abc123"
        assert rows[0]["result"]     == "ok"


# ---------------------------------------------------------------------------
# write_task / write_result integration
# ---------------------------------------------------------------------------

class TestWriteTaskWithColumnMap:
    def test_obfuscates_keys_in_put_body(self, firebase_env_with_maps):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "command": "shell", "status": "pending",
                "payload": "", "target": "", "created_at": ""}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_task(data)
        body = mock_put.call_args[1]["json"]
        assert "f3a7k" in body        # command_id → f3a7k
        assert "command_id" not in body

    def test_url_still_keyed_by_logical_command_id(self, firebase_env_with_maps):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "command": "shell", "status": "pending",
                "payload": "", "target": "", "created_at": ""}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_task(data)
        url = mock_put.call_args[0][0]
        assert "abc123" in url

    def test_no_map_sends_logical_keys(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "command": "shell", "status": "pending",
                "payload": "", "target": "", "created_at": ""}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_task(data)
        body = mock_put.call_args[1]["json"]
        assert "command_id" in body


class TestWriteResultWithColumnMap:
    def test_obfuscates_keys_in_put_body(self, firebase_env_with_maps):
        from channel.firebase import FirebaseChannel
        data = {"command_id": "abc123", "client_id": "c1",
                "status": "success", "result": "ok", "timestamp": "ts"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_result(data)
        body = mock_put.call_args[1]["json"]
        assert "p7c4s" in body        # command_id → p7c4s
        assert "a9b3l" in body        # result → a9b3l
        assert "command_id" not in body
        assert "result"     not in body
