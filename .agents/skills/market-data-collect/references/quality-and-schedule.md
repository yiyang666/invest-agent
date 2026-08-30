# Market-data quality and scheduling reference

## Quality contract

- Raw response is archived before parsing.
- Credentials are supplied only through the environment and never serialized.
- Provider, tool, arguments, retrieval time, observation time, schema hash, quality status, and warnings remain traceable.
- Required fields must match the reviewed response shape.
- Numeric observations must be finite and have a known unit or an explicit unit warning.
- Duplicate identity keys are rejected or deterministically idempotent.
- Unreleased macro placeholders with null values are skipped and warned, not coerced to zero.
- `is_estimated` on index weights is preserved.
- Forward-PE retrieval dates are marked as proxies when the provider omits a snapshot date.
- Missing independent validation keeps Guchacha evidence at research authority; it must not silently become a production risk gate.

## Cadence ownership

`config/market_data_sync_v1.json` is the source of truth for market jobs. The existing fund sync config remains the source of truth for fund NAV jobs. `invest_agent.automation.maintenance_cli` combines them into one registry.

The visible Codex controller invokes the unified runner on its configured workdays. Cadences are evaluated inside deterministic code:

- `business_daily`: once per eligible business date;
- `weekly`: once per configured weekday/week;
- `monthly`: once per configured day/month;
- `quarterly`: once per configured month/day/quarter.

State records successful periods. A failed period is not marked complete and can be rerun deliberately. The file lock prevents concurrent writers.

The active heartbeat currently runs Monday through Friday at 23:55 Asia/Shanghai. Fund NAV and the configured rates/FX/margin series are both registered as `business_daily`. Monthly and quarterly jobs catch up on the next heartbeat after their nominal date. A weekly Friday snapshot can still run later on Friday, Saturday, or Sunday within the same ISO week if the runner is invoked manually, but an entirely missed Friday heartbeat is not reconstructed as a point-in-time snapshot on Monday. Series requests use a trailing window, so missed observations can normally be backfilled even when their first-seen timestamp is later.

## Visible Codex controller

Use one Codex heartbeat as the preferred observable trigger. It runs `maintenance_cli plan`, then `maintenance_cli run-due`, and explains the resulting aggregate report in one fixed conversation. The Agent is not the collector: the deterministic CLI owns cadence state, locking, requests, normalization, quality gates, publication and reports.

Never bind daily, weekly, monthly, and quarterly collection to separate conversations. Never let the Agent replace `run-due` with improvised individual MCP or provider calls.

The launchd wrapper is a fallback for future zero-LLM operation. Keep it unloaded while the Codex controller is active. If migrating trigger mechanisms, disable the old trigger before enabling the new one so fund NAV is not synchronized twice.

## Credential readiness

Interactive and Codex-local commands may inherit `GUCHACHA_MCP_TOKEN`; a missing credential must appear as a failed market command and must not be printed. A future background launchd process generally does not inherit the terminal environment, so its wrapper can retrieve the token from macOS Keychain service `invest-agent-guchacha-mcp`. Check existence without printing the secret.

## Failure handling

- No automatic provider fallback.
- No retry on ambiguous network or server failure.
- Independent jobs may continue after one job fails.
- The aggregate report must record each failed command and keep successful batch IDs.
- Collection failure must not trigger a strategy change or an order.
