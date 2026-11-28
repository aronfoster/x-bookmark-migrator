# Repository instructions

For project planning, first read ROADMAP.md and the relevant section of
docs/SPRINTS.md; v1 search and categorization acceptance lives in
docs/V1_ACCEPTANCE.md. Per owner direction on 18 November 2026, GitHub owns the
project plan and sprint/milestone changes. Linear tracks concise outcomes, status,
dependencies and private evidence links. Read the current working agreement and
latest linked-ticket checkpoint, but do not recreate a second detailed plan in
Linear. Preserve explicit owner decisions and existing live-operation boundaries.

Before changing an API integration, read the current official X API documentation
for every affected endpoint and shared behavior. Cite direct documentation links
in the issue and PR. Start with docs/API_AUDIT.md; recheck its links rather than
assuming the recorded contract is still current. Distinguish documentation,
observed live behavior, local policy and unresolved gaps. Never infer one endpoint's
response schema or pagination guarantees from another endpoint.

Use only documented official APIs. Never publish real tokens, callback URLs,
account IDs/usernames, bookmark content, folder names or private diagnostic filenames.
Tests must use synthetic data. When iterating on code changes, run targeted tests (`uv run pytest -q tests/<file> -k <pattern>`). Before uploading a PR that changes code, tests, or dependencies, run:

```
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Skip the test suite for docs-only or instruction-only changes. Synthetic tests do not prove live access, billing, completeness or archival.

For local desktop deployment, do not improvise throwaway multi-step installer scripts in `~/.cache/` or `/tmp/`. Prefer a simple repo-tracked deploy script or direct `sudo cp` / `systemctl restart` commands, and always syntax-check (`python3 -m py_compile` / `bash -n`) any script handed to the user before presenting it.
