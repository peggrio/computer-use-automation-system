# Same-session takeover acceptance evidence

Run: [bddea514d5884c13ac3fbf15020d842b](bddea514d5884c13ac3fbf15020d842b/takeover-acceptance.json).

This is an automated real-browser exercise, explicitly labeled `source: test_harness` and `actual_human_participated: false`. No model calls were made. It does not claim an actual person completed takeover.

The harness logs out through the UI, verifies replay pauses, activates human ownership/recording, simulates login through the existing browser's form, explicitly returns the matching session/request/epoch ticket, and verifies replay succeeds after entry revalidation.

All acceptance checks passed: same page and session, advanced control epoch, correct transient typed transaction output, and automation ownership restored. Human-event files retain redacted action metadata; the initial paused replay result and resumed result are both preserved. Runtime credentials and bank values are omitted.

Try the interactive flow yourself:

```sh
.venv/bin/python -m tools.takeover --demo
```

Use the opened Chromium window to log in with the public local sample credentials, return to Accounts Overview, then type `resume` in the terminal. See [the intervention guide](../../docs/advanced/live-takeover.md) for scope and recovery semantics.
