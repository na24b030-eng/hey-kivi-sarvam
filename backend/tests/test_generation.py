from kivi_memory.generation import synthesize
from kivi_memory.settings import Settings


def test_generation_without_key_is_explicit():
    result = synthesize(Settings(sarvam_api_key=""), "What changed?", [{"id": "src_1", "text": "A plan changed."}])
    assert result.text is None
    assert result.error == "model_not_configured"
