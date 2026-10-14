# 2026-10-14 — CI log gate: default-deny output for public Actions logs

## Why

`asht0nmb/t1d-optimizer` is a public repo. On a public repo, any signed-in
GitHub user can read the GitHub Actions logs, and GitHub has no setting that
makes the logs private while the code stays public. Until today the nightly
Tandem sync printed the owner's name, Tandem account IDs, JWT claims, pump
serials and device UUIDs into those logs.

## Three layers

1. **Source redaction (PR #19, merged).**
   - `ingestion/redaction.py` scrubs identifiers in-process, using the
     formatter, a filter, the record factory and the excepthook.
   - The `tconnectsync` logger is held at WARNING, which removes the
     `Decoded JWT: {...}` line at its origin.
   - Runtime identifiers are emitted as `::add-mask::` so the runner masks
     them as well.
   - Two Smoke runs on `main` scanned clean.
   - Weakness: this layer only works if every library writes through Python
     `logging` the way we expect. A new `print()`, a C-extension write to
     stderr, an HTTP debug dump, or a logger configured with its own handler
     would get past it.

2. **Output gate (this PR).**
   - `scripts/ci_log_gate.py` wraps the Python step in
     `tandem-nightly-sync.yml`, `test-tandem-sync.yml` and
     `meal-rise-cron.yml`. `tests.yml` is deliberately not wrapped.
   - It is default-deny. The command's merged stdout and stderr are streamed
     line by line, and only three kinds of line reach the log:
     - **Our own log lines.** These must match one of the two formats our
       entrypoints configure and come from an allowlisted logger:
       - `__main__`
       - `ci_tandem_smoke`
       - `meal_rise_cron`
       - `ingestion.*`, `core.*`, `scripts.*` and `apps.*`, matched on a
         dot boundary

       Each passed line is run through `redaction.redact()` a second time
       and stripped of control characters.
     - **`::add-mask::` lines.** These pass through byte for byte. The gate
       also registers the value with its own redactor, because the child's
       registered secrets live in a different process.
     - **One `[gate] ExceptionType (message suppressed; reproduce locally
       for details)` line per Python traceback.**
       - The exception message is never printed. Names and serials are only
         add-masked in CI after login, so an exception raised earlier would
         otherwise print them verbatim.
       - A line only counts as the terminal exception line when it is inside
         an active traceback and its last component is a dotted identifier
         with an exception-like suffix, such as `Error`, `Exception`,
         `Warning` or `Exit`.
       - Anything else, such as an interleaved `Word: text` line or a
         traceback cut off at EOF, produces a text-free
         `[gate] traceback suppressed (...)` notice instead.
   - Lines are bounded:
     - The reader buffers at most 64 KB per line. Anything longer is
       discarded whole and counted.
     - Allowlisted lines over 8 KB are dropped rather than redacted, because
       `redact()`'s email pattern is quadratic on long runs. Truncating
       instead could split an identifier and defeat its pattern.
   - Everything else is dropped and counted, then reported at the end as
     `[gate] suppressed N lines from non-allowlisted sources`. That includes
     `tconnectsync.*`, `urllib3`, `py.warnings`, the root logger, bare
     prints, HTTP dumps and every other `::workflow command::`. A third-party
     `::warning::` would otherwise become a public annotation, and
     `::stop-commands::` would switch off mask processing.
   - The exit code is preserved, and a child killed by a signal is re-raised
     as that signal, so the `if: failure()` Telegram step still fires.
   - It fails closed. If the gate crashes, it stops emitting, drains and
     waits for the child, and exits non-zero.
   - It needs no network and no secrets. It loads `ingestion/redaction.py`
     by file path, which is stdlib-only, and does not import the ingestion
     package.

3. **7-day log retention (already set).** This shortens the exposure
   window, but it is **not a privacy control for static identity data**:
   - A name, an account UUID or a pump serial does not change. If it is
     scraped once, at any point in those 7 days, it is gone for good.
     Retention limits how long the data is exposed, not whether it was
     exposed.
   - Public logs can be fetched by anyone through the API while they exist,
     and automated scrapers are fast.
   - Retention therefore only reduces the amount of old data to clean up
     after a leak. Layers 1 and 2 are the actual controls.

## `::add-mask::` through a wrapper

- The GitHub docs say workflow commands are sent to the runner "over
  `stdout`", and that a masked value "is treated as a secret and will be
  redacted on the runner". They do not say whether the command line itself is
  echoed.
- From the `actions/runner` source, in
  `src/Runner.Worker/ActionCommandManager.cs`:
  - `OutputManager.OnDataReceived` passes each output line to
    `TryProcessCommand`. A parsed command is consumed rather than written to
    the log.
  - The line is only echoed when
    `context.EchoOnActionCommand && !extension.OmitEcho`.
  - `AddMaskCommandExtension` declares `OmitEcho => true`, so an add-mask
    line is never echoed, even with `::echo::on`. Its value goes to the
    secret masker.
- The runner only sees the gate's stdout, so the requirements are:
  - the line reaches stdout exactly as emitted, starting with `::add-mask::`;
  - it arrives before any later line that contains the value.
- Streaming line by line, in order, without modification satisfies both.
- Empirical evidence:
  - PR #19's Smoke runs emitted raw add-mask lines and the full-log scan found
    zero raw values.
  - This PR's Smoke run, wrapped by the gate, was scanned the same way. See
    the PR body for that result.

## What is now hidden, and how to see it

The public log no longer shows:

- traceback frames and exception messages (only the exception type remains);
- `tconnectsync` WARNING lines, such as HTTP errors and retries;
- `urllib3` retry notices;
- Python warnings;
- the `version_guard` staleness print;
- the smoke script's `print()` summary box. Its contents are already in the
  `ci_tandem_smoke` logger lines.

To debug, run the same command locally, without the gate, using `.env`:

```bash
uv run python -m scripts.ci_tandem_smoke
uv run python scripts/sync_tandem_to_supabase.py   # writes to production DB
```

There is deliberately **no** environment-variable bypass for the gate in CI.
On a public repo, a bypass is one careless dispatch away from a leak.

## Remaining options if this is not enough

GitHub offers no private logs on a public repo. The only ways to get the
scheduled jobs' logs out of public view are:

- **Make the repo private.** Logs become private, but the code is no longer
  public.
- **Use a private companion repo for scheduled jobs.** The public repo keeps
  the code. A private repo checks it out (or pins a release) and runs the
  nightly sync, meal-rise and smoke workflows with the secrets, so its logs
  are private. Secrets move to the private repo.

## Tests

- `tests/test_ci_log_gate.py` has 51 tests. Every test runs the gate by file
  path, from a non-repo cwd, around a real child process. The log lines come
  from the real `configure_logging` and `basicConfig` calls in each
  entrypoint's format.
- They cover:
  - allowlist pass-through and redaction;
  - dot-boundary matching;
  - third-party, warnings, prints, HTTP dumps and `Decoded JWT` lines
    suppressed;
  - other workflow commands suppressed;
  - `::add-mask::` passed through raw;
  - single and chained tracebacks collapsing to one line;
  - truncated tracebacks;
  - exception messages never printed, even when unmasked;
  - name-like lines inside a traceback not mistaken for exceptions;
  - over-long lines (over 8 KB and over 64 KB) discarded;
  - exit codes 0, 1 and 2, and signal death;
  - binary garbage;
  - streaming before the child exits;
  - the suppressed-count summary;
  - fail-closed behaviour on an internal error and on an unlaunchable
    command.
- Full suite: 1036 passed, 42 skipped, 48 deselected.
