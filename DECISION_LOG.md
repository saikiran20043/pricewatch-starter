---
# Machine-read by the autograder. Keep the keys; fill in the values.
name: ""
median_basis: "observations"        # the median your below_median rule computes: "observations" or "daily_close"
levels_attempted: []    # e.g. [1, 2, 3, 4, 5]
llm_provider: ""        # e.g. "openai", "anthropic", "gemini"
llm_model: ""
ai_tools_used: []       # e.g. ["Claude Code", "Cursor", "ChatGPT"]
hours_spent: 0
---

# Decision log

Keep this to one page. Bullet points are fine. We read this before we read your code.

## Stage 1 — the false alerts
- I did not assume the reporter's watcher hypothesis was the only cause. I reproduced the failing Maple test and traced the values through extraction, normalization, and alert evaluation.
- I found three contributing issues:
  - `rule_drop_pct` calculated the percentage relative to the current price instead of the previous price. I changed the denominator to the previous price and added a regression test showing that 10000 → 8000 is a 20% drop, not 25%.
  - `parse_money()` did not handle European comma-decimal formatting. For example, `720,92 €` was interpreted as 7,209,200 cents instead of 72,092 cents. I updated the shared normalizer and added coverage for this format while preserving the existing USD tests.
  - Maple's `.price .price` selector could select the compare-at price before the sale/current price. I changed the adapter to prefer `.price--sale` and added tests for both sale and regular products.
- Verification: the full local test suite passes, and two scans of the running Maple fake store produced identical `price_cents` values for all 12 products; the watcher produced no alert for the unchanged prices.
- I did not treat the Zon scan as proof of stable pricing because its current adapter returned `price_cents=None` for the products scanned. That remains separate work for the later extraction levels.
## Stage 2 — the median

- `below_median` uses all valid prior observations for the same product in the trailing window, based on actual `observed_at` timestamps. The current observation is excluded, and multiple observations on the same day count individually.
- Observations with `price_cents == null` are ignored, and at least three valid prior observations are required. A currency mismatch prevents the rule from firing and records a note on the current observation.
- The threshold is inclusive: `current <= median * (1 - pct / 100)`. `previous_cents` contains the rounded median.
- When both rules match, `below_median` takes priority so at most one alert is emitted per product/evaluation.
- Verification: `pricewatch watch --history fixtures/history.jsonl --new fixtures/new.jsonl --rules alerts.yaml` ran successfully and produced the expected `below_median` alert for Demo Kettle.

## Levels 3–5 — how you got in

<!-- Per level: what the store does, how your agent handles it, what you chose not to do (and why). -->

## Stage 3 — what the model got wrong

<!-- Failure patterns you saw. Which labels you think are wrong and how you decided. What you'd change with another day. -->

## Where AI helped and where it didn't

<!-- Be specific. "The assistant wrote the retry logic correctly first time, but confidently misread a response header and I lost 40 minutes before checking the raw response" is the level of detail we want. -->

## If I had another day

