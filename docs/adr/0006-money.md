# 6. Money as Decimal with ISO codes

Status: accepted

## Decision
`Money` is `Decimal` plus ISO 4217 code; constructing it from a float raises. Quantisation uses CLDR
minor units. Conversion keeps the original amount and the rate's source and date. Databases store
amounts as decimal strings.

## Consequences
No rounding drift, and every displayed conversion can be traced to a rate.
