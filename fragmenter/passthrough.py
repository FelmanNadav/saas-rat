from fragmenter.base import Fragmenter


class PassthroughFragmenter(Fragmenter):
    """No-op fragmenter — always returns the data as a single chunk."""

    def fragment(self, data: str) -> list:
        return [data]
