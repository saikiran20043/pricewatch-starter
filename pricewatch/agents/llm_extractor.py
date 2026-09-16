"""LLM extractor: the fallback for stores we have no adapter for.

Given raw HTML and its URL, ask a model for the structured offer.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..models import Observation
from ..providers import Provider

SYSTEM = """You extract e-commerce offer data from a product page and answer ONLY with a JSON object:
{"name": str|null, "price_cents": int|null, "currency": "ISO-4217"|null, "availability": "in_stock"|"out_of_stock"|"unknown",
 "pack_size": int, "compare_at_cents": int|null}
price_cents is the price of the pack actually being sold, in minor units. If there is no price, use null."""


class MalformedLLMResponse(ValueError):
    """Raised when a provider response is not a JSON object."""


def clean_html(html: str, limit: int = 12000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))
    return text[:limit]


def _json_object(raw: str) -> dict | None:
    text = raw.strip() if isinstance(raw, str) else ""
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _pack_size(value: object) -> int:
    pack_size = _optional_int(value)
    return pack_size if pack_size is not None and pack_size > 0 else 1


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def observation_from_response(raw: str, store: str, url: str, *, strict: bool = False) -> Observation:
    data = _json_object(raw)
    if data is None:
        if strict:
            raise MalformedLLMResponse("provider response was not a JSON object")
        data = {}
    availability = data.get("availability")
    if availability not in {"in_stock", "out_of_stock", "unknown"}:
        availability = "unknown"
    return Observation(
        store=store, product_id=url.rstrip("/").split("/")[-1], url=url, name=_text(data.get("name")),
        price_cents=_optional_int(data.get("price_cents")), currency=_text(data.get("currency")),
        compare_at_cents=_optional_int(data.get("compare_at_cents")), availability=availability,
        pack_size=_pack_size(data.get("pack_size")), source="llm",
    )


def extract_with_llm(provider: Provider, store: str, url: str, html: str, timeout: float = 30.0) -> Observation:
    user = f"URL: {url}\n\nPAGE TEXT:\n{clean_html(html)}"
    raw = provider.complete(SYSTEM, user, metadata={"source_url": url, "store": store}, timeout=timeout)
    return observation_from_response(raw, store, url)
