# AI Operator Console

The AI console is an interactive REPL that accepts natural language. GPT-4o translates your input into structured commands, dispatches them to the client, and interprets results in plain English.

```bash
python server.py ai
```

Requires `OPENAI_API_KEY` in `.env`.

---

## Disclaimer

**GPT-4o is non-deterministic.** The same prompt may produce different commands across sessions. The model can misinterpret ambiguous instructions, hallucinate command syntax, or choose a more aggressive action than intended.

**Always use `mode confirm` when in doubt** — it shows the exact command that will be sent and requires explicit approval before dispatch. This is the default mode.

**Destructive commands are always intercepted.** Commands matching patterns like `rm -rf`, `kill -9`, `shutdown`, `dd`, `mkfs` are flagged with a warning and require explicit confirmation regardless of mode. This is a safety net, not a guarantee — always review what the AI proposes.

---

## Send Mode

Controls whether commands require confirmation before being dispatched.

| Command | Effect |
|---------|--------|
| `mode confirm` | Preview and confirm before each send **(default)** |
| `mode auto` | Send commands immediately without confirmation |
| `mode` | Show current mode |

Use `mode auto` only when you trust the AI's interpretation of your intent completely. For destructive or sensitive operations, stay in `mode confirm`.

---

## Output Mode

Controls how results are displayed.

| Command | Effect |
|---------|--------|
| `output interpreted` | GPT-4o summarizes and explains results **(default)** |
| `output raw` | Display raw stdout directly |
| `output` | Show current mode |

---

## Refresh Interval

The server's background poller re-reads the outbox on a configurable interval. By default it starts at 5s and auto-syncs to the client's cycle timing once the first heartbeat arrives.

| Command | Effect |
|---------|--------|
| `refresh <sec>` | Override refresh interval manually — pauses heartbeat sync |
| `refresh auto` | Clear override — sync back to client heartbeat timing |
| `refresh` | Show current interval and whether it is manual or heartbeat-synced |

---

## Shortcuts

| Input | Effect |
|-------|--------|
| `do it` / `yes` / `go` | Execute the last AI-suggested command |
| `raw` / `exact` / `full output` | Print raw stdout of the most recent result |
| `?` / `show` / `results` | Show any arrived-but-unseen results |
| `help` | Print all REPL commands (never sent to AI) |
| `exit` / `quit` | Exit the console |

---

## Background Result Polling

When a command is sent, the server spawns a background thread that watches the outbox for a matching result. If no result arrives within **5 minutes** — because the client is offline, slow, or the command is long-running — the thread stops watching.

The result is **not lost** — it will appear in the sheet when the client eventually responds. Retrieve it manually:

```bash
python server.py collect --id <command_id>
```

---

## Customizing AI Behavior

Edit `system_prompt.txt` — it is loaded fresh at the start of each `server.py ai` session. The system prompt explains the full command protocol to GPT-4o: available commands, payload formats, config keys, timing behavior, and REPL commands.

---

## Tips

- **Start with `mode confirm`** until you understand how the AI interprets your phrasing.
- **Be specific.** "List files in /home" is better than "look around". Ambiguous prompts lead to unpredictable commands.
- **Use `output raw`** when you need exact output — interpreted mode may omit details.
- **If a result doesn't arrive**, check `refresh` — the interval may be out of sync with the client. Run `refresh auto` to re-sync.
- **The AI knows the REPL commands.** You can ask it "what mode am I in" or "how do I change the refresh interval" and it will explain.
