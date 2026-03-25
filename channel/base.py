from abc import ABC, abstractmethod


class Channel(ABC):
    def __init__(self):
        # Default server refresh interval — how often the server reads the outbox.
        # Distinct from the client cycle interval (how often the client wakes up).
        # See ideas/sync_refresh_interval.md for the full design.
        self._refresh_interval: float = 30.0
        self._manual_override: bool = False

    @abstractmethod
    def read_inbox(self) -> list:
        """Read pending tasks from the inbox."""

    @abstractmethod
    def read_outbox(self) -> list:
        """Read results from the outbox."""

    @abstractmethod
    def write_result(self, data: dict) -> bool:
        """Write a result row to the outbox. Returns True on success."""

    @abstractmethod
    def write_task(self, data: dict) -> bool:
        """Write a task row to the inbox. Returns True on success."""

    @abstractmethod
    def build_outbox_fragments(self, data: dict, chunks: list) -> list:
        """Build outbox fragment rows from pre-fragmented chunks."""

    @abstractmethod
    def build_inbox_fragments(self, data: dict, chunks: list) -> list:
        """Build inbox fragment rows from pre-fragmented chunks."""

    def refresh_interval(self) -> float:
        """Seconds the server waits between outbox reads.
        Re-queried on every server poll cycle so operator overrides take effect immediately.
        """
        return self._refresh_interval

    def set_refresh_interval(self, seconds: float, manual: bool = False) -> None:
        """Update the server refresh interval.

        manual=True  — operator-set via the 'refresh' REPL command; heartbeat
                       values are ignored until clear_refresh_override() is called.
        manual=False — applied only when no manual override is active (used by
                       the heartbeat handler to sync to client cycle timing).
        """
        if manual or not self._manual_override:
            self._refresh_interval = float(seconds)
            if manual:
                self._manual_override = True

    def clear_refresh_override(self) -> None:
        """Remove the manual override — next heartbeat will update the interval."""
        self._manual_override = False

    # ------------------------------------------------------------------
    # Optional cleanup interface
    # Channels that support entry deletion override these.
    # Sheets is append-only so it inherits the no-op defaults.
    # ------------------------------------------------------------------

    @property
    def supports_cleanup(self) -> bool:
        """True if this channel can delete individual inbox/outbox entries."""
        return False

    def delete_task(self, command_id: str) -> bool:
        """Delete an inbox entry by command_id. No-op if not supported."""
        return False

    def delete_result(self, command_id: str) -> bool:
        """Delete an outbox entry by command_id. No-op if not supported."""
        return False
