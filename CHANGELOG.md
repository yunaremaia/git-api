# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- `tests/test_install_name.py`: regression guard asserting the declared
  distribution name never collides with a third-party PyPI project and that no
  tracked doc tells readers to `pip install` a bare name that is not ours.

### Changed

- Distribution renamed from `git-api` to `git-api-py`. The short `git-api` name
  on PyPI belongs to an unrelated project (JBYT27/Git-API), so a bare
  `pip install git-api` would silently install another author's code instead of
  failing. The console script (`git-api`) and the import package (`git_api`) are
  unchanged — only the distribution name moves, since that is the only one of the
  three that must be unique across all of PyPI.

### Fixed

## [Initial Release]

- Initial project release
