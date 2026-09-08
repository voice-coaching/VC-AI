"""Application services.

The source-conditioned adversarial TTS engine is the default TTS application
adapter. It is imported lazily so contract preflight remains torch-free.
"""

__all__ = ["AdversarialTTSEngine", "ClosedLoopSession", "ClosedLoopTTSService"]


def __getattr__(name: str):
    if name == "AdversarialTTSEngine":
        from voice_coach.application.adversarial_tts import AdversarialTTSEngine

        return AdversarialTTSEngine
    if name in {"ClosedLoopSession", "ClosedLoopTTSService"}:
        from voice_coach.application.closed_loop import (
            ClosedLoopSession,
            ClosedLoopTTSService,
        )

        return {
            "ClosedLoopSession": ClosedLoopSession,
            "ClosedLoopTTSService": ClosedLoopTTSService,
        }[name]
    raise AttributeError(name)
