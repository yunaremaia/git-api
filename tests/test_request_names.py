"""Tests for saved-request name resolution (issue #35).

``save_request()`` rewrites the name into a filesystem-safe form, but ``replay``
and ``curl`` looked the file up with the raw name. Any name containing a space
or punctuation was written under one filename and searched for under another,
so ``save`` reported success and the very next ``replay`` claimed the request
did not exist.

Worse, two distinct names can sanitize to the same file, and the write was not
exclusive, so the second ``save`` destroyed the first request with no warning.
"""

import json

import pytest

from git_api import cli


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Temp CWD; ``cli.py`` resolves ``.git-api`` from the CWD."""
    monkeypatch.chdir(tmp_path)
    cli.readline.clear_history()
    return tmp_path


def _run_repl(lines, monkeypatch, *, execute_request=None):
    """Drive ``cli.main()`` over ``lines``; EOF ends the loop."""
    from unittest.mock import patch

    iterator = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)

    patches = [
        patch.object(cli.readline, "read_history_file"),
        patch.object(cli.readline, "write_history_file"),
        patch.object(cli.readline, "add_history"),
    ]
    if execute_request is not None:
        patches.append(patch.object(cli, "execute_request", execute_request))
    for p in patches:
        p.start()
    try:
        cli.main()
    finally:
        for p in reversed(patches):
            p.stop()


def _seed(name, **kwargs):
    """Save a request under *name* and return the path it landed on."""
    cli.init()
    return cli.save_request(
        name, "get", "https://api.example.com/x", {}, None, 200, {}, "ok",
        **kwargs,
    )


# --- canonical name resolution ---------------------------------------------


def test_safe_name_keeps_alphanumeric_dash_and_underscore():
    assert cli.safe_name("my-call_2") == "my-call_2"


def test_safe_name_replaces_spaces_and_punctuation():
    assert cli.safe_name("my request!") == "my_request_"


def test_safe_name_neutralises_path_traversal():
    safe = cli.safe_name("../../etc/passwd")
    assert ".." not in safe
    assert "/" not in safe


def test_safe_name_is_idempotent():
    """The sanitizer must be stable, or lookup can never match the write."""
    once = cli.safe_name("my first request")
    assert cli.safe_name(once) == once


def test_save_then_replay_with_a_spaced_name_round_trips(capsys, monkeypatch):
    """The regression: save printed a path, replay could not find it."""
    _seed("my first request")
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append(url)
        return 200, {}, b"ok"

    _run_repl(["replay my first request", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert calls == ["https://api.example.com/x"]
    assert "No saved request" not in capsys.readouterr().out


def test_replay_finds_a_name_with_punctuation(capsys, monkeypatch):
    _seed("my:req#1")
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append(url)
        return 200, {}, b"ok"

    _run_repl(["replay my:req#1", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert calls == ["https://api.example.com/x"]


def test_replay_also_accepts_the_sanitized_name(capsys, monkeypatch):
    """Both spellings resolve, so `list` output stays usable as an argument."""
    _seed("my first request")
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append(url)
        return 200, {}, b"ok"

    _run_repl(["replay my_first_request", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert calls == ["https://api.example.com/x"]


def test_curl_finds_a_name_with_a_space(capsys, monkeypatch):
    _seed("my first request")
    _run_repl(["curl my first request", "quit"], monkeypatch)
    out = capsys.readouterr().out
    flat = " ".join(out.replace("\\\n", " ").split())
    assert "https://api.example.com/x" in flat
    assert "No saved request" not in out


def test_replay_cannot_escape_the_requests_directory(capsys, monkeypatch):
    """The raw name used to be interpolated straight into the path.

    `../escape` resolved to .git-api/escape.json, outside the requests
    directory, and would have been read if such a file existed. The sanitizer
    is now applied on reads too.
    """
    cli.init()
    outside = cli.GAPI_DIR / "escape.json"
    outside.write_text('{"request": {}, "response": {}}')
    try:
        _run_repl(["replay ../escape", "quit"], monkeypatch,
                  execute_request=lambda *a: (200, {}, b"ok"))
        assert "No saved request: ../escape" in capsys.readouterr().out
    finally:
        outside.unlink()


def test_missing_request_still_reports_the_name_as_typed(capsys, monkeypatch):
    _run_repl(["replay never saved", "quit"], monkeypatch)
    assert "No saved request: never saved" in capsys.readouterr().out


# --- no silent overwrite on sanitize collision -----------------------------


def test_saving_a_colliding_name_does_not_overwrite(capsys):
    """`foo bar` and `foo/bar` both sanitize to foo_bar.json.

    The first request must survive: losing it silently is data loss the user
    never agreed to.
    """
    _seed("foo bar")
    cli.save_request("foo/bar", "get", "https://api.example.com/other", {},
                     None, 200, {}, "other")
    saved = json.loads(
        (cli.GAPI_DIR / "requests" / "foo_bar.json").read_text()
    )
    assert saved["request"]["url"] == "https://api.example.com/x"
    assert "already exists" in capsys.readouterr().out


def test_save_reports_the_existing_request_instead_of_pretending(capsys):
    _seed("my call")
    out_before = capsys.readouterr().out
    path = cli.save_request("my/call", "get", "https://api.example.com/x", {},
                            None, 200, {}, "ok")
    out = capsys.readouterr().out
    assert "Saved to" not in out
    assert "my call" in out  # points at the name that actually owns the file
    assert path.exists()


def test_overwrite_is_possible_when_asked_for_explicitly():
    _seed("my call")
    path = cli.save_request("my/call", "get", "https://api.example.com/new",
                            {}, None, 200, {}, "new", overwrite=True)
    saved = json.loads(path.read_text())
    assert saved["request"]["url"] == "https://api.example.com/new"


def test_resaving_the_same_name_is_not_treated_as_a_collision():
    """Identical names are an update, not a collision -- they are one request."""
    _seed("my-call")
    path = cli.save_request("my-call", "get", "https://api.example.com/new",
                            {}, None, 200, {}, "new")
    assert json.loads(path.read_text())["request"]["url"] == \
        "https://api.example.com/new"


def test_list_shows_the_saved_name_for_a_sanitized_file(capsys, monkeypatch):
    _seed("my first request")
    _run_repl(["list", "quit"], monkeypatch)
    assert "my_first_request: GET https://api.example.com/x" in \
        capsys.readouterr().out


# --- the save --force flag --------------------------------------------------


def test_repl_save_refuses_a_colliding_name_by_default(capsys, monkeypatch):
    """The whole round trip through the REPL, not just the helper."""
    _run_repl([
        "GET https://api.example.com/first",
        "save foo bar",
        "POST https://api.example.com/second payload",
        "save foo/bar",
        "quit",
    ], monkeypatch, execute_request=lambda *a: (200, {}, b"ok"))

    out = capsys.readouterr().out
    assert "already exists" in out
    saved = json.loads(
        (cli.GAPI_DIR / "requests" / "foo_bar.json").read_text()
    )
    assert saved["request"]["url"] == "https://api.example.com/first"


def test_repl_save_force_replaces_the_colliding_request(capsys, monkeypatch):
    _run_repl([
        "GET https://api.example.com/first",
        "save foo bar",
        "GET https://api.example.com/second",
        "save foo/bar --force",
        "quit",
    ], monkeypatch, execute_request=lambda *a: (200, {}, b"ok"))

    saved = json.loads(
        (cli.GAPI_DIR / "requests" / "foo_bar.json").read_text()
    )
    assert saved["request"]["url"] == "https://api.example.com/second"
    assert saved["name"] == "foo/bar"


def test_repl_save_without_a_name_shows_usage(capsys, monkeypatch):
    _run_repl(["save", "quit"], monkeypatch)
    assert "Usage: save NAME [--force]" in capsys.readouterr().out


def test_repl_save_force_without_a_name_shows_usage(capsys, monkeypatch):
    _run_repl(["GET https://api.example.com/x", "save --force", "quit"],
              monkeypatch, execute_request=lambda *a: (200, {}, b"ok"))
    assert "Usage: save NAME [--force]" in capsys.readouterr().out


def test_repl_save_with_a_spaced_name_round_trips(capsys, monkeypatch):
    """`save my first request` then `replay my first request`."""
    calls = []
    _run_repl([
        "GET https://api.example.com/x",
        "save my first request",
        "replay my first request",
        "quit",
    ], monkeypatch, execute_request=lambda *a: (calls.append(1), (200, {}, b"ok"))[1])

    assert len(calls) == 2
    assert "No saved request" not in capsys.readouterr().out