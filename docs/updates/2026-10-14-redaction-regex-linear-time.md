# 2026-10-14 — Redaction regexes made linear-time

## Problem
`ingestion/redaction.py` scrubs every log record (message, args, traceback text)
in-process before it reaches the public GitHub Actions logs. Two of its patterns
were super-linear (about O(n^2)) on long inputs:

- `_EMAIL_RE` (`[local]+@domain`): on a long run of local-part characters with no
  `@`, the engine restarts at every position and each attempt rescans to the end
  of the run. 64 KB took about 2.3 s and 256 KB about 38 s.
- `_JWT_TOKEN_RE` (`eyJ[..]+\.[..]+\.[..]*`): `eyJeyJeyJ...` with no dots had the
  same shape. It took about 0.8 s alone on 64 KB, and `redact()` took about 3.1 s
  on that input.

Any long log line could stall the nightly sync, for example an exception that
echoes an HTTP response body. PR #25 caps line length in its CI gate as a
workaround; this change fixes the root cause.

## Fix: consume-then-decide
Possessive quantifiers alone do not help, because the cost comes from restarting
at each position, not from backtracking inside one attempt. The rewrite takes the
whole run once and then decides:

- `_EMAIL_RE = [local]++(@label++(?:\.label++)++)?`. `_redact_emails` replaces a
  match with `<email>` only when the `@domain` group matched; otherwise it leaves
  the run unchanged.
- `_JWT_TOKEN_RE = eyJ[..]++(\.[..]++\.[..]*+)?`. `_redact_jwts` uses the same
  approach.

**Why the output is unchanged.** Within a run of local-part characters, the
leftmost possible match start is always the start of the run, because every later
start reaches the same `@` and the same domain. If the start of the run fails,
every later start fails the same way. The JWT pattern follows the same argument
for its header segment. Domain labels cannot contain `.`, so possessive labels
never give back a character the pattern needs.

**Why there are no length bounds.** RFC-style bounds (64 for the local part, 255
for the domain) were considered and rejected. A bound fails open: an address or
token longer than the bound would be partly or fully left in the logs. Real JWT
payloads are routinely longer than 1 KB. Linear time comes from the
consume-then-decide shape, so this layer still fails closed.

## Audit of the other patterns
- **UUID:** fixed width with `\b`, so each attempt is at most 36 characters.
- **`pumperId=` and `pump-logs/`:** a literal prefix followed by a greedy negated
  class with nothing after it, so there is no backtracking.
- **Decoded JWT:** a literal followed by `[^\n]*`.
- **Claims:** a literal key, then `[^"']*` and a quote backreference. Each scan
  stops at the next quote.
- **Bot token:** starts only at the literal `bot`, and digit runs cannot contain
  `bot`.
- **Runtime name pattern (`_name_pattern`):** literal tokens joined by `\s+`.
  Each attempt is bounded by its own whitespace run.

All of these measured under 20 ms on 64 KB adversarial inputs before the change.
Possessive quantifiers were added to the claim and bot-token patterns as
hardening. They give exactly the same output, because each class is disjoint
from the token that follows it.

## Timings for `redact()`, before → after (local, M-series Mac)
| Input | 64 KB | 256 KB |
|---|---|---|
| email characters, no `@` | 2308 ms → 1.0 ms | 37984 ms → 4.7 ms |
| `a.a.` runs | 2357 ms → 1.1 ms | 37921 ms → 4.7 ms |
| `eyJ` repeated | 3143 ms → 0.8 ms | 50954 ms → 3.4 ms |
| `bot1234567` repeated | 2367 ms → 0.9 ms | 37932 ms → 4.1 ms |
| many `@`, no TLD | 2.6 ms → 4.0 ms | 10.3 ms → 15.7 ms |
| realistic log line | 6.5 ms → 1.6 ms | 25.1 ms → 6.4 ms |

## Tests (`tests/test_redaction.py`, +30)
- **Timing bounds:** under 50 ms at 64 KB and under 200 ms at 256 KB, best of 3,
  for each of six adversarial inputs.
- **Equivalence (hypothesis, 2000 examples each):** `_redact_emails` and
  `_redact_jwts` produce byte-identical output to the old patterns, which are kept
  in the test file as oracles.
- **Edge cases:**
  - emails at the start of a line, the end of a line, and next to newlines;
  - an email inside a URL query and as URL userinfo;
  - `mailto:` addresses and plus-addressing;
  - adjacent emails after a partial domain;
  - a 300-character local part and an over-long domain, both fully redacted;
  - hosts with no TLD left untouched;
  - a JWT with a payload of about 1.5 KB.

Full suite: 1015 passed, 42 skipped, 48 deselected.
