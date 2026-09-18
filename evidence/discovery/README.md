# Genuine discovery evidence

- **Successful run:** `dbce8a9d535c40a4ba91a003792854ea/`. Real OpenAI Responses calls to `gpt-5.4-mini`, returned model `gpt-5.4-mini-2026-03-17`. Seven decisions, six UI actions, nine compiled capability steps, 6,894 tokens, 20.941 seconds. The runtime independently verified account membership, detail identity and typed outputs. The generated capability/profile are version `0.2.0`. The original result retains its historical pending-review status; the same hashes were subsequently reviewed and pinned in `capabilities/registry.json`.
- **Earlier failed attempt:** `0e6b0be8d024418ab0d2763ab10928b4/`. Real model calls stopped on an invalid model decision; no capability was generated. Four validated replies and their usage receipts were retained. The fifth reply was rejected before its receipt was captured in that implementation, so the saved 3,762-token total is incomplete for this attempt. The current transport retains safe rejection categories and receipts for completed replies that fail JSON/schema validation.

No scripted decider is presented as a genuine model run. The goal/control vocabulary was supplied by reviewed application definitions; the action sequence was selected by the model. Compiler-added waits, negative-outcome guards and extraction are trusted contract operations, not model decisions or discovered negative examples.

Evidence excludes the API key, login credentials, runtime account/transaction values and extracted banking data. Source request IDs, model version, safe observations, decisions and typed input references remain reviewable. Nothing was uploaded or pushed to GitHub by the discovery command.

From the repository root:

```sh
.venv/bin/python -m tools.verify discovery evidence/discovery/dbce8a9d535c40a4ba91a003792854ea
```

This is an offline consistency check. Deterministic changed-input replay is implemented; see [replay evidence](../replay/README.md) and the [complete acceptance report](../acceptance/report.json).
