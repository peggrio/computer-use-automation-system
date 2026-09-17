# Step 3: UI execution foundation

## What is implemented

`automation/browser.py` provides one asynchronous Python Playwright adapter. Discovery and replay will call this interface instead of calling Playwright themselves. The browser runs in a fresh Chromium context owned by this project, separate from the developer's personal browser sessions. No API key is required.

| Operation | Behavior |
| --- | --- |
| `observe()` | Transient accessibility-oriented DOM snapshot with session ID, document generation and content signature; never persisted automatically |
| `resolve(target, observation)` | Profile-based targeting; rejects observations from another session or an obsolete UI state; returns explicit match count |
| `perform(action, resolution, epoch=...)` | Click, fill, select, wait or extract; checks current ownership, target uniqueness and the configured sandbox action policy |
| `read(target)` | Reads exactly one visible control's text; refuses password fields |
| `evaluate(condition)` | True/false, or `None` for an incomplete-load count; execution errors remain typed errors |
| `wait(condition, timeout_ms=...)` | Bounded condition polling; no blind sleep as a completion signal |
| `set_owner(owner)` | Serialized ownership/epoch gate; blocks stale queued automation commands |
| `capture_failure()` | Captures a redacted structural DOM snapshot; raw screenshots/traces remain disabled |

Actions and conditions are validated against Step 2's schema. Output extraction uses its strict transforms and validates all declared output fields. Adapter errors are safe codes; raw Playwright messages are suppressed. `UIError.result_code` maps adapter-specific diagnostics to the frozen v1 result vocabulary.

The method signatures refine the design pseudocode: session identity is attached to the adapter and each observation. Policy is a trusted adapter dependency, not an LLM-supplied permit. Condition evaluation observes live state; target resolution carries a snapshot signature and actions resolve again. The complete ownership-transfer/operator protocol remains Step 7 work.

## Profile and legacy UI challenges

`profiles/parabank_local_browser.json` binds all 18 logical targets for the pinned sandbox. It uses role/name where unambiguous and existing CSS identifiers where necessary. There are no added test IDs.

- Four search buttons have the identical accessible name. `button#findById` identifies the ID-search action.
- Unlabeled fields use existing IDs, including `#transactionId` and `select#accountId` within the search form.
- Account/transaction links use exact parsed query comparisons; input values never become selector fragments. Duplicate or malformed query keys are rejected.
- Transaction detail values are found relative to their labeled table row, rather than by an overall row number.
- Candidate fallbacks are ordered; multiple matches never cause a fallback to a more convenient target. Actions require exactly one match.
- Before dispatch the adapter captures a handle to the selected node. Detachment causes an explicit error rather than silently selecting a replacement node by index.

The current adapter supports role and CSS strategies only. Desktop accessibility IDs and frame contexts are deliberately unsupported and fail preflight. This is a DOM/accessibility-based browser implementation, not screenshot/OS automation.

## Readiness and negative results

The adapter passively observes metadata for requests made by ParaBank's own frontend. It never calls banking APIs, reads response bodies or substitutes network data for the UI. Readiness rules combine completion/status of the appropriate request in the current document generation with the rendered UI:

| View | Completion evidence |
| --- | --- |
| Overview | Customer-account request completed successfully; the total row appended after account rendering exists |
| Account activity | Both account-details and all-transactions requests completed; requested account is displayed; transaction rows or the explicit no-transactions state is visible |
| Search form | Server-rendered document completed, form is visible and account options exist |
| Search results | The transaction request after the current submission completed; result view is visible and form hidden; an actual 404 can produce a completed empty result |
| Details | Server-rendered document completed; heading and requested transaction ID match |

A delayed transaction response cannot masquerade as an empty account. Count predicates in a readiness-bound scope return unknown until that view is ready. Request failure/HTTP error and visible application errors stop the run. A timeout is `load_timeout`, not a business outcome.

This app-specific readiness strategy depends on the inspected ParaBank release. A different frontend or desktop application needs different registered rules, not renamed selectors alone. The Compose digest pins the tested image; the browser does not cryptographically attest the server's deployed version.

## Account scope and dates

Inspection of the pinned app confirmed that Find by Transaction ID constructs a global transaction lookup and ignores the selected account. The smoke scenario checks completed account activity before searching and will not report success for a transaction absent from that account.

The date discrepancy is explained by two different formatting paths: list/activity JavaScript uses the browser's local timezone, while the detail page is formatted server-side. The container timezone is UTC. The adapter defaults its browser timezone to UTC for reproducibility and takes output dates from the detail page. A separate smoke run also succeeds with `America/Los_Angeles`; it does not assert that the two date displays agree. No artificial one-day correction is applied.

## Run locally

From the repository root:

```sh
docker compose up -d
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-ui.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m tools.ui_smoke --headed
```

The walkthrough uses the upstream public sample login by default. Optional `PARABANK_USERNAME` and `PARABANK_PASSWORD` environment variables can supply other synthetic sandbox accounts. Do not place credentials in capability artifacts. Login bootstrap also handles an already-authenticated session.

The default scenario selects account `12345`, verifies transaction `12145` belongs to it, searches for that ID, opens the result and extracts its details. To exercise different inputs:

```sh
.venv/bin/python -m tools.ui_smoke --transaction 12256
.venv/bin/python -m tools.ui_smoke --transaction 999999999
```

Only a safe status and output field names are printed. Extracted values remain in memory. The browser closes when the check ends; `--headed` lets you watch the actions but is not an operator handoff console.

## Validation and boundaries

```sh
.venv/bin/python tools/validate_contracts.py profiles/parabank_local_browser.json --capability examples/lookup_transaction.capability.json
.venv/bin/python -m unittest discover -s tests -v
```

The full suite includes real Chromium/ParaBank tests and needs Docker running. For contract-only checks, use `-p 'test_contracts.py'` with unittest discovery.

The integration tests cover two successful transaction inputs, missing records/accounts, delayed and failed loads, bounded timeout, repeated/duplicated controls, stale/cross-session observations, malformed query keys, ownership epochs, explicit session expiry and a denied write-link substitution. Test-only DOM mutations and injected HTTP failures are limited to isolated local browser contexts and are not native ParaBank behavior. They do not modify the container's stored banking records.

`tools/ui_smoke.py` is a fixed adapter test scenario, not the production artifact replay engine and not LLM discovery evidence. Full replay execution/result envelopes, LLM decisions and human takeover are later steps; persistence/redaction is now implemented in Step 4. Step 4 adds configurable action/network policy, per-hop redirect checks, popup/WebSocket blocking, structured logs and redacted failure snapshots. See [safety and evidence](safety-and-evidence.md) for its trust boundaries. Raw screenshots and traces remain disabled.
