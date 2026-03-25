from abc import ABC, abstractmethod


class Channel(ABC):
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

    def poll_interval(self) -> float:
        """Max seconds to wait between server-side result polls.
        Override in channel implementations to reflect actual client timing.
        Default is conservative (30s) — subclasses should tighten this.
        """
        return 30.0
