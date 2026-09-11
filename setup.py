from setuptools import setup, find_packages

setup(
    name="git-api",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "git-api=git_api.cli:main",
        ],
    },
    python_requires=">=3.11",
    description="Git-native API REPL - persist requests as JSON in your repo",
)
