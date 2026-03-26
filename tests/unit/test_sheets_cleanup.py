"""Unit tests for Sheets channel cleanup via service account.

Covers: supports_cleanup property, _gspread_client, _delete_by_command_id,
delete_task, delete_result — with column obfuscation and encryption variants.

All gspread calls are mocked — no real Google API connection required.
"""

import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SHEETS_BASE_ENV = {
    "CHANNEL":            "sheets",
    "SPREADSHEET_ID":     "sheet123",
    "INBOX_GID":          "111",
    "OUTBOX_GID":         "222",
    "ENCRYPTION_METHOD":  "plaintext",
    "FRAGMENT_METHOD":    "passthrough",
}


@pytest.fixture
def sheets_env(monkeypatch):
    for k, v in SHEETS_BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("INBOX_COLUMN_MAP",            raising=False)
    monkeypatch.delenv("OUTBOX_COLUMN_MAP",           raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    import common
    common._active_channel = None
    yield
    common._active_channel = None


@pytest.fixture
def sheets_sa_env(monkeypatch, tmp_path):
    """Sheets env with GOOGLE_SERVICE_ACCOUNT_JSON set to a dummy path."""
    sa_file = tmp_path / "sa.json"
    sa_file.write_text("{}")
    for k, v in SHEETS_BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(sa_file))
    monkeypatch.delenv("INBOX_COLUMN_MAP",  raising=False)
    monkeypatch.delenv("OUTBOX_COLUMN_MAP", raising=False)
    import common
    common._active_channel = None
    yield str(sa_file)
    common._active_channel = None


def _make_worksheet(gid, headers, cmd_id_col_values):
    """Build a mock gspread worksheet."""
    ws = MagicMock()
    ws.id = gid
    ws.row_values.return_value = headers
    col_idx = headers.index("command_id") + 1 if "command_id" in headers else 1
    ws.col_values.return_value = cmd_id_col_values
    return ws, col_idx


def _make_gspread_client(worksheets):
    gc = MagicMock()
    gc.open_by_key.return_value.worksheets.return_value = worksheets
    return gc


# ---------------------------------------------------------------------------
# supports_cleanup
# ---------------------------------------------------------------------------

class TestSheetsSupportsCleanup:
    def test_false_without_service_account(self, sheets_env):
        from channel.sheets import SheetsChannel
        assert SheetsChannel().supports_cleanup is False

    def test_true_with_service_account(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        assert SheetsChannel().supports_cleanup is True

    def test_false_when_env_var_is_empty_string(self, monkeypatch, sheets_env):
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "   ")
        from channel.sheets import SheetsChannel
        assert SheetsChannel().supports_cleanup is False


# ---------------------------------------------------------------------------
# _gspread_client
# ---------------------------------------------------------------------------

class TestGspreadClient:
    def test_returns_none_when_no_sa_env(self, sheets_env):
        from channel.sheets import SheetsChannel
        assert SheetsChannel()._gspread_client() is None

    def test_returns_client_when_configured(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        mock_gc = MagicMock()
        with patch("gspread.service_account", return_value=mock_gc):
            client = SheetsChannel()._gspread_client()
        assert client is mock_gc

    def test_returns_none_on_auth_failure(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        with patch("gspread.service_account", side_effect=Exception("auth failed")):
            client = SheetsChannel()._gspread_client()
        assert client is None


# ---------------------------------------------------------------------------
# _delete_by_command_id
# ---------------------------------------------------------------------------

class TestDeleteByCommandId:
    def test_deletes_matching_row(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "command", "status"]
        ws.col_values.return_value = ["command_id", "abc123", "other_cmd"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        ws.delete_rows.assert_called_once_with(2)
        assert result is True

    def test_deletes_multiple_matching_rows_in_reverse(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "status"]
        ws.col_values.return_value = ["command_id", "abc123", "other", "abc123"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        # rows 2 and 4 match; must delete in reverse to preserve indices
        assert ws.delete_rows.call_args_list == [call(4), call(2)]

    def test_returns_false_when_no_match(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "status"]
        ws.col_values.return_value = ["command_id", "other_cmd"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        ws.delete_rows.assert_not_called()
        assert result is False

    def test_returns_false_when_column_not_in_headers(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["status", "result"]  # no command_id column

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        assert result is False

    def test_returns_false_when_tab_not_found(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 999  # wrong GID

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        assert result is False

    def test_returns_false_on_gspread_exception(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        gc = MagicMock()
        gc.open_by_key.side_effect = Exception("API error")
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        assert result is False

    def test_returns_false_when_no_gspread_client(self, sheets_env):
        from channel.sheets import SheetsChannel
        # No SA configured — _gspread_client returns None
        result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")
        assert result is False


# ---------------------------------------------------------------------------
# Column obfuscation
# ---------------------------------------------------------------------------

class TestDeleteWithColumnObfuscation:
    def test_uses_obfuscated_column_name_to_find_rows(self, sheets_sa_env, monkeypatch):
        from channel.sheets import SheetsChannel
        monkeypatch.setenv("INBOX_COLUMN_MAP", '{"command_id":"f3a7k","command":"x9m2p"}')

        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["f3a7k", "x9m2p"]  # obfuscated headers
        ws.col_values.return_value = ["f3a7k", "abc123"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        ws.delete_rows.assert_called_once_with(2)
        assert result is True

    def test_falls_back_to_logical_name_when_no_map(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "status"]
        ws.col_values.return_value = ["command_id", "abc123"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        assert result is True


# ---------------------------------------------------------------------------
# Fernet encryption
# ---------------------------------------------------------------------------

class TestDeleteWithEncryption:
    def test_decrypts_cell_value_before_comparing(self, sheets_sa_env, monkeypatch):
        from channel.sheets import SheetsChannel
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        encrypted_id = Fernet(key).encrypt(b"abc123").decode()

        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key.decode())

        import common
        common._active_channel = None

        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "status"]
        ws.col_values.return_value = ["command_id", encrypted_id]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            result = SheetsChannel()._delete_by_command_id("abc123", "INBOX_GID", "INBOX_COLUMN_MAP")

        ws.delete_rows.assert_called_once_with(2)
        assert result is True
        common._active_channel = None


# ---------------------------------------------------------------------------
# delete_task / delete_result routing
# ---------------------------------------------------------------------------

class TestDeleteTaskResult:
    def test_delete_task_targets_inbox_gid(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 111  # INBOX_GID
        ws.row_values.return_value = ["command_id"]
        ws.col_values.return_value = ["command_id", "abc123"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            SheetsChannel().delete_task("abc123")

        ws.delete_rows.assert_called_once()

    def test_delete_result_targets_outbox_gid(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        ws = MagicMock()
        ws.id = 222  # OUTBOX_GID
        ws.row_values.return_value = ["command_id"]
        ws.col_values.return_value = ["command_id", "abc123"]

        gc = _make_gspread_client([ws])
        with patch("gspread.service_account", return_value=gc):
            SheetsChannel().delete_result("abc123")

        ws.delete_rows.assert_called_once()

    def test_delete_task_does_not_touch_outbox(self, sheets_sa_env):
        from channel.sheets import SheetsChannel
        inbox_ws = MagicMock()
        inbox_ws.id = 111
        inbox_ws.row_values.return_value = ["command_id"]
        inbox_ws.col_values.return_value = ["command_id", "abc123"]

        outbox_ws = MagicMock()
        outbox_ws.id = 222

        gc = _make_gspread_client([inbox_ws, outbox_ws])
        with patch("gspread.service_account", return_value=gc):
            SheetsChannel().delete_task("abc123")

        outbox_ws.delete_rows.assert_not_called()
