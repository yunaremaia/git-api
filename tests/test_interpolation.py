"""Tests for ``{{variable}}`` interpolation (issue #37).

The help text promises that ``{{var_name}}`` is substituted in URLs, headers
and bodies. Before this fix only the URL and the body went through the
substitution loop, so a templated header such as ``Bearer {{token}}`` was sent
to the API with the placeholder still in it -- and the resulting 401 gave the
user nothing to act on.
"""

import json

import pytest

from git_api import cli


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Run each test in a temp CWD; ``cli.py`` resolves ``.git-api`` from CWD."""
    monkeypatch.chdir(tmp_path)
    cli.readline.clear_history()
    return tmp_path


def _run_repl(lines, monkeypatch, *, execute_request=None, keep_history=False):
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
    ]
    if not keep_history:
        patches.append(patch.object(cli.readline, "add_history"))
    if execute_request is not None:
        patches.append(patch.object(cli, "execute_request", execute_request))
    for p in patches:
        p.start()
    try:
        cli.main()
    finally:
        for p in reversed(patches):
            p.stop()


def _write_config(headers=None, **env_vars):
    """Seed ``config.json`` with an active environment of our choosing."""
    cli.init()
    env = {"base_url": "https://api.example.com", "headers": headers or {}}
    env.update(env_vars)
    cli.CONFIG_FILE.write_text(json.dumps({
        "environments": {"default": env},
        "active_env": "default",
        "history_size": 100,
    }))
    return env


# --- _interpolate ----------------------------------------------------------


def test_interpolate_replaces_a_known_variable():
    assert cli._interpolate("Bearer {{token}}", {"token": "tok-1"}) == \
        "Bearer tok-1"


def test_interpolate_replaces_every_occurrence():
    text = "{{a}}/{{b}}/{{a}}"
    assert cli._interpolate(text, {"a": "1", "b": "2"}) == "1/2/1"


def test_interpolate_leaves_unknown_placeholders_untouched():
    """An undefined variable is not silently blanked -- it is reported."""
    assert cli._interpolate("{{known}} {{unknown}}", {"known": "x"}) == \
        "x {{unknown}}"


def test_interpolate_ignores_non_string_variables():
    """``headers`` is a dict in the environment and is not a substitution."""
    env = {"base_url": "https://x.test", "headers": {"A": "b"}, "n": 3}
    assert cli._interpolate("plain", env) == "plain"


def test_interpolate_returns_empty_string_for_empty_input():
    assert cli._interpolate("", {"a": "1"}) == ""


def test_unresolved_placeholders_lists_only_the_missing_names():
    assert cli._unresolved_placeholders(
        "{{a}}/{{b}}", {"a": "1"},
    ) == ["b"]


# --- header substitution in the live request path --------------------------


def test_repl_substitutes_variables_in_headers(monkeypatch):
    """The core regression: a templated header must reach the wire resolved."""
    _write_config(headers={"Authorization": "Bearer {{token}}"}, token="tok-9")
    seen = []

    def fake_execute_request(method, url, headers, body):
        seen.append(headers)
        return 200, {}, b"{}"

    _run_repl(["GET /users", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert seen == [{"Authorization": "Bearer tok-9"}]


def test_repl_substitutes_variables_in_every_header_value(monkeypatch):
    _write_config(headers={"X-Token": "{{token}}", "X-Org": "acme-{{org}}"},
                  token="tok-9", org="42")
    seen = []

    def fake_execute_request(method, url, headers, body):
        seen.append(headers)
        return 200, {}, b"{}"

    _run_repl(["GET /users", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert seen == [{"X-Token": "tok-9", "X-Org": "acme-42"}]


def test_replay_substitutes_variables_in_headers(monkeypatch):
    """A saved request replayed later re-applies header variables."""
    _write_config(headers={"Authorization": "Bearer {{token}}"})
    cli.save_request("saved-call", "get", "https://api.example.com/users",
                     {"Authorization": "Bearer {{token}}"}, None,
                     200, {}, "ok")
    seen = []

    def fake_execute_request(method, url, headers, body):
        seen.append(headers)
        return 200, {}, b"ok"

    _run_repl(["var token=tok-9", "replay saved-call", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert seen == [{"Authorization": "Bearer tok-9"}]


def test_repl_warns_when_a_placeholder_cannot_be_resolved(capsys, monkeypatch):
    """An unresolved ``{{...}}`` is reported instead of being sent verbatim."""
    _write_config(headers={"Authorization": "Bearer {{token}}"})
    _run_repl(["GET /users", "quit"], monkeypatch,
              execute_request=lambda *a: (200, {}, b"{}"))

    out = capsys.readouterr().out
    assert "{{token}}" in out
    assert "Unresolved variable" in out


def test_repl_does_not_warn_when_every_placeholder_resolves(capsys, monkeypatch):
    _write_config(headers={"Authorization": "Bearer {{token}}"}, token="tok-9")
    _run_repl(["GET /users", "quit"], monkeypatch,
              execute_request=lambda *a: (200, {}, b"{}"))

    assert "Unresolved variable" not in capsys.readouterr().out


def test_empty_body_stays_empty_string(monkeypatch):
    """The ``body`` guard is hoisted out of the loop: ``''`` is a real body."""
    _write_config()
    seen = []

    def fake_execute_request(method, url, headers, body):
        seen.append(body)
        return 200, {}, b"{}"

    _run_repl(["POST /items ''", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert seen == [""]