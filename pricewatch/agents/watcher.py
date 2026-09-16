"""Watcher agent: compares new observations against history and raises alerts.

History is a list of observations ordered by `observed_at`. Rules come from alerts.yaml.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from ..models import Alert, Observation


def _key(o: Observation) -> tuple[str, str]:
    return (o.store, o.product_id)


def _by_product(history: Iterable[Observation]) -> dict[tuple[str, str], list[Observation]]:
    out: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in history:
        out[_key(o)].append(o)
    for v in out.values():
        v.sort(key=lambda o: o.observed_at)
    return out


def rule_drop_pct(prev: Observation, cur: Observation, pct: float) -> Alert | None:
    if prev.price_cents is None or cur.price_cents is None:
        return None
    if cur.price_cents >= prev.price_cents:
        return None
    drop = (prev.price_cents - cur.price_cents) / prev.price_cents * 100
    if drop >= pct:
        return Alert(store=cur.store, product_id=cur.product_id, rule="drop_pct",
                     message=f"{cur.name or cur.product_id}: {prev.price_cents} -> {cur.price_cents} ({drop:.1f}% drop)",
                     previous_cents=prev.price_cents, current_cents=cur.price_cents, observed_at=cur.observed_at)
    return None


def _observed_datetime(observation: Observation) -> datetime:
    observed_at = observation.observed_at.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(observed_at)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def rule_below_median(prior: Iterable[Observation], cur: Observation, pct: float, window_days: float) -> Alert | None:
    if cur.price_cents is None:
        return None

    current_at = _observed_datetime(cur)
    window_start = current_at - timedelta(days=window_days)
    valid_prior = [
        observation for observation in prior
        if observation.price_cents is not None
        and window_start <= _observed_datetime(observation) < current_at
    ]
    if len(valid_prior) < 3:
        return None

    currencies = {observation.currency for observation in valid_prior}
    currencies.add(cur.currency)
    if len(currencies) != 1:
        cur.notes.append("below_median skipped: currency mismatch in trailing window")
        return None

    median_cents = median(observation.price_cents for observation in valid_prior)
    threshold = median_cents * (1 - pct / 100)
    if cur.price_cents <= threshold:
        median_rounded = int(round(median_cents))
        return Alert(
            store=cur.store,
            product_id=cur.product_id,
            rule="below_median",
            message=f"{cur.name or cur.product_id}: {cur.price_cents} is {pct:g}% below median {median_rounded}",
            previous_cents=median_rounded,
            current_cents=cur.price_cents,
            observed_at=cur.observed_at,
        )
    return None


def evaluate(history: list[Observation], new: list[Observation], rules: list[dict]) -> list[Alert]:
    """Evaluate `rules` for each observation in `new` against `history` (which must not include `new`)."""
    alerts: list[Alert] = []
    hist = _by_product(history)
    for cur in sorted(new, key=lambda o: o.observed_at):
        prior = hist.get(_key(cur), [])
        prev = prior[-1] if prior else None
        below_alert = None
        for rule in rules:
            if rule.get("type") == "below_median":
                below_alert = rule_below_median(
                    prior,
                    cur,
                    float(rule.get("pct", 15)),
                    float(rule.get("window_days", 30)),
                )
                if below_alert:
                    break
        if below_alert:
            alerts.append(below_alert)
        else:
            for rule in rules:
                if rule.get("type") == "drop_pct" and prev is not None:
                    a = rule_drop_pct(prev, cur, float(rule.get("pct", 10)))
                    if a:
                        alerts.append(a)
                        break
        hist[_key(cur)] = prior + [cur]
    return alerts
