# Safety and evidence collection

## Implemented boundary

`policies/parabank-read-only.json` is trusted environment configuration, independent of the capability and surface profile. It lists permitted origins, HTTP methods, route patterns, query parameters, operations, concrete control identities and an action budget. The adapter uses one selected origin per session. The current policy permits read-only banking actions plus the explicit local login bootstrap; risky or unlisted actions are blocked, not automatically approved.

Every public adapter operation is policy-checked and audited. Before an action is dispatched, the adapter verifies live ownership, the resolved element's identity, its actual link/form destination, the permitted input reference, and its current view. An artifact's claim that an operation is read-only cannot override the environment policy. Account and transaction links must carry the requested input ID. Rebinding the search target to a Transfer Funds link is rejected before clicking. An altered form destination is rejected before typing.

Login is a separate, credential-aware bootstrap: its actual form must submit by POST to the configured local login endpoint before credential entry. Login POST requests are permitted during that bootstrap, or temporarily during an active human-owned session-expiry recovery (Step 7). The existing ParaBank URL-rewritten session suffix is recognized without persisting it.

## Network enforcement

Two browser layers enforce the destination boundary:

1. Playwright context routing checks initial requests and rejects child frames, popup requests and unlisted methods/routes/query parameters.
2. Chromium CDP request-stage interception checks every request again, including every redirect hop. Redirect chains cannot escape the initial allowlist.

Requests are continued through the browser's normal transport. The adapter does not make separate banking API calls, read response bodies or extract data from network responses. This preserves the UI-only execution model.

WebSocket connections, service workers, downloads and extra browsing contexts are blocked. A network block leaves the session faulted; callers must not clear it and retry blindly. No arbitrary URL, script evaluation or file upload operation is exposed to a future model through the action schema.

The redirect test uses a local server with two allowed URLs followed by a forbidden URL and verifies the forbidden handler receives zero requests. Popup and WebSocket tests likewise check the local server never receives the blocked request. These are deliberate test fixtures, not tests against real third-party destinations.

The boundary trusts the Python runtime, installed adapter code, policy configuration and reviewed application profile. It is not an OS/process sandbox against malicious local code or a compromised Chromium binary. Production isolation would additionally need network/process controls and a hardened target application. Browser evidence also does not establish banking authorization beyond the UI/account checks in this synthetic app.

## Evidence written before execution

Every adapter instance creates a UUID-named directory under ignored `runs/` by default. `EvidenceWriter` writes:

- `manifest.json`: source, run ID and hashes of the capability, profile and policy; no invocation values or session credentials.
- `events.jsonl`: ordered timestamps, operation/target identifiers, safe reason/error codes and control epochs.
- `failure-*.json`: a redacted structural DOM snapshot when an operation fails and the session is available.
- Optional `capability-draft-*.json`: a sanitized, non-executable candidate for human review.

An operation's start record is flushed before the action occurs. If evidence cannot be written, execution fails closed. A write failure after dispatch is reported as an evidence failure and must not cause an automatic replay of the action. Files have mode 0600 and run directories mode 0700. The writer refuses symlink files and never silently falls back to raw output.

The event API accepts only a fixed set of fields and code values. It does not accept arbitrary messages, model rationale, exceptions, URLs, headers, request/response bodies, field values, extracted outputs or raw observations. Unrecognized targets/codes are replaced with safe placeholders. The CLI also suppresses raw validation/driver errors. The run-finished event means the browser session closed, not that the task succeeded; use operation events and the caller's structured result to interpret the run.

## Failure evidence and redaction

The DOM projection removes data inside the browser before persistence. It retains tag hierarchy, approved semantic roles, visibility and enabled/disabled states. It replaces every text node and form value with `[REDACTED]`. It omits all other attributes, including IDs, classes, names, URLs, titles and accessible labels; image/canvas/SVG/frame contents are omitted. The writer independently validates this closed shape before saving it.

This supplies a richer failure signal than a log alone: reviewers can inspect the page structure, control presence and state without receiving displayed bank data. The trade-off is deliberate: saved snapshots cannot show exact account names, balances or server messages. The operator will use the live session for that context in Step 7. If the page is gone, capture is explicitly reported as unavailable.

Raw screenshots, videos, HAR files, browser storage and Playwright traces are not saved. Masking selected inputs in a screenshot would leave unknown sensitive text and images visible, so this implementation uses the structural snapshot instead. Raw observations and returned outputs remain transient in memory for the authorized execution caller; future LLM calls still need their own data-minimization boundary before transmission.

Tests inject an email-like value, SSN-like value, token, names and account/transaction numbers into visible text and attributes. They assert these values do not appear anywhere in the saved run, while a useful redacted table/control structure is retained.

## Artifact export

Saving an arbitrary model transcript or dictionary is not supported. `export_capability(candidate, trusted_contract)` validates the candidate, requires its business contract/targets/application metadata to match reviewed definitions, uses trusted name/description/provenance, replaces model step prose and identifiers, and rejects literals other than the currently approved fixed currency `USD`. Runtime account/transaction values must use typed input references.

The saved envelope has `format: sanitized_capability_candidate_v1` and `requires_new_version_and_review: true`. It deliberately cannot be passed directly to the executable capability validator. Step 5's compiler must allocate a new version, attach genuine discovery provenance and pass validation/review before promotion. This prevents sanitization from silently rewriting an immutable deployed version.

The reviewed metadata and permitted constants must be extended deliberately for a new capability. This is an explicit trusted-contract boundary, not a claim that regexes can detect every possible person's name. Raw model-authored metadata that changes that contract is rejected before writing.

## Reproduce the evidence

From the repository root, with Docker running and UI dependencies installed:

```sh
.venv/bin/python -m tools._ui --export-draft
.venv/bin/python -m tools._ui --inject-failure
```

The second command injects a local activity-response failure and intentionally exits with code 1. Both commands print the generated evidence directory. To create a reviewable bundle in a chosen location, use `--evidence-root evidence/safety-demo`.

The included example location `evidence/safety-demo/` contains one successful scripted adapter check and one injected failure. The manifests explicitly state these contain no LLM discovery. They are Step 4 evidence examples, not substitutes for the assignment's required discovery/replay runs.

Run the full test suite:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Steps 5–7 now add genuine model evidence, replay, and [local human takeover](live-takeover.md). Human ownership does not make a blocked financial action permitted. The only additional manual allowance is the existing local login POST during eligible session-expiry recovery.
