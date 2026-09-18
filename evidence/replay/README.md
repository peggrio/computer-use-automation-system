# Deterministic replay acceptance evidence

All cases use the same registered `lookup_transaction` capability version `0.2.0`, with zero model calls. Artifact/profile hashes match the original successful discovery bundle. Runtime inputs and banking outputs are omitted. `acceptance.json` records boolean checks against transient results; synthetic fixture values are in `tools/replay_demo.py`.

| Scenario | Result | Classification | Evidence |
| --- | --- | --- | --- |
| different_existing_account | business_outcome | business_outcome | [26facd998e97475fa36f1584c67af033](26facd998e97475fa36f1584c67af033/replay-result.json) |
| load_failed | failed | hard_failure | [2b69e7c27e194488aef2acae76f2825f](2b69e7c27e194488aef2acae76f2825f/replay-result.json) |
| transaction_belongs_elsewhere | business_outcome | business_outcome | [2e2f6b06c89c490c9ce86f9d92586af3](2e2f6b06c89c490c9ce86f9d92586af3/replay-result.json) |
| policy_block | failed | hard_failure | [4ef50214617d4dbc96e90355be05e056](4ef50214617d4dbc96e90355be05e056/replay-result.json) |
| session_expired | failed | recoverable_condition | [a0ef6983d9e2476480ec6627bc70f1d4](a0ef6983d9e2476480ec6627bc70f1d4/replay-result.json) |
| account_absent | business_outcome | business_outcome | [b87202972e3b4a3eb32e16a1abe2f921](b87202972e3b4a3eb32e16a1abe2f921/replay-result.json) |
| different_transaction | succeeded | success | [dc6c3062373e4278aa3a6d0fb9f545e7](dc6c3062373e4278aa3a6d0fb9f545e7/replay-result.json) |
| slow_load | succeeded | success | [dd0a7e07d97c4fd2af481606c3e5d4ba](dd0a7e07d97c4fd2af481606c3e5d4ba/replay-result.json) |
| transaction_absent | business_outcome | business_outcome | [ec795b04f84f4aec88aa3bcb43715eeb](ec795b04f84f4aec88aa3bcb43715eeb/replay-result.json) |
| different_account_and_transaction | succeeded | success | [eeb1d3beb1174089917ab3a196f4240b](eeb1d3beb1174089917ab3a196f4240b/replay-result.json) |

The first standalone replay is also retained in `d90cbff94d36492b90ebd48c99c0e702/`. It succeeded with a different transaction from discovery.

Successful changed-input cases include a different transaction on the original account and a transaction in a second valid account. The cross-account case asks for that second transaction under the original account and correctly returns `transaction_not_found_in_account`.

Slow loading, HTTP 500, session expiry and policy-block cases are labeled test-harness scenarios. Session expiry uses real UI logout; delay, failed response and altered download-control metadata are injected. They are not claims of naturally occurring service incidents. CLI recoverable cases report failed plus `recoverable_condition`, since that command closes its browser. Live-session pause/ownership transfer is tested separately.

Reproduce all ten cases without a model or API key:

```sh
.venv/bin/python -m tools.replay --demo --evidence-root runs
```

Full regression suite: 84 tests passed after Step 6. No files or evidence were pushed to GitHub.
