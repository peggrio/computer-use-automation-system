# Step 1: ParaBank demo workflow

## Scope and goal

Capability name: `lookup_transaction`.

Example goal: "For account 12345, find transaction 12145 and return its date, description, credit/debit type, and amount. Do not change any banking data."

ParaBank is a customer banking portal, not a staff member-search application. This workflow uses its real account overview, account activity, transaction search, and detail screens. All records are upstream synthetic fixtures.

## Inputs and outputs

Inputs: `account_id` and `transaction_id` as nonempty digit strings. Session/login configuration is separate from the capability's business parameters; artifacts must never embed login values.

Success output: verified account ID, transaction ID, detail-page date normalized to ISO date, description, type (`credit` or `debit`), and amount as a decimal string. Currency can be a documented sandbox configuration value (`USD`), not an inference from a dollar symbol alone.

Business outcomes (refined in Step 2): `account_not_available` or `transaction_not_found_in_account` when positively established. The latter makes no claim about whether the transaction exists elsewhere. A loading failure or ambiguous state must never be converted into a not-found result.

## Planned complete flow

1. Start a visible browser session at the local ParaBank entry point. Establish login through runtime credential handling or operator login, with credential entry excluded from persisted observations.
2. Open Accounts Overview and confirm the requested account is listed for the current session. If absent after a completed load, return `account_not_available`.
3. Open that account's activity page and verify its displayed account number. Use its transaction links to establish account membership; after verified complete loading, an absent match returns `transaction_not_found_in_account`. Do not assume a global transaction-ID lookup enforces account scoping.
4. Open Find Transactions, select the requested account, enter the transaction ID, and use the corresponding search button. The page has four identically named search buttons, so targeting must identify the correct form section.
5. Wait for a completed Transaction Results state. If the search is empty after membership was established, stop with a checkpoint failure: the observations are inconsistent or data changed. Require a unique matching transaction; ambiguity stops the run.
6. Open its detail page. Verify the displayed transaction ID and extract the declared outputs. Require the earlier account-membership check before returning account-scoped success.
7. Return a structured result with redacted execution evidence. No funds transfer, bill payment, loan request, profile update, or account creation is part of this capability.

The exact account-membership and empty-result checkpoints must be validated during implementation. Missing rows before asynchronous loading finishes are not sufficient evidence.

## Manual observations from the pinned sandbox

- The image runs as `linux/arm64` on this laptop; login and the UI were verified.
- Account `12345` contains transaction `12145` (`Check # 1111`, credit, `300.00`) and transaction `12256` (`Check # 1211`, debit, `100.00`). Step 3 verified the latter detail page in the second-input adapter test.
- Searching for `12145` returned a link to its detail page. That page displayed the correct transaction ID, description, credit type, and amount.
- Searching for `999999999` returned a Transaction Results table with no data rows in this baseline.
- There is a date-display discrepancy: the result/activity row for `12145` displayed `12-10-2025`, while its detail page displayed `12-11-2025`. Step 3 confirmed browser-local list formatting versus server-side detail formatting; the adapter defaults to UTC and uses the detail page as its output source. Do not silently claim these displays agree.
- Several controls have weak accessible labels and repeated button text. Existing HTML IDs are available, but no test IDs need to be added to the target app.

These are exploratory checks, not the required standalone project's LLM discovery/replay evidence.

## Acceptance scenarios for later implementation

| Scenario | Expected behavior |
| --- | --- |
| Genuine LLM discovery for 12345 / 12145 | Goal completed through UI; validated parameterized artifact saved |
| Replay with a different verified transaction | Same artifact, no LLM decisions, correct outputs |
| Missing transaction | Known business outcome after verified completion |
| Account not listed or transaction belongs elsewhere | Explicit outcome; never report an unverified account association |
| Missing or malformed input | Reject with a structured validation error |
| Slow response | Bounded waits/retries; preserve the original goal |
| Session expires mid-run | Pause, route intervention, let operator log in in the same session, revalidate and resume |
| Unrecognized error or ambiguous target | Stop with step/expected/observed evidence; escalate where appropriate |
| Attempt to navigate to a write operation or external site | Policy block before execution |
| Human handoff | Automation cedes control, records redacted manual actions, resumes only after explicit control transfer |

Session expiry and transient failures will be deliberately triggered in a documented test harness. Permission-denial/error overlays may be simulated later and must be labeled as injected, not claimed as native ParaBank features.

## Boundaries

- Automation observes and operates the UI; it does not call banking APIs to accomplish the goal. ParaBank's own frontend can naturally make its normal requests.
- Test-fixture setup and failure injection are separate from discovery/replay behavior.
- Policy enforcement, redaction, artifact generation, deterministic replay, and live handoff are future implementation work.
- The app does not provide a production-grade banking security model. This local demo demonstrates the automation design using synthetic data.

## Definition of done for Step 1

Local sandbox starts reproducibly, demo login works, the proposed workflow has been explored through the real UI, inputs/outputs and checkpoints are specified, and native versus planned injected error scenarios are distinguished. These conditions are met; the automation implementation comes next.

## Step 2 refinement

See [system contracts](contracts.md) for the versioned capability format, scoped business outcomes, profile boundary and exact checkpoint semantics. Step 1 exploration did not verify every checkpoint; implementation must still do so.

## Step 3 implementation

The [UI foundation](ui-foundation.md) now verifies the core workflow through a shared browser adapter, including completed-load membership checks and a second transaction input. This remains a scripted adapter test, not LLM discovery evidence.
