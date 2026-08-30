# Guchacha tool routing and authority

| Question | Reviewed tool | Current coverage | Authority and cautions |
|---|---|---|---|
| What datasets exist? | `list_datasets` | Provider catalog | Discovery only; catalog presence is not validation. |
| Current or historical broad-index valuation | `get_index_valuation` | Current panel; selected index history | Research context. Specific-index history is weekly. Preserve observation dates. |
| Index constituents and concentration | `get_index_weight` | Current/latest weight snapshot | Preserve `weight_date` and `is_estimated`; estimated weights are not official rebalances. |
| Forward earnings valuation | `get_index_forward_pe` | Forecast-year PE and coverage | Low authority. Preserve coverage and expected-growth fields. Provider snapshot time may be a retrieval-date proxy. |
| Current industry crowding or turnover-share history | `get_industry_crowding` | Current multi-metric panel; specific-industry turnover-share history | Descriptive, not predictive. Historical dimensions are currently incomplete. |
| Rates | `get_market_series` | `bond_yield`: `CN_2Y`, `CN_10Y`, `US_2Y`, `US_10Y`, `JP_2Y`, `JP_10Y` | Use the exact configured key and unit. Historical analysis uses the local store. |
| FX | `get_market_series` | `forex`: `usdjpy`, `eurusd`, `gbpusd`, `usdcny`, `dxy` | Context for QDII exposure, not a standalone timing signal. |
| Market leverage | `get_market_series` | `margin`: `margin_bal`, `leverage_pct` | Context only; avoid causal claims from correlation. |
| Inflation, activity, GDP, Buffett ratio, US labor | `get_macro` | `cpi`, `ppi`, `pmi`, `gdp`, `buffett`, `nonfarm` | Preserve release/observation dates and units. Skip unreleased null placeholders. |

## Forbidden routes

The project intentionally excludes `search_stocks`, `get_stock`, `get_dcf_report`, and `get_watchlist`. They do not match the fund-system scope or evidence contract. Do not work around the allowlist with browser scraping.

## Local restrictions

The exact allowlists live in `config/market_data_sync_v1.json` and `config/data_sources.example.yaml`. Configuration is authoritative if this reference becomes stale.

Any value used by strategy, risk, attribution, reporting, or an order suggestion must be captured raw, normalized, quality-gated, and read from the local store. A direct MCP answer remains ephemeral even when it looks plausible.
