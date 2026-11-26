# Security

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" on this repository
rather than opening a public issue.

## Design summary

- **Secrets** live only in environment variables. `.env` is git-ignored; `.env.example` has no values.
  HTTP client request logging is lowered so tokens in URLs are not written to logs.
- **Mini App auth**: Telegram `initData` is verified with HMAC-SHA256 (key derived from the bot token),
  duplicate keys and oversized payloads are rejected, and data older than
  `RADAR_WEBAPP_AUTH_MAX_AGE` seconds is refused.
- **Webhook**: Telegram's secret-token header is required and compared in constant time.
- **Authorization**: every radar, booking and stored search is looked up together with its owner;
  another user's id returns "not found".
- **Untrusted content**: provider text (names, descriptions, reviews) is escaped before it reaches
  Telegram, inserted with `textContent` in the Mini App, served under a strict CSP, and never passed to a
  language model.
- **Language models** have no tools, no network or database access through RADAR, and their output is
  a fixed schema validated by deterministic code. They are not involved in booking.
- **Bookings** require provider revalidation, a one-time nonce bound to the summary the user saw
  (10 minute TTL), and are idempotent. Traveller details typed in chat are deleted from the chat after
  being read and are sent only to the booking provider.
- **Abuse limits**: per-user request rates in the bot and API, per-user radar quotas, per-provider rate
  limits and circuit breakers.
- **Privacy**: location shared in chat is kept in conversation state only. `/forget` deletes the user,
  radars, observations, notifications, bookings, plans and searches, and anonymises audit entries.
  The audit log records actions, never message text.
- **Container** runs as an unprivileged user.
