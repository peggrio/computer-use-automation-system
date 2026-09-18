# System contracts, version 1.0.0

Status: executable validation, browser adapter, genuine discovery and deterministic replay are implemented through Step 8, including local same-session takeover and complete-flow acceptance. See [replay semantics](deterministic-replay.md) for the current interpreter and recovery boundary. The ParaBank design example remains authored, not recorded by an LLM; the genuine generated artifact is separately pinned in the local capability registry. Nothing in `examples/` is submission evidence.

## 1. Boundaries

| Component | Receives | Returns | Responsibility |
| --- | --- | --- | --- |
| Discovery | Goal, session, policy, bounded action budget | Recorded actions and verified observations | Model chooses actions through the same policy-gated adapter used by replay |
| Capability compiler | Successful discovery record and input/output declarations | Draft capability JSON | Parameterize values, identify controls, define checkpoints; never merely save a transcript |
| Validator | Capability, profile, invocation or result | Valid or explicit contract error | Structural validation and cross-reference/type checks, without browser or LLM access |
| Replay executor | Pinned capability + profile, invocation, trusted session/policy | Structured result | Execute explicit steps, enforce limits, check outcomes and outputs; no model decisions |
| Surface adapter | Logical target + profile + typed values | Observation, resolution, action receipt | Translate targeting and actions into UI-specific mechanisms |
| Session controller | Session ID and current control epoch | Exclusive automation/operator ownership | Pause, transfer, record manual actions, revalidate on resume |
| Evidence sink | Events and observations | Redacted evidence references | Enforce redaction before persistence; raw observations stay transient |

The workflow refers to `transaction_input`; a profile can map it to the browser's `#transactionId`. A desktop profile could use an accessibility ID. The executor does not know what CSS is. Business values are supplied through typed references, never interpolated into scripts or selectors.

## 2. Four serialized contracts

The normative structure is `contracts/schema.json`, a strict JSON Schema Draft 2020-12 document. `tools/validate_contracts.py` adds semantic checks JSON Schema alone cannot express. Unknown fields and unsupported schema versions are rejected.

### Capability

- `schema_version`: serialization/interpreter version, currently exactly `1.0.0`.
- `id`, `version`: reusable capability identity and immutable semantic version.
- `provenance`: `design_example` with no run ID, or `llm_discovery` with an evidence run ID. An evidence reference is not proof by itself; evidence must be verified before promotion.
- `application`: product family and documented compatibility boundary.
- `requires_session`: authenticated or anonymous. Login values never appear in this artifact.
- `effect`: declared read-only, reversible write, or irreversible write. This declaration cannot grant permissions.
- `inputs`, `outputs`: closed maps of required typed fields, descriptions and sensitivity classifications.
- `targets`: logical view/control/data identifiers and expected cardinality.
- `steps`: ordered actions with preconditions, postconditions, bounded waits, outcome rules and failure routing.
- `known_outcomes`: named business outcomes with explicit meanings.
- `success_conditions`: mandatory final assertions, in addition to validating every output.

All fields are required in v1. Types are nonempty string, digit-string identifier, decimal string with exactly two fractional digits, ISO calendar date, integer, boolean, and string enum. Money is never a binary floating-point value. Optional/nested/array outputs are deferred until a workflow needs them; extending this subset requires a schema version change.

Every output has exactly one producer in the final `extract` step. Sources are a UI target, an input reference, or an explicitly declared literal. Built-in transforms are `text` (trim surrounding whitespace only), `date_mm_dd_yyyy` (strict calendar conversion without timezone shifts), `usd_amount` (strict US monetary parsing to a two-decimal string), and `credit_debit` (trim/case normalize then require credit/debit). Invalid or ambiguous parsing fails. Transform names are registry identifiers, never executable expressions. Raw UI values remain transient.

Example reference:

```json
{"type":"fill","target":"transaction_input","value":{"input":"transaction_id"}}
```

### Surface profile

Pins a capability ID/version, application family/release, adapter name and adapter contract version. Binds every logical target exactly once, including ordered locator candidates, optional parent scope, typed filters, and a robustness explanation.

Supported locator descriptions in this contract are role/name, static CSS, and accessibility ID. This describes a format, not implemented support: an adapter must advertise supported strategies and reject the rest before any action. Scoped matching applies inside the resolved parent. Scope cycles are invalid.

Filters support exact text equality and exact decoded link-query equality. Input values are compared as data. The latter allows an account/transaction link to match an ID without embedding that ID in a CSS string. URL parsing must reject malformed or duplicate query keys, and destination policy is checked separately.

Candidates are tried in declared order. Zero matches permits the next candidate. Ambiguous matches stop; never fall back merely to find a convenient single element. Control actions require exactly one visible, actionable match even when a target allows multiple results for counting. A successful count check is not a reusable element handle; actions resolve again immediately before use. Stale resolution triggers re-observation, not an unchecked click.

Readiness maps each required view to a named, versioned adapter rule. Those rules must establish that the current navigation/data-load generation has completed successfully. A visible heading, an arbitrary delay, network inactivity alone, or an empty table alone is insufficient. The profile cannot contain JavaScript or Python to implement a rule. Trusted adapter code provides the rule; unknown rules fail preflight. Binding scopes and readiness rules must be reviewed together so a negative check concerns data loaded by that view.

Step 2 contract tests use a deliberately synthetic profile. Step 3 adds `profiles/parabank_local_browser.json` and real load-completion rules; see [UI foundation](ui-foundation.md).

### Invocation

Contains run ID; exact capability and profile ID/version/content SHA-256; session ID; a reference to trusted policy; typed inputs; duration/action limits. Session configuration holds the actual local entry point, tenant/environment and runtime authentication, outside the portable workflow.

Hashing v1 uses UTF-8-compatible ASCII bytes from JSON serialized with sorted keys, compact separators, ASCII escaping and no NaN/Infinity (`digest()` is the reference implementation). It hashes the complete parsed artifact/profile, not a pretty-printed file. Cross-language implementations must reproduce this encoding. Hashes pin content; they are not signatures or authorization.

Input values and invocation files may be sensitive. Never persist the full invocation by default. Committed examples use synthetic data only. Opaque session/policy IDs refer to trusted runtime objects; they are not authentication tokens.

### Run result

| Status | Payload | Meaning |
| --- | --- | --- |
| `succeeded` | Declared typed outputs | Final checkpoints passed and every output validated |
| `business_outcome` | Declared code and originating step | Legitimate terminal answer; not an exception |
| `failed` | Error code, step (or null for preflight), redacted expected/observed state, evidence references | No safe automatic continuation |
| `paused` | Error plus intervention request/session/owner/epoch/resume step | Nonterminal: same live session is waiting for human control |

Payloads are mutually exclusive. Results pin the exact capability/profile. Output data may be returned to the authorized caller, but must be redacted in persisted evidence. Structural validation cannot prove a run really happened: the executor must correlate run/session identity, inputs, action receipts and verified checkpoints.

The error taxonomy includes invalid input, denied policy, missing/ambiguous target, failed precondition/checkpoint, expired session, permission denial, load timeout, app error, unsupported adapter, invalid output, exceeded limit and indeterminate action completion. Recovery is execution behavior, not a business outcome or a false success.

## 3. Execution semantics

Preflight validates all contracts, content pins, application compatibility, strategy/readiness support, session ownership and effective permissions. Establish the authenticated entry session before running the capability; the first example expects Accounts Overview. Re-entry after manual login must restore/revalidate that entry state.

Each step executes in this order:

1. Check cancellation, run/action budgets, live session ownership and control epoch. Detect recognized app errors, permission denial or expired authentication before normal success checks.
2. Evaluate `requires` against current observations. Conditions return true, false or unknown; unknown/error never means false for a business outcome.
3. Ask trusted policy to authorize the resolved action and its actual destination. Execute the action once. Navigation and redirects are checked by the browser adapter, including navigation triggered by clicks. Recheck ownership immediately before dispatch.
4. Wait up to the step deadline for all `ensures` conditions. `wait` is observation-only. Polling is bounded by the same overall step deadline across all retry attempts; retries do not reset the run deadline. Only waits can retry in v1. All click/fill/select/extract actions have one attempt.
5. Only after `ensures` succeeds, evaluate business-outcome conditions. Exactly one matching rule terminates with that outcome; multiple matching rules are a contract/runtime error. No matching rule continues to the next step.
6. On failure, honor `on_failure` for ordinary execution errors: fail or request intervention. A policy denial or uncertain write cannot be bypassed by the artifact or operator resume. When every step completes, assert final success conditions and validate outputs before reporting success.

`ready` is a load-completion predicate; `visible` is only visibility. `count_equals` counts visible matches after a relevant completed load, and `text_equals`/`value_equals` perform exact normalized string comparisons. Comparisons cannot invoke a model. A condition timeout does not prove an element is absent.

These rules are deterministic control decisions, not a promise that a changing application produces identical data forever. There are no loops, arbitrary code, open-ended branching or automatic LLM recovery in v1. Known business outcomes terminate early. Unexpected states fail or pause.

## 4. Adapter and control-transfer interface

The following is the design-level interface. Step 3 implements the browser operations with the signature refinements documented in [UI foundation](ui-foundation.md); Step 4 implements safe failure evidence; Step 7 implements local operator handoff as documented in [live takeover](live-takeover.md):

| Method | Required behavior |
| --- | --- |
| `describe()` | Advertise adapter contract version, locator strategies, actions and readiness-rule versions |
| `observe(session_id)` | Return transient UI observations with a monotonically changing observation generation; do not persist raw content |
| `resolve(target, profile, inputs, observation)` | Return zero/one/many candidates scoped to this observation; ambiguity remains explicit |
| `evaluate(condition, observation)` | Return true/false/unknown plus redacted diagnostics and observation generation |
| `perform(action, resolution, control_token, policy_permit)` | Check fresh ownership and policy, dispatch once, return dispatched/completed/indeterminate receipt |
| `capture_failure(session_id)` | Return redacted screenshot/snapshot reference; suppress capture if safe redaction cannot be established |

The session controller owns `automation -> pausing -> human -> resuming -> automation` transitions. A pause request drains/cancels pending actions before publishing owner=human and increments `control_epoch`; stale queued commands must be rejected. If an action may already have executed, report indeterminate completion and reconcile UI state rather than repeating it.

The intervention payload retains the same session ID and identifies the goal/capability via the run record, failed step, reason and safe evidence. The operator acts in that live session. Record manual action metadata with sensitive text redacted, including login actions. The implemented resume call carries request/session ID and expected epoch; the terminal offers resume after revalidation or abort, without trusting a manual-completion claim. The runtime checks these against the pending request, regains exclusive control, and verifies applicable checkpoints. It must not skip steps solely because the operator says they are done. Session loss fails the run; it cannot silently substitute a new browser.

The local controller implements ownership locking, terminal resume/abort transport, and redacted event persistence. Remote operator authentication remains future work.

## 5. Policy and privacy

Trusted environment policy is outside artifacts and profiles. Effective permissions are the intersection of environment/session permissions and the capability's allowed scope, never whatever the LLM declares. The read-only ParaBank capability can navigate approved pages and submit search forms; a generic click is not inherently read-only. Transfers, account creation, payments, profile updates and external links remain blocked by resolved control/destination checks.

The schema rejects undeclared executable fields but is not a secret detector or a security sandbox. Compiler review, conservative redaction, evidence sanitization and runtime policy enforcement are all necessary. Field sensitivity is metadata, not permission to transmit data. Even synthetic credential entry should use the same redaction path as real secrets. Screenshots, locators, free-text descriptions and errors can contain sensitive data; all require review before persistence or export. Do not persist raw model transcripts.

## 6. Versioning and reuse

Capability versions are immutable; bump major for incompatible input/output meaning, minor for compatible additions or flow changes, patch for fixes that preserve the contract. Profiles have separate immutable versions. A profile targets an exact capability version; an invocation pins both content hashes. Never silently migrate old artifacts or select `latest` during replay.

Tenant reuse uses the same capability with independently versioned, reviewed profiles for different app releases/branding. Environment configuration supplies entry points and authentication. Materialize overrides into a complete profile and validate/hash it, rather than applying invisible runtime patches. Verify compatibility and readiness at startup; mismatches pause/fail rather than trigger exploratory clicks. Changes to workflow meaning need a new capability version, not a locator override.

The browser implementation is first. Accessibility IDs leave a path to desktop adapters. Frames require explicit frame-context support in a later adapter/profile version; screenshot/OCR targeting likewise needs a versioned strategy with confidence/ambiguity rules. None is claimed to work merely because the contracts are separated.

## 7. ParaBank decisions and remaining work

- Inputs: account ID and transaction ID. Authentication is session setup, not stored business input.
- Account membership is established from completed account activity before searching globally by transaction ID.
- `transaction_not_found_in_account` means absent from that account only. It does not distinguish globally nonexistent from present elsewhere.
- If membership was established but the subsequent search is empty, treat that as a checkpoint failure or inconsistent/changing data, not a new not-found business outcome.
- The detail page is the source for output date. Investigate the observed list/detail date discrepancy before writing fixed date expectations.
- Step 3 validates the membership checkpoint, second-input fixture, readiness rules and control identifiers using real browser tests. Real model evidence is retained under `evidence/discovery/`, with replay and acceptance evidence alongside it.

## Validation

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-contracts.txt
.venv/bin/python tools/validate_contracts.py examples/lookup_transaction.capability.json
.venv/bin/python -m unittest discover -s tests -p 'test_contracts.py' -v
```

Profiles require `--capability FILE`; invocations and results require both `--capability FILE --profile FILE`. Validation does not execute the UI, authenticate to a service, enforce policy or approve an artifact for use.

Specification references: [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12) and [python-jsonschema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/).

## Step 4 implementation

See [safety and evidence](safety-and-evidence.md) for the configured environment policy, structured event sink, redacted failure snapshots and reviewed-contract artifact export boundary. Sanitized candidates are wrapped as non-executable drafts requiring a new version and review.
