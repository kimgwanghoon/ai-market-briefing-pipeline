# Research quality upgrade

## Scope
Preserve the white/navy editorial report layout. Prioritize evidence and changes,
not dashboard gauges. Shared review: 6ab1cd3c-6708-83ee-87f2-23fc019544bc.

## P0: observation identity
- Yahoo prices must come from the same observation as their timestamps. Prefer
  regularMarketPrice + regularMarketTime and previousClose; never attach metadata
  timestamps to an arbitrary final daily bar. Fallback bars are unadjusted and
  explicitly described as daily bars, not latest trades.
- Persist source, full source_timestamp, collected_at, price basis and a content
  hash snapshot_id in each report. Score, model input, web and Discord consume
  that report payload. Daily and live can have different collection times;
  identical observation timestamps with different prices are a conflict.
- Reject conflicting or regressing new observations against compatible persisted
  observations before analysis/publication. Do not silently rewrite old reports.

## P1: research reading flow
- Executive Summary uses stored analysis, with a separate fact and watch point.
- What Changed compares the immediately preceding report of the same kind,
  including archived reports; show both timestamps and link to the prior report.
- House Indicator contributions = normalized component * weight * 25 / 1.5.
  Base 50, clamping and rounding are disclosed separately. Never present raw
  component changes as causal attribution or invent a normalization residual.
- Coverage, age-at-publication and cross-source verification are separate.
  Closed-market observations may legitimately be older. No invented percentages.
- Publish actual validation sample count, hit rate and false-alarm rate.

## P2: evidence enrichment
- News/disclosures: fact, conditional implication, next verification point.
- Watchlist: actual screening counts/rejection reasons and evidence-backed
  catalyst; no fabricated events, dates, targets or buy/sell labels.
- Weekly: existing opportunity/risk evidence and base/upside/downside conditions.
  An economic/earnings calendar and historical performance page require a
  verified event feed and more samples; do not manufacture them from headlines.

## Validation and rollout
Regression tests cover stale daily bars versus fresh metadata, missing timestamps,
immutable snapshot identity, conflicting observations and time-ordered comparisons.
Typecheck/build and fixture rendering cover legacy/new payloads and mobile layout.
Publish via master; observe CI/deployment. Historical values remain unchanged.
