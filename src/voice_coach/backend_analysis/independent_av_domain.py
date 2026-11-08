"""AI-owned audiovisual input; UUIDs are never Backend database identifiers."""
from dataclasses import dataclass

from voice_coach.direct_analysis.domain import DirectInput


@dataclass(frozen=True)
class IndependentAudiovisualInput(DirectInput):
    visual_input: object = None
    media_preparation: dict | None = None

    @property
    def is_audiovisual(self):
        return self.visual_input is not None


def is_local_input(request):
    return type(request) in (DirectInput, IndependentAudiovisualInput)
