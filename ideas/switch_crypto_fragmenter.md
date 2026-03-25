# Idea: switch_encryption and switch_fragmenter Commands

## Context

`switch_channel` (planned) allows pivoting to a different C2 channel mid-operation.
The same pattern applies to encryption and fragmentation — both are pluggable and
swappable at runtime without restarting the client.

---

## switch_encryption

Change the active encryption method on the client without a restart.

**Use cases:**
- Upgrade from `plaintext` (debug) to `fernet` (ops) after confirming connectivity
- Rotate to a new key mid-operation if the current key is suspected compromised
- Downgrade to `plaintext` temporarily to debug a decryption mismatch

**Payload:**
```json
{
  "method": "fernet",
  "key": "<base64-fernet-key>"
}
```

**Implementation notes:**
- Client updates `_client_config` with new method and key
- Calls `common.set_encryptor(method, key)` — same path as initial setup
- All subsequent reads/writes use the new encryptor immediately
- The server operator must switch their own side manually (re-run with new `.env`)
  or via a parallel `switch_encryption` sent to themselves — this is a coordination
  problem, not a technical one
- Key material travels over the current channel — if the channel is unencrypted,
  send the key switch over an already-encrypted session or accept the exposure

---

## switch_fragmenter

Change the active fragmentation method on the client without a restart.

**Use cases:**
- Enable fixed-size fragmentation after a large result hits the Google Forms 4000-char limit
- Disable fragmentation if it's causing reassembly issues
- Adjust chunk size dynamically based on observed payload sizes

**Payload:**
```json
{
  "method": "fixed",
  "chunk_size": 1500
}
```

**Implementation notes:**
- Client updates active fragmenter via `common.set_fragmenter(method, chunk_size)`
- In-flight fragment queues should be flushed or drained before switching —
  a mid-stream fragmenter change will corrupt reassembly on the server side
- Safe sequence: wait for current send queue to empty, then switch

---

## Relationship to switch_channel

All three switch commands follow the same pattern:
1. Server sends a switch command with new config
2. Client applies the new config in-memory
3. Client ACKs on the new configuration
4. Bootstrap config is now dead — only the new config matters

The order of operations for a full pivot:
```
switch_encryption  →  switch_fragmenter  →  switch_channel
```

Encrypt first so the channel switch itself travels encrypted. Switch fragmentation
before the channel so the new channel inherits the correct fragmentation settings.

---

## Implementation Order

1. `switch_encryption` — lowest risk, no channel coordination needed
2. `switch_fragmenter` — requires queue drain logic, slightly more complex
3. `switch_channel` — depends on Firebase backend being implemented first
