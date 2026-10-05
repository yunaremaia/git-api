"""Tests for git_api.cli.

Covers the pure helpers, the persistence format, and the REPL command loop so
regressions are caught before they reach users. Network access is mocked and
every test runs in a temporary CWD, so nothing touches a real ``.git-api``
directory.
"""

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from git_api import __version__, cli


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Run each test in a temp CWD with a clean readline history.

    ``cli.py`` resolves ``.git-api`` relative to the process CWD, so chdir is
    enough to isolate the filesystem. readline history is process-global, so it
    must be cleared too or ``!N`` shortcuts would replay entries left over from
    an earlier test.
    """
    monkeypatch.chdir(tmp_path)
    cli.readline.clear_history()
    return tmp_path


def _run_repl(lines, monkeypatch, *, keep_history=False, execute_request=None):
    """Drive ``cli.main()`` over ``lines``; EOF ends the loop."""
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


# --- package metadata -------------------------------------------------------


def test_version_is_exposed():
    assert __version__


def test_package_exposes_cli_main():
    assert callable(cli.main)


# --- resolve_url ------------------------------------------------------------


def test_resolve_url_keeps_absolute_url():
    env = {"base_url": "https://api.example.com"}
    assert cli.resolve_url("https://other.test/x", env) == "https://other.test/x"


def test_resolve_url_joins_root_relative_url_onto_base():
    """A /-prefixed URL is root-relative, not already-resolved.

    Passing it through unchanged handed urllib a path with no scheme, which
    raised ValueError and killed the REPL, so it is joined onto base_url.
    """
    env = {"base_url": "https://api.example.com"}
    assert cli.resolve_url("/users", env) == "https://api.example.com/users"


def test_resolve_url_rejects_root_relative_url_without_base_url():
    """No base_url means the root-relative URL cannot be resolved at all."""
    with pytest.raises(ValueError, match="base_url"):
        cli.resolve_url("/users", {})


def test_resolve_url_joins_relative_url_onto_base():
    env = {"base_url": "https://api.example.com"}
    assert cli.resolve_url("users", env) == "https://api.example.com/users"


def test_resolve_url_strips_trailing_slash_from_base():
    env = {"base_url": "https://api.example.com/"}
    assert cli.resolve_url("users", env) == "https://api.example.com/users"


def test_resolve_url_without_base_url_produces_root_relative_path():
    """With no base_url the join still yields a leading slash."""
    assert cli.resolve_url("users", {}) == "/users"


# --- init / config ----------------------------------------------------------


def test_init_creates_directory_structure():
    cli.init()
    assert cli.GAPI_DIR.is_dir()
    assert (cli.GAPI_DIR / "requests").is_dir()
    assert cli.CONFIG_FILE.is_file()
    assert cli.HISTORY_FILE.is_file()


def test_init_default_config_shape():
    cli.init()
    config = cli.load_config()
    assert config["active_env"] == "default"
    assert config["environments"]["default"] == {"base_url": "", "headers": {}}
    assert config["history_size"] == 100


def test_init_is_idempotent_and_preserves_config():
    cli.init()
    cli.CONFIG_FILE.write_text(json.dumps({
        "environments": {
            "default": {"base_url": "https://kept.test", "headers": {}},
        },
        "active_env": "default",
        "history_size": 7,
    }))
    cli.init()
    config = cli.load_config()
    assert config["environments"]["default"]["base_url"] == "https://kept.test"
    assert config["history_size"] == 7


def test_get_env_returns_active_environment():
    config = {
        "environments": {
            "default": {"base_url": "", "headers": {}},
            "prod": {"base_url": "https://prod.test", "headers": {"X-A": "1"}},
        },
        "active_env": "prod",
    }
    assert cli.get_env(config)["base_url"] == "https://prod.test"


@pytest.mark.parametrize("cfg,expected_snippet", [
    # KeyError: environments first (checked before active_env)
    ("{}", "missing required key 'environments'"),
    # KeyError: environments
    (json.dumps({"active_env": "default"}), "missing required key 'environments'"),
    # KeyError: active_env not in environments
    (json.dumps({"environments": {}, "active_env": "prod"}), "active_env='prod' is not in environments"),
    # active_env not a string
    (json.dumps({"environments": {"default": {}}, "active_env": 123}),
     "active_env=123 is not in environments"),
    # environments not a dict
    (json.dumps({"environments": "not-a-dict", "active_env": "default"}),
     "active_env='default' is not in environments"),
    # active_env unhashable: `active not in envs` would raise TypeError
    (json.dumps({"environments": {"a": {}}, "active_env": ["a"]}),
     "active_env=['a'] is not in environments"),
    (json.dumps({"environments": {"a": {}}, "active_env": {"a": 1}}),
     "active_env={'a': 1} is not in environments"),
    # environment entry not a dict: used to crash mid-session in resolve_url()
    (json.dumps({"environments": {"prod": "nope"}, "active_env": "prod"}),
     "environments.prod must be an object, got str"),
    (json.dumps({"environments": {"prod": 7}, "active_env": "prod"}),
     "environments.prod must be an object, got int"),
    # history_size wrong type
    (json.dumps({"environments": {"default": {}}, "active_env": "default",
                 "history_size": "ten"}),
     "history_size must be an integer"),
])
def test_bad_config_reports_instead_of_crashing(cfg, expected_snippet, tmp_path, monkeypatch):
    """Malformed config.json produces a readable SystemExit instead of a traceback."""
    monkeypatch.chdir(tmp_path)
    cli.GAPI_DIR.mkdir()
    cli.CONFIG_FILE.write_text(cfg)
    with pytest.raises(SystemExit) as exc:
        cli.load_config()
    assert str(cli.CONFIG_FILE) in str(exc.value)
    assert expected_snippet in str(exc.value)


def test_headers_string_type_error_named(tmp_path, monkeypatch):
    """headers set to a string produces a named SystemExit."""
    monkeypatch.chdir(tmp_path)
    cli.init()
    # Patch load_config to return a config where headers is a string
    bad_config = {
        "environments": {"default": {"base_url": "", "headers": "Authorization: Bearer tok"}},
        "active_env": "default",
    }
    monkeypatch.setattr(cli, "load_config", lambda: bad_config)
    with pytest.raises(SystemExit) as exc:
        cli._interpolate_headers(bad_config["environments"]["default"]["headers"], {})
    assert ".git-api/config.json" in str(exc.value)
    assert "headers must be an object" in str(exc.value)


# --- save_request -----------------------------------------------------------


def test_save_request_writes_expected_json():
    cli.init()
    path = cli.save_request(
        "my-call", "get", "https://api.example.com/users", {"X-Token": "abc"},
        None, 200, {"Content-Type": "application/json"}, '{"ok": true}',
    )
    assert path == cli.GAPI_DIR / "requests" / "my-call.json"
    saved = json.loads(path.read_text())
    assert saved["request"]["method"] == "GET"
    assert saved["request"]["url"] == "https://api.example.com/users"
    assert saved["request"]["headers"] == {"X-Token": "abc"}
    assert saved["request"]["body"] is None
    assert saved["response"]["status"] == 200
    assert saved["response"]["body"] == '{"ok": true}'


def test_save_request_sanitizes_unsafe_characters_in_name():
    cli.init()
    path = cli.save_request(
        "../../etc/passwd", "get", "https://api.example.com/x", {}, None,
        200, {}, "",
    )
    assert path.parent == cli.GAPI_DIR / "requests"
    assert path.exists()
    assert ".." not in path.name


def test_save_request_serializes_multiline_body():
    cli.init()
    path = cli.save_request(
        "binary-body", "post", "https://api.example.com/x", {},
        None, 200, {}, "line one\nline two",
    )
    saved = json.loads(path.read_text())
    assert saved["response"]["body"] == "line one\nline two"


# --- execute_request --------------------------------------------------------


class _FakeHTTPResponse:
    """Minimal stand-in for the object ``urlopen`` returns."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_execute_request_returns_status_headers_and_body():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        return _FakeHTTPResponse(201, {"X-Trace": "t1"}, b"created")

    with patch.object(cli.urllib.request, "urlopen", fake_urlopen):
        status, headers, body = cli.execute_request(
            "post", "https://api.example.com/x", {"X-Token": "abc"}, "payload",
        )

    assert (status, headers, body) == (201, {"X-Trace": "t1"}, b"created")
    assert captured["method"] == "POST"
    assert captured["timeout"] == 30


def test_execute_request_sends_headers_and_encodes_body():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["header"] = req.get_header("X-token")
        captured["data"] = req.data
        return _FakeHTTPResponse(200, {}, b"ok")

    with patch.object(cli.urllib.request, "urlopen", fake_urlopen):
        cli.execute_request("post", "https://api.example.com/x",
                            {"X-Token": "abc"}, "payload")

    assert captured["header"] == "abc"
    assert captured["data"] == b"payload"


def test_execute_request_get_sends_no_body():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["data"] = req.data
        return _FakeHTTPResponse(200, {}, b"ok")

    with patch.object(cli.urllib.request, "urlopen", fake_urlopen):
        cli.execute_request("get", "https://api.example.com/x", {}, None)

    assert captured["data"] is None


def test_execute_request_returns_body_for_http_error_status():
    err = urllib.error.HTTPError(
        "https://api.example.com/x", 404, "Not Found", {"X-E": "1"},
        io.BytesIO(b"Not Found"),
    )

    def fake_urlopen(req, timeout=None):
        raise err

    with patch.object(cli.urllib.request, "urlopen", fake_urlopen):
        status, headers, body = cli.execute_request(
            "get", "https://api.example.com/x", {}, None,
        )

    assert status == 404
    assert headers == {"X-E": "1"}
    assert b"Not Found" in body


def test_execute_request_returns_status_zero_on_connection_error():
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    with patch.object(cli.urllib.request, "urlopen", fake_urlopen):
        status, headers, body = cli.execute_request(
            "get", "https://api.example.com/x", {}, None,
        )

    assert status == 0
    assert headers == {}
    assert b"connection refused" in body


# --- cmd_help ---------------------------------------------------------------


def test_cmd_help_lists_core_commands(capsys):
    cli.cmd_help()
    out = capsys.readouterr().out
    for command in ("GET", "POST", "save", "replay", "curl", "quit"):
        assert command in out


# --- REPL driver ------------------------------------------------------------


def test_repl_prints_banner_with_version(capsys, monkeypatch):
    _run_repl(["quit"], monkeypatch)
    out = capsys.readouterr().out
    assert "Git-API REPL" in out
    assert __version__ in out


def test_repl_quit_prints_bye(capsys, monkeypatch):
    _run_repl(["quit"], monkeypatch)
    assert "Bye." in capsys.readouterr().out


def test_repl_treats_keyboard_interrupt_as_exit(capsys, monkeypatch):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with patch.object(cli.readline, "read_history_file"), \
            patch.object(cli.readline, "write_history_file"), \
            patch.object(cli.readline, "add_history"):
        cli.main()
    assert "Bye." in capsys.readouterr().out


def test_repl_blank_line_is_ignored(capsys, monkeypatch):
    _run_repl(["", "   ", "quit"], monkeypatch)
    assert capsys.readouterr().out.count("Bye.") == 1


def test_repl_unknown_command_is_reported(capsys, monkeypatch):
    _run_repl(["bogus", "quit"], monkeypatch)
    assert "Unknown command: BOGUS" in capsys.readouterr().out


def test_repl_help_prints_command_list(capsys, monkeypatch):
    _run_repl(["help", "quit"], monkeypatch)
    assert "Git-API REPL — commands:" in capsys.readouterr().out


def test_repl_config_prints_json(capsys, monkeypatch):
    _run_repl(["config", "quit"], monkeypatch)
    assert '"active_env": "default"' in capsys.readouterr().out


def test_repl_var_sets_and_lists_variable(capsys, monkeypatch):
    _run_repl(["var TOKEN=abc123", "var", "quit"], monkeypatch)
    out = capsys.readouterr().out
    assert "Set TOKEN = abc123" in out
    assert "TOKEN = abc123" in out
    assert cli.load_config()["environments"]["default"]["TOKEN"] == "abc123"


def test_repl_var_without_assignment_shows_usage(capsys, monkeypatch):
    _run_repl(["var justkey", "quit"], monkeypatch)
    assert "Usage: var KEY=VALUE" in capsys.readouterr().out


def test_repl_var_listing_hides_internal_keys(capsys, monkeypatch):
    """`var` with no args hides base_url and headers."""
    _run_repl(["var TOKEN=abc", "var", "quit"], monkeypatch)
    listing = capsys.readouterr().out.split("Set TOKEN = abc")[-1]
    assert "base_url" not in listing
    assert "headers" not in listing


def test_repl_get_substitutes_variables_and_saves_request(capsys, monkeypatch):
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append((method, url))
        return 200, {}, b'{"users": []}'

    _run_repl([
        "var TOKEN=secret",
        "GET https://api.example.com/users?token={{TOKEN}}",
        "save my-call",
        "quit",
    ], monkeypatch, execute_request=fake_execute_request)

    assert calls == [("GET", "https://api.example.com/users?token=secret")]
    saved = json.loads((cli.GAPI_DIR / "requests" / "my-call.json").read_text())
    assert saved["request"]["url"] == "https://api.example.com/users?token=secret"
    assert saved["response"]["status"] == 200


def test_repl_post_substitutes_variables_in_body(capsys, monkeypatch):
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append((method, url, body))
        return 201, {}, b"created"

    _run_repl([
        "var NAME=widget",
        "POST https://api.example.com/items name={{NAME}}",
        "quit",
    ], monkeypatch, execute_request=fake_execute_request)

    assert calls == [("POST", "https://api.example.com/items", "name=widget")]
    assert "201" in capsys.readouterr().out


def test_repl_get_without_url_shows_usage(capsys, monkeypatch):
    _run_repl(["GET", "quit"], monkeypatch)
    assert "Usage: GET <url> [body]" in capsys.readouterr().out


def test_repl_list_shows_saved_request(capsys, monkeypatch):
    cli.init()
    cli.save_request("seeded", "get", "https://api.example.com/x", {},
                     None, 204, {}, "")
    _run_repl(["list", "quit"], monkeypatch)
    assert "seeded: GET https://api.example.com/x" in capsys.readouterr().out


def test_repl_list_without_saved_requests(capsys, monkeypatch):
    _run_repl(["list", "quit"], monkeypatch)
    assert "No saved requests" in capsys.readouterr().out


def test_repl_curl_exports_saved_request(capsys, monkeypatch):
    cli.init()
    cli.save_request("seeded", "post", "https://api.example.com/x",
                     {"X-Token": "abc"}, "payload", 200, {}, "ok")
    _run_repl(["curl seeded", "quit"], monkeypatch)
    # Each flag lands on its own shell-continuation line.
    out = capsys.readouterr().out
    flat = " ".join(out.replace("\\\n", " ").split())
    assert "curl -X POST -H X-Token: abc -d payload " \
           "https://api.example.com/x" in flat


def test_repl_curl_without_name_shows_usage(capsys, monkeypatch):
    _run_repl(["curl", "quit"], monkeypatch)
    assert "Usage: curl NAME" in capsys.readouterr().out


def test_repl_replay_re_executes_saved_request(capsys, monkeypatch):
    cli.init()
    cli.save_request("seeded", "get", "https://api.example.com/x", {},
                     None, 200, {}, "ok")
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append((method, url))
        return 200, {}, b"replayed"

    _run_repl(["replay seeded", "quit"], monkeypatch,
              execute_request=fake_execute_request)

    assert calls == [("GET", "https://api.example.com/x")]
    assert "replayed" in capsys.readouterr().out


def test_repl_replay_missing_name_reports_error(capsys, monkeypatch):
    _run_repl(["replay nope", "quit"], monkeypatch)
    assert "No saved request: nope" in capsys.readouterr().out


def test_repl_replay_without_name_shows_usage(capsys, monkeypatch):
    _run_repl(["replay", "quit"], monkeypatch)
    assert "Usage: replay NAME" in capsys.readouterr().out


def test_repl_save_without_request_reports_error(capsys, monkeypatch):
    _run_repl(["save orphan", "quit"], monkeypatch)
    assert "No request to save" in capsys.readouterr().out


def test_repl_env_switches_and_persists(capsys, monkeypatch):
    cli.init()
    cli.CONFIG_FILE.write_text(json.dumps({
        "environments": {
            "default": {"base_url": "", "headers": {}},
            "prod": {"base_url": "https://prod.test", "headers": {}},
        },
        "active_env": "default",
        "history_size": 100,
    }))
    _run_repl(["env prod", "env", "quit"], monkeypatch)
    out = capsys.readouterr().out
    assert "Switched to environment: prod" in out
    assert "https://prod.test" in out
    assert cli.load_config()["active_env"] == "prod"


def test_repl_env_unknown_name_reports_error(capsys, monkeypatch):
    _run_repl(["env nope", "quit"], monkeypatch)
    assert "Unknown environment: nope" in capsys.readouterr().out


def test_repl_history_lists_recorded_commands(capsys, monkeypatch):
    # add_history stays live here so the buffer the `history` command reads is
    # the real one.
    _run_repl(["var TOKEN=abc", "history", "quit"], monkeypatch,
              keep_history=True)
    out = capsys.readouterr().out
    assert "1: var TOKEN=abc" in out
    assert "2: history" in out


def test_repl_numbered_history_shortcut_repeats_entry(capsys, monkeypatch):
    """``!1`` repeats readline history entry 1 (the var command)."""
    calls = []

    def fake_execute_request(method, url, headers, body):
        calls.append(url)
        return 200, {}, b"ok"

    _run_repl(["var base_url=https://api.example.com", "!1", "quit"],
              monkeypatch, keep_history=True,
              execute_request=fake_execute_request)

    # "!1" replays the var command — no HTTP call, variable set twice.
    assert calls == []
    assert cli.load_config()["environments"]["default"]["base_url"] == \
        "https://api.example.com"
    assert capsys.readouterr().out.count(
        "Set base_url = https://api.example.com") == 2


def test_repl_bang_with_empty_history_does_not_crash(capsys, monkeypatch):
    _run_repl(["!1", "quit"], monkeypatch, keep_history=True)
    assert "Bye." in capsys.readouterr().out


def test_repl_bang_non_numeric_is_unknown_command(capsys, monkeypatch):
    _run_repl(["!abc", "quit"], monkeypatch, keep_history=True)
    assert "Unknown command" in capsys.readouterr().out


def test_repl_eof_exits_cleanly(capsys, monkeypatch):
    _run_repl([], monkeypatch)
    assert "Bye." in capsys.readouterr().out