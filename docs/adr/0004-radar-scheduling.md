# 4. Radar scheduling in the database

Status: accepted

## Context
Radars must survive restarts, scale across workers and respect provider rate policies.

## Decision
Each radar row has `next_check_at`. Workers claim due rows with `SELECT ... FOR UPDATE SKIP LOCKED`
(PostgreSQL) in small batches, run the normal pipeline in monitoring mode restricted to providers that
permit scheduled queries, and reschedule with jitter. Failures use full-jitter exponential backoff.
Notifications are rows with a unique dedupe key and a `deliver_after` that encodes quiet hours and
digests.

## Consequences
No separate queue to operate. Delivery is at-least-once with dedupe at creation time.
