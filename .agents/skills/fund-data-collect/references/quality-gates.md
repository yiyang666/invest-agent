# Fund batch quality gates

## Visibility grades

- `strict_point_in_time`: require a timezone-aware `announcement_at` or an actual `first_seen_at` no later than the run's `as_of`.
- `historical_visibility_assumed`: preserve missing announcement time and apply a conservative visibility assumption only in research outputs.

Never synthesize `announcement_at` from `nav_date`.

## Rejection conditions

Reject publication of observations when any error exists, including:

- missing provider, batch ID, provenance, or timezone;
- invalid six-digit fund code;
- nonpositive unit or accumulated NAV;
- duplicate fund/date observation;
- NAV dated after `as_of`;
- strict observation not visible by `as_of`;
- empty batch after provider filtering.

Keep the rejected batch metadata and issues in the audit table.

## Warning conditions

Publish with `partial` status and an explicit warning for suspicious changes that can be legitimate, including:

- a unit-NAV move above the configured threshold, which may indicate a split or dividend;
- a long NAV gap, including possible QDII calendar effects.

Warnings must not be silently removed. Resolve them later with dividend, split, calendar, or second-source evidence.

## Provenance

Record the actual underlying source such as `fund.10jqka.com.cn` or Eastmoney rather than only `AKShare`. Bind each batch to request parameters, raw payload SHA-256, normalized-content SHA-256, fetch time, `as_of`, and adapter identity.
