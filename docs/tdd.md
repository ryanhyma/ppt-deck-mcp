# Test-driven, by default

The standing rule for this repo (user instruction, 2026-10-26): **write the failing test first**.

Why it is written down: the security work exposed the difference. The auth code was written first and
tested after — the tests did catch three real design errors (scope-**or**-role, subject-before-scope
ordering, and advertising `resource_metadata` in token mode, which made LibreChat stop listing tools), but
each one cost a round trip. The audit log was written test-first: the red run produced the module
boundary, the redaction contract and the rotation semantics in one pass, and the second red run turned up
two genuine ambiguities (whether a credential keeps its `Bearer` label, and how much history rotation is
supposed to retain) that would otherwise have shipped as someone's guess.

## The loop

1. **Red.** Add the test to `tests/`, run it, paste the failure when reporting. A test that has never
   failed has not been shown to test anything.
2. **Green.** Write the smallest implementation that satisfies it. No speculative options.
3. **Refactor.** Tidy with the tests green; the tests are the reason you can.
4. **Commit** with what went red and what the fix was, so the next person knows the defect class.

## What "a test" means here

- **One test per defect class**, named for the failure it prevents (`test_a_graph_token_is_not_an_access_token_for_this_api`),
  not for the function it happens to call.
- **Assert the contract, not the implementation**: status codes, JSONL shape, "no path appears", owners.
- **Offline and fast.** No network, no cloud tenant, no model. Stubs live next to the code they double
  (`entra_stub.py`), fixtures next to the tests (`_env.py`).
- **Pin the negative case** for anything security-relevant: wrong tenant, expired token, someone else's
  deck, tampered signature, missing scope.
- **Behaviour suites are separate.** `tests/run_suite.py` measures the model against 108 cases — that is a
  measurement, not a unit test, and it never gates a commit.

## Before every push: `tests/preflight.sh`

CI cannot be trusted as your first signal. A commit that was green here once went red on GitHub because the
suite assumed a binary that is installed on this box and not on a runner, and a missing dependency was
invisible until a clean install. The gate now reproduces that reality in ~2 minutes:

```sh
tests/preflight.sh          # clean venv from requirements.txt + python 3.12 and 3.13 in containers
tests/preflight.sh --fast   # clean venv only (no docker)
```

It fails on: an undeclared dependency, a test that needs the host, a committed env/key file, a
token-shaped hex string in source, or any suite failure on a CI interpreter. Exit status is the signal —
the script never scrapes the tail of the output (ResourceWarnings print *after* the summary line, which is
exactly how a shell check can report success while the suite failed).

## Where the tests live

| File | Covers | Needs services |
|---|---|---|
| `tests/test_regressions.py` | layout, styling, cache, renderers, validation | no |
| `tests/test_security.py` | path scope, opaque ids, signed URLs, no-path-in-results | no |
| `tests/test_auth.py` | token validation matrix (Entra shapes, mutating stub) | no |
| `tests/test_auth_http.py` | the ASGI gate end to end | no |
| `tests/test_audit.py` | audit format, redaction, rotation, wiring | no |
| `tests/test_deployment_assets.py` | prompts/skills/interpreter drift, installer flags | no |
| `tests/e2e/deck-studio.js` | the pane really renders (Playwright) | yes, opt-in |
