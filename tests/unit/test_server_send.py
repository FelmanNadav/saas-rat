"""Tests for server.send_command() and sheets cleanup warning format."""

import json
import uuid
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# send_command()
# ---------------------------------------------------------------------------

class TestSendCommand:
    def test_returns_valid_uuid(self, monkeypatch):
        import server
        monkeypatch.setattr("common.write_inbox_form", MagicMock())
        result = server.send_command("system_info")
        uuid.UUID(result)  # raises if not a valid UUID

    def test_calls_write_inbox_form_once_for_small_payload(self, monkeypatch):
        import server
        mock_write = MagicMock()
        monkeypatch.setattr("common.write_inbox_form", mock_write)
        server.send_command("echo", payload={"msg": "hello"})
        assert mock_write.call_count == 1

    def test_write_inbox_form_data_shape(self, monkeypatch):
        import server
        captured = {}

        def fake_write(data):
            captured.update(data)

        monkeypatch.setattr("common.write_inbox_form", fake_write)
        cmd_id = server.send_command("system_info", payload={"k": "v"}, target="outbox")

        assert captured["command_id"] == cmd_id
        assert captured["command"] == "system_info"
        assert captured["target"] == "outbox"
        assert captured["status"] == "pending"
        assert "created_at" in captured
        # payload is JSON-encoded
        assert json.loads(captured["payload"]) == {"k": "v"}

    def test_default_target_is_outbox(self, monkeypatch):
        import server
        captured = {}

        def fake_write(data):
            captured.update(data)

        monkeypatch.setattr("common.write_inbox_form", fake_write)
        server.send_command("echo")
        assert captured["target"] == "outbox"

    def test_no_timing_output(self, monkeypatch, capsys):
        """_print_delivery_estimate was removed — no [timing] lines should appear."""
        import server
        monkeypatch.setattr("common.write_inbox_form", MagicMock())
        server.send_command("system_info")
        out = capsys.readouterr().out
        assert "[timing]" not in out

    def test_no_fragmentation_config_leaked(self, monkeypatch, capsys):
        """Timing removal also means chunk size / interval never printed."""
        import server
        monkeypatch.setattr("common.write_inbox_form", MagicMock())
        server.send_command("system_info", payload={"data": "x" * 100})
        out = capsys.readouterr().out
        assert "chunk" not in out.lower() or "[server] Payload fragmented" in out
        # The old _print_delivery_estimate printed "chunk_size" — confirm gone
        assert "chunk_size" not in out

    def test_fragmented_payload_calls_write_multiple_times(self, monkeypatch):
        import server

        # Use fixed fragmenter with small chunk size via env
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        mock_write = MagicMock()
        monkeypatch.setattr("common.write_inbox_form", mock_write)

        big_payload = {"data": "A" * 100}
        server.send_command("run_command", payload=big_payload)

        assert mock_write.call_count > 1

    def test_fragmented_rows_share_command_id(self, monkeypatch):
        import server

        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        written = []
        monkeypatch.setattr("common.write_inbox_form", lambda d: written.append(dict(d)))

        cmd_id = server.send_command("run_command", payload={"data": "B" * 100})

        assert len(written) > 1
        for row in written:
            assert row["command_id"] == cmd_id

    def test_empty_payload_defaults_to_empty_dict(self, monkeypatch):
        import server
        captured = {}

        def fake_write(data):
            captured.update(data)

        monkeypatch.setattr("common.write_inbox_form", fake_write)
        server.send_command("system_info")
        assert json.loads(captured["payload"]) == {}


# ---------------------------------------------------------------------------
# Sheets cleanup warning format
# ---------------------------------------------------------------------------

class TestSheetsCleanupWarning:
    """_delete_by_command_id must include the exception type in its warning."""

    def _make_channel(self):
        from channel.sheets import SheetsChannel
        ch = SheetsChannel.__new__(SheetsChannel)
        return ch

    def test_warning_includes_exception_type(self, monkeypatch, capsys):
        ch = self._make_channel()

        def bad_gspread_client():
            return MagicMock(
                open_by_key=MagicMock(side_effect=ValueError("no access"))
            )

        monkeypatch.setattr(ch, "_gspread_client", bad_gspread_client)
        monkeypatch.setenv("SPREADSHEET_ID", "sid123")
        monkeypatch.setenv("INBOX_GID", "111")

        result = ch._delete_by_command_id("cmd-1", "INBOX_GID", "INBOX_COLUMN_MAP")

        assert result is False
        out = capsys.readouterr().out
        assert "ValueError" in out
        assert "no access" in out

    def test_warning_format_type_colon_message(self, monkeypatch, capsys):
        """Format must be: [warn] sheet cleanup failed: ExcType: message"""
        ch = self._make_channel()

        def bad_client():
            return MagicMock(
                open_by_key=MagicMock(side_effect=RuntimeError("boom"))
            )

        monkeypatch.setattr(ch, "_gspread_client", bad_client)
        monkeypatch.setenv("SPREADSHEET_ID", "sid123")
        monkeypatch.setenv("INBOX_GID", "111")

        ch._delete_by_command_id("cmd-1", "INBOX_GID", "INBOX_COLUMN_MAP")
        out = capsys.readouterr().out
        assert "[warn] sheet cleanup failed: RuntimeError: boom" in out

    def test_returns_false_when_gspread_client_is_none(self, monkeypatch):
        ch = self._make_channel()
        monkeypatch.setattr(ch, "_gspread_client", lambda: None)
        result = ch._delete_by_command_id("cmd-1", "INBOX_GID", "INBOX_COLUMN_MAP")
        assert result is False

    def test_warning_not_emitted_on_success(self, monkeypatch, capsys):
        ch = self._make_channel()

        ws = MagicMock()
        ws.id = 111
        ws.row_values.return_value = ["command_id", "command", "status"]
        ws.col_values.return_value = ["command_id", "target-cmd"]
        ws.delete_rows = MagicMock()

        ss = MagicMock()
        ss.worksheets.return_value = [ws]

        gc = MagicMock()
        gc.open_by_key.return_value = ss

        monkeypatch.setattr(ch, "_gspread_client", lambda: gc)
        monkeypatch.setenv("SPREADSHEET_ID", "sid123")
        monkeypatch.setenv("INBOX_GID", "111")

        import common
        monkeypatch.setattr("common.get_encryptor", lambda: MagicMock(decrypt=lambda v: v))

        result = ch._delete_by_command_id("target-cmd", "INBOX_GID", "INBOX_COLUMN_MAP")
        out = capsys.readouterr().out
        assert "[warn] sheet cleanup failed" not in out
        assert result is True
