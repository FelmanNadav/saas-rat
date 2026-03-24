class Fragmenter:
    """Abstract fragmenter. Splits a string into chunks for multi-cycle delivery."""

    def fragment(self, data: str) -> list:
        """Split data into a list of string chunks. Single-element list = no split."""
        raise NotImplementedError

    def is_fragmented(self, data: str) -> bool:
        return len(self.fragment(data)) > 1
