from abc import ABC, abstractmethod


class WizardChannel(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier."""

    @abstractmethod
    def _manual_setup(self, obfuscation: dict) -> dict:
        """Interactive browser-guided setup.

        obfuscation: {"inbox": {logical: random, ...}, "outbox": {logical: random, ...}}
                     Empty dict when obfuscation is disabled.
        Returns dict of env vars.
        """

    def setup(self, obfuscation: dict) -> dict:
        """Entry point. Override to add auto mode detection later."""
        return self._manual_setup(obfuscation)
