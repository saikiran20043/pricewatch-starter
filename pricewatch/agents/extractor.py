"""Extractor agent: one adapter per storefront turns a fetched page into an Observation.

Adapters receive the raw HTML (already fetched by the Client) and the URL. They must not do
their own networking except through the Client they are given, so throttling and cookies
stay in one place.

Adapters are registered by name; `stores.yaml` maps each store to an adapter.
"""
from __future__ import annotations

import json
import hashlib
import re
import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..http import Client
from ..models import Observation
from .normalizer import parse_money, unit_price

ADAPTERS: dict[str, Callable[[Client, str, str, str], Observation]] = {}


def adapter(name: str):
    def deco(fn):
        ADAPTERS[name] = fn
        return fn
    return deco


def _pid_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


# --------------------------------------------------------------------------- level 1
@adapter("corner")
def extract_corner(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    ld = soup.find("script", type="application/ld+json")
    data = json.loads(ld.string) if ld and ld.string else {}
    offer = data.get("offers", {})
    price_cents, currency = parse_money(str(offer.get("price", "")), offer.get("priceCurrency"))
    was = soup.find("s")
    compare, _ = parse_money(was.get_text(), currency) if was else (None, None)
    return Observation(
        store=store, product_id=data.get("sku") or _pid_from_url(url), url=url,
        name=data.get("name") or soup.h1.get_text(strip=True),
        price_cents=price_cents, currency=currency or "USD", compare_at_cents=compare,
        availability="in_stock" if "InStock" in str(offer.get("availability", "")) else "out_of_stock",
    )


# --------------------------------------------------------------------------- level 2
@adapter("maple")
def extract_maple(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    name = soup.select_one(".product__title").get_text(strip=True)
    # Prefer the sale/current price; fall back to the existing price selector.
    price_el = soup.select_one(".price--sale") or soup.select_one(".price .price")
    price_cents, currency = parse_money(price_el.get_text(" ", strip=True), "EUR")
    compare_el = soup.select_one(".price--compare")
    compare, _ = parse_money(compare_el.get_text(" ", strip=True), currency) if compare_el else (None, None)
    avail = "out_of_stock" if "Sold out" in soup.get_text() else "in_stock"
    return Observation(
        store=store, product_id=_pid_from_url(url), url=url, name=name,
        price_cents=price_cents, currency=currency or "EUR", compare_at_cents=compare, availability=avail,
    )


# --------------------------------------------------------------------------- level 3
def _zon_money(text: str) -> tuple[int | None, str]:
    match = re.search(r"([$€£])\s*([0-9][0-9,]*)(?:[.,](\d{2})|\s+(\d{2}))?", text)
    if not match:
        return None, "USD"
    currency = {"$": "USD", "€": "EUR", "£": "GBP"}[match.group(1)]
    whole = int(match.group(2).replace(",", ""))
    fraction = int(match.group(3) or match.group(4) or 0)
    return whole * 100 + fraction, currency


@adapter("zon")
def extract_zon(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    if "Are you a human" in html:
        return Observation(store=store, product_id=_pid_from_url(url), url=url, name="", price_cents=None,
                           currency="USD", notes=["robot check page"])
    title = soup.find("h1")
    name = title.get_text(" ", strip=True) if title else ""
    pack_match = re.search(r"\bPack of\s+(\d+)\b", name, flags=re.IGNORECASE)
    pack = int(pack_match.group(1)) if pack_match else 1
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    primary_text = re.split(r"\bOther sellers on Zon\b", page_text, maxsplit=1)[0]
    price_cents: int | None = None
    currency = "USD"
    if not re.search(r"See price in cart", primary_text, flags=re.IGNORECASE):
        if pack_match:
            pack_text = re.search(
                r"\bPack of\s+\d+\s*:\s*(.*?)(?=\bList Price\b|\bIn Stock\b|\bOut of Stock\b|$)",
                primary_text,
                flags=re.IGNORECASE,
            )
            if pack_text:
                price_cents, currency = _zon_money(pack_text.group(1))
        if price_cents is None:
            main_text = re.split(r"\bList Price\b", primary_text, maxsplit=1)[0]
            price_cents, currency = _zon_money(main_text)
    avail = "out_of_stock" if re.search(r"\b(?:Out of Stock|Currently unavailable)\b", primary_text, re.IGNORECASE) else "in_stock"
    return Observation(
        store=store, product_id=_pid_from_url(url), url=url, name=re.sub(r"\s*\(Pack of \d+\)", "", name, flags=re.IGNORECASE),
        price_cents=price_cents, currency=currency, availability=avail, pack_size=pack,
        unit_price_cents=unit_price(price_cents, pack),
        notes=[] if price_cents is not None else ["no price found"],
    )


# --------------------------------------------------------------------------- levels 4-5
@adapter("shield")
def extract_shield(client: Client, store: str, url: str, html: str) -> Observation:
    match = re.search(r"window\.__STATE__\s*=\s*(\{.*?\})\s*;", html, flags=re.DOTALL)
    try:
        data = json.loads(match.group(1)) if match else {}
    except json.JSONDecodeError:
        data = {}
    product = data.get("product", {}) if isinstance(data, dict) else {}
    offer = product.get("offer", {}) if isinstance(product, dict) else {}
    amount = offer.get("amount")
    price_cents = int(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None
    currency = offer.get("currency") if isinstance(offer.get("currency"), str) else "GBP"
    stock = offer.get("stock")
    availability = "in_stock" if stock == "IN_STOCK" else "out_of_stock" if stock == "OUT_OF_STOCK" else "unknown"
    compare_at = offer.get("was")
    compare_at_cents = int(compare_at) if isinstance(compare_at, (int, float)) and not isinstance(compare_at, bool) else None
    return Observation(
        store=store, product_id=product.get("id") or _pid_from_url(url), url=url,
        name=product.get("title") if isinstance(product.get("title"), str) else "",
        price_cents=price_cents, currency=currency, compare_at_cents=compare_at_cents,
        availability=availability, notes=[] if product else ["no product state found"],
    )


def _flux_signature_inputs(bundle: str) -> tuple[str, str, str] | None:
    join = re.search(
        r"(?P<parts>[A-Za-z_$][\w$]*)\s*\.\s*join\(\s*(['\"])\2\s*\)\s*\+\s*(?P<separator>[A-Za-z_$][\w$]*)\s*\+\s*[A-Za-z_$][\w$]*",
        bundle,
    )
    if not join:
        return None
    parts_name, separator_name = join.group("parts"), join.group("separator")
    declaration = re.search(
        rf"(?:var|let|const)\s+{re.escape(parts_name)}\s*=\s*\[(?P<values>.*?)\]",
        bundle,
        flags=re.DOTALL,
    )
    if not declaration:
        return None
    try:
        literals = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', declaration.group("values"))
        parts = [ast.literal_eval(value) for value in literals]
    except (SyntaxError, ValueError):
        return None
    if not parts or not all(isinstance(part, str) for part in parts):
        return None
    separator = re.search(
        rf"(?:var|let|const)\s+{re.escape(separator_name)}\s*=\s*String\.fromCharCode\(\s*(\d+)\s*\)",
        bundle,
    )
    if separator:
        separator_value = chr(int(separator.group(1)))
    else:
        literal = re.search(
            rf"(?:var|let|const)\s+{re.escape(separator_name)}\s*=\s*(['\"])(.*?)\1",
            bundle,
        )
        if not literal:
            return None
        separator_value = literal.group(2)
    endpoint = re.search(r"fetch\(\s*(['\"])(.*?)\1", bundle)
    if not endpoint:
        return None
    return "".join(parts), separator_value, endpoint.group(2)


@adapter("flux")
def extract_flux(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    product_id = (soup.select_one("[data-sku]") or {}).get("data-sku") or _pid_from_url(url)
    title_el = soup.find("h1")
    name = title_el.get_text(" ", strip=True) if title_el else ""
    build_el = soup.select_one('meta[name="flux-build"]')
    script_el = soup.find("script", src=True)
    if not build_el or not script_el or not product_id:
        return Observation(store=store, product_id=product_id, url=url, name=name, price_cents=None,
                           currency="USD", notes=["flux metadata unavailable"])
    bundle_url = urljoin(url, script_el["src"])
    bundle = client.get(bundle_url).text
    signature_inputs = _flux_signature_inputs(bundle)
    if not signature_inputs:
        return Observation(store=store, product_id=product_id, url=url, name=name, price_cents=None,
                           currency="USD", notes=["flux API metadata unavailable"])
    secret, separator, endpoint = signature_inputs
    signature = hashlib.sha256(f"{secret}{separator}{product_id}".encode()).hexdigest()[:32]
    query = "query($sku:String!){product(sku:$sku){sku title offer{amount unit currency stock stale}}}"
    response = client.post(
        urljoin(url, endpoint),
        json={"query": query, "variables": {"sku": product_id}},
        headers={"x-flux-sig": signature, "x-flux-build": build_el["content"]},
    )
    try:
        payload = response.json()
        product = payload["data"]["product"]
        offer = product["offer"]
    except (ValueError, KeyError, TypeError):
        return Observation(store=store, product_id=product_id, url=url, name=name, price_cents=None,
                           currency="USD", notes=[f"Flux API HTTP {response.status_code}"])
    amount = offer.get("amount")
    try:
        price_cents = int(amount) if offer.get("unit") == "minor" else int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        price_cents = None
    stock = offer.get("stock")
    availability = "in_stock" if stock == "IN_STOCK" else "out_of_stock" if stock == "OUT_OF_STOCK" else "unknown"
    notes = ["stale price"] if offer.get("stale") else []
    return Observation(
        store=store, product_id=product_id, url=url, name=product.get("title") or name,
        price_cents=price_cents, currency=offer.get("currency") or "USD", availability=availability, notes=notes,
    )


def extract(client: Client, store: str, adapter_name: str, url: str, html: str) -> Observation:
    fn = ADAPTERS.get(adapter_name)
    if fn is None:
        raise KeyError(f"no adapter named {adapter_name!r}")
    return fn(client, store, url, html)
