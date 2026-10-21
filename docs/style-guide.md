# Python style guide

These are the project's conventions, informed by the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
We use Ruff's formatter with an 88-character line length and the import choices
below rather than adopting every Google rule. Ruff enables naming (`N`) and
Google-convention docstring (`D`) checks. Only the test-module docstring rule
(`D100`) is omitted: descriptive test filenames already explain their scope.

## Imports and module privacy

Keep imports at module level and avoid wildcard imports. Use relative imports
between sibling modules and absolute imports across functional subpackages or
when consuming a package's public API. Let Ruff order imports.

A single leading underscore means **internal to the defining module**. Do not
import or access underscore-prefixed helpers, types, or constants from another
module, including tests. Shared definitions have names without an underscore.
Python special names such as `__version__` are a separate convention.

Prefer module imports for shared implementation helpers so their owner is clear:

```python
# Inside soybean_sim.data.usda.loader:
from soybean_sim.models import Commodity, MarketBalance, Region

from . import mappings, parsing

quantity = parsing.parse_observation(row, source_context)
production_field, description = mappings.ATTRIBUTES["028"]
```

Keep implementation imports directed toward sibling modules rather than their
package's re-exported names to avoid circular imports. External consumers use
the stable package API:

```python
from soybean_sim.data.usda import USDADataError, load_usda_psd_csv
```

Re-export stable entry points through `__init__.py` and document them in
`__all__`. A shared module definition such as `parsing.ParsedQuantity` does not
need a package-level export. Consumers outside USDA ingestion should use its
package API; compatibility tests may inspect nonprivate implementation names.
Underscores and `__all__` express intent, not enforced access control.

## Names and constants

| Element | Shared or public | Module-local |
| --- | --- | --- |
| Constant | `UPPER_SNAKE_CASE` | `_UPPER_SNAKE_CASE` |
| Function | `snake_case` | `_snake_case` |
| Class or type alias | `PascalCase` | `_PascalCase` |

Local variables use `snake_case`. Avoid double-leading underscores for privacy.
Preserve external contracts: model fields, enum values, CSV codes, YAML keys,
CLI commands, and established public imports.

Extract a literal when a name explains its meaning or keeps related uses
consistent. `TONNES_PER_SOURCE_UNIT` describes a conversion; zero bounds and
nonempty lengths can stay inline. Type aliases such as `NonNegativeTonnes`
describe types rather than numeric constants.

Use `typing.Final` for fixed module constants so Pyright catches reassignment.
It does not freeze contents: use `MappingProxyType` for fixed lookup tables and
`frozenset` for fixed collections of unique values. Keep nested values immutable
too, as with the USDA tables' tuples of enums and strings.
Keep constants with their domain: USDA codes and units belong in
`data/usda/mappings.py`, while `_YEAR_LABEL_PATTERN` stays in `parsing.py`.
Avoid a global constants module containing unrelated values.

## Docstrings

Explain what a function does and any non-obvious units, inputs, or results.
Keep docstrings shorter than the implementation: small helpers usually need
one sentence. Follow Google docstring layout; use `Args` and `Returns` only when
they add useful information. Keep exception details in the relevant API
documentation rather than repeating them in every docstring. Source modules
have a short description of their responsibility; test modules need none.

## Types, readability, and errors

Annotate function parameters and returns using Python 3.12 syntax: `str | None`,
`list[str]`, and existing domain types. Prefer precise types over `Any`, including
raw CSV cells that may be missing before validation. Keep Pyright in standard mode.

Use focused functions, guard clauses, and descriptive local names. Review
functions approaching 40 lines of executable code for mixed responsibilities,
but do not split already readable code just to reduce its length.

Catch specific exceptions and preserve file paths, row numbers, and invalid
values in messages. Chain translated exceptions with `raise ... from ...`.
Use the existing USDA error categories; retain `ValueError` in Pydantic validators
and for invalid loader arguments. Avoid bare catches and silent failures.

Comments explain domain decisions or limitations, such as excluded local USDA
variants. Omit comments that merely describe the next Python statement.

Run `just check` for Ruff linting, formatting, Pyright, and tests. Test behavior
through public APIs where possible. We use these tools instead of Google's
Pylint setup and keep the existing import policy and concise documentation.
