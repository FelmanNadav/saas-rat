from wizard.crypto.base import WizardCrypto
from wizard import core


class FernetWizard(WizardCrypto):
    @property
    def name(self):
        return "fernet"

    def setup(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        core.info("Generated Fernet key:")
        core.info(f"  {key}")
        core.info()
        core.info("This key must be identical on every machine running client or server.")
        core.info("It is written to .env — do not commit .env to version control.")
        return {"ENCRYPTION_METHOD": "fernet", "ENCRYPTION_KEY": key}
