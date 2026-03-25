---
name: block-fstring-logging
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: new_text
    operator: regex_match
    pattern: logger\.\w+\(f["']
---

**Blocked: f-string logging detected.**

This project requires `%s`-style lazy formatting for all logger calls. f-strings evaluate eagerly and bypass log-level filtering.

**Wrong:**
```python
logger.info(f"Fetched {count} movies in {elapsed:.2f}s")
```

**Correct:**
```python
logger.info("Fetched %d movies in %.2fs", count, elapsed)
```
