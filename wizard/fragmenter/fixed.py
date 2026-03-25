from wizard.fragmenter.base import WizardFragmenter
from wizard import core


class FixedWizard(WizardFragmenter):
    @property
    def name(self):
        return "fixed"

    def setup(self):
        core.info("Results split into fixed-size chunks, one chunk sent per poll cycle.")
        core.info("Smaller chunks = smaller per-cycle footprint, more cycles to reassemble.")
        core.info("2000 chars leaves safe headroom for Fernet overhead within the ~4000 char Forms limit.")
        core.info()

        def _validate(v):
            try:
                n = int(v)
            except ValueError:
                return "Must be a whole number"
            if n < 100:
                return "Minimum is 100"
            if n > 4000:
                return "Maximum is 4000 (Google Forms field size limit)"

        chunk_size = core.ask("Chunk size (characters)", default="2000", validator=_validate)
        return {"FRAGMENT_METHOD": "fixed", "FRAGMENT_CHUNK_SIZE": chunk_size}
