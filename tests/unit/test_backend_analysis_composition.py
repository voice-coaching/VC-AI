from __future__ import annotations

import pytest

from voice_coach.backend_analysis.adapters.redis_stream import WorkerConfigurationError
from voice_coach.backend_analysis.composition import load_worker_from_environment
from voice_coach.backend_analysis.production import create_worker


def test_unconfigured_factory_refuses_startup_before_stream_consumption() -> None:
    with pytest.raises(WorkerConfigurationError) as error:
        load_worker_from_environment({})

    assert error.value.code == "analysis_worker_factory_unconfigured"


def test_builtin_production_factory_fails_closed_when_required_settings_are_missing() -> None:
    with pytest.raises(ValueError) as error:
        create_worker({})

    assert str(error.value) == "analysis_worker_production_configuration_invalid"
