# Evidence index

Start with the generated artifact and the real discovery trace, then inspect changed-input replay and exceptional results. The design example under `examples/` is not a model recording.

| Evidence | What it establishes |
| --- | --- |
| [Generated capability](discovery/dbce8a9d535c40a4ba91a003792854ea/capability.json) and [surface profile](discovery/dbce8a9d535c40a4ba91a003792854ea/surface-profile.json) | Parameterized, typed `0.2.0` artifacts compiled from real discovery; exact copies are pinned in the local registry |
| [Real discovery result](discovery/dbce8a9d535c40a4ba91a003792854ea/discovery-result.json) and [bundle guide](discovery/README.md) | Seven actual `gpt-5.4-mini` responses, six UI actions, nine compiled steps, 6,894 tokens; per-call request/response files, execution records, and provenance hashes |
| [Original replay matrix](replay/README.md) | Same registered artifact with different account/transaction inputs, business outcomes, and injected failures; no model calls |
| [Complete acceptance report](acceptance/report.json) and [scenario index](acceptance/README.md) | 103 passing tests; ten browser replay runs plus simulated takeover with model access blocked; 412 evidence files scanned without known-secret violations |
| [Same-session takeover](takeover/README.md) | Real ownership transfer, redacted operator events, explicit resume and successful revalidation, with operator actions simulated by the test harness |
| [Safety demo](safety-demo/README.md) | Policy checks and richer structural failure evidence with sensitive text omitted |

Discovery requests contain sanitized facts and typed input references. Decisions include finite reason codes rather than unconstrained model prose. Replay events record actions, checkpoint checks, retry/failure context, and classifications. Failure snapshots preserve structure, not banking text. Typed output values are checked in memory and omitted from evidence; acceptance files retain boolean assertions.

The earlier failed real discovery attempt is retained and labeled in the discovery guide, including incomplete usage accounting for its rejected fifth response. Compiler-added guards are trusted logic, not additional model decisions. Replay delay, HTTP failure, download-control alteration, and logout fixtures are explicitly identified. No simulated operator is described as an actual human participant.

The acceptance export preserves browser logs unchanged and makes its report paths relative for review on another machine. Existing discovery/replay/takeover evidence is retained. No API key, `.env.local`, browser profile, or session storage is included. The known-secret scan is useful validation, not a universal proof that arbitrary unknown secrets could never be present.

Reproduce using the [README demo commands](../README.md). The original discovery can be checked offline:

```sh
.venv/bin/python -m tools.verify discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
```
