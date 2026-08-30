---
name: market-data-collect
description: Collect, archive, validate, publish, replay, inspect, backfill, and schedule reviewed market-context data for this project. Covers Guchacha valuation/weights/rates/FX/margin/macro/crowding and public all-A-share breadth, validated fund-NAV trend proxies, allowlisted FRED personal-research series, plus manual SSE breadth validation; also diagnoses stale or missing market data and operates the unified maintenance schedule.
---

# Market Data Collect

## Purpose

Maintain reproducible market-context evidence through a raw-first pipeline. This skill complements `fund-data-collect`: fund NAV collection remains independent, while the unified maintenance runner schedules both domains and emits one report.

This is a reusable data capability, not a heartbeat-specific skill. Use it interactively for inspection, one-off evidence collection, replay, backfill, stale-data diagnosis, or a manually requested job. The heartbeat is only one caller of the same deterministic CLI. For a one-off explanatory lookup that does not need to become project evidence, route through `market-context-research` instead.

## Read First

Before acting, read:

1. `AGENTS.md`
2. `docs/capabilities.md`
3. `docs/market-data.md`
4. `docs/decisions/ADR-0029-guchacha-market-data-and-scheduling.md`
5. `docs/decisions/ADR-0031-reviewed-public-downloads-and-local-history.md`
6. `docs/integrations/guchacha-mcp-review.md`
7. `docs/integrations/market-regime-source-review.md`
8. `config/market_data_sync_v1.json`

Read [references/quality-and-schedule.md](references/quality-and-schedule.md) before changing cadence, quality gates, credentials, or scheduler activation.

## Route the Task

### Inspect without network access

Use the local database and archives:

```bash
python -m invest_agent.market_data.cli inspect
python -m invest_agent.automation.maintenance_cli plan
```

### Probe an endpoint

Use `probe` only for schema discovery or debugging. It archives the raw response but does not publish formal evidence:

```bash
python -m invest_agent.market_data.cli probe --tool list_datasets --arguments '{}'
```

Never treat probe output as strategy or risk input.

### Collect and publish one response

Use `collect`. The command must archive immutable raw input before normalization and validation:

```bash
python -m invest_agent.market_data.cli collect \
  --tool get_index_valuation \
  --arguments '{"index_code":"000300","limit":500}'
```

Only reviewed tools, datasets, keys, and macro indicators may pass policy validation.

### Publish reviewed trend proxies

Use validated local fund NAVs when official index storage permission is not available. These are labelled proxies, not official index levels:

```bash
python -m invest_agent.market_data.cli publish-fund-proxies --history
```

Use `--history` only for bootstrap. Daily maintenance publishes the latest observations.

### Collect reviewed FRED evidence

Only series allowlisted in `config/market_data_sync_v1.json` may run:

```bash
python -m invest_agent.market_data.cli collect-fred --series NFCI --lookback-days 120
```

FRED observations stay in the private local store for personal research, require citation, and may not be redistributed.

### Collect reviewed all-A-share breadth

Use the public server-rendered Guchacha dashboard after the close. It publishes aggregate all-A-share counts, so the project does not build a per-stock history table. The adapter preserves the provider-reported total, classified denominator, unclassified count, last-sync time, and reported upstream label:

```bash
python -m invest_agent.market_data.cli collect-guchacha-breadth
```

This is the scheduled China breadth route. It remains `research_only` and starts strict history at the first successful collection.

### Validate with reviewed SSE breadth manually

Use the fixed public SSE end-of-day endpoint only after the close. The adapter rejects intraday or truncated responses, excludes B shares, archives the full raw response, and publishes explicitly partial China breadth:

```bash
python -m invest_agent.market_data.cli collect-sse-breadth
```

It covers Shanghai A shares only and is not in the default maintenance plan. Use it only when a manual exchange-level cross-check is worth the extra request; never relabel it as full-A-share breadth.

### Replay an archive

Use replay to reproduce normalization without calling the provider:

```bash
python -m invest_agent.market_data.cli replay --batch-id BATCH_ID --as-of 2026-08-30T16:00:00+08:00
python -m invest_agent.market_data.cli replay-guchacha-breadth --batch-id BATCH_ID --as-of 2026-08-30T16:00:00+08:00
python -m invest_agent.market_data.cli replay-sse-breadth --batch-id BATCH_ID --as-of 2026-08-30T16:00:00+08:00
```

Never edit the raw archive to make a replay pass.

### Operate scheduled maintenance

Preview first:

```bash
python -m invest_agent.automation.maintenance_cli plan
```

Run due jobs under the shared lock and state registry:

```bash
python -m invest_agent.automation.maintenance_cli run-due
```

Run one reviewed job for diagnosis:

```bash
python -m invest_agent.automation.maintenance_cli run-job --job-id market_daily_series
```

The preferred visible trigger is the single Codex heartbeat named `Invest Agent 数据更新总控`, bound to one maintenance conversation. It must invoke only the unified maintenance CLI. Do not create one task or conversation per cadence. `scripts/run_scheduled_data_maintenance.sh` and its launchd template are an inactive fallback and must not run concurrently with the Codex controller.

## Required Pipeline

1. Resolve the exact reviewed provider/source config, usage scope, and environment secret when required.
2. Validate the tool name and arguments locally before network access.
3. Make one read-only request; do not automatically retry an ambiguous failure.
4. Archive the exact raw response without credentials.
5. Normalize using the approved schema contract.
6. Run quality gates for dates, units, numeric values, nulls, estimation flags, duplicates, and coverage.
7. Publish only accepted or explicitly partial batches to isolated market-data tables.
8. Emit a machine-readable report with warnings and batch identifiers.

## Scheduler Model

Use one deterministic local runner that evaluates four cadences:

- daily: fund NAV, validated trend proxies, Guchacha all-A-share aggregate breadth, and high-frequency market series;
- weekly: FRED NFCI, dataset catalog, index valuation panel, forward PE, and current industry crowding;
- monthly: index weights and monthly/release-driven macro series;
- quarterly: GDP refresh.

One Codex heartbeat wakes on the configured workdays and invokes `plan` followed by `run-due`. The deterministic runner—not the Agent—decides which jobs are due, prevents duplicate periods, uses one lock, continues independent jobs after a failure, writes the database, and emits one aggregate report.

“Today” is resolved from the runner's timezone-aware `as_of` value, normally the current `Asia/Shanghai` time. For each registered job, code checks its cadence, target weekday/date/time, derives a period key such as `2026-08-28`, `2026-W35`, `2026-08`, or `2026-Q3`, and compares that key with `last_success_period` in the private state file. Only eligible, not-yet-successful periods enter `due_jobs`; the Agent does not select them.

The Codex task is the visible control surface: it presents the plan, reads the resulting report, and explains failures in one fixed conversation. It must not own cadence state, construct individual collection calls, normalize data, or write database rows itself. The local CLI remains independently runnable. Launchd is a fallback trigger only; enable exactly one trigger mechanism at a time.

## Safety and Data Boundaries

- Never print, persist, copy, or expose `GUCHACHA_MCP_TOKEN`.
- Use the existing environment secret or the reviewed macOS Keychain service; never embed credentials in plist files or Git.
- Never invoke excluded Guchacha tools or bypass argument allowlists.
- Never write Guchacha values directly into fund NAV tables.
- Never let collection code generate strategy signals, risk approvals, or orders.
- Do not activate or change either Codex scheduling or launchd silently. Validate the exact task, prompt, cadence, target conversation, and credential readiness, then tell the user exactly what will run.
- Do not use the browser as an invisible fallback when the MCP endpoint fails.
- Do not treat open-source wrappers as data permission. FinClaw, efinance, AKShare, adata and Ashare are interface maps until each upstream is reviewed.
- Do not archive Eastmoney full-market/index quote payloads under the current terms; do not use Snowball automated or cookie routes.
- Never bypass login, captcha, anti-bot controls, paywalls, or rotate proxies. A reviewed public download has one fixed host, one request, no automatic retry, and a kill switch.
- Preserve provider limitations: estimated weights, partial forecast coverage, incomplete crowding history, and retrieval-date proxies.

## Completion Checks

Before reporting success:

1. Confirm an immutable archive exists.
2. Confirm the batch is visible through `inspect` or the database.
3. Confirm warnings and partial status were preserved.
4. Run focused tests for policy, normalization, idempotency, and scheduling.
5. State which trigger is active: Codex heartbeat, launchd fallback, or neither. Never leave both active.
