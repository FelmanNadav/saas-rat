"""Tests for runtime channel switching — _apply_channel_switch (client + server),
deferred switch pattern in the main loop, and server-side mirroring.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_channel(monkeypatch):
    """Reset common._active_channel between every test."""
    import common
    common._active_channel = None
    monkeypatch.setenv("CHANNEL", "sheets")
    yield
    common._active_channel = None


@pytest.fixture(autouse=True)
def reset_client_config():
    import client
    client._client_config.update({
        "cycle_interval_sec": "1",
        "cycle_jitter_min": "0",
        "cycle_jitter_max": "0",
        "client_id": "NADAV",
        "heartbeat_every": "100",
    })
    client._send_queue.clear()


def _task(command, payload=None, command_id="test-id"):
    return {
        "command_id": command_id,
        "command": command,
        "payload": json.dumps(payload or {}),
        "status": "pending",
    }


def _stub_channel(name, write_ok=True):
    """Return a minimal channel stub."""
    ch = MagicMock()
    ch.name = name
    ch.write_result.return_value = write_ok
    ch.read_inbox.return_value = []
    ch.read_outbox.return_value = []
    ch.supports_cleanup = False
    return ch


# ---------------------------------------------------------------------------
# Client: _apply_channel_switch
# ---------------------------------------------------------------------------

class TestClientApplyChannelSwitch:
    def test_switches_active_channel_to_firebase(self, monkeypatch):
        from client import _apply_channel_switch
        import common
        from channel.firebase import FirebaseChannel
        _apply_channel_switch("firebase")
        assert isinstance(common.get_channel(), FirebaseChannel)

    def test_switches_active_channel_to_sheets(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "firebase")
        import common
        from channel.firebase import FirebaseChannel
        common.set_channel(FirebaseChannel())

        from client import _apply_channel_switch
        from channel.sheets import SheetsChannel
        _apply_channel_switch("sheets")
        assert isinstance(common.get_channel(), SheetsChannel)

    def test_updates_channel_env_var_to_firebase(self, monkeypatch):
        from client import _apply_channel_switch
        _apply_channel_switch("firebase")
        assert os.environ["CHANNEL"] == "firebase"

    def test_updates_channel_env_var_to_sheets(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "firebase")
        from client import _apply_channel_switch
        _apply_channel_switch("sheets")
        assert os.environ["CHANNEL"] == "sheets"


# ---------------------------------------------------------------------------
# Client: deferred switch applied after write in dispatch flow
# ---------------------------------------------------------------------------

class TestDeferredSwitchAfterWrite:
    def test_ack_written_on_old_channel_before_switch(self, monkeypatch):
        """The write_result call must go to the OLD channel, not the new one."""
        monkeypatch.setenv("CHANNEL", "sheets")
        import common

        old_ch = _stub_channel("sheets")
        new_ch = _stub_channel("firebase")
        common.set_channel(old_ch)

        from client import dispatch, _apply_channel_switch
        result = dispatch(_task("switch_channel", {"channel": "firebase"}))
        deferred = result.pop("_deferred_switch", None)
        assert deferred == "firebase"

        # Simulate main loop: write on current (old) channel, then switch
        common.write_form(result)
        assert old_ch.write_result.called
        assert not new_ch.write_result.called

        common.set_channel(new_ch)  # simulates _apply_channel_switch
        assert isinstance(common.get_channel(), MagicMock)
        assert common.get_channel().name == "firebase"

    def test_subsequent_writes_go_to_new_channel(self, monkeypatch):
        """After _apply_channel_switch, write_form uses the new channel."""
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        from client import _apply_channel_switch

        new_ch = _stub_channel("firebase")
        _apply_channel_switch.__module__  # trigger import

        # Switch and verify
        common.set_channel(new_ch)
        data = {"command_id": "x", "client_id": "c", "status": "success",
                "result": "{}", "timestamp": "t"}
        common.write_form(data)
        assert new_ch.write_result.called

    def test_no_switch_applied_when_deferred_switch_absent(self, monkeypatch):
        """dispatch() for non-switch commands must not produce _deferred_switch."""
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        from client import dispatch
        from channel.sheets import SheetsChannel

        common.set_channel(None)  # force lazy init
        result = dispatch(_task("echo", {"msg": "hi"}))
        assert "_deferred_switch" not in result
        # Channel should still be sheets after an echo command
        assert isinstance(common.get_channel(), SheetsChannel)

    def test_error_result_has_no_deferred_switch(self, monkeypatch):
        """Invalid channel name → error result, no _deferred_switch."""
        monkeypatch.setenv("CHANNEL", "sheets")
        from client import dispatch
        result = dispatch(_task("switch_channel", {"channel": "bad_channel"}))
        assert "_deferred_switch" not in result
        result_data = json.loads(result["result"])
        assert "error" in result_data


# ---------------------------------------------------------------------------
# Server: _apply_channel_switch
# ---------------------------------------------------------------------------

class TestServerApplyChannelSwitch:
    def test_switches_to_firebase(self):
        import common
        from server import _apply_channel_switch
        from channel.firebase import FirebaseChannel
        _apply_channel_switch("firebase")
        assert isinstance(common.get_channel(), FirebaseChannel)
        assert os.environ["CHANNEL"] == "firebase"

    def test_switches_to_sheets(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "firebase")
        import common
        from channel.firebase import FirebaseChannel
        common.set_channel(FirebaseChannel())

        from server import _apply_channel_switch
        from channel.sheets import SheetsChannel
        _apply_channel_switch("sheets")
        assert isinstance(common.get_channel(), SheetsChannel)
        assert os.environ["CHANNEL"] == "sheets"

    def test_updates_env_var(self):
        from server import _apply_channel_switch
        _apply_channel_switch("firebase")
        assert os.environ.get("CHANNEL") == "firebase"
        _apply_channel_switch("sheets")
        assert os.environ.get("CHANNEL") == "sheets"


# ---------------------------------------------------------------------------
# Full round-trip: dispatch → deferred switch → server mirrors
# ---------------------------------------------------------------------------

class TestSwitchChannelRoundTrip:
    def test_result_json_contains_switched_to(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        from client import dispatch
        result = dispatch(_task("switch_channel", {"channel": "firebase"}, "round-trip-id"))
        result_data = json.loads(result["result"])
        assert result_data["switched_to"] == "firebase"
        assert result_data["previous"] == "sheets"

    def test_command_id_preserved_through_switch(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        from client import dispatch
        result = dispatch(_task("switch_channel", {"channel": "firebase"}, "keep-this-id"))
        assert result["command_id"] == "keep-this-id"

    def test_status_is_success_on_valid_switch(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        from client import dispatch
        result = dispatch(_task("switch_channel", {"channel": "firebase"}))
        assert result["status"] == "success"

    def test_server_mirrors_switch_via_apply(self, monkeypatch):
        """Simulate server detecting switch_channel result and mirroring."""
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        from server import _apply_channel_switch
        from channel.firebase import FirebaseChannel

        # Server reads result with switched_to = firebase
        result_row = {"result": json.dumps({"switched_to": "firebase", "previous": "sheets"})}
        result_data = json.loads(result_row["result"])
        new_ch = result_data.get("switched_to", "")
        assert new_ch in ("sheets", "firebase")
        _apply_channel_switch(new_ch)

        assert isinstance(common.get_channel(), FirebaseChannel)
        assert os.environ["CHANNEL"] == "firebase"

    def test_switch_back_to_sheets(self, monkeypatch):
        """Can switch from firebase back to sheets."""
        monkeypatch.setenv("CHANNEL", "firebase")
        import common
        from channel.firebase import FirebaseChannel
        common.set_channel(FirebaseChannel())

        from client import dispatch, _apply_channel_switch
        from channel.sheets import SheetsChannel

        result = dispatch(_task("switch_channel", {"channel": "sheets"}))
        deferred = result.pop("_deferred_switch", None)
        assert deferred == "sheets"
        _apply_channel_switch(deferred)
        assert isinstance(common.get_channel(), SheetsChannel)
        assert os.environ["CHANNEL"] == "sheets"

    def test_switch_channel_in_handlers_registry(self):
        """switch_channel must be registered in the HANDLERS dict."""
        from client import HANDLERS
        assert "switch_channel" in HANDLERS

    def test_deferred_switch_not_in_fragments(self, monkeypatch):
        """_deferred_switch must not leak into fragment data."""
        monkeypatch.setenv("CHANNEL", "sheets")
        monkeypatch.setenv("FRAGMENT_METHOD", "passthrough")
        from client import dispatch
        result = dispatch(_task("switch_channel", {"channel": "firebase"}))
        # No fragments for a tiny switch_channel result
        assert "_fragments" not in result
        # But _deferred_switch must still be present at top level
        assert "_deferred_switch" in result
