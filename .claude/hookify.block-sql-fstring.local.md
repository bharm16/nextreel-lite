---
name: block-sql-fstring
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: new_text
    operator: regex_match
    pattern: f["'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\s
---

**Blocked: SQL query with f-string interpolation detected.**

All queries must use parameterized placeholders (`%s`), including LIMIT and OFFSET. f-string SQL is a SQL injection risk.

**Wrong:**
```python
query = f"SELECT * FROM movies WHERE id = {movie_id}"
```

**Correct:**
```python
query = "SELECT * FROM movies WHERE id = %s"
await cursor.execute(query, (movie_id,))
```

Use `MovieQueryBuilder` in `movies/query_builder.py` for complex queries.
