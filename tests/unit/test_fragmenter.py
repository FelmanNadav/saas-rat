import math
import pytest

from fragmenter.passthrough import PassthroughFragmenter
from fragmenter.fixed import FixedFragmenter
from common import _reassemble_fragments, build_outbox_fragments, build_inbox_fragments


# ---------------------------------------------------------------------------
# PassthroughFragmenter
# ---------------------------------------------------------------------------

class TestPassthroughFragmenter:
    def test_always_single_chunk(self):
        f = PassthroughFragmenter()
        result = f.fragment("hello world")
        assert result == ["hello world"]

    def test_empty_string(self):
        f = PassthroughFragmenter()
        assert f.fragment("") == [""]

    def test_large_string_still_single(self):
        f = PassthroughFragmenter()
        data = "x" * 10_000
        assert f.fragment(data) == [data]


# ---------------------------------------------------------------------------
# FixedFragmenter
# ---------------------------------------------------------------------------

class TestFixedFragmenter:
    @pytest.fixture
    def frag(self, monkeypatch):
        monkeypatch.setenv("FRAGMENT_CHUNK_SIZE", "10")
        return FixedFragmenter()

    def test_under_threshold_no_split(self, frag):
        assert frag.fragment("hello") == ["hello"]

    def test_exact_threshold_no_split(self, frag):
        assert frag.fragment("1234567890") == ["1234567890"]

    def test_over_threshold_splits(self, frag):
        chunks = frag.fragment("12345678901")
        assert chunks == ["1234567890", "1"]

    def test_reassembly_recovers_original(self, frag):
        data = "hello world this is a long string for testing"
        assert "".join(frag.fragment(data)) == data

    def test_empty_string(self, frag):
        assert frag.fragment("") == [""]

    def test_chunk_count(self, frag):
        data = "x" * 35
        assert len(frag.fragment(data)) == math.ceil(35 / 10)


# ---------------------------------------------------------------------------
# _reassemble_fragments
# ---------------------------------------------------------------------------

class TestReassembleFragments:
    def test_normal_rows_pass_through(self):
        rows = [{"command_id": "abc", "status": "success", "result": "ok"}]
        assert _reassemble_fragments(rows, "result", "success") == rows

    def test_complete_set_in_order(self):
        rows = [
            {"command_id": "abc", "client_id": "w1", "status": "frag:0:2", "result": "hello ", "timestamp": "t"},
            {"command_id": "abc", "client_id": "w1", "status": "frag:1:2", "result": "world",  "timestamp": "t"},
        ]
        result = _reassemble_fragments(rows, "result", "success")
        assert len(result) == 1
        assert result[0]["result"] == "hello world"
        assert result[0]["status"] == "success"
        assert result[0]["command_id"] == "abc"

    def test_complete_set_out_of_order(self):
        rows = [
            {"command_id": "abc", "status": "frag:2:3", "result": "world!", "timestamp": "t"},
            {"command_id": "abc", "status": "frag:0:3", "result": "hello ", "timestamp": "t"},
            {"command_id": "abc", "status": "frag:1:3", "result": "beautiful ", "timestamp": "t"},
        ]
        result = _reassemble_fragments(rows, "result", "success")
        assert len(result) == 1
        assert result[0]["result"] == "hello beautiful world!"

    def test_incomplete_set_dropped(self):
        rows = [
            {"command_id": "abc", "status": "frag:0:3", "result": "chunk0", "timestamp": "t"},
            {"command_id": "abc", "status": "frag:1:3", "result": "chunk1", "timestamp": "t"},
            # frag:2:3 missing
        ]
        assert _reassemble_fragments(rows, "result", "success") == []

    def test_two_interleaved_message_ids(self):
        rows = [
            {"command_id": "aaa", "status": "frag:0:2", "result": "a0", "timestamp": "t"},
            {"command_id": "bbb", "status": "frag:0:2", "result": "b0", "timestamp": "t"},
            {"command_id": "aaa", "status": "frag:1:2", "result": "a1", "timestamp": "t"},
            {"command_id": "bbb", "status": "frag:1:2", "result": "b1", "timestamp": "t"},
        ]
        result = _reassemble_fragments(rows, "result", "success")
        assert len(result) == 2
        by_id = {r["command_id"]: r["result"] for r in result}
        assert by_id["aaa"] == "a0a1"
        assert by_id["bbb"] == "b0b1"

    def test_malformed_frag_status_passes_through(self):
        row = {"command_id": "abc", "status": "frag:0", "result": "data"}  # missing total
        result = _reassemble_fragments([row], "result", "success")
        assert result == [row]

    def test_mixed_normal_and_complete_fragments(self):
        rows = [
            {"command_id": "normal",   "status": "success",  "result": "ok"},
            {"command_id": "frag_msg", "status": "frag:0:2", "result": "part0", "timestamp": "t"},
            {"command_id": "frag_msg", "status": "frag:1:2", "result": "part1", "timestamp": "t"},
        ]
        result = _reassemble_fragments(rows, "result", "success")
        assert len(result) == 2
        by_id = {r["command_id"]: r["result"] for r in result}
        assert by_id["normal"] == "ok"
        assert by_id["frag_msg"] == "part0part1"

    def test_done_status_applied_to_reassembled_row(self):
        rows = [
            {"command_id": "abc", "status": "frag:0:2", "payload": "he", "timestamp": "t"},
            {"command_id": "abc", "status": "frag:1:2", "payload": "llo", "timestamp": "t"},
        ]
        result = _reassemble_fragments(rows, "payload", "pending")
        assert result[0]["status"] == "pending"
        assert result[0]["payload"] == "hello"


# ---------------------------------------------------------------------------
# build_outbox_fragments / build_inbox_fragments
# ---------------------------------------------------------------------------

class TestBuildFragments:
    def test_outbox_status_format(self):
        data = {"command_id": "abc", "client_id": "w1", "status": "success", "result": "x", "timestamp": "t"}
        frags = build_outbox_fragments(data, ["a", "b", "c"])
        assert [f["status"] for f in frags] == ["frag:0:3", "frag:1:3", "frag:2:3"]

    def test_outbox_metadata_preserved(self):
        data = {"command_id": "abc", "client_id": "w1", "status": "success", "result": "x", "timestamp": "t"}
        frags = build_outbox_fragments(data, ["chunk0", "chunk1"])
        assert all(f["command_id"] == "abc" for f in frags)
        assert all(f["client_id"] == "w1" for f in frags)
        assert frags[0]["result"] == "chunk0"
        assert frags[1]["result"] == "chunk1"

    def test_inbox_preserves_real_command(self):
        data = {"command_id": "abc", "command": "shell", "payload": "x",
                "target": "", "status": "pending", "created_at": "t"}
        frags = build_inbox_fragments(data, ["part0", "part1"])
        assert all(f["command"] == "shell" for f in frags)

    def test_inbox_status_format(self):
        data = {"command_id": "abc", "command": "shell", "payload": "x",
                "target": "", "status": "pending", "created_at": "t"}
        frags = build_inbox_fragments(data, ["a", "b"])
        assert frags[0]["status"] == "frag:0:2"
        assert frags[1]["status"] == "frag:1:2"

    def test_inbox_payload_chunks(self):
        data = {"command_id": "abc", "command": "echo", "payload": "x",
                "target": "", "status": "pending", "created_at": "t"}
        frags = build_inbox_fragments(data, ["hello", "world"])
        assert frags[0]["payload"] == "hello"
        assert frags[1]["payload"] == "world"
