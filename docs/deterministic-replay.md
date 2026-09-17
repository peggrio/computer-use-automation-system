# Step 6: Deterministic replay

## What runs

`automation/replay.py` is a workflow interpreter. It executes the saved capability's ordered steps, conditions and outcome rules through the same browser adapter as discovery. It contains no ParaBank selectors, model calls or workflow-specific action sequence. The CLI defaults to `lookup_transaction` version `0.2.0`, learned in the retained Step 5 run.

The application uses **account IDs and transaction IDs**, rather than member IDs. Both are runtime inputs. The artifact was discovered with account `12345` and transaction `12145`; successful replay was verified with `12345` / `12256` and with a different account and transaction, `12678` / `12367`. These are public synthetic sandbox fixtures.

## Immutable local selection

`capabilities/registry.json` lists reviewed exact capability/profile identities and content hashes, the policy hash and original discovery run ID. The files under `capabilities/lookup_transaction/0.2.0/` are byte-for-byte copies of the generated artifact/profile. Their recorded sequence and provenance were not rewritten for replay.

The registry reader rejects unknown versions, duplicate selections, changed content under an existing version, changed policy and paths outside the capability directory. It never selects `latest`. This is a trusted local registry, not a signed distribution service or a general promotion UI. Discovery evidence remains historically marked pending review; the later registry entry records its selection for this local read-only replay scope.

The reusable Python interpreter can also accept a caller-supplied validated adapter/invocation. Such callers are responsible for selecting trusted artifacts and policy; the provided CLI always uses the registry.

## Run without a model or API key

Only the UI requirements, Chromium and running ParaBank are needed:

```sh
.venv/bin/python -m pip install -r requirements-ui.txt
.venv/bin/python -m playwright install chromium
docker compose up -d
.venv/bin/python -m tools.replay --account 12345 --transaction 12256
.venv/bin/python -m tools.replay --account 12678 --transaction 12367
```

Replay never imports the OpenAI SDK, discovery module or model configuration, and does not read `.env.local`. An import-isolation test explicitly blocks those modules. Login uses the public sandbox defaults, or `PARABANK_USERNAME` and `PARABANK_PASSWORD` from the process environment. No OpenAI API calls or token charges are incurred by replay.

Optional controls:

```sh
.venv/bin/python -m tools.replay \
  --capability lookup_transaction --version 0.2.0 \
  --account 12345 --transaction 12256 \
  --max-duration-ms 120000 --max-actions 40 \
  --evidence-root runs --headed
```

The CLI prints a redacted summary and evidence directory, not raw outputs. The Python API returns a schema-valid `run_result` with typed outputs to its authorized caller:

```python
runner = Replay(adapter)
result = await runner.run(invocation_for(adapter))
if result['status'] == 'succeeded':
    outputs = result['outputs']  # transient; do not put bank values in logs
```

The caller establishes the browser and authenticated entry state first. The invocation pins the capability/profile and binds run ID, session ID, policy ID, typed inputs and limits. It stays in memory. Registry/input errors are rejected before browser execution; the interpreter also validates pins and runtime identity before dispatch.

## Execution order and bounds

For each saved step, replay:

1. Checks cancellation, ownership/epoch, unchanged runtime inputs/artifact pins and action budget.
2. Evaluates all preconditions. False or unknown stops execution; neither is silently accepted.
3. Resolves controls from a fresh observation and dispatches the action through adapter policy checks.
4. Polls postconditions to the step deadline. This includes completed-load readiness, exact input matches and verified IDs.
5. Evaluates business outcomes only after postconditions succeed. One true rule terminates normally; unknown conditions stop, and conflicting true outcomes are a hard failure.
6. After the final step, rechecks final success conditions and validates the complete typed output map.

One step deadline covers all its work. Only wait actions may retry, and attempts divide the remaining deadline rather than resetting it. Click, fill, select and extraction are never automatically retried. The original artifact has one attempt per step; separate tests exercise bounded wait retry semantics. A timeout while a control action is in flight is `indeterminate_action`, which cannot be automatically replayed or bypassed by requesting intervention.

A separate invocation deadline and action budget cover the whole interpreter run. Failure capture can add up to three seconds; bootstrap/login and browser cleanup are outside the interpreter deadline. Cooperative cancellation is checked between operations and checkpoint polls. Calling the same runner twice is rejected; there is no implicit retry/resume.

## Results and recovery classification

Returned results keep the v1 contract unchanged. Persisted summaries add a separate classification without storing output values.

| Classification | Result | Examples and behavior |
| --- | --- | --- |
| Success | `succeeded` with typed outputs | Every checkpoint and output validation passed |
| Business outcome | `business_outcome` with declared code/step | Account unavailable, or transaction absent from the selected account after completed loading |
| Recoverable condition | `failed` in CLI, optionally `paused` for an embedding caller | Session expiry, load timeout, missing target, stale/precondition/checkpoint failure; stop and require deliberate recovery |
| Hard failure | `failed` | Invalid input/pins, policy denial, ambiguous target, permission denial, app error, invalid output, exhausted limit, lost session, evidence failure or uncertain action |

A transaction that exists in a different account returns `transaction_not_found_in_account`, not a globally nonexistent claim. HTTP/load errors never become empty-result business outcomes. A slow successful load simply completes within its deadline.

`on_failure: intervene` is honored only for eligible recoverable conditions when `Replay(adapter, allow_pause=True)` is used by a caller that retains the live browser. It transfers ownership to human, increments the control epoch and returns a schema-valid intervention request for the same session. `on_failure: fail` and hard failures never pause. This foundation does not implement operator authentication, a handoff UI, manual-action capture or resume; those remain Step 7.

The CLI uses `allow_pause=False` and closes its browser. It reports recoverable conditions accurately without claiming a live paused session remains available. CLI exit codes are 0 for success/business outcomes, 3 for recoverable conditions and 1 for hard/preflight/setup failures.

## Evidence and acceptance checks

Each replay run persists:

- Exact artifact/profile/policy hashes and a replay manifest with zero model calls.
- Ordered step/action/checkpoint events containing safe identifiers and tri-state results, never expected/observed bank values.
- The adapter's existing action/safety logs and redacted failure snapshots.
- `replay-result.json`, a `replay_summary` with result classification, output field names, action count and elapsed time. It is intentionally not a serialized successful `run_result`, since output values are omitted.

The acceptance harness adds a labeled fixture and boolean assertions comparing transient outputs with synthetic expected values. It never saves the invocation, raw outputs, credentials or API key. Session expiry is triggered by UI logout; delay, HTTP 500 and download-control policy cases are explicitly labeled injections.

```sh
.venv/bin/python -m tools.replay_demo --evidence-root runs
.venv/bin/python -m unittest discover -s tests -v
```

The retained [acceptance evidence](../evidence/replay/README.md) covers changed inputs, a second valid account, cross-account rejection, absent records, slow loading, session expiry, load failure and policy denial. Replay tests additionally cover stale pins/inputs, limits, cancellation, bounded wait retries, indeterminate actions, ownership transfer, output redaction and checkpoint failure after extraction.
