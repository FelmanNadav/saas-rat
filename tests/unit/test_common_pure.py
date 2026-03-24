import json
import pytest

from common import _translate_row, _get_column_map, get_encryptor, get_fragmenter


# ---------------------------------------------------------------------------
# _translate_row
# ---------------------------------------------------------------------------

class TestTranslateRow:
    def test_with_map_translates_keys(self):
        col_map = {"command_id": "f3a7k", "command": "x9m2p"}
        row = {"f3a7k": "abc-123", "x9m2p": "shell"}
        assert _translate_row(row, col_map) == {"command_id": "abc-123", "command": "shell"}

    def test_without_map_unchanged(self):
        row = {"command_id": "abc-123", "command": "shell"}
        assert _translate_row(row, {}) == row

    def test_unknown_keys_pass_through(self):
        col_map = {"command_id": "f3a7k"}
        row = {"f3a7k": "abc-123", "form_timestamp": "2024-01-01"}
        result = _translate_row(row, col_map)
        assert result["command_id"] == "abc-123"
        assert result["form_timestamp"] == "2024-01-01"

    def test_partial_map_translates_known_only(self):
        col_map = {"command_id": "f3a7k"}
        row = {"f3a7k": "abc", "status": "pending"}  # status not in map
        result = _translate_row(row, col_map)
        assert result["command_id"] == "abc"
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# _get_column_map
# ---------------------------------------------------------------------------

class TestGetColumnMap:
    def test_valid_json(self, monkeypatch):
        monkeypatch.setenv("TEST_COL_MAP", '{"a": "x", "b": "y"}')
        assert _get_column_map("TEST_COL_MAP") == {"a": "x", "b": "y"}

    def test_invalid_json_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_COL_MAP", "not valid json {{")
        assert _get_column_map("TEST_COL_MAP") == {}

    def test_missing_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("TEST_COL_MAP", raising=False)
        assert _get_column_map("TEST_COL_MAP") == {}

    def test_empty_string_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_COL_MAP", "")
        assert _get_column_map("TEST_COL_MAP") == {}


# ---------------------------------------------------------------------------
# get_encryptor factory
# ---------------------------------------------------------------------------

class TestGetEncryptor:
    def test_default_plaintext(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        from crypto.plaintext import PlaintextEncryptor
        assert isinstance(get_encryptor(), PlaintextEncryptor)

    def test_fernet(self, monkeypatch):
        from cryptography.fernet import Fernet
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
        from crypto.fernet import FernetEncryptor
        assert isinstance(get_encryptor(), FernetEncryptor)

    def test_unknown_falls_back_to_plaintext(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "unknown_method")
        from crypto.plaintext import PlaintextEncryptor
        assert isinstance(get_encryptor(), PlaintextEncryptor)

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "PLAINTEXT")
        from crypto.plaintext import PlaintextEncryptor
        assert isinstance(get_encryptor(), PlaintextEncryptor)


# ---------------------------------------------------------------------------
# get_fragmenter factory
# ---------------------------------------------------------------------------

class TestGetFragmenter:
    def test_default_passthrough(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "passthrough")
        from fragmenter.passthrough import PassthroughFragmenter
        assert isinstance(get_fragmenter(), PassthroughFragmenter)

    def test_fixed(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        from fragmenter.fixed import FixedFragmenter
        assert isinstance(get_fragmenter(), FixedFragmenter)

    def test_unknown_falls_back_to_passthrough(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "unknown")
        from fragmenter.passthrough import PassthroughFragmenter
        assert isinstance(get_fragmenter(), PassthroughFragmenter)
