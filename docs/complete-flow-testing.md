# Complete-flow acceptance (Step 8)

Start the local ParaBank container (`docker compose up -d`) and install the project's Python dependencies and Playwright Chromium, as described in the README. Run:

```sh
.venv/bin/python -m tools.acceptance
```

The command exits nonzero if any required check fails. It writes a private `report.json` and detailed browser evidence below a unique `runs/acceptance-…/` directory. Existing evidence and Git ignore rules are unchanged.

## What is exercised

1. **Real discovery provenance:** verify the retained `gpt-5.4-mini` discovery receipts, sanitized observations, action trace, token accounting, and recompiled capability. Match the generated capability and surface profile hashes to the exact registered artifacts used for replay. This verifies the previous real discovery; it does not make a new model call. Discovery regression tests use explicitly labeled model doubles to exercise decisions and failure cases.
2. **Full regression suite:** run contract, adapter, safety, discovery, replay, takeover, and acceptance tests. These include bounded step/action/time limits, successful and exhausted wait retries, uncertain clicks without retries, rejected checkpoints, denied actions, stale ownership, and takeover timeout. Exhausted waits share one step deadline.
3. **Ten actual browser replay scenarios:** changed transaction; changed account and transaction; transaction in another account; another existing account without that transaction; absent account; absent transaction; slow load; expired session; failed data load; policy block. Positive runs check typed output identities, and negative runs check expected status/code.
4. **Same-session takeover:** expire the login, pause replay, simulate operator login through actual UI controls in the same page/session, explicitly return control, revalidate, and complete replay. This is an automated operator simulation, not evidence that a person performed the interaction. Use `python -m tools.takeover --demo-expiry` for the interactive demonstration.
5. **No model access during replay:** run the complete browser scenarios in a fresh subprocess with OpenAI environment variables removed, model imports denied, and Python external socket connections denied. Any attempted access fails acceptance, even if application code catches the exception. Browser traffic remains subject to the adapter's destination/action policy. The Python socket guard does not sandbox the Chromium subprocess or arbitrary child processes.
6. **Evidence redaction:** recursively parse every JSON/JSONL artifact in the real discovery bundle and new browser runs; reject unknown file types, symlinks, malformed data, known credential/fixture values, canaries, API-key patterns, and the configured local OpenAI key. The report contains counts only, never matched values. Negative-control tests prove detection. Valid ISO timestamps are distinguished from coincidental numeric fixture IDs. This checks known secrets and patterns, not every possible unknown secret.

No API key is needed for execution. If `.env.local` contains a key, acceptance reads it only to check that it is absent from evidence. It never sends it to the worker or a provider. There are no new billable calls. To obtain a fresh real discovery instead, use the separately documented discovery command; review and register that candidate before replaying it.

Tests depend on the seeded local ParaBank demo data. Avoid running them concurrently with an interactive demo or resetting the database during the run. Reports explicitly distinguish prior real model activity, current automated browser runs, and simulated operator actions.
