# Computer-Use Automation System

A real LLM discovers a read-only ParaBank workflow; a typed, versioned capability replays it with new account/transaction inputs and no model decisions. The implementation includes policy enforcement, redacted evidence, explicit outcomes, and same-session human takeover.

[Design report](REPORT.md) · [Evidence index](evidence/README.md) · [Acceptance coverage](docs/complete-flow-testing.md)

## Setup

Prerequisites: Python **3.11**, Docker Desktop with Compose running, and a free local port **8080**. Run commands from the repository root. The pinned ParaBank image supports Apple Silicon; no separate Java or database installation is needed.

```sh
# If cloning rather than using an existing checkout (private-repository access required):
git clone https://github.com/peggrio/computer-use-automation-system.git
cd computer-use-automation-system

python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-discovery.txt
.venv/bin/python -m playwright install chromium
docker compose up -d
docker compose ps
```

`requirements-discovery.txt` includes the UI and contract dependencies. Open <http://127.0.0.1:8080/parabank/> and wait for the login page. Synthetic demo credentials are `john` / `demo`. Use only the local seeded sandbox. The transaction workflow starts at Accounts Overview after the command logs in.

Only the web port is published, bound to loopback. No host directories or persistent database volumes are mounted. Stop/start preserves the container; recreating it can discard data. Avoid resetting or manually changing fixture data while acceptance tests run.

```sh
docker compose logs --tail 50 parabank
docker compose stop
docker compose start
```

### Optional API key: needed only for a new discovery

Create `.env.local` **only if it does not already exist**, restrict permissions, and edit it locally. Do not put the key in a command argument or commit it.

```sh
test -f .env.local || cp .env.example .env.local
chmod 600 .env.local
# Open .env.local in your local editor and fill OPENAI_API_KEY.
git check-ignore .env.local
```

The file accepts `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.4-mini`, and optional `PARABANK_USERNAME`/`PARABANK_PASSWORD`. It is parsed as data, never sourced as shell code. Environment variables override file values. Discovery's `--model` argument selects the model. Replay uses public demo credentials by default; custom replay credentials come from environment variables, not `.env.local`.

New discovery calls require API billing/access and may incur charges. Saved-evidence verification, replay, and tests do not call a model. Acceptance reads an existing local key only to check that it is absent from saved evidence.

## Demo 1: replay the retained real discovery without an API key

First verify the saved model receipts and compiled artifact; then replay that exact artifact, registered as `lookup_transaction@0.2.0`, with different inputs:

```sh
.venv/bin/python -m tools.verify_discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
.venv/bin/python -m tools.replay --version 0.2.0 --account 12345 --transaction 12256 --headed
.venv/bin/python -m tools.replay --version 0.2.0 --account 12678 --transaction 12367
.venv/bin/python -m tools.replay --version 0.2.0 --account 12345 --transaction 999999999
```

Expected: two successes, then `transaction_not_found_in_account` as a business outcome. Discovery used a different transaction. The CLI prints redacted JSON and output field names; authorized Python callers receive typed outputs in memory. Success/business outcome exits 0; recoverable failure exits 3; hard failure exits 1. Each run prints its evidence directory under ignored `runs/`.

## Demo 2: run genuine discovery, then replay its new candidate

The named goal in [goals/lookup_transaction.json](goals/lookup_transaction.json) contains the natural-language objective. The CLI currently supports this reviewed goal, not arbitrary goal text. This command makes real `gpt-5.4-mini` calls against the local UI, with explicit limits:

```sh
.venv/bin/python -m tools.discover --goal lookup_transaction \
  --target http://127.0.0.1:8080 --model gpt-5.4-mini \
  --account 12345 --transaction 12145 --version 0.2.1 \
  --max-steps 20 --max-seconds 180 --max-tokens 60000 \
  --call-timeout 30 --evidence-root runs/discovery --headed
```

Continue only if discovery reports `succeeded`. Set `DISCOVERY_RUN` to the exact directory printed by that command (replace `<run-id>` below). Verify its evidence and review `capability.json` and `surface-profile.json`: actions, input references, membership checkpoint, outcomes, and final extraction. The candidate is not automatically registered or approved.

```sh
export DISCOVERY_RUN='runs/discovery/<run-id>'
.venv/bin/python -m tools.verify_discovery "$DISCOVERY_RUN"
```

After that local review, this bounded Python demo replays **that candidate's files** with new inputs, under the same browser policy. It leaves the existing registry untouched. Verification loads discovery code to recompile the trace offline; no provider call is made. Replay itself uses no model decisions.

```sh
.venv/bin/python - <<'PY'
import asyncio, json, os
from pathlib import Path
from tools.verify_discovery import verify
from tools.validate_contracts import load
from automation.browser import BrowserAdapter
from automation.replay import Replay, invocation_for

path = Path(os.environ['DISCOVERY_RUN'])
verify(path)
cap = load(path / 'capability.json')
profile = load(path / 'surface-profile.json')

async def demo():
    async with BrowserAdapter(cap, profile,
            {'account_id': '12345', 'transaction_id': '12256'},
            evidence_root='runs/candidate-replay',
            evidence_source='deterministic_replay', headless=False) as adapter:
        await adapter.login('john', 'demo')
        runner = Replay(adapter)
        result = await runner.run(invocation_for(adapter))
        print(json.dumps({'status': result['status'],
            'classification': runner.classification,
            'output_fields': sorted(result.get('outputs', {})),
            'evidence_directory': str(adapter.evidence.directory),
            'model_calls': 0}))
        if result['status'] != 'succeeded': raise SystemExit(1)
asyncio.run(demo())
PY
```

For regular registered replay, promotion is still a reviewed manual operation: assign a new immutable version, validate capability/profile/policy hashes, install the pair, and add the exact pins to `capabilities/registry.json`. No automatic promotion tool or `latest` resolution is implemented. See [REPORT.md](REPORT.md) for compatibility and tenant override design.

## Demo 3: live human takeover

```sh
.venv/bin/python -m tools.takeover --demo-expiry
```

This opens a browser, deliberately expires its login, and pauses replay. In the **same browser tab**, log in using `john` / `demo` and restore Accounts Overview. Type `resume` in the terminal to explicitly return control, or `abort` to stop. The runner rechecks ownership, state, inputs, pins, and budgets before continuing. Do not operate the browser while automation owns it. The default human timeout is bounded; this is a local trusted-operator interface.

For a repeatable automated operator simulation:

```sh
.venv/bin/python -m tools.takeover_demo --evidence-root runs
```

The simulated run is labeled `actual_human_participated: false`. It uses real browser controls and the same session; it is not evidence of a person operating the demo.

## Complete tests and exceptional runs

```sh
.venv/bin/python -m tools.acceptance
.venv/bin/python -m tools.replay_demo --evidence-root runs
```

Acceptance verifies the earlier real discovery and its registered hashes, runs all 103 regression tests, and executes ten replay scenarios plus same-session simulated takeover with model access blocked. It scans discovery/browser evidence for known secrets and writes a pass/fail report under `runs/acceptance-…/`. The retained passing report and browser logs are in [evidence/acceptance/](evidence/acceptance/). Fixtures include changed inputs, missing records, delays, HTTP failures, logout, and policy denial; injected conditions are labeled. No fresh model calls occur.

To investigate a failing test locally:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

### Without live services

After dependencies are installed, these checks need neither Docker nor API access:

```sh
.venv/bin/python -m tools.verify_discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
.venv/bin/python -m unittest discover -s tests -p 'test_contracts.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_acceptance.py' -v
```

These are offline validation/scanner checks, not a replacement for UI execution. Full replay, discovery, takeover, and the full regression suite require the local ParaBank container and Chromium. If login fails, check container readiness and seeded credentials; if fixture assertions fail, inspect the redacted report before changing the application data.

## Implementation and evidence map

| Area | Reference |
| --- | --- |
| Workflow and typed contracts | [Workflow](docs/demo-workflow.md), [contracts](docs/contracts.md), [schema](contracts/schema.json) |
| UI mechanism | [Browser foundation](docs/ui-foundation.md) |
| Discovery and replay | [Discovery](docs/llm-discovery.md), [replay](docs/deterministic-replay.md) |
| Policy and control transfer | [Safety](docs/safety-and-evidence.md), [takeover](docs/live-takeover.md) |
| Required seven-section write-up | [REPORT.md](REPORT.md) |
| Generated artifact, discovery/replay logs, failures | [evidence/README.md](evidence/README.md) |

Desktop execution, tenant override resolution, and cross-version migration are design proposals, not implemented features. Browser execution currently uses DOM/role targeting without test IDs. The repository remains private; these commands do not publish or submit it.
