# Getting started

This guide expands the README's quick start. Run all commands from the repository root.

## 1. Start the local sandbox

Requirements:

- Python 3.11;
- Docker Desktop with Compose;
- Chromium installed through Playwright; and
- local port 8080 available.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
docker compose up -d
docker compose ps
```

Open <http://127.0.0.1:8080/parabank/> and wait for the login page. The local image uses synthetic credentials `john` / `demo`.

Only the web port is published, and it is bound to loopback. No host directory or persistent database volume is mounted. `docker compose stop` and `docker compose start` preserve the current container; recreating it can discard fixture changes.

## 2. Verify and replay the retained artifact

Verify that the retained discovery still recompiles to the registered capability:

```sh
.venv/bin/python -m tools.verify discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
```

Replay version `0.2.0` with a different transaction from the discovery run:

```sh
.venv/bin/python -m tools.replay --version 0.2.0 \
  --account 12345 --transaction 12256 --headed --slow-mo-ms 500
```

For a visible demonstration, `--slow-mo-ms` adds a delay to each Playwright operation. Accepted values are `0` through `5000`; the default is `0`.

Authorized Python callers receive typed outputs in memory. The terminal receives only a redacted summary and output field names. Success and business outcomes exit 0, recoverable failures exit 3, and hard failures exit 1.

Each execution writes evidence under the ignored `runs/` directory. Replay uses no model SDK, API key, or model decision.

## 3. Explore expected outcomes

```sh
.venv/bin/python -m tools.replay --account 12678 --transaction 12367
.venv/bin/python -m tools.replay --account 12345 --transaction 999999999
.venv/bin/python -m tools.replay --demo --evidence-root runs
```

The demo command runs ten labeled scenarios covering changed inputs, absent records, delays, logout, HTTP failure, and policy denial.

## 4. Run a new discovery

This step is optional. It requires API access and may incur charges. Create a local environment file only if one does not exist:

```sh
test -f .env.local || cp .env.example .env.local
chmod 600 .env.local
git check-ignore .env.local
```

Edit `.env.local` locally and set `OPENAI_API_KEY`. Environment variables override file values. The file is parsed as data and is never sourced as shell code.

Run discovery with explicit limits:

```sh
.venv/bin/python -m tools.discover --goal lookup_transaction \
  --target http://127.0.0.1:8080 --model gpt-5.4-mini \
  --account 12345 --transaction 12145 --version 0.2.1 \
  --max-steps 20 --max-seconds 180 --max-tokens 60000 \
  --call-timeout 30 --evidence-root runs/discovery --headed
```

Continue only when discovery reports `succeeded`. Verify the exact directory printed by that command:

```sh
.venv/bin/python -m tools.verify discovery runs/discovery/<run-id>
```

Review `capability.json`, `surface-profile.json`, the model receipts, checkpoints, outcomes, and final extraction. Discovery does not modify the registry. Promotion requires assigning a new immutable version, validating artifact and policy hashes, installing the capability/profile pair, and adding exact pins to `capabilities/registry.json`.

## 5. Try human intervention

```sh
.venv/bin/python -m tools.takeover --demo
```

The command deliberately logs out, pauses replay, and opens the same browser tab to the operator. Log in with the synthetic credentials, restore Accounts Overview, then type `resume` in the terminal. Type `abort` to stop. Do not operate the browser while automation owns it.

For an automated test-harness simulation:

```sh
.venv/bin/python -m tools.takeover --simulate --evidence-root runs
```

The simulation is explicitly labeled `actual_human_participated: false`.

## 6. Test the system

Offline checks require no Docker service or API access:

```sh
.venv/bin/python -m tools.verify discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
.venv/bin/python -m unittest discover -s tests -p 'test_contracts.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_acceptance.py' -v
```

The full suite requires the local ParaBank container and Chromium:

```sh
.venv/bin/python -m tools.verify acceptance
```

The acceptance command verifies the retained real discovery, runs the regression suite, executes isolated browser scenarios with model access blocked, scans evidence for known secrets, and writes a report under `runs/`.

The tests are grouped by purpose:

| Layer | Tests | Use |
| --- | --- | --- |
| Fast and mostly offline | `test_contracts`, `test_discovery`, `test_replay` | Contract semantics, compilation, and interpreter behavior |
| Safety and intervention | `test_safety`, `test_takeover` | Policy boundaries, evidence, ownership, and resume behavior |
| Browser and complete flow | `test_browser_adapter`, `test_acceptance` | Real UI integration and isolated end-to-end checks |

## Troubleshooting

- If login fails, confirm container readiness with `docker compose logs --tail 50 parabank`.
- If port 8080 is occupied, stop the conflicting local service; the registered policy and profile intentionally pin the sandbox endpoint.
- If fixture assertions fail, inspect the redacted report before changing application data.
- If Playwright cannot launch Chromium, rerun `.venv/bin/python -m playwright install chromium`.
