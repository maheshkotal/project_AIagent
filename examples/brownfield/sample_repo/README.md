# URL Shortener (v1)

A small FastAPI + SQLAlchemy URL shortener. Used as the **brownfield sample** for agentforge.

```bash
uvicorn app.main:create_app --factory   # run
pytest                                  # test
```

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/links` | Create a short link (`{"url": ..., "custom_alias": optional}`) |
| GET | `/api/v1/links/{code}` | Link details and click count |
| GET | `/{code}` | 307 redirect to the target URL |

## Known issues

- Creating a link with a custom alias that is already taken returns **500** (should be 409).
- Links never expire.
