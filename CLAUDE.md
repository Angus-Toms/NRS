# Style guidelines

This is a solo project. Write code as a competent human would — LEAN and DIRECT. The guiding principle is fail fast: if something is wrong, crash loudly and immediately rather than silently degrading or papering over errors.

## General

- Prefer clear, direct code over code that hedges against every theoretical misuse.
- Do NOT add None checks or defensive early returns just in case — if something shouldn't be None, let it fail.
- Comments should explain why, not describe what the code obviously does.
- Don't make a seperate function for every little thing. Prepare a monolithic function for readability, feel free to section it in comments if needed.


## Error handling

- Do NOT wrap everything in try/except. Only catch exceptions where you have a specific recovery strategy.
- Do NOT use broad except Exception or bare except clauses.
- Raise exceptions directly with a clear message; don't swallow or re-wrap them.
- I/O operations (file reads, network calls) are acceptable places to catch exceptions, but handle them explicitly — not just to suppress the error.