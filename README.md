# Computer-Use Automation System

This project demonstrates one design principle: let an LLM discover a browser workflow once, then replay the reviewed workflow without model decisions.

The reference workflow looks up a transaction in a local ParaBank sandbox. It is intentionally read-only. A saved discovery is compiled into a typed, versioned **capability**—a reviewed workflow recipe that accepts new inputs.

```text
Goal -> Discover -> Validate -> Replay
                               |
                               +-> optional human intervention
```

The beginner path is replay first, discovery second. Policy enforcement, redacted evidence, version pins, and same-session human intervention remain part of the design, but they are explained separately from the first run.

## Quick start

Prerequisites are Python **3.11**, Docker Desktop with Compose, and local port **8080**.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
docker compose up -d
```

Wait for <http://127.0.0.1:8080/parabank/> to show the login page. The sandbox uses the synthetic credentials `john` / `demo`.

Replay the retained, reviewed capability with new inputs:

```sh
.venv/bin/python -m tools.replay --account 12345 --transaction 12256 \
  --headed --slow-mo-ms 500
```

`--slow-mo-ms 500` adds roughly half a second to each Playwright operation so the visible workflow is easier to follow. Use a value from `0` to `5000`, or omit it for full speed. Replay makes no model calls. It prints a redacted result summary and writes detailed local evidence under the ignored `runs/` directory.

Try two more outcomes:

```sh
.venv/bin/python -m tools.replay --account 12678 --transaction 12367
.venv/bin/python -m tools.replay --account 12345 --transaction 999999999
```

The first succeeds. The second returns the expected business outcome `transaction_not_found_in_account`; business outcomes are not system failures.

## The four core concepts

1. **Goal** describes the desired result in natural language.
2. **Discovery** lets a bounded model observe the UI and propose actions.
3. **Capability** stores the reviewed actions, checkpoints, inputs, outputs, and outcomes as a versioned contract.
4. **Replay** executes that contract deterministically. It never asks a model what to do next.

Policy and evidence apply across the flow. Human intervention is an optional recovery layer: automation can pause, transfer the same browser session to an operator, revalidate the page, and resume.

Read [Architecture](docs/architecture.md) for the component boundaries and [Getting started](docs/getting-started.md) for the complete local workflow.

## Run a new discovery

Discovery is optional and requires an OpenAI API key. Copy the example only when `.env.local` does not already exist, restrict its permissions, and edit it locally:

```sh
test -f .env.local || cp .env.example .env.local
chmod 600 .env.local
```

Then run the bounded discovery:

```sh
.venv/bin/python -m tools.discover --goal lookup_transaction \
  --target http://127.0.0.1:8080 --model gpt-5.4-mini \
  --account 12345 --transaction 12145 --version 0.2.1 \
  --max-steps 20 --max-seconds 180 --max-tokens 60000 \
  --call-timeout 30 --evidence-root runs/discovery --headed
```

Discovery creates a candidate; it does not register or approve it. Verify the generated directory before reviewing its capability and surface profile:

```sh
.venv/bin/python -m tools.verify discovery runs/discovery/<run-id>
```

Promotion remains a deliberate manual operation. There is no automatic promotion and no mutable `latest` version.

## Optional demos

Run deterministic replay acceptance scenarios:

```sh
.venv/bin/python -m tools.replay --demo --evidence-root runs
```

Run same-session human intervention after a deliberate logout:

```sh
.venv/bin/python -m tools.takeover --demo
```

Run the full regression and browser acceptance suite:

```sh
.venv/bin/python -m tools.verify acceptance
```

## Project map

```text
automation/     Core discovery, replay, policy, evidence, and intervention code
adapters/       Reference-application rules; ParaBank is the included example
capabilities/   Reviewed and versioned workflow artifacts
contracts/      Machine-readable artifact schema
tools/          Three primary entry points: discover, replay, and verify
tests/          Unit, safety, and complete-flow checks
docs/           Architecture, setup, and advanced design notes
evidence/       Retained redacted evidence from verified runs
```

## Advanced design notes

- [Discovery](docs/advanced/llm-discovery.md)
- [Deterministic replay](docs/advanced/deterministic-replay.md)
- [Safety and evidence](docs/advanced/safety-and-evidence.md)
- [Human intervention](docs/advanced/live-takeover.md)
- [Contracts](docs/advanced/contracts.md)
- [Browser foundation](docs/advanced/ui-foundation.md)
- [Complete-flow testing](docs/advanced/complete-flow-testing.md)
- [Design report](REPORT.md)
- [Evidence index](evidence/README.md)

Desktop execution, tenant override resolution, and cross-version migration are design proposals, not implemented features. Browser execution currently uses DOM and role targeting without test IDs. Use only the local seeded sandbox.
