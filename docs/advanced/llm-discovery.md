# LLM discovery

## Implemented scope

The standalone discovery runner accepts the named goal `lookup_transaction`, a local ParaBank target origin, and typed runtime inputs. The goal catalog supplies a business objective, output definitions and safety/completion constraints. It does not supply a workflow sequence. Unsupported goals are rejected; arbitrary natural-language goal routing is not implemented.

`gpt-5.4-mini` chooses each next operation using current browser facts. The existing surface profile supplies reviewed control bindings and readiness rules. This is workflow discovery over known application controls, not discovery of selectors or an arbitrary application's entire interface. The internal scripted test walkthrough is never called and the design example's action sequence is never sent to the model.

The browser adapter still loads the Step 2 design document as its reviewed typing/control contract. Only its declared output extraction mapping is reused as an operation; the discovery compiler builds a new action sequence from the successful execution trace. A separate `goals/lookup_transaction.json` holds the model's goal and constraints.

## Local setup

```sh
.venv/bin/python -m pip install -r requirements-discovery.txt
.venv/bin/python -m playwright install chromium
docker compose up -d
```

Store the key in `.env.local` in the repository root:

```dotenv
OPENAI_API_KEY='your-key-here'
OPENAI_MODEL='gpt-5.4-mini'
```

This file is Git-ignored and should have permissions `0600`. The runner reads it as data, without shell evaluation. Environment variables override values in the file. The CLI model is explicitly fixed to `gpt-5.4-mini` for this stage; `OPENAI_MODEL` documents the chosen model locally. Do not paste credentials into chat, commit them, or force-add this ignored file. No API key value is printed or persisted in evidence.

The key is sent only for authentication to the official OpenAI API endpoint. Sanitized observations are sent to the model. Local storage does not mean local inference. The SDK has automatic retries disabled and requests use `store=False`; this is not a claim of zero provider retention under every account policy.

API billing is separate from ChatGPT. A free usage tier lists rate ceilings, not a promise of free tokens. The verified live calls establish that this account can currently invoke the chosen model, not that the usage is free.

## Run discovery

```sh
.venv/bin/python -m tools.discover \
  --goal lookup_transaction \
  --target http://127.0.0.1:8080 \
  --model gpt-5.4-mini \
  --account 12345 --transaction 12145 \
  --max-steps 20 --max-seconds 180 --max-tokens 60000
```

Add `--headed` to watch the isolated browser. Evidence defaults to ignored `runs/`. To deliberately retain a shareable redacted bundle, use `--evidence-root evidence/discovery`. No command commits or uploads anything.

The step limit counts model decisions, including finish/stop; UI safety has its own action budget. The overall deadline covers observation, model calls, actions and completion. A model call is limited to 30 seconds and 1,024 output tokens. A conservative byte-based input reservation plus output allowance prevents starting a call near the total token limit; returned usage is accumulated as well. These are application limits, not an exact dollar-spend guarantee. Separate browser setup/login have the adapter's timeouts. Failure capture may take up to three additional seconds, followed by browser cleanup.

## Observe → decide → act → verify

1. **Observe:** Read the real browser through the shared adapter. Wait for a completed known view, then expose only readiness, matching-control cardinalities (`zero`, `one`, `many`, `unknown`), input-equality booleans, verified membership and which input references were explicitly applied. No account numbers, bank values, names, credentials, raw DOM text, URLs, cookies or screenshots are sent to the model.
2. **Decide:** Send the goal, constraints, reviewed control descriptions, safe observation and prior decisions to the Responses API. The model chooses `click`, `fill`, `select`, `wait`, `finish` or `stop`, a target, an input reference when needed and a short reason code. No arbitrary scripts, destinations, literals or free-text rationale are accepted.
3. **Act:** Validate the decision and persist its receipt before execution. Recheck the UI facts after the model call; changed facts stop execution. The adapter independently checks policy, ownership and the actual control. Each click in this reviewed vocabulary changes views, so discovery waits for a different completed view before the next decision. Fill/select verify against the current view. No failed action is automatically retried.
4. **Verify:** A model's `finish` does not establish success. The runner requires prior account-membership verification, explicit application of both search inputs, matching detail identity, completed detail loading and valid typed extraction. Outputs stay in caller memory; only field names are saved.
5. **Compile:** Build a new versioned capability from actual successful actions and observed before/after checkpoints. References such as `{"input":"transaction_id"}` replace runtime values. Add trusted completed-load guards for the two known negative outcomes and the reviewed extraction operation. These guards and extraction are compiler-supplied; the negative cases were not discovered in the positive run.

A failed, timed-out, stopped or unverified run cannot yield a successful capability. Test-double runs never export a capability marked as live discovery. The artifact and matching profile are validated and hashed. They remain pending review; registry promotion and deterministic replay belong to Step 6. Run-scoped candidates may share the proposed `0.2.0` version; a registry must enforce immutable identity/version/content on promotion.

## Evidence

Each bundle includes:

- `manifest.json`: hashes of the reviewed adapter contract, profile and policy, with a link to the final discovery result.
- `discovery-manifest.json`: requested model/provider, limits, goal/schema hashes, prompt and explicit scope.
- `model-request-NNN.json`: exact sanitized context and decision schema sent for that call.
- `model-response-NNN.json`: validated decision, response ID, returned model version and token usage. Rejected output is removed; only a rejection category and receipt are retained when available.
- `execution-NNN.json`: the dispatched parameterized action and verified UI facts before and after it, linked to its model response.
- `events.jsonl`: the shared adapter's safety/operation audit.
- `discovery-result.json`: final status, usage, verified output field names and artifact hashes, with no bank values.
- `capability.json` and `surface-profile.json`: generated only after verified live success. Failed runs may include a redacted structural failure snapshot.

The compiler adds readiness waits and final extraction, so compiled steps can outnumber model-selected UI actions. Evidence makes that distinction explicit. API receipts and the trace provide inspectable provenance, not a cryptographic attestation of OpenAI's response or an immutable audit service. Treat a capability as promotable only alongside a successful, verified bundle.

Verify a saved successful bundle offline, without model or browser calls:

```sh
.venv/bin/python -m tools.verify discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
```

The verifier checks contract/profile validity, hashes, request/response links, model receipts, decision/action correspondence, checkpoint continuity, token totals and recompilation equality. It does not re-execute the workflow or prove behavior with a second input.

## Verified run and tests

The retained successful run used `gpt-5.4-mini-2026-03-17`: seven genuine model calls, six selected UI actions, nine compiled steps, 6,894 total tokens, and approximately 21 seconds. It looked up the configured synthetic transaction through account activity, search and details. See `evidence/discovery/README.md` for the run and the earlier failed attempt.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Discovery tests use explicitly labeled test doubles and do not spend API tokens. They cover model-selected order differing from the design example, redaction, premature success, unlisted actions, explicit parameter application, missing membership, invalid responses and step/time/token limits. The full suite also covers the previous contract, browser and safety behavior.

## API references

The implementation follows the official [Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create) and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs). Current charges are governed by [API pricing](https://developers.openai.com/api/docs/pricing).
