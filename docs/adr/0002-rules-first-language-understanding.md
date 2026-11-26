# 2. Rule parser first, LLM optional

Status: accepted

## Context
Most requests follow a handful of shapes. An LLM for every message adds latency, cost, a failure mode
and an attack surface, and its output must be validated anyway.

## Decision
A deterministic multilingual rule parser produces the `SearchIntent` by default. LLM backends
(Anthropic SDK structured outputs, or any OpenAI-compatible server in JSON mode) are consulted only when
the rule result is weak. Their output is a flat schema with enums, re-validated by deterministic code.
The model never sees provider content and has no tools.

## Consequences
RADAR works fully offline from model providers. Model outages degrade understanding of unusual phrasing,
not availability. The vocabulary in `domain/vocabulary.py` is the single list both paths share.
