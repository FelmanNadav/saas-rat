import pytest
from cryptography.fernet import Fernet

from crypto.plaintext import PlaintextEncryptor
from common import _encrypt_row, _decrypt_row


# ---------------------------------------------------------------------------
# PlaintextEncryptor
# ---------------------------------------------------------------------------

class TestPlaintextEncryptor:
    def test_encrypt_is_identity(self):
        enc = PlaintextEncryptor()
        assert enc.encrypt("hello") == "hello"

    def test_decrypt_is_identity(self):
        enc = PlaintextEncryptor()
        assert enc.decrypt("hello") == "hello"

    def test_roundtrip_special_chars(self):
        enc = PlaintextEncryptor()
        data = 'some data !@#$ {"key": "value"} \n tabs\t'
        assert enc.decrypt(enc.encrypt(data)) == data


# ---------------------------------------------------------------------------
# FernetEncryptor
# ---------------------------------------------------------------------------

class TestFernetEncryptor:
    @pytest.fixture
    def key(self):
        return Fernet.generate_key().decode()

    @pytest.fixture
    def enc(self, monkeypatch, key):
        monkeypatch.setenv("ENCRYPTION_METHOD", "fernet")
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        return FernetEncryptor()

    def test_roundtrip(self, enc):
        data = "sensitive payload 123"
        assert enc.decrypt(enc.encrypt(data)) == data

    def test_encrypt_differs_from_plaintext(self, enc):
        data = "sensitive payload"
        assert enc.encrypt(data) != data

    def test_non_deterministic(self, enc):
        data = "same input"
        assert enc.encrypt(data) != enc.encrypt(data)

    def test_wrong_key_raises_on_decrypt(self, monkeypatch, key):
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        enc = FernetEncryptor()
        ciphertext = enc.encrypt("hello")

        other_key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", other_key)
        enc2 = FernetEncryptor()
        with pytest.raises(Exception):
            enc2.decrypt(ciphertext)

    def test_missing_key_raises_on_init(self, monkeypatch):
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        from crypto.fernet import FernetEncryptor
        with pytest.raises(Exception):
            FernetEncryptor()


# ---------------------------------------------------------------------------
# _encrypt_row / _decrypt_row
# ---------------------------------------------------------------------------

class TestEncryptRow:
    def test_non_empty_values_encrypted(self):
        enc = PlaintextEncryptor()  # identity, so we verify the call is made
        row = {"a": "hello", "b": "world"}
        result = _encrypt_row(row, enc)
        assert result == {"a": "hello", "b": "world"}

    def test_empty_values_skipped(self):
        enc = PlaintextEncryptor()
        row = {"a": "hello", "b": ""}
        result = _encrypt_row(row, enc)
        assert result["b"] == ""

    def test_fernet_encrypt_row_produces_ciphertext(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        enc = FernetEncryptor()
        row = {"command_id": "abc-123", "result": "secret"}
        result = _encrypt_row(row, enc)
        assert result["command_id"] != "abc-123"
        assert result["result"] != "secret"


class TestDecryptRow:
    def test_valid_ciphertext_decrypted(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        enc = FernetEncryptor()
        row = _encrypt_row({"a": "hello", "b": "world"}, enc)
        result = _decrypt_row(row, enc)
        assert result == {"a": "hello", "b": "world"}

    def test_bad_ciphertext_passes_through_silently(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        from crypto.fernet import FernetEncryptor
        enc = FernetEncryptor()
        row = {"a": "not_valid_ciphertext", "b": "also_invalid"}
        result = _decrypt_row(row, enc)
        assert result["a"] == "not_valid_ciphertext"
        assert result["b"] == "also_invalid"

    def test_empty_values_pass_through(self):
        enc = PlaintextEncryptor()
        row = {"a": "", "b": "hello"}
        result = _decrypt_row(row, enc)
        assert result["a"] == ""
        assert result["b"] == "hello"
