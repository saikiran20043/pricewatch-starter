from pricewatch.agents.watcher import evaluate
from pricewatch.models import Observation


def obs(price, at, pid="P1"):
    return Observation(store="corner", product_id=pid, url="u", name="n", price_cents=price, currency="USD", observed_at=at)


def test_drop_fires():
    alerts = evaluate([obs(10000, "2026-09-01T00:00:00")], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}])
    assert len(alerts) == 1 and alerts[0].rule == "drop_pct"


def test_no_history_no_alert():
    assert evaluate([], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}]) == []


def test_increase_no_alert():
    assert evaluate([obs(8000, "2026-09-01T00:00:00")], [obs(10000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}]) == []


def test_drop_pct_message_percentage():
    # Regression test for the alert copy — keep in sync with rule_drop_pct.
    alerts = evaluate([obs(10000, "2026-09-01T00:00:00")], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}])
    assert "20.0% drop" in alerts[0].message


def test_drop_pct_uses_previous_price_as_denominator():
    alerts = evaluate(
        [obs(10000, "2026-09-01T00:00:00")],
        [obs(8000, "2026-09-02T00:00:00")],
        [{"type": "drop_pct", "pct": 21}],
    )

    assert alerts == []


def test_below_median_fires_from_three_prior_observations():
    history = [
        obs(10000, "2026-09-01T00:00:00"),
        obs(11000, "2026-09-02T00:00:00"),
        obs(10000, "2026-09-03T00:00:00"),
    ]

    alerts = evaluate(history, [obs(8000, "2026-09-04T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}])

    assert len(alerts) == 1
    assert alerts[0].rule == "below_median"
    assert alerts[0].previous_cents == 10000
    assert alerts[0].current_cents == 8000


def test_below_median_threshold_is_inclusive():
    history = [obs(10000, "2026-09-01T00:00:00"), obs(10000, "2026-09-02T00:00:00"), obs(10000, "2026-09-03T00:00:00")]

    assert evaluate(history, [obs(8500, "2026-09-04T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}])
    assert evaluate(history, [obs(8501, "2026-09-04T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []


def test_below_median_requires_three_valid_prior_prices():
    history = [obs(10000, "2026-09-01T00:00:00"), obs(None, "2026-09-02T00:00:00"), obs(10000, "2026-09-03T00:00:00")]

    assert evaluate(history, [obs(7000, "2026-09-04T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []


def test_below_median_currency_mismatch_records_note():
    history = [obs(10000, "2026-09-01T00:00:00"), obs(10000, "2026-09-02T00:00:00"), obs(10000, "2026-09-03T00:00:00")]
    current = obs(7000, "2026-09-04T00:00:00")
    current.currency = "EUR"

    assert evaluate(history, [current], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []
    assert "currency mismatch" in current.notes[-1]


def test_below_median_counts_multiple_observations_on_same_day():
    history = [
        obs(10000, "2026-09-01T00:00:00"),
        obs(10000, "2026-09-01T04:00:00"),
        obs(10000, "2026-09-01T08:00:00"),
    ]

    alerts = evaluate(history, [obs(7000, "2026-09-02T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}])

    assert len(alerts) == 1


def test_below_median_excludes_observations_outside_window():
    history = [
        obs(10000, "2026-08-01T00:00:00"),
        obs(10000, "2026-08-02T00:00:00"),
        obs(10000, "2026-08-03T00:00:00"),
    ]

    assert evaluate(history, [obs(7000, "2026-09-04T00:00:00")], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []


def test_below_median_excludes_current_observation():
    history = [obs(10000, "2026-09-01T00:00:00"), obs(10000, "2026-09-02T00:00:00"), obs(10000, "2026-09-03T00:00:00")]
    current = obs(1000, "2026-09-04T00:00:00")

    alerts = evaluate(history, [current], [{"type": "below_median", "pct": 15, "window_days": 30}])

    assert alerts[0].previous_cents == 10000


def test_below_median_takes_priority_over_drop_pct():
    history = [obs(10000, "2026-09-01T00:00:00"), obs(10000, "2026-09-02T00:00:00"), obs(10000, "2026-09-03T00:00:00")]

    alerts = evaluate(
        history,
        [obs(7000, "2026-09-04T00:00:00")],
        [{"type": "drop_pct", "pct": 10}, {"type": "below_median", "pct": 15, "window_days": 30}],
    )

    assert len(alerts) == 1
    assert alerts[0].rule == "below_median"
