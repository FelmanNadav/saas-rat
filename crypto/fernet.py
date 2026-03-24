import os
import sys

from cryptography.fernet import Fernet

if __name__ != "__main__":
    from crypto.base import Encryptor
else:
    # Running as a standalone script — base import not needed
    class Encryptor:
        def encrypt(self, plaintext: str) -> str: raise NotImplementedError
        def decrypt(self, ciphertext: str) -> str: raise NotImplementedError


class FernetEncryptor(Encryptor):
    """AES-128-CBC + HMAC-SHA256 via cryptography.Fernet.

    Key must be a valid Fernet key (32 url-safe base64-encoded bytes).
    Load from .env: ENCRYPTION_KEY=<key>

    Generate a new key:
        python crypto/fernet.py
    """

    def __init__(self):
        key = os.environ.get("ENCRYPTION_KEY", "").strip()
        if not key:
            raise ValueError("ENCRYPTION_KEY is not set in environment")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


def generate_key():
    """Print a new random Fernet key. Copy the output into .env as ENCRYPTION_KEY=<key>."""
    print(Fernet.generate_key().decode())


if __name__ == "__main__":
    generate_key()
