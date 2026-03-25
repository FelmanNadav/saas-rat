from wizard.crypto.base import WizardCrypto
from wizard import core


class PlaintextWizard(WizardCrypto):
    @property
    def name(self):
        return "plaintext"

    def setup(self):
        core.info("No encryption — values stored as cleartext in the sheet.")
        core.info("Fine for local testing; not recommended for operational use.")
        return {"ENCRYPTION_METHOD": "plaintext"}
