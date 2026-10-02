"""Regression guard for the declared distribution name.

The distribution name is read from ``setup.py`` at import time, never hardcoded,
so a future rename cannot leave the docs pointing at a package that is not ours.

One fact is pinned here: the PyPI name ``git-api`` belongs to an unrelated
third-party project (JBYT27/Git-API), so it must never be this project's
distribution name and must never appear as a bare install target.

Note the repo name, the console script and the import package all stay
``git-api``/``git_api``. Only the *distribution* name carries the ``-py`` suffix,
because the distribution name is the only one of the four that has to be unique
across all of PyPI.

The collision is worse than a plain 404. ``git-api`` on PyPI is at 1.7.5 with the
summary "A GitHub API. Extracts data from GitHub into json style data", so a bare
``pip install git-api`` does not fail loudly -- it installs somebody else's code.
The distribution rename removes that failure mode; this file keeps it removed.

Only one fact flips on publication. Publishing is gated on creating the project on
PyPI and registering a trusted publisher; once ``pip install git-api-py``
resolves, the git line becomes unnecessary and installing *that* name becomes
correct. Flip ``PUBLISHED`` in that same commit -- do not leave a guard that forces
one of two wrong states. The ``git-api`` entry does not move with the flag.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
SETUP_PY = REPO_ROOT / "setup.py"

# Flip to True in the same commit that restores the PyPI install line, once
# https://pypi.org/pypi/git-api-py/json answers 200.
PUBLISHED = False

# The repository name -- which is also the console script name, and the name of
# the third-party PyPI project.
REPO_NAME = "git-api"

# Every tracked surface a reader can copy an install line out of.
DOC_SURFACES = (
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "SUPPORT.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "action.yml",
    ".pre-commit-hooks.yaml",
    "Dockerfile",
)


def _setup_py_dist_name(path: Path) -> str:
    """Return the ``name=`` passed to ``setup()``, read without executing it.

    ``setup.py`` is parsed rather than imported: running it would invoke
    ``setup()`` and either try to build or emit a distutils error. Parsing keeps
    the guard free of side effects and free of setuptools being importable.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = getattr(func, "id", None) or getattr(func, "attr", None)
        if called != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    raise AssertionError(f"no literal name= found in the setup() call in {path}")


DIST_NAME: str = _setup_py_dist_name(SETUP_PY)

EXPECTED_INSTALL = (
    f"pip install {DIST_NAME}"
    if PUBLISHED
    else f"pip install git+https://github.com/yunaremaia/{REPO_NAME}.git"
)

# Install targets that must never appear. While unpublished, a bare
# `pip install git-api-py` 404s just like the short name does; the short name
# fails worse, by silently installing another author's project.
#
# REPO_NAME is permanent and is NOT gated on PUBLISHED: `git-api` on PyPI is
# JBYT27's project and will never be this one, so no future publication makes it
# a valid install target here.
FORBIDDEN_TARGETS = {REPO_NAME} | (set() if PUBLISHED else {DIST_NAME})

# `pip install`, `pip3 install`, `uv tool install`, `uv pip install` and
# `python -m pip install`, plus everything after them on the line.
INSTALL_COMMAND = re.compile(
    r"(?:uv\s+(?:tool|pip)|pip3?|python3?\s+-m\s+pip)\s+install(?P<args>[^\n]*)",
    re.MULTILINE,
)

# A PEP 508 requirement: a bare name, optional extras, optional version spec.
#
# The negative lookahead `(?![\w.-])` is load-bearing. A plain substring check
# for "pip install git-api" is True for "pip install git-api-py", so the naive
# grep would flag the very line it is meant to protect -- the one install command
# that becomes correct the day this project is published. Anchoring the name and
# refusing to stop mid-token keeps the two apart.
#
# This also rejects, for free, every target that is not a bare name: a `git+` URL
# fails the spec part at the `+`, and `.` / `.[dev]` never start with an
# alphanumeric.
REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?![\w.-])"
    r"(?P<spec>\[[^\]]*\])?(?:[<>=!~].*)?$"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _requirement_name(token: str) -> str | None:
    """Return the distribution name a pip target token names, if it names one."""
    match = REQUIREMENT.match(token)
    return match.group("name") if match else None


def install_targets(line: str) -> list[str]:
    """Return the distribution names pip would be handed by an install command."""
    names = []
    for command in INSTALL_COMMAND.finditer(line):
        try:
            tokens = shlex.split(command.group("args"))
        except ValueError:
            tokens = command.group("args").split()
        for token in tokens:
            if token.startswith("-"):  # -e, --upgrade, -r, --no-cache-dir ...
                continue
            name = _requirement_name(token)
            if name is not None:
                names.append(name)
    return names


def bare_install_lines(text: str) -> list[str]:
    """Return every line in *text* that installs a forbidden bare target.

    Only the tokens pip would actually receive are considered, so options and
    their values never register: a legitimate `git clone` + `pip install -e .`
    from-source block cannot show up as an offender.
    """
    return [
        line.strip()
        for line in text.splitlines()
        if FORBIDDEN_TARGETS.intersection(install_targets(line))
    ]


def doc_surfaces() -> list[tuple[str, Path]]:
    """The DOC_SURFACES that exist in this checkout."""
    return [
        (name, REPO_ROOT / name) for name in DOC_SURFACES if (REPO_ROOT / name).exists()
    ]


class TestDocsInstallFromGitWhileUnpublished:
    """The expected install line is asserted first, so a failure names the fix."""

    def test_readme_carries_the_expected_install_line(self):
        assert EXPECTED_INSTALL in _read(README), f"README must carry `{EXPECTED_INSTALL}`"


class TestNoBarePyPIInstallAnywhere:
    def test_surfaces_were_found(self):
        """Guard the guard: an empty surface list would assert nothing."""
        found = doc_surfaces()
        assert found, f"none of {DOC_SURFACES} exists -- the surface list is stale"
        assert "README.md" in [name for name, _ in found]

    def test_no_surface_installs_a_forbidden_bare_name(self):
        offenders = {
            name: lines[:5]
            for name, path in doc_surfaces()
            if (lines := bare_install_lines(_read(path)))
        }
        offenders = {name: lines for name, lines in offenders.items() if lines}
        assert not offenders, (
            f"these files tell readers to `pip install` {sorted(FORBIDDEN_TARGETS)}, which on "
            f"PyPI is not this project. Use `{EXPECTED_INSTALL}`. "
            f"Offending files and lines: {offenders}"
        )

    def test_no_pypi_badge_while_unpublished(self):
        """A PyPI badge renders "not found" for a package that is not on PyPI."""
        badges = [
            line for line in _read(README).splitlines() if "img.shields.io/pypi" in line
        ]
        assert not badges, f"PyPI badge would render broken: {badges}"


class TestTargetParsing:
    """Literal inputs, so editing a constant above cannot make these pass."""

    def test_git_install_is_not_a_bare_name(self):
        line = "pip install git+https://github.com/yunaremaia/git-api.git"
        assert install_targets(line) == []
        assert bare_install_lines(line) == []

    def test_short_name_is_a_bare_name(self):
        assert install_targets("pip install git-api") == ["git-api"]
        assert bare_install_lines("pip install git-api")

    def test_uv_and_pip3_variants_are_covered(self):
        for line in (
            "uv tool install git-api",
            "uv pip install git-api",
            "pip3 install git-api",
        ):
            assert bare_install_lines(line), line

    def test_unrelated_packages_are_not_caught(self):
        for line in (
            "pip install pre-commit",
            "python -m pip install --upgrade pip",
            "python -m pip install build twine",
        ):
            assert install_targets(line) and not bare_install_lines(line), line

    def test_from_source_blocks_are_not_caught(self):
        """`git clone` + `pip install -e .` is a legitimate install, not a lie."""
        for line in (
            "pip install -e .",
            'pip install -e ".[dev]"',
            "pip install -r requirements.txt",
            "RUN pip install --no-cache-dir .",
        ):
            assert not bare_install_lines(line), line

    def test_bare_distribution_name_is_forbidden_while_unpublished(self):
        """`-py` is still a bare install target, and it still 404s."""
        line = f"pip install {DIST_NAME}"
        assert install_targets(line) == [DIST_NAME]
        assert bool(bare_install_lines(line)) is not PUBLISHED


class TestTheRegexIsNotANaiveSubstringCheck:
    def test_a_substring_check_could_not_separate_the_two_names(self):
        """Documents the trap this guard exists to avoid.

        The needle is built from the repo name, so the check reads as the naive
        grep it warns about rather than as two unrelated literals.
        """
        needle = f"pip install {REPO_NAME}"
        assert needle in f"{needle}-py"

    def test_the_requirement_parser_does_separate_them(self):
        assert _requirement_name("git-api-py") == "git-api-py"
        assert _requirement_name("git-api") == "git-api"
        assert (
            _requirement_name("git+https://github.com/yunaremaia/git-api.git") is None
        )


class TestTheSquattedNameStaysForbiddenForever:
    """The one rule that does not move when PUBLISHED flips."""

    def test_repo_name_is_forbidden_regardless_of_published(self):
        assert REPO_NAME in FORBIDDEN_TARGETS, (
            f"`{REPO_NAME}` is another author's PyPI name; it must stay forbidden "
            "after publication too"
        )

    def test_repo_name_is_never_the_chosen_distribution_name(self):
        """If these ever match, the rename was reverted and the hijack is back."""
        assert DIST_NAME != REPO_NAME, (
            f"distribution name {DIST_NAME!r} collides with the third-party "
            f"PyPI project {REPO_NAME!r}"
        )

    def test_repo_name_is_forbidden_in_a_hypothetical_published_world(self):
        """Assert the post-publication behaviour directly, flag included."""
        published_targets = {REPO_NAME} | (set() if PUBLISHED else {DIST_NAME})
        offenders = [
            line
            for line in (f"pip install {REPO_NAME}", f"pip install {DIST_NAME}")
            if published_targets.intersection(install_targets(line))
        ]
        assert f"pip install {REPO_NAME}" in offenders
        assert (f"pip install {DIST_NAME}" in offenders) is not PUBLISHED


class TestReadmeDisclosesTheNameSituation:
    """A reader who sees `git-api-py` deserves to know what is going on."""

    def test_short_name_is_disclosed_as_foreign(self):
        text = _read(README).lower()
        assert REPO_NAME in text
        assert "pypi" in text
        assert any(
            phrase in text
            for phrase in (
                "different author",
                "another author",
                "unrelated",
                "taken",
                "another project",
            )
        ), f"README must say the {REPO_NAME} PyPI name belongs to another project"

    def test_unpublished_state_is_disclosed(self):
        """Otherwise a git URL in the install block reads as a mistake."""
        text = _read(README).lower()
        assert any(
            phrase in text for phrase in ("not published", "not yet on pypi", "not yet published")
        ), "README must state the project is not on PyPI yet, so the git URL is expected"


class TestPackaging:
    def test_console_script_name_is_the_repo_name(self):
        """Only the distribution moves; the command a user types does not."""
        source = _read(SETUP_PY)
        assert f'"{REPO_NAME}={REPO_NAME.replace("-", "_")}.cli:main"' in source, (
            f"console script must stay `{REPO_NAME}`; only the distribution "
            "carries the `-py` suffix"
        )

    def test_import_module_is_unchanged(self):
        """The `-py` suffix belongs to the distribution, never the import path."""
        assert (REPO_ROOT / REPO_NAME.replace("-", "_")).is_dir(), (
            f"import package {REPO_NAME.replace('-', '_')}/ must exist unchanged"
        )
        assert (REPO_ROOT / REPO_NAME).is_dir() is False, (
            f"the `-py` suffix must never create a {REPO_NAME}/ import directory"
        )

    def test_console_script_target_is_importable(self):
        """The entry point must resolve against the package that ships in the wheel.

        ``setup.py`` uses ``find_packages()`` over a flat layout, so the wheel
        carries ``git_api/`` at its root. A target pointing anywhere else (for
        example ``cli:main``) ships a console script that raises ImportError on
        every install.
        """
        source = _read(SETUP_PY)
        match = re.search(rf'"{REPO_NAME}=(?P<module>[\w.]+):(?P<attr>\w+)"', source)
        assert match, f"console script entry not found in {SETUP_PY}"

        module_path, attr = match.group("module"), match.group("attr")
        top_level = module_path.split(".")[0]
        assert top_level == REPO_NAME.replace("-", "_"), (
            f"console script imports {top_level!r}; the import module must stay "
            f"{REPO_NAME.replace('-', '_')!r} regardless of the distribution name"
        )

        resolved = REPO_ROOT / Path(*module_path.split(".")).with_suffix(".py")
        assert resolved.exists(), (
            f"console script module not packaged: {module_path} ({resolved})"
        )

        assert re.search(
            rf"^def {re.escape(attr)}\b", resolved.read_text(encoding="utf-8"), re.MULTILINE
        ), f"{module_path} does not define {attr}() in {resolved.name}"


@pytest.mark.parametrize("line", ["pip install pre-commit", "pip install -e ."])
def test_parser_noise_is_not_reported(line):
    """Explicitly assert the two shapes that produced false positives before."""
    assert bare_install_lines(line) == []