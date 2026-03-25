from abc import ABC, abstractmethod


class WizardFragmenter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier."""

    @abstractmethod
    def setup(self) -> dict:
        """Collect fragmenter config. Returns dict of env vars."""
