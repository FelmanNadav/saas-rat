# Example Prompts & Results

Each scenario follows the full assignment flow:

```
Operator input (natural language)
    → LLM translation to command
    → Client execution
    → LLM interpretation of result
```

The server runs in `mode confirm` (default) — the AI proposes a command and the operator approves before dispatch.

---

## Scenario 1 — System Reconnaissance

**Goal:** Establish initial footprint. Understand the target machine before doing anything else.

---

**Prompt 1.1 — Identity**

```
> who am I running as and what machine is this
```

<!-- SCREENSHOT: AI console showing the prompt, the proposed command, and the confirmation request -->

<!-- SCREENSHOT: Result arriving — AI interpreted summary showing username, hostname, OS -->

---

**Prompt 1.2 — System Profile**

```
> give me a full system profile — OS, architecture, uptime, and how much RAM is available
```

<!-- SCREENSHOT: AI console showing the translated shell command (uname, free, uptime or equivalent) -->

<!-- SCREENSHOT: Interpreted result — clean summary of system specs -->

---

**Prompt 1.3 — Network Position**

```
> what are the network interfaces and their IP addresses
```

<!-- SCREENSHOT: AI console — translated command (ip addr or ifconfig) and confirmation -->

<!-- SCREENSHOT: Interpreted result — list of interfaces and IPs in plain English -->

---

## Scenario 2 — File System Operations

**Goal:** Navigate the file system, create artifacts, verify they exist, then clean up.

---

**Prompt 2.1 — Explore**

```
> list the contents of the home directory
```

<!-- SCREENSHOT: AI console — translated command and confirmation -->

<!-- SCREENSHOT: Interpreted result — directory listing summarized -->

---

**Prompt 2.2 — Create**

```
> create a file called hello.txt in the home directory containing the text "interview test"
```

<!-- SCREENSHOT: AI console — translated command (echo or tee) and confirmation -->

<!-- SCREENSHOT: Interpreted result — confirmation that file was created -->

---

**Prompt 2.3 — Verify**

```
> show me the contents of hello.txt
```

<!-- SCREENSHOT: AI console — cat command and result arriving -->

<!-- SCREENSHOT: Interpreted result — AI confirms content matches what was written -->

---

**Prompt 2.4 — Clean Up**

```
> remove hello.txt
```

<!-- SCREENSHOT: AI console — destructive command warning triggered, confirmation required regardless of mode -->

<!-- SCREENSHOT: Interpreted result — file removed, directory listing shows it's gone -->

---

## Scenario 3 — Process & Environment Recon

**Goal:** Understand what's running on the target, what environment variables are set, and what processes are active.

---

**Prompt 3.1 — Running Processes**

```
> what processes are currently running on this machine
```

<!-- SCREENSHOT: AI console — translated command (ps aux or equivalent) -->

<!-- SCREENSHOT: Interpreted result — AI summarizes notable processes, filters noise -->

---

**Prompt 3.2 — Environment**

```
> show me the environment variables set for this process
```

<!-- SCREENSHOT: AI console — env command and result -->

<!-- SCREENSHOT: Interpreted result — AI highlights notable variables (PATH, HOME, SHELL, etc.) -->

---

**Prompt 3.3 — Specific Process Check**

```
> is there a web server running on this machine? check common ports
```

<!-- SCREENSHOT: AI console — translated command (ss -tlnp or netstat) and confirmation -->

<!-- SCREENSHOT: Interpreted result — AI reports open ports and associated processes in plain English -->

---

## Scenario 4 — Dynamic Client Configuration

**Goal:** Demonstrate runtime control over the client — adjusting behavior without restarting.

---

**Prompt 4.1 — Slow Down the Client**

```
> slow the client down, I don't want it polling too aggressively
```

<!-- SCREENSHOT: AI console — translated config command with cycle_interval_sec adjustment -->

<!-- SCREENSHOT: Interpreted result — confirmation that config was applied -->

---

**Prompt 4.2 — Rename the Client**

```
> rename this client to "target-01"
```

<!-- SCREENSHOT: AI console — config command setting client_id -->

<!-- SCREENSHOT: Next heartbeat arriving — client_id field now shows "target-01" in the sheet -->

---

## The Google Sheet

The sheet is the underlying transport — everything above flows through it.

<!-- SCREENSHOT: The Google Sheet inbox tab — showing command rows with encrypted/obfuscated column values -->

<!-- SCREENSHOT: The Google Sheet outbox tab — showing result rows arriving as the scenarios run -->

> With Fernet encryption and column obfuscation enabled, the sheet shows only random column names and encrypted field values. An observer with access to the sheet sees no readable content.

<!-- SCREENSHOT: Sheet with encryption OFF (debug/demo mode) — readable commands and results for comparison -->
