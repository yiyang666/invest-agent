---
name: market-context-research
description: Route, retrieve, and explain validated market-context evidence for this investment project. Use when the user asks about current or historical index valuation, index weights, forward PE, rates, foreign exchange, margin leverage, macro indicators, industry crowding, market state, or how those observations affect a fund portfolio, strategy, risk review, attribution, or report.
---

# Market Context Research

## Purpose

Use Guchacha and the project's local market-data store without letting an LLM improvise data provenance. Market context explains the environment; it does not predict markets or replace deterministic fund data, backtests, or risk rules.

## Read First

Before acting, read:

1. `AGENTS.md`
2. `docs/capabilities.md`
3. `docs/market-data.md`
4. `docs/market-regime.md` when the question concerns overall market state
5. `docs/integrations/guchacha-mcp-review.md`
6. `config/investment_policy.yaml` when present

Read [references/tool-routing.md](references/tool-routing.md) whenever choosing a Guchacha tool or interpreting its authority.

## Choose the Evidence Mode

Choose by downstream use, not by whether a human or Agent initiated the request.

### Interactive ephemeral

Use for a one-off factual lookup whose result will not enter a strategy, risk conclusion, attribution, formal report, or order suggestion.

- A direct allowlisted Guchacha MCP call is permitted when the tool is callable in the current session.
- State source, retrieval time, observation date, and important limitations.
- Do not persist the response.
- Do not later treat the response as reusable project evidence.

### Evidence snapshot

Use whenever the result may affect a portfolio, strategy, risk conclusion, attribution, report, or trade suggestion.

- Invoke `market-data-collect` or the reviewed market-data CLI.
- Archive raw input first, validate it, publish it locally, then read the local store.
- Preserve warnings such as estimated index weights, retrieval-date proxies, stale observations, partial coverage, and null releases.
- Do not cite an unarchived MCP response as formal evidence.

### Historical series

Use for metrics, comparisons, regime analysis, backtests, risk, and attribution.

- Read only through the validated local database.
- If history is missing, request a deterministic backfill through `market-data-collect`.
- Never let an MCP response bypass the local data gate and enter a calculation.

### Global market-state snapshot

When the user asks for an overall assessment of the current global market environment,
run the deterministic local snapshot before interpreting it:

```bash
python -m invest_agent.market_regime.cli --as-of YYYY-MM-DD
```

- Preserve `overall_state`, seven-axis coverage, point-in-time percentage, source count,
  missing capabilities, and conflicts.
- If the result is `insufficient_evidence`, explain the observed partial evidence but do
  not promote it to a directional market conclusion.
- Do not confuse this snapshot with `metrics.market_state`, which is a per-fund NAV regime.

## Research Workflow

1. Restate the exact question, instruments or market concepts, as-of date, and intended downstream use.
2. Select the evidence mode above.
3. Route the question using the reference table; do not guess among similarly named tools.
4. Check coverage, observation date, units, estimation flags, missing values, and data authority.
5. For formal analysis, combine market context with the project's deterministic fund, metric, risk, backtest, or attribution CLI. Keep the sources separate.
6. Explain what the data supports, what it does not support, and which conclusion is an inference.

## Hard Boundaries

- Never use Guchacha context as a market-timing oracle.
- Never convert high crowding, low valuation, macro releases, or forward PE into an automatic buy/sell rule without a versioned strategy specification and backtest.
- Never call `search_stocks`, `get_stock`, `get_dcf_report`, or `get_watchlist` in this project.
- Never use Snowball/Xueqiu cookie APIs or FinClaw's automatic source fallback as formal evidence.
- `get_market_series` and `get_macro` may only use the local allowlists.
- Current industry crowding contains several dimensions, but requested historical industry detail currently exposes only turnover-share history. Do not imply that other dimensions have history until daily snapshots accumulate.
- Estimated index weights must stay explicitly estimated.
- Forward PE is low-authority contextual evidence because forecast coverage and provider timing vary.
- If the Guchacha MCP tools are absent from the current session, use the reviewed local CLI/store or report that the session must be reloaded. Never pretend a tool was called.
- Keep the system in `advisory_only`; this skill cannot submit or authorize an order.

## Expected Output

For a concise answer, include:

- the observation and as-of date;
- the source and evidence mode;
- the portfolio relevance, if any;
- limitations or conflicting evidence;
- whether the statement is fact, deterministic calculation, or LLM interpretation.
