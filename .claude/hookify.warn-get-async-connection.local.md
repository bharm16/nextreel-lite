---
name: warn-get-async-connection
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: new_text
    operator: regex_match
    pattern: get_async_connection\s*\(
---

**`get_async_connection()` raises `NotImplementedError`.**

Use the connection pool context manager instead:

```python
async with pool.acquire() as conn:
    async with conn.cursor() as cursor:
        await cursor.execute(query, params)
```
