# NNNN: <short name of the rule or metric>

Status: proposed | accepted | superseded by NNNN
Date: YYYY-MM-DD
Workstream: B (data foundation) | C (metrics) | E (metric suite) | H (AI layer)

## Decision

One or two sentences stating exactly what the code will do. Example: "CGM readings with value 0 or outside 40 to 400 mg/dL are dropped before any metric is computed."

## Why

The problem this solves, with a link to the `DATA_ISSUES.md` entry, review finding or question that raised it.

## Definition

The precise rule or formula. Include units, time windows, timezone handling, edge cases (empty input, gaps, ties), and what's returned when there isn't enough data. For a published metric, cite the source and say where this version differs from it.

## Known-answer tests

Small hand-checkable cases that the implementation's tests must reproduce exactly.

| Input | Expected output | Why |
| --- | --- | --- |
| | | |

## Alternatives considered

What else was considered and why it was rejected.

## Downstream effects

Which metrics, tables, surfaces or parquet files change. Whether `PIPELINE_VERSION` needs a bump.

## Validation

How this will be checked against reality, for example against Dexcom Clarity over the same period, and the tolerance that counts as a match.
