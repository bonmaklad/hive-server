# Cron Setup

## Wrapper

Use:

```bash
/opt/surgecodex/scripts/run_hourly_queue.sh
```

## Example Cron

Run once per hour:

```cron
0 * * * * cd /opt/surgecodex && /usr/bin/env bash /opt/surgecodex/scripts/run_hourly_queue.sh >> /opt/surgecodex/logs/queue.log 2>&1
```

## Environment

The cron environment must expose or load:

- `NEXT_PUBLIC_SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SURGE_PRODUCT_OWNER_USER_ID`
- `SURGE_ANALYST_USER_ID`
- `SURGE_DEVELOPER_USER_ID`
- `SURGE_QA_USER_ID`
- `SURGE_RELEASE_USER_ID`

If using `.env.local`, make sure the wrapper or shell profile loads it before execution.
GitHub auth is expected to already work on the server.

## Operational Notes

- The runner processes only one ticket per client per batch.
- Different clients may run in parallel in the same batch.
- If a client lock file already exists, that client is skipped.
- If a repo sync fails, the ticket remains unadvanced and an internal note is written when possible.
