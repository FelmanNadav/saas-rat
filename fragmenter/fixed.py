import os

from fragmenter.base import Fragmenter


class FixedFragmenter(Fragmenter):
    """Splits data into fixed-size chunks.

    FRAGMENT_CHUNK_SIZE controls the max chars per chunk (pre-encryption).
    Default 2000 leaves headroom for Fernet base64 overhead within the
    ~4000 char Google Forms field limit.
    """

    def __init__(self):
        self.chunk_size = int(os.environ.get("FRAGMENT_CHUNK_SIZE", "2000"))

    def fragment(self, data: str) -> list:
        if len(data) <= self.chunk_size:
            return [data]
        return [data[i:i + self.chunk_size] for i in range(0, len(data), self.chunk_size)]
