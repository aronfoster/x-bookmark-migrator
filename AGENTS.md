# Repository instructions

Before changing an API integration, read the current official X API documentation
for every affected endpoint and shared behavior. Cite direct documentation links
in the issue and PR. Start with docs/API_AUDIT.md; recheck its links rather than
assuming the recorded contract is still current. Distinguish documentation,
observed live behavior, local policy and unresolved gaps. Never infer one endpoint's
response schema or pagination guarantees from another endpoint.

Use only documented official APIs. Never publish real tokens, callback URLs,
account IDs/usernames, bookmark content, folder names or private diagnostic filenames.
Tests must use synthetic data. Verify locally before uploading:

```
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Synthetic tests do not prove live access, billing, completeness or archival.
