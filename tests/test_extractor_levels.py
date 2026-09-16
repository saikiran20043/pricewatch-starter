import json

from pricewatch.agents.extractor import extract_flux, extract_shield, extract_zon
from pricewatch.http import Client


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return self.responses[url]

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses[url]


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return json.loads(self.text)


def test_zon_prefers_pack_price_over_list_and_sellers():
    html = """<h1>Trail Shoe (Pack of 12)</h1><div>
    <div>$ 20 29 / count</div><div>Pack of 12: $ 243 48</div>
    <div>List Price: $294.47</div><div>In Stock</div></div>
    <div><h3>Other sellers on Zon</h3><div>$ 260 98</div></div>"""

    observation = extract_zon(Client(), "zon", "http://x/stores/zon/dp/item", html)

    assert observation.price_cents == 24348
    assert observation.pack_size == 12
    assert observation.availability == "in_stock"


def test_zon_ignores_other_seller_prices():
    html = """<h1>Desk Lamp</h1><div><span>$ 128 30</span> <span>In Stock</span></div>
    <div><h3>Other sellers on Zon</h3><div>$ 1,841 13</div></div>"""

    observation = extract_zon(Client(), "zon", "http://x/stores/zon/dp/item", html)

    assert observation.price_cents == 12830


def test_zon_keeps_cart_only_price_null():
    html = "<h1>Backpack (Pack of 12)</h1><div>See price in cart</div><div>In Stock</div>"

    observation = extract_zon(Client(), "zon", "http://x/stores/zon/dp/item", html)

    assert observation.price_cents is None
    assert observation.pack_size == 12


def test_shield_extracts_embedded_state():
    html = '''<h1>Desk Lamp</h1><script>window.__STATE__={"product":{"id":"item","title":"Desk Lamp","offer":{"amount":108514,"currency":"GBP","was":120000,"stock":"OUT_OF_STOCK"}}};</script>'''

    observation = extract_shield(Client(), "shield", "http://x/stores/shield/item/item", html)

    assert observation.name == "Desk Lamp"
    assert observation.price_cents == 108514
    assert observation.compare_at_cents == 120000
    assert observation.currency == "GBP"
    assert observation.availability == "out_of_stock"


def test_shield_challenge_uses_page_token_and_path(monkeypatch):
    class ChallengeSession:
        def __init__(self):
            self.payload = None

        def post(self, url, **kwargs):
            self.payload = (url, kwargs["json"])
            return Response('{"ok":true}')

    session = ChallengeSession()
    client = Client()
    client.session = session
    monkeypatch.setattr("pricewatch.http.time.sleep", lambda _: None)
    response = Response('<div id="cf-c" data-s="token-123" data-p="/stores/shield"></div>')
    response.headers = {"Retry-After": "2"}

    assert client._solve_browser_challenge("http://x/stores/shield/", response)
    assert session.payload[0] == "http://x/stores/shield/challenge"
    assert session.payload[1]["s"] == "token-123"
    assert session.payload[1]["p"] == "/stores/shield"
    assert session.payload[1]["a"] == "c4c198cf211454a2"


def test_shield_malformed_state_returns_safe_observation():
    html = '<script>window.__STATE__={"product": invalid};</script>'

    observation = extract_shield(Client(), "shield", "http://x/stores/shield/item/item", html)

    assert observation.price_cents is None
    assert observation.name == ""
    assert observation.availability == "unknown"
    assert observation.notes == ["no product state found"]


def test_flux_handles_minor_and_major_amounts():
    html = '<meta name="flux-build" content="build"><div class="pdp" data-sku="SKU"><h1>Product</h1></div><script src="/bundle.js"></script>'
    bundle = 'const secretParts = [ "secret", \'part\' ]; const separator = String.fromCharCode(47); secretParts.join(\'\') + separator + sku; fetch( \'/api/graphql\', {} );'
    minor = Response('{"data":{"product":{"title":"Minor","offer":{"amount":91716,"unit":"minor","currency":"USD","stock":"IN_STOCK","stale":false}}}}')
    major = Response('{"data":{"product":{"title":"Major","offer":{"amount":"606.64","unit":"major","currency":"USD","stock":"OUT_OF_STOCK","stale":true}}}}')
    client = StubClient({"http://x/bundle.js": Response(bundle), "http://x/api/graphql": minor})

    observation = extract_flux(client, "flux", "http://x/item/SKU", html)
    assert observation.price_cents == 91716
    assert observation.availability == "in_stock"

    client.responses["http://x/api/graphql"] = major
    observation = extract_flux(client, "flux", "http://x/item/SKU", html)
    assert observation.price_cents == 60664
    assert observation.availability == "out_of_stock"
    assert observation.notes == ["stale price"]


def test_flux_api_error_returns_safe_observation():
    html = '<meta name="flux-build" content="build"><div data-sku="SKU"><h1>Product</h1></div><script src="/bundle.js"></script>'
    bundle = 'var _0x1=["secret","part"]; var _0x2=String.fromCharCode(47); _0x1.join("")+_0x2+sku; fetch("/api/graphql",{});'
    client = StubClient({"http://x/bundle.js": Response(bundle), "http://x/api/graphql": Response('{"errors":[{"message":"unavailable"}]}', 503)})

    observation = extract_flux(client, "flux", "http://x/item/SKU", html)

    assert observation.price_cents is None
    assert "Flux API HTTP 503" in observation.notes