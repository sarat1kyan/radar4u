# 2. Deterministic language understanding, buttons first

Status: accepted (revised)

## Context
Requests must be understood the same way every time, offline, with no external service, no per-message
cost and no way for listing text to influence the result.

## Decision
- The primary interface is buttons: a single self-updating panel in the bot and a form in the Mini App
  build a structured draft (category, place, date, time, guests, filters, sort).
- Typed requests are optional and go through a deterministic multilingual rule parser. Anything it cannot
  read is reported as missing and the bot offers buttons for it.

## Consequences
Behaviour is reproducible and testable (`tests/eval/parser_cases.json`). Unusual phrasing may need a tap
instead of a sentence, which is the intended trade-off.
