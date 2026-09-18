# Live human intervention

## Run the interactive demonstration

Start ParaBank, then run this command in a local terminal:

```sh
.venv/bin/python -m tools.takeover --demo-expiry --operator-timeout 300
```

The command opens its own visible Chromium browser, logs in, deliberately logs out through the UI, and starts replay. The resulting session-expiry condition pauses replay. The terminal prints the capability, failed step/action, phase, safe expected/observed context, evidence references, session/request IDs, control epoch and remaining action budget.

1. When the terminal says **Human owns the browser**, use that existing Chromium window.
2. Log in through its form with the public synthetic sandbox credentials `john` / `demo`. Never enter credentials into the terminal command prompt.
3. Return to the Accounts Overview page in that same tab.
4. Type `resume` in the terminal to explicitly return control, or `abort` to stop.

Without `--demo-expiry`, the same command performs ordinary replay and offers takeover only if an eligible condition occurs. The demo flag is an explicit fixture, not a claim of spontaneous authentication failure. `--account`, `--transaction`, `--max-actions`, `--max-duration-ms` and `--evidence-root` have the replay meanings. This command always uses a headed browser; it does not need a model or API key.

## Ownership and return protocol

`Replay` stops dispatching before it returns a paused result. The browser adapter transfers ownership under the same lock used for action dispatch and increments a control epoch. The ownership audit is written before the new owner is published. Old queued automation commands cannot use the previous epoch. Automation `perform` and `login` calls are rejected while owner is human.

`Takeover` activates recording and returns a ticket bound to the pending intervention, the existing session ID and its exact epoch. Resume requests must match all three. Foreign, stale and duplicate tickets are rejected. Return requests are serialized; concurrent requests cannot execute replay twice.

On explicit return, the recorder seals page input and drains its pending callbacks. The runtime rechecks the session, pending ticket, ownership, unchanged inputs and artifact/profile pins, policy faults, remaining budgets and the entry-view checkpoint. A failed entry check leaves human ownership intact and permits another attempt within the original human timeout. A lost session, policy fault or exhausted budget terminates the run. No new browser/tab/context is substituted, and policy faults are not cleared to force recovery.

After successful revalidation, ownership transfers explicitly to automation with another epoch increment. Recording is disabled for automation actions and replay restarts the saved read-only capability from its first verified entry wait. This is a deliberate recovery strategy: login or manual navigation can invalidate historical membership checks, so the complete account-membership and identity checks run again. It does not skip a failed step just because the operator claims it is complete.

The original run ID, browser object, browser context, session ID, artifact pins, action count and consumed automation time are retained. Human waiting has a separate timeout (300 seconds by default); it does not consume the automation runtime budget. Entry revalidation consumes automation time. Subsequent replay segments get additional manifest/result files; the original paused result is retained. Existing evidence event layout and Git-ignore rules are unchanged.

This version supports read-only restart recovery. It does not support resuming writes, declaring a step manually complete, changing runtime inputs, switching users/tenants as a permission grant, or skipping the workflow prefix. Future write-capable workflows require explicit reconciliation/idempotency semantics before adding those options.

## What is recorded

A script installed in the same page and its subsequent documents observes browser click, input, change and submit events, plus navigation-related Enter/Tab/Escape key events. It accepts trusted browser-generated events. Recorder messages contain only:

- A fixed event kind and allowlisted element tag.
- A reviewed control category, such as `login_username`, `login_password` or `transaction_input`; unrecognized controls become `other`.
- Session/control epoch metadata and timestamps.
- The constant `[REDACTED]` marker instead of any field values.

No typed characters, field values, element text, arbitrary DOM IDs, URLs, credentials or screenshots cross the recorder's persistence boundary. The Python receiver independently validates the closed message shape and checks its page, main frame, owner and epoch. Navigation records contain a safe category rather than the URL. Return, rejection, abort and timeout events are also recorded. Capture is bounded to 1,000 accepted manual events per controller; exhaustion stops recovery.

The binding observes user interaction; it is not a keystroke recording or exact macro that could replay credentials. DOM event capture is best effort around document/browser crashes, not an OS-level lossless audit. Recorder/evidence faults prevent safe continuation; they are not silently ignored to resume automation.

## Safety during manual work

The existing destination/method allowlist, redirect checks, popup/frame/download restrictions and sticky policy faults remain active. Human ownership does not permit transfers, payments, profile changes or external sites.

An eligible session-expiry handoff temporarily permits the existing local login POST while the human owns the session. That allowance is revoked on return, abort, timeout or recording failure. The login values are neither read by the controller nor persisted. Other blocked operations stay blocked.

This is a local, single-operator tool. Access to the local terminal is the operator trust boundary; there is no remote control server, shared operator account or new network listener. The ownership protocol governs adapter commands. It does not make the user's physical keyboard/mouse inaccessible while automation runs, so the operator must wait for the explicit human-ownership prompt before interacting. The page is sealed while return is being revalidated. A production remote takeover service would additionally require operator authentication, authorization and stronger session isolation.

## Abort, timeout and completion

`abort` and terminal EOF revoke the pending ticket and temporary login allowance, seal manual input, record a terminal result and let the CLI close the browser. A watchdog performs equivalent revocation if the operator timeout expires, even if no command is entered. The stdin reader is cancelable, so an abandoned prompt does not leave a blocked background input thread keeping the process alive.

The automation action/time budgets carry over between segments. An uncertain action, policy denial or lost session remains a hard failure. Recoverable conditions can produce another explicit handoff request; no automatic operator return or blind action retry is implemented.

## Validation and evidence

Automated real-browser tests cover same-session login and return, mid-workflow expiry, redaction, rejected automation commands during human ownership, entry-state rejection, stale/foreign/duplicate/concurrent returns, abort, timeout, session loss, policy faults and budget continuity.

The retained [acceptance evidence](../../evidence/takeover/README.md) is explicitly labeled **test_harness**, with `actual_human_participated: false`. It uses real Chromium UI controls to simulate operator login, then invokes the explicit return protocol and verifies typed results. It is not presented as evidence that a person participated.

```sh
.venv/bin/python -m tools.takeover --simulate --evidence-root runs
.venv/bin/python -m unittest discover -s tests -v
```
