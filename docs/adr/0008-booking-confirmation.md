# 8. Booking needs revalidation and a one-time confirmation

Status: accepted

## Decision
A booking always starts with a provider revalidation, then shows the exact total and conditions with a
nonce bound to that summary (10 minute TTL). Confirmation checks owner, state, nonce and expiry; it is
idempotent and passes an idempotency key to the provider. A price change returns to the user. Language
models are never in this path.

## Consequences
A stale or replayed button cannot book, a double tap cannot book twice, and the user always approved the
exact price that was submitted.
