---
# Machine-read by the autograder. Keep the keys; fill in the values.
name: "SaiKiran Doddi"
median_basis: "observations"        # the median your below_median rule computes: "observations" or "daily_close"
levels_attempted: [1, 2, 3, 4, 5]   # e.g. [1, 2, 3, 4, 5]
llm_provider: ""        # e.g. "openai", "anthropic", "gemini"
llm_model: ""
ai_tools_used: ["ChatGPT", "GitHub Copilot"]       # e.g. ["Claude Code", "Cursor", "ChatGPT"]
hours_spent: 8
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
- I did not treat the Zon scan as proof of stable pricing because its current adapter returned `price_cents=None` for the products scanned. That remains separate extraction work rather than evidence of stable pricing.
## Stage 2 — the median

- `below_median` uses all valid prior observations for the same product in the trailing window, based on actual `observed_at` timestamps. The current observation is excluded, and multiple observations on the same day count individually.
- Observations with `price_cents == null` are ignored, and at least three valid prior observations are required. A currency mismatch prevents the rule from firing and records a note on the current observation.
- The threshold is inclusive: `current <= median * (1 - pct / 100)`. `previous_cents` contains the rounded median.
- When both rules match, `below_median` takes priority so at most one alert is emitted per product/evaluation.
- Verification: `pricewatch watch --history fixtures/history.jsonl --new fixtures/new.jsonl --rules alerts.yaml` ran successfully and produced the expected `below_median` alert for Demo Kettle.

## Levels 3–5 — how you got in

The README defines Levels 3–5 as progressively harder store-extraction
problems: Zon uses unstable marketplace-style markup with multiple prices,
Shield introduces a server-provided access challenge, and Flux is an SPA
whose price is obtained through a signed API.

For Level 3 (Zon), I replaced brittle generated-class assumptions with
semantic/structural extraction. The extractor prioritizes the actual pack
offer when present, excludes list/seller/per-unit prices, and preserves
cart-only products as unavailable price observations.

For Level 4 (Shield Outfitters), I implemented the server-provided challenge
flow using the token/path supplied by the page, respecting the server's
Retry-After/delay behavior and preserving the session clearance cookie.
Product data is then extracted from the embedded window.__STATE__ object.

For Level 5 (Flux), I implemented extraction from the SPA's served bundle and
signed GraphQL API. The extractor interprets the API's amount according to
its unit: minor values are already in minor currency units, while major
values are converted to minor units. Transient API failures use the existing
bounded Retry-After handling.

The implementation was validated against a non-public STORE_SEED=mytest:
Zon returned 3 priced products and 9 explicit cart-only/no-price cases,
Shield returned 10 priced products, and Flux returned 12 priced products.
Focused Level 3–5 tests and the full test suite passed.
## Stage 3 — what the model got wrong

- Part A uses one generic prompt and cleans page HTML before sending it to the provider. It accepts normal or fenced JSON, validates the fields, and keeps malformed responses separate from provider timeout/error failures so the harness can continue.
- Part B evaluates all 90 supplied snapshots with overall and per-store metrics, cached successful responses, latency percentiles, and deterministic token/cost estimates. I did not run a real OpenAI or Anthropic evaluation because no active API key was available; the local Echo provider was used only as a pipeline sanity check, so I have no real model failure pattern or accuracy claim.
- I reviewed all 90 labels against their pages. I recorded 8 clear issues in `eval/out/label_issues.json`, kept 7 cases ambiguous, and found no obvious issue in the remaining 75 entries.

**## Where AI helped and where it didn't**

- ChatGPT helped with reasoning about the existing architecture, tracing the watcher/extractor/evaluator paths, and shaping focused regression tests.
- GitHub Copilot was used in VS Code for focused implementation and test changes. I reviewed the generated diffs, checked the implementation against the observed page behavior, and ran the test suite rather than accepting generated changes blindly.
- I also checked the fake-store behavior directly with a non-public seed before accepting the Level 3–5 implementation.

## If I had another day

- Run the evaluation with a real provider key, inspect the model's actual misses, and use those results to improve the generic prompt or cleaning only where the evidence supports it. I would also investigate additional store-specific extraction behavior only where evaluation evidence shows it is necessary.
