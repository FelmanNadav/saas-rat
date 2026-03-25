from wizard.fragmenter.base import WizardFragmenter
from wizard import core


class PassthroughWizard(WizardFragmenter):
    @property
    def name(self):
        return "passthrough"

    def setup(self):
        core.info("No fragmentation — each result sent in a single write.")
        return {"FRAGMENT_METHOD": "passthrough"}
