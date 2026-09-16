import json

from pricewatch.evaluate import _percentile, run
from pricewatch.providers import ProviderError, ProviderTimeout


class MappingProvider:
    name = "stub"
    model = "test-model"

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def complete(self, system, user, *, metadata, timeout=30.0):
        url = metadata["source_url"]
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def _labels(tmp_path, rows):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    labels = []
    for index, row in enumerate(rows):
        filename = f"page-{index}.html"
        (snapshots / filename).write_text("<html><body>product</body></html>")
        labels.append({"id": f"id-{index}", "file": filename, **row})
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels))
    return snapshots, labels_path


def _answer(price=100, currency="USD", availability="in_stock", pack_size=1):
    return json.dumps({
        "name": "Product",
        "price_cents": price,
        "currency": currency,
        "availability": availability,
        "pack_size": pack_size,
        "compare_at_cents": None,
    })


def test_run_reports_all_metrics_per_store_and_schema(tmp_path):
    rows = [
        {"store": "nordkart", "url": "https://example.test/nk-1", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}},
        {"store": "bazaario", "url": "https://example.test/bz-1", "expected": {"price_cents": 200, "currency": "INR", "availability": "out_of_stock", "pack_size": 2}},
    ]
    snapshots, labels = _labels(tmp_path, rows)
    provider = MappingProvider({rows[0]["url"]: _answer(), rows[1]["url"]: _answer(999)})

    report = run(provider, snapshots, labels, tmp_path / "out")

    assert report["n"] == 2
    assert report["overall"] == {"price_exact": 0.5, "currency": 0.5, "availability": 0.5, "pack_size": 0.5}
    assert report["per_store"]["nordkart"]["n"] == 1
    assert report["per_store"]["bazaario"]["price_exact"] == 0.0
    assert set(report) >= {"provider", "model", "n", "overall", "per_store", "errors", "cost", "latency_ms", "baseline"}
    assert json.loads((tmp_path / "out" / "report.json").read_text()) == report
    assert json.loads((tmp_path / "out" / "label_issues.json").read_text()) == []


def test_errors_count_wrong_and_do_not_stop_later_snapshots(tmp_path):
    rows = [
        {"store": "one", "url": "https://example.test/timeout", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}},
        {"store": "one", "url": "https://example.test/malformed", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}},
        {"store": "two", "url": "https://example.test/error", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}},
        {"store": "two", "url": "https://example.test/ok", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}},
    ]
    snapshots, labels = _labels(tmp_path, rows)
    provider = MappingProvider({
        rows[0]["url"]: ProviderTimeout("slow"),
        rows[1]["url"]: "not json",
        rows[2]["url"]: ProviderError("failed"),
        rows[3]["url"]: _answer(),
    })

    report = run(provider, snapshots, labels, tmp_path / "out")

    assert report["n"] == 4
    assert report["errors"] == {"timeout": 1, "malformed_output": 1, "provider_error": 1}
    assert report["overall"] == {"price_exact": 0.25, "currency": 0.25, "availability": 0.25, "pack_size": 0.25}
    assert provider.calls == [row["url"] for row in rows]


def test_cache_prevents_second_provider_call(tmp_path):
    row = {"store": "unknown", "url": "https://example.test/cached", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}}
    snapshots, labels = _labels(tmp_path, [row])
    out = tmp_path / "out"
    first = MappingProvider({row["url"]: _answer()})
    second = MappingProvider({row["url"]: ProviderError("should not call")})

    run(first, snapshots, labels, out)
    report = run(second, snapshots, labels, out)

    assert len(first.calls) == 1
    assert second.calls == []
    assert report["errors"] == {"timeout": 0, "malformed_output": 0, "provider_error": 0}


def test_latency_percentile_and_empty_behavior():
    assert _percentile([10, 20, 30, 40], 0.50) == 25
    assert _percentile([], 0.95) == 0.0


def test_baseline_without_adapter_is_zero(tmp_path):
    row = {"store": "unknown", "url": "https://example.test/no-adapter", "expected": {"price_cents": 100, "currency": "USD", "availability": "in_stock", "pack_size": 1}}
    snapshots, labels = _labels(tmp_path, [row])

    report = run(MappingProvider({row["url"]: _answer()}), snapshots, labels, tmp_path / "out")

    assert report["baseline"] == {"price_exact": 0.0}
