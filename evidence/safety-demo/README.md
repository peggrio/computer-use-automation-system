# Step 4 safety evidence examples

These are scripted adapter checks, not LLM discovery or production replay evidence.

- `7f86430ef786423784b4dad3e6a78f1e/`: successful transaction lookup, structured logs and sanitized capability candidate.
- `730a80af3a7442f09860e9373cf52e46/`: deliberately injected activity HTTP failure, structured error logs and a redacted structural DOM snapshot.

Candidate artifacts require a new version and review before promotion. No credentials, runtime inputs, extracted bank values or raw snapshots are included.
