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

# Run tests (the project's pytest configuration already enables coverage).
test:
    uv run --locked pytest

# Run tests with the same explicit coverage options as CI.
coverage:
    uv run --locked pytest --cov=soybean_sim --cov-report=term-missing

# Run the CI lint, formatting, and test checks.
check: lint format-check coverage

# Display help for the implemented CLI commands.
cli:
    uv run --locked soybean-sim --help

# Remove project caches and test reports; preserve .venv, datasets, and Git files.
clean:
    find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
    rm -rf ./.pytest_cache ./.ruff_cache ./htmlcov ./__pycache__
    rm -f ./.coverage ./.coverage.* ./coverage.xml
