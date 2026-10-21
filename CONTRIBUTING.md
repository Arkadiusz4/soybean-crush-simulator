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

## Imports and public APIs

Use relative imports between sibling modules within a functional subpackage,
and absolute imports across subpackages or when consuming a public API:

```python
# Inside soybean_sim.data.usda.loader:
from soybean_sim.models import Commodity, MarketBalance, Region

from .mappings import _ATTRIBUTES
from .parsing import _parse_observation

# In a consumer outside the USDA package:
from soybean_sim.data.usda import USDADataError, load_usda_psd_csv
```

Keep imports at module level, avoid wildcard imports, and let Ruff order them.
Within a subpackage, import from the sibling implementation module rather than
its `__init__.py` to avoid circular imports.

Public names have no leading underscore; internal helpers use one. Sibling
modules may share internal helpers, but consumers outside the subpackage should
use its public API so internal refactors do not break them. Compatibility tests
may inspect implementation modules to verify public exports.

Re-export stable entry points in the relevant `__init__.py` and list them in
`__all__`, as in `models` and `data.usda`. Keep helpers internal and avoid adding
exports for unimplemented components. Underscores signal intent; `__all__`
controls wildcard imports. Neither enforces access restrictions.

## Quality checks

```bash
just lint
just format-check
just typecheck
just test
just cli
uv run soybean-sim version
uv run soybean-sim validate-config configs/baseline.yaml
uv run python -c "import soybean_sim"
```

Tests are split into focused unit tests and CLI integration tests. `pytest`
includes coverage reporting and a 90% minimum. To apply formatting, run
`just format`. For reviewed automatic lint fixes, use
`uv run ruff check --fix .` and inspect the diff.

GitHub Actions runs independent `lint`, `typecheck`, and `tests` jobs on pull requests
targeting `main`, pushes to `main`, and manual dispatch. All use Python 3.12 and
`just install`, which installs all locked dependency groups through uv. CI runs
`just lint`, `just format-check`, `just typecheck`, and `just coverage`, matching
`just check` locally. Pyright checks `src/` in standard mode against Python 3.12.

After dependency changes, regenerate `uv.lock` with `uv lock`, verify with
`uv sync --locked`, and include both the dependency declaration and lockfile in
the pull request. Use `uv build` to check distributable packaging.

Keep domain rules independent from CLI and future dashboard code. Avoid adding
services, frameworks, or abstractions without an implemented requirement. Local
datasets belong in the ignored root `data/` directory, with provenance recorded
alongside them.
