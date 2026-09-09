from kivi_memory.generation import synthesize
from kivi_memory.settings import Settings


def test_generation_without_key_is_explicit():
    result = synthesize(Settings(sarvam_api_key=""), "What changed?", [{"id": "src_1", "text": "A plan changed."}])
    assert result.text is None
    assert result.error == "model_not_configured"


def test_malformed_provider_array_response(monkeypatch):
    import httpx

    class MockResponse:
        status_code = 200

        def __init__(self):
            self.headers = {}

        def json(self):
            return ["not", "a", "dict"]

        def raise_for_status(self):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr(httpx, "Client", MockClient)
    settings = Settings(sarvam_api_key="dummy-key")
    result = synthesize(settings, "What changed?", [{"id": "src_1", "text": "A plan changed."}])
    assert result.text is None
    assert result.error == "unsupported_model_output"


def test_deadline_enforced():
    settings = Settings(sarvam_api_key="dummy-key", query_deadline_seconds=-1.0)
    result = synthesize(settings, "What changed?", [{"id": "src_1", "text": "A plan changed."}])
    assert result.text is None
    assert result.error == "provider_timeout"
