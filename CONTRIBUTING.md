# Contributing

Use Python 3.12+ and uv. Start with `uv sync --locked` from the repository root.
Keep changes small enough for another member of the three-person team to review.

## Branches and commits

Create branches from the latest `main` using a short description:

- `feat/config-validation`
- `fix/config-error-message`
- `docs/data-assumptions`
- `chore/tooling-update`

Use Conventional Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
or `chore:`. Write a short imperative summary, for example
`fix: reject duplicate regions in configuration`.

## Pull request workflow

1. Create a focused branch and implement the change with appropriate type hints.
2. Add tests for new behavior, especially validation boundaries and error paths.
3. Update documentation and examples when public behavior changes. Clearly
   distinguish implemented behavior from plans.
4. Run the checks below, push your branch, and open a pull request against `main`.
5. Describe the problem, resulting behavior, validation performed, and any known
   limitations. Ask a teammate to review and address feedback before merging.

These are team conventions; this bootstrap does not configure branch protection,
required approvals, or any repository settings.

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run soybean-sim --help
uv run soybean-sim version
uv run soybean-sim validate-config configs/baseline.yaml
uv run python -c "import soybean_sim"
```

Tests are split into focused unit tests and CLI integration tests. `pytest`
includes coverage reporting and a 90% minimum. To apply formatting, run
`uv run ruff format .`. For reviewed automatic lint fixes, use
`uv run ruff check --fix .` and inspect the diff.

GitHub Actions runs independent `lint` and `tests` jobs on pull requests targeting
`main`, pushes to `main`, and manual dispatch. Both use Python 3.12 and
`uv sync --locked --all-groups`. Local commands are available for checking changes
before opening a pull request.

After dependency changes, regenerate `uv.lock` with `uv lock`, verify with
`uv sync --locked`, and include both the dependency declaration and lockfile in
the pull request. Use `uv build` to check distributable packaging.

Keep domain rules independent from CLI and future dashboard code. Avoid adding
services, frameworks, or abstractions without an implemented requirement. Local
datasets belong in the ignored root `data/` directory, with provenance recorded
alongside them.
