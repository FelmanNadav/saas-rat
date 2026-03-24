from crypto.base import Encryptor


class PlaintextEncryptor(Encryptor):
    """Pass-through encryptor — no encryption. Default for backward compatibility."""

    def encrypt(self, plaintext: str) -> str:
        return plaintext

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext
