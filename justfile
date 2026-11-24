# List available recipes when running `just` without arguments.
default:
    @just --list

# Install all dependency groups from uv.lock, matching CI.
install:
    uv sync --locked --all-groups

# Check Python code with Ruff.
lint:
    uv run --locked ruff check .

# Apply Ruff formatting to Python code.
format:
    uv run --locked ruff format .

# Check formatting without modifying files.
format-check:
    uv run --locked ruff format --check .

# Check Python type annotations in the application package.
typecheck:
    uv run --locked pyright

# Run tests (the project's pytest configuration already enables coverage).
test:
    uv run --locked pytest

# Run integration tests without requiring full-suite coverage.
integration:
    uv run --locked pytest tests/integration --no-cov

# Display the synthetic physical market report.
report-market:
    uv run --locked python tests/support/smoke_test_market.py

# Display the synthetic baseline and drought comparison.
report-scenarios:
    uv run --locked python tests/support/compare_scenarios.py

# Display the historical baseline and drought comparison using local datasets.
report-historical:
    uv run --locked python tests/support/smoke_test_historical_market.py --compare

# Run tests with the same explicit coverage options as CI.
coverage:
    uv run --locked pytest --cov=soybean_sim --cov-report=term-missing

# Run the CI lint, formatting, type, and test checks.
check: lint format-check typecheck coverage

# Display help for the implemented CLI commands.
cli:
    uv run --locked soybean-sim --help

# Launch the synthetic-data dashboard from the repository root.
dashboard:
    uv run --locked streamlit run dashboard/app.py

# Remove project caches and test reports; preserve .venv, datasets, and Git files.
clean:
    find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
    rm -rf ./.artifacts ./.pytest_cache ./.ruff_cache ./htmlcov ./__pycache__
    rm -f ./.coverage ./.coverage.* ./coverage.xml
