from pricewatch.agents.normalizer import parse_money


def test_usd_plain():
    assert parse_money("$596.85") == (59685, "USD")


def test_usd_thousands():
    assert parse_money("$1,299.00") == (129900, "USD")


def test_no_number():
    assert parse_money("Price unavailable", "USD") == (None, "USD")


def test_eur_comma_decimal():
    assert parse_money("720,92 €", "EUR") == (72092, "EUR")
