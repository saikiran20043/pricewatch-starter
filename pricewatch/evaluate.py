"""Evaluation harness for the generic LLM extractor."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable

from . import config
from .agents import extractor
from .agents.llm_extractor import SYSTEM, MalformedLLMResponse, clean_html, observation_from_response
from .http import Client
from .providers import Provider, ProviderError, ProviderTimeout

PRICING_PER_MILLION = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_PRICING = (0.0, 0.0)


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _provider_model(provider: Provider) -> str | None:
    model = getattr(provider, "model", None)
    return str(model) if model else None


def _cache_key(provider: Provider, store: str, url: str, user: str) -> str:
    payload = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": _provider_model(provider),
        "store": store,
        "source_url": url,
        "system": SYSTEM,
        "user": user,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _metric_row() -> dict[str, int]:
    return {"price_exact": 0, "currency": 0, "availability": 0, "pack_size": 0}


def _accuracy(row: dict[str, int], count: int) -> dict[str, float]:
    denominator = max(1, count)
    return {field: row[field] / denominator for field in row}


def _load_verified_issues(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        issues = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return issues if isinstance(issues, list) else []


def _baseline_price_exact(labels: Iterable[dict], snapshots_dir: Path) -> float:
    try:
        stores = config.load_stores()
    except (OSError, KeyError, TypeError):
        stores = {"stores": {}}
    client = Client()
    correct = 0
    total = 0
    for row in labels:
        total += 1
        html = (snapshots_dir / row["file"]).read_text()
        store = row["store"]
        adapter_name = stores.get("stores", {}).get(store, {}).get("adapter", store)
        try:
            baseline = extractor.extract(client, store, adapter_name, row["url"], html)
        except (KeyError, AttributeError, TypeError, ValueError):
            # Unsupported or failing rule-based adapters are incorrect baseline results.
            continue
        if baseline.price_cents == row.get("expected", {}).get("price_cents"):
            correct += 1
    return correct / max(1, total)


def run(provider: Provider, snapshots_dir: str | Path, labels_path: str | Path, out_dir: str | Path,
        verified_label_issues: list[dict] | None = None) -> dict:
    """Run an evaluation; successful raw responses are cached under ``out_dir/.cache``.

    Label issues are preserved only when explicitly supplied or pre-existing.
    """
    snapshots_dir, out_dir = Path(snapshots_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = json.loads(Path(labels_path).read_text())
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    overall_counts = _metric_row()
    store_counts: dict[str, dict[str, int]] = {}
    store_totals: dict[str, int] = {}
    errors = {"timeout": 0, "malformed_output": 0, "provider_error": 0}
    latencies: list[float] = []
    input_tokens = output_tokens = 0
    for row in labels:
        store = row["store"]
        store_counts.setdefault(store, _metric_row())
        store_totals[store] = store_totals.get(store, 0) + 1
        html = (snapshots_dir / row["file"]).read_text()
        user = f"URL: {row['url']}\n\nPAGE TEXT:\n{clean_html(html)}"
        cache_file = cache_dir / f"{_cache_key(provider, store, row['url'], user)}.txt"
        started = None
        raw = None
        input_tokens += _estimate_tokens(user)
        try:
            if cache_file.exists():
                raw = cache_file.read_text()
            else:
                started = time.perf_counter()
                raw = provider.complete(SYSTEM, user, metadata={"source_url": row["url"], "store": store}, timeout=30.0)
                latencies.append((time.perf_counter() - started) * 1000)
            observation = observation_from_response(raw, store, row["url"], strict=True)
            if not cache_file.exists():
                cache_file.write_text(raw)
            output_tokens += _estimate_tokens(raw)
            expected = row.get("expected", {})
            values = {
                "price_exact": observation.price_cents == expected.get("price_cents"),
                "currency": observation.currency == expected.get("currency"),
                "availability": observation.availability == expected.get("availability"),
                "pack_size": observation.pack_size == expected.get("pack_size"),
            }
            for field, matched in values.items():
                overall_counts[field] += int(matched)
                store_counts[store][field] += int(matched)
        except ProviderTimeout:
            errors["timeout"] += 1
        except ProviderError:
            errors["provider_error"] += 1
        except MalformedLLMResponse:
            errors["malformed_output"] += 1

    model = _provider_model(provider)
    input_rate, output_rate = PRICING_PER_MILLION.get(model, DEFAULT_PRICING)
    report = {
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": model,
        "n": len(labels),
        "overall": _accuracy(overall_counts, len(labels)),
        "per_store": {
            store: {"n": store_totals[store], **_accuracy(counts, store_totals[store])}
            for store, counts in store_counts.items()
        },
        "errors": errors,
        "cost": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usd_estimate": round(input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000, 6),
            "basis": "deterministic character-based token estimate; provider usage is not exposed",
        },
        "latency_ms": {"p50": round(_percentile(latencies, 0.50), 2), "p95": round(_percentile(latencies, 0.95), 2)},
        "baseline": {"price_exact": _baseline_price_exact(labels, snapshots_dir)},
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    issues = verified_label_issues if verified_label_issues is not None else _load_verified_issues(out_dir / "label_issues.json")
    (out_dir / "label_issues.json").write_text(json.dumps(issues, indent=2))
    return report
