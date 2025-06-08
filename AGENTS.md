# Contributor Guidance

This repository contains a Python webhook service. Follow these guidelines when modifying code or documentation.

## Development workflow

1. Install dependencies before running tests:
   ```bash
   pip install -r requirements.txt
   ```
   If installation of `PyYAML` fails on your Python version, install version `6.0.1` manually:
   ```bash
   pip install PyYAML==6.0.1
   ```
2. Ensure all tests pass by running:
   ```bash
   pytest -q
   ```

## Code style

- Keep code compatible with Python 3.8+.
- Follow PEP 8 style conventions with 4-space indentation.
- Format Python files with `black` (target line length 100) and sort imports using `isort`.
- Include docstrings for new functions or classes.

## Pull requests

- Summarise your changes and note the test status in the PR description.
