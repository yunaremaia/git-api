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

- `{{variable}}` substitution now applies to headers, not only URLs and bodies.
  The help text promised all three, so `Authorization: Bearer {{token}}` was
  sent to the API with the placeholder intact and the resulting 401 pointed
  nowhere. `replay` substitutes header variables too, and a request that still
  contains an unresolved `{{...}}` after substitution now says so instead of
  sending the placeholder silently.
- `!!` repeats the immediately preceding command. It was reading one entry too
  far back, so re-sending a `POST` or `DELETE` actually re-ran an *older*
  request. `!!` on an otherwise empty history no longer feeds `None` to
  `shlex.split()`.
- `history.txt` is no longer truncated to `history_size`. `set_history_length()`
  caps the in-memory buffer that `write_history_file()` serialises, so every
  command rewrote the file with only the most recent N entries. `history_size`
  still bounds recall depth in memory; on-disk retention has its own cap.
- Saved request names now resolve consistently. `save` sanitized the name but
  `replay` and `curl` looked it up unsanitized, so any name with a space or
  punctuation was saved successfully and then reported as missing. Two
  different names that sanitize to the same filename (`foo bar` and `foo/bar`)
  no longer silently overwrite each other: the existing request is reported and
  kept unless `save --force` is passed. The sanitizer also runs on the read
  path, so a lookup can no longer reach outside `.git-api/requests/`.

## [Initial Release]

- Initial project release
