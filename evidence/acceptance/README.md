# Complete-flow acceptance evidence

The retained run passed 103 regression tests, ten live replay scenarios, and same-session simulated takeover. No new model calls occurred. The earlier real discovery was verified and its hashes matched the replay artifacts.

[Aggregate report](report.json). All paths in this exported report are relative to this directory. Browser artifacts are byte-for-byte copies of the passing run; the report only replaces workstation-specific paths and adds an export note.

| Scenario | Expected observed result | Log |
| --- | --- | --- |
| different_transaction | succeeded / success | [Result](browser/37b51bf724ea4ce69368fecdec423ce5/replay-result.json) |
| different_account_and_transaction | succeeded / success | [Result](browser/6b25de15a62c4e95914512e874daa8ed/replay-result.json) |
| transaction_belongs_elsewhere | business_outcome / business_outcome | [Result](browser/668fa39d4ff94a3084c8d1339081c86f/replay-result.json) |
| different_existing_account | business_outcome / business_outcome | [Result](browser/f9a3eb974b814f4eacf9725fd0c90197/replay-result.json) |
| account_absent | business_outcome / business_outcome | [Result](browser/bf56d501668943178053dc4191c606b2/replay-result.json) |
| transaction_absent | business_outcome / business_outcome | [Result](browser/fe53416846db4e2aa9b4e6cb03508063/replay-result.json) |
| slow_load | succeeded / success | [Result](browser/3f21dcb49bf54994aa8d68c8a152cfc8/replay-result.json) |
| session_expired | failed / recoverable_condition | [Result](browser/d728f837c3094130bee14b97a79ee7ff/replay-result.json) |
| load_failed | failed / hard_failure | [Result](browser/889e0bc94b20410e891e11c649e918fc/replay-result.json) |
| policy_block | failed / hard_failure | [Result](browser/f6b2346fddb04950971669e74bb96bcb/replay-result.json) |
| Simulated operator takeover | paused then succeeded, same session | [Checks](browser/b1f5e2605cb840ea92b9e08b5a2b9a02/takeover-acceptance.json) |

`replay-event-*.json` contains checkpoints and actions; failure files retain structural DOM evidence without text/values. Takeover logs capture ownership transfer and redacted manual-action metadata. Fixtures are labeled test-harness actions, not naturally occurring incidents. Actual human participation is false.

The scan covered 26 discovery files and 386 browser files, with zero known-secret violations. Scanner limits and isolation boundaries are described in [the testing guide](../../docs/advanced/complete-flow-testing.md). Reproduce with `.venv/bin/python -m tools.verify acceptance`.
