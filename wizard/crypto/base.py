from abc import ABC, abstractmethod


class WizardCrypto(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier."""

    @abstractmethod
    def setup(self) -> dict:
        """Collect crypto config. Returns dict of env vars."""
