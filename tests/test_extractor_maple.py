"""Reproduces one of the symptoms reported in tasks/ISSUE-1.md."""
from pricewatch.agents.extractor import extract_maple
from pricewatch.http import Client

HTML = """<html><body><div class="product"><h1 class="product__title">Kestrel Lumen Notebook</h1>
<div class="price"><span class="price price--compare"><s>937,20 €</s></span> <span class="price price--sale">720,92 €</span></div>
<p class="muted">In stock · prices shown in EUR</p></div></body></html>"""


def test_maple_sale_item_reports_sale_price():
    o = extract_maple(Client(), "maple", "http://x/stores/maple/products/kestrel-lumen-notebook", HTML)
    assert o.price_cents == 72092
    assert o.currency == "EUR"

def test_maple_regular_item_reports_current_price():
    html = """<html><body>
    <div class="product">
        <h1 class="product__title">Regular Product</h1>
        <div class="price">
            <span class="price">123,45 €</span>
        </div>
        <p class="muted">In stock · prices shown in EUR</p>
    </div>
    </body></html>"""

    o = extract_maple(
        Client(),
        "maple",
        "http://x/stores/maple/products/regular-product",
        html,
    )

    assert o.price_cents == 12345
    assert o.currency == "EUR"
