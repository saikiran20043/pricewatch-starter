from pricewatch.agents.llm_extractor import extract_with_llm
from pricewatch.providers import ProviderError, ProviderTimeout


URL = "https://unknown.example/products/widget-7"
HTML = "<html><head><title>Widget</title></head><body><h1>Widget</h1><p>$12.34</p></body></html>"


class StubProvider:
    name = "stub"

    def __init__(self, response):
        self.response = response
        self.metadata = None

    def complete(self, system, user, *, metadata, timeout=30.0):
        self.metadata = metadata
        return self.response


def test_valid_json_response_preserves_structured_fields_and_metadata():
    provider = StubProvider('{"name":"Widget","price_cents":1234,"currency":"USD","availability":"in_stock","pack_size":2,"compare_at_cents":1500}')

    observation = extract_with_llm(provider, "unknown", URL, HTML)

    assert observation.name == "Widget"
    assert observation.price_cents == 1234
    assert observation.currency == "USD"
    assert observation.availability == "in_stock"
    assert observation.pack_size == 2
    assert observation.compare_at_cents == 1500
    assert observation.source == "llm"
    assert provider.metadata == {"source_url": URL, "store": "unknown"}


def test_markdown_fenced_json_response_is_parsed():
    provider = StubProvider('```json\n{"name":"Widget","price_cents":1234,"currency":"USD"}\n```')

    observation = extract_with_llm(provider, "unknown", URL, HTML)

    assert observation.price_cents == 1234


def test_malformed_json_returns_safe_observation():
    observation = extract_with_llm(StubProvider("not json"), "unknown", URL, HTML)

    assert observation.price_cents is None
    assert observation.name == ""
    assert observation.currency == ""
    assert observation.availability == "unknown"
    assert observation.pack_size == 1


def test_invalid_and_missing_fields_use_safe_defaults():
    provider = StubProvider('{"name":7,"price_cents":"bad","currency":null,"availability":"maybe","pack_size":"bad","compare_at_cents":false}')

    observation = extract_with_llm(provider, "unknown", URL, HTML)

    assert observation.name == ""
    assert observation.price_cents is None
    assert observation.currency == ""
    assert observation.availability == "unknown"
    assert observation.pack_size == 1
    assert observation.compare_at_cents is None


def test_provider_timeout_is_preserved():
    class TimeoutProvider(StubProvider):
        def complete(self, system, user, *, metadata, timeout=30.0):
            raise ProviderTimeout("timed out")

    try:
        extract_with_llm(TimeoutProvider(""), "unknown", URL, HTML)
    except ProviderTimeout as error:
        assert str(error) == "timed out"
    else:
        raise AssertionError("ProviderTimeout was swallowed")


def test_provider_error_is_preserved():
    class ErrorProvider(StubProvider):
        def complete(self, system, user, *, metadata, timeout=30.0):
            raise ProviderError("provider failed")

    try:
        extract_with_llm(ErrorProvider(""), "unknown", URL, HTML)
    except ProviderError as error:
        assert str(error) == "provider failed"
    else:
        raise AssertionError("ProviderError was swallowed")