---
name: fund-data-collect
description: Collect, archive, validate, publish, and inspect reproducible off-exchange public-fund data batches for this project. Use when asked to initialize the local fund store, import authorized NAV exports, backfill or synchronize fund data, inspect batch quality, diagnose rejected observations, or prepare AKShare THS/Eastmoney collection without allowing live data to bypass the local quality gate.
---

# Fund Data Collect

Use deterministic project modules to move remote or authorized fund data through an immutable raw archive and quality gate into the local SQLite store.

## Required reading

Before changing collection behavior, read:

1. `AGENTS.md`
2. `docs/data/phase-2-data-plan.md`
3. `docs/decisions/README.md` 中的 ADR-0005
4. `references/quality-gates.md` in this Skill

Resolve project paths from the repository root. Keep account data, raw batches, databases, credentials, and tokens out of Git.

## Workflow

1. Inspect `config/data_sources.example.yaml` and confirm the requested provider role.
2. Prefer local validation or inspection when no network collection is explicitly needed.
3. Archive the exact raw payload before normalization. Never overwrite or silently repair an existing batch.
4. Normalize values using `Decimal`, timezone-aware timestamps, explicit underlying domains, and the project data contracts.
5. Evaluate the complete batch quality report.
6. Publish observations only when the quality report is `pass` or `partial`; retain failed batch metadata for audit without publishing its observations.
7. Report batch ID, source, content hash, visibility grade, record count, and every warning/error.

Never fall back to mock data. Never fetch AKShare during strategy inference, reporting, metrics, or backtesting. Never import or invoke any trading/execution module.

## Local commands

Initialize the private store:

```bash
python3 -m invest_agent.data.cli init-store
```

Import an authorized CSV export:

```bash
python3 -m invest_agent.data.cli ingest-csv \
  --csv data/imports/fund_nav.csv \
  --fund-code 000001 \
  --start-date 2020-01-01 \
  --end-date 2026-08-15 \
  --as-of 2026-08-15T20:00:00+08:00
```

Collect an Eastmoney historical-NAV batch through the reviewed AKShare-compatible adapter:

```bash
.conda-env/bin/python -m invest_agent.data.cli collect-akshare-eastmoney \
  --fund-code 000001 \
  --start-date 2020-01-01 \
  --end-date 2026-08-15 \
  --as-of 2026-08-15T20:00:00+08:00
```

This command archives the exact response envelope before parsing. The adapter does not execute remote JavaScript and does not publish any batch that fails its quality gate.

Collect THS fund metadata and an explicit-date daily cross-check batch:

```bash
.conda-env/bin/python -m invest_agent.data.cli collect-akshare-ths-metadata \
  --fund-code 000001 \
  --as-of 2026-08-15T20:00:00+08:00

.conda-env/bin/python -m invest_agent.data.cli collect-akshare-ths-daily \
  --fund-code 000001 \
  --start-date 2026-08-14 \
  --end-date 2026-08-14 \
  --as-of 2026-08-15T20:00:00+08:00
```

Compare the stored values without silently selecting either source:

```bash
.conda-env/bin/python -m invest_agent.data.cli crosscheck-nav \
  --left-provider akshare_eastmoney \
  --right-provider akshare_ths_daily \
  --fund-code 000001
```

If collection archived a payload but normalization was interrupted, replay that exact raw batch without another network request:

```bash
.conda-env/bin/python -m invest_agent.data.cli replay-akshare-ths-daily \
  --batch-id akthsdaily-YYYYMMDDTHHMMSS-xxxxxxxxxxxx \
  --as-of 2026-08-15T20:00:00+08:00
```

Inspect batch summaries:

```bash
python3 -m invest_agent.data.cli inspect
```

Treat exit code `2` as a quality-gate rejection, not a retry signal. Diagnose the recorded issues before collecting again.

## External collectors

Keep AKShare adapters in `invest_agent/data/providers/`; keep this Skill thin. Split `akshare_ths` and `akshare_eastmoney` provenance even though both use the AKShare package. Pin and review AKShare before installation or upgrade, and obtain user confirmation as required by project policy.

Do not claim `fund_info_ths` supplies historical NAV. Use it for basic metadata. The confusingly named `fund_etf_category_ths(symbol="")` supplies all-category explicit-date snapshots, but it is only a cross-check channel: observed coverage gaps prove it cannot replace the Eastmoney historical backfill.

## Verification

After changes, run:

```bash
python3 -m unittest discover -s tests -v
python3 /Users/ethan/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/fund-data-collect
```

Do not publish a real collected batch merely to make a test pass. Use temporary directories and synthetic fixtures for tests.
