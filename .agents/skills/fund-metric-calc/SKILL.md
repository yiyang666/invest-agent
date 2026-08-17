---
name: fund-metric-calc
description: Calculate reproducible off-exchange fund returns, volatility, drawdowns, portfolio concentration, rolling correlations, and explainable trailing market states from this project's validated local SQLite store. Use when asked to analyze fund performance, risk, drawdowns, recovery, overlap, correlations, or market state without fetching live data.
---

# Fund Metric Calc

Read only from the validated local store. Never call AKShare, webpages, account APIs, or trading modules while calculating metrics.

## Workflow

1. Read `AGENTS.md`, `config/investment_policy.yaml`, and `docs/data/phase-2-data-plan.md`.
2. Confirm the requested `as_of` date and provider. Default to `akshare_eastmoney` and `accumulated_nav`.
3. Run deterministic project commands; do not recreate formulas in a prompt or spreadsheet.
4. Report the source provider, observation range, batch hashes, visibility limitation, and gaps with every result.
5. Treat `research_only: true` as binding. Historical batches with assumed visibility cannot be presented as strict point-in-time results.

## Commands

Calculate individual fund metrics:

```bash
python3 -m invest_agent.metrics.cli fund \
  --fund-code 000001 \
  --as-of 2026-08-15
```

Calculate a return-correlation matrix:

```bash
python3 -m invest_agent.metrics.cli correlation \
  --fund-code 000001 \
  --fund-code 000002 \
  --as-of 2026-08-15 \
  --minimum-overlap 60
```

Calculate a current-weight portfolio risk proxy:

```bash
python3 -m invest_agent.metrics.cli portfolio \
  --snapshot data/private/portfolio_snapshots/<snapshot>.json \
  --as-of 2026-08-15
```

Calculate explainable trailing fund states:

```bash
python3 -m invest_agent.metrics.cli market-state \
  --snapshot data/private/portfolio_snapshots/<snapshot>.json \
  --as-of 2026-08-15
```

Use `--output data/private/reports/<name>.json` for private reproducible artifacts. Keep fund/account-specific reports out of Git.

## Metric rules

- Use accumulated NAV for total-return metrics. Do not add cash dividends again.
- Use unit NAV only when explicitly requested for price-path analysis.
- Annualize return by actual elapsed calendar days and volatility by 252 observed return periods.
- Compute drawdown against the prior running peak; leave recovery null when unrecovered.
- Align correlations on common return dates and expose overlap counts.
- Never forward-fill missing NAV or silently combine providers.
- Market state `fund_regime_v1` uses only trailing observation windows and fixed ordered rules. It describes evidence and never acts as a strategy signal.
- An `as_of` NAV-date cutoff prevents future observations from entering a calculation. Historical backfills without trustworthy publication times remain `research_only`, even when the NAV-date leakage tests pass.

## Verification

Run `python3 -m unittest discover -s tests -v` after metric changes. A strategy or trade recommendation is outside this Skill.
