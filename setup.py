from pathlib import Path

from setuptools import setup, find_packages

README = Path(__file__).parent / "README.md"

setup(
    # The PyPI name `git-api` belongs to JBYT27/Git-API, an unrelated project.
    # `git-api-py` follows the short-`-py` convention and is free on PyPI.
    # The console script below and the import package stay `git-api`/`git_api`.
    name="git-api-py",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "git-api=git_api.cli:main",
        ],
    },
    python_requires=">=3.11",
    description="Git-native API REPL - persist requests as JSON in your repo",
    long_description=README.read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
)
