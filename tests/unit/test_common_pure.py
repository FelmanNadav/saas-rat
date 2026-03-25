import json
import pytest

from channel.sheets import _translate_row, _get_column_map
from common import get_encryptor, get_fragmenter


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


# ---------------------------------------------------------------------------
# refresh_interval — channel base + SheetsChannel
# ---------------------------------------------------------------------------

def _make_stub_channel():
    from channel.base import Channel
    class StubChannel(Channel):
        def read_inbox(self): pass
        def read_outbox(self): pass
        def write_result(self, d): pass
        def write_task(self, d): pass
        def build_outbox_fragments(self, d, c): pass
        def build_inbox_fragments(self, d, c): pass
    return StubChannel()


class TestRefreshInterval:
    def test_sheets_default_is_five(self):
        from channel.sheets import SheetsChannel
        assert SheetsChannel().refresh_interval() == 5.0

    def test_base_default_is_thirty(self):
        ch = _make_stub_channel()
        assert ch.refresh_interval() == 30.0

    def test_set_refresh_interval_no_override_updates_value(self):
        ch = _make_stub_channel()
        ch.set_refresh_interval(10.0, manual=False)
        assert ch.refresh_interval() == 10.0

    def test_set_refresh_interval_manual_updates_value_and_sets_flag(self):
        ch = _make_stub_channel()
        ch.set_refresh_interval(20.0, manual=True)
        assert ch.refresh_interval() == 20.0
        assert ch._manual_override is True

    def test_set_refresh_interval_non_manual_blocked_by_override(self):
        ch = _make_stub_channel()
        ch.set_refresh_interval(20.0, manual=True)   # set override
        ch.set_refresh_interval(99.0, manual=False)  # heartbeat attempt — must be ignored
        assert ch.refresh_interval() == 20.0

    def test_set_refresh_interval_manual_overrides_existing_manual(self):
        ch = _make_stub_channel()
        ch.set_refresh_interval(20.0, manual=True)
        ch.set_refresh_interval(40.0, manual=True)
        assert ch.refresh_interval() == 40.0

    def test_clear_refresh_override_allows_heartbeat_sync(self):
        ch = _make_stub_channel()
        ch.set_refresh_interval(20.0, manual=True)
        ch.clear_refresh_override()
        assert ch._manual_override is False
        ch.set_refresh_interval(7.0, manual=False)
        assert ch.refresh_interval() == 7.0

    def test_clear_refresh_override_idempotent(self):
        ch = _make_stub_channel()
        ch.clear_refresh_override()
        ch.clear_refresh_override()
        assert ch._manual_override is False
