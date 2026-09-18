# Architecture

The system separates model-guided exploration from repeatable execution. The model may choose actions only during discovery. Replay consumes a reviewed artifact and contains no model dependency.

```text
Natural-language goal
        |
        v
Bounded discovery -----> candidate capability + surface profile
                              |
                              v
                       contract validation
                              |
                              v
                     reviewed versioned artifact
                              |
                 inputs ----> deterministic replay ----> typed result
                                      |                      |
                                      v                      v
                                policy checks         redacted evidence
                                      |
                                      v
                           optional human intervention
```

## Core boundaries

### Discovery

Discovery is the only model-guided phase. It receives a named goal, current browser facts, a closed action schema, and explicit step, time, and token limits. It records decisions and UI transitions, then compiles them into a candidate capability. It cannot approve or register its own output.

### Capability

A capability is a reviewed workflow recipe. It declares typed inputs and outputs, ordered actions, preconditions, postconditions, business outcomes, retry limits, and an effect classification. Versions are immutable and resolved through exact registry pins; there is no `latest` alias.

A simplified capability reads like this:

```json
{
  "id": "lookup_transaction",
  "version": "0.2.0",
  "inputs": ["account_id", "transaction_id"],
  "steps": ["select account", "find transaction", "extract details"]
}
```

The executable contract adds explicit types, target references, checkpoints, outcomes, retry limits, provenance, and content pins. Start with the workflow above; consult the [full contract reference](advanced/contracts.md) only when changing artifact semantics.

The surface profile is separate from the workflow. It binds semantic targets such as `account_link` to the reference application's UI and defines readiness checks. This separation keeps workflow meaning distinct from UI mechanics.

### Replay

Replay interprets a pinned capability. It validates inputs, checks policy, resolves each target against a fresh observation, executes the action, and verifies checkpoints. It returns one of three classifications:

- success;
- a positively established business outcome; or
- a recoverable or hard system failure.

Replay never asks a model what to do next.

### Human intervention

Human intervention is an optional recovery layer. On an eligible recoverable failure, automation can pause and transfer ownership of the same browser session. The operator explicitly returns control; replay then rechecks session identity, ownership epoch, inputs, artifact pins, budgets, and page state before restarting from a safe boundary.

## Cross-cutting guarantees

- Policy is enforced at both the workflow-operation and browser-network boundaries.
- The reference capability is read-only; write operations and external destinations are denied.
- Evidence stores structural facts and references, not credentials or business values.
- Candidate artifacts require review and a new immutable version before registration.
- Missing or ambiguous UI state fails closed instead of being converted into a business outcome.

## Code map

| Area | Responsibility |
| --- | --- |
| `automation/discovery.py` | Bounded observe/decide/act loop and candidate compilation |
| `automation/replay.py` | Deterministic interpreter and result classification |
| `automation/browser.py` | Browser lifecycle, observation, targeting, actions, and guarded I/O |
| `automation/policy.py` | Allowed effects, operations, methods, and destinations |
| `automation/evidence.py` | Redacted manifests, events, and failure snapshots |
| `automation/takeover.py` | Same-session ownership transfer and resume protocol |
| `adapters/parabank.py` | ParaBank-specific release and readiness rules |
| `contracts/schema.json` | Executable artifact contracts |

ParaBank is the included reference application, not the architecture boundary. Supporting another application requires a reviewed surface profile and application adapter while preserving the same capability, policy, replay, and evidence interfaces.
