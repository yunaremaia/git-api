"""Tests for readline history handling (issue #36).

Two independent bugs lived in this code:

* ``!!`` looked one entry further back than it should, because the ``!!`` line
  itself had already been pushed with ``add_history()`` by the time the lookup
  ran. Re-sending the wrong POST or DELETE is the worst failure this tool has.
* ``history_size`` was passed straight to ``set_history_length()``, which caps
  the buffer that ``write_history_file()`` then serialises -- so every command
  rewrote ``history.txt`` with only the most recent N entries.
"""

import pytest

from git_api import cli


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Temp CWD plus a clean process-global readline history."""
    monkeypatch.chdir(tmp_path)
    cli.readline.clear_history()
    yield tmp_path
    cli.readline.clear_history()
    # set_history_length is process-global too; don't leak a test's value.
    cli.readline.set_history_length(cli.MEMORY_HISTORY_DEFAULT)


def _run_repl(lines, monkeypatch, *, execute_request=None, keep_history=True):
    """Drive ``cli.main()`` over ``lines``; EOF ends the loop.

    ``add_history`` stays live by default: ``!!`` and ``!N`` read the real
    buffer, so patching it away would make these tests assert nothing.
    """
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


def _ok_execute(calls):
    def fake_execute_request(method, url, headers, body):
        calls.append(url)
        return 200, {}, b"ok"

    return fake_execute_request


# --- !! must repeat the immediately preceding command ----------------------


def test_bang_bang_repeats_the_most_recent_command(monkeypatch):
    """The regression: `!!` skipped one entry and re-sent an older request."""
    calls = []
    _run_repl([
        "GET https://api.example.com/1",
        "GET https://api.example.com/2",
        "GET https://api.example.com/3",
        "!!",
        "quit",
    ], monkeypatch, execute_request=_ok_execute(calls))

    assert calls == [
        "https://api.example.com/1",
        "https://api.example.com/2",
        "https://api.example.com/3",
        "https://api.example.com/3",
    ]


def test_bang_bang_after_a_single_command_repeats_it(monkeypatch):
    """One prior entry is enough; the old `hist >= 2` guard rejected it."""
    calls = []
    _run_repl([
        "GET https://api.example.com/only",
        "!!",
        "quit",
    ], monkeypatch, execute_request=_ok_execute(calls))

    assert calls == [
        "https://api.example.com/only",
        "https://api.example.com/only",
    ]


def test_bang_bang_with_no_previous_command_reports_error(capsys, monkeypatch):
    _run_repl(["!!", "quit"], monkeypatch)
    out = capsys.readouterr().out
    assert "No previous command." in out
    assert "Traceback" not in out


def test_bang_bang_does_not_crash_on_the_first_command(capsys, monkeypatch):
    """`!!` alone used to reach shlex.split(None) and read from stdin."""
    calls = []
    _run_repl(["!!", "GET https://api.example.com/after", "quit"], monkeypatch,
              execute_request=_ok_execute(calls))

    assert calls == ["https://api.example.com/after"]
    assert "No previous command." in capsys.readouterr().out


def test_bang_bang_repeats_a_mutating_request_not_an_older_one(monkeypatch):
    """A `!!` after a POST must not re-send the GET that preceded it."""
    calls = []
    _run_repl([
        "GET https://api.example.com/read",
        "POST https://api.example.com/write payload",
        "!!",
        "quit",
    ], monkeypatch, execute_request=_ok_execute(calls))

    assert calls == [
        "https://api.example.com/read",
        "https://api.example.com/write",
        "https://api.example.com/write",
    ]


def test_bang_bang_does_not_execute_itself_recursively(monkeypatch):
    """`!!` resolves to the previous command, never to another `!!`."""
    calls = []
    _run_repl([
        "GET https://api.example.com/one",
        "!!",
        "!!",
        "quit",
    ], monkeypatch, execute_request=_ok_execute(calls))

    assert calls == ["https://api.example.com/one"] * 3


# --- history_size must not truncate history.txt ----------------------------


def test_history_size_is_not_also_the_disk_cap():
    """Two different limits: recall depth in memory, retention on disk."""
    assert cli.HISTORY_FILE_MAX_LINES > cli.MEMORY_HISTORY_DEFAULT


def test_write_history_file_would_truncate_and_is_not_used(isolated_env):
    """Pins the mechanism of the bug, so the fix cannot silently regress.

    set_history_length(3) then write_history_file() writes 3 lines, whatever
    was added. That is why the REPL appends to history.txt itself.
    """
    cli.init()
    cli.readline.set_history_length(3)
    for i in range(10):
        cli.readline.add_history(f"cmd{i}")
    cli.readline.write_history_file(str(cli.HISTORY_FILE))

    assert len(cli.HISTORY_FILE.read_text().splitlines()) == 3


def test_append_history_keeps_every_entry_past_the_memory_cap(isolated_env):
    """The regression: a session longer than the memory cap must not be cut."""
    cli.init()
    cli.readline.set_history_length(3)
    for i in range(10):
        cli.readline.add_history(f"cmd{i}")
        cli.append_history(f"cmd{i}")

    lines = cli.HISTORY_FILE.read_text().splitlines()
    assert len(lines) == 10
    assert lines[0] == "cmd0"
    assert lines[-1] == "cmd9"


def test_append_history_trims_only_past_the_disk_cap(isolated_env,
                                                     monkeypatch):
    monkeypatch.setattr(cli, "HISTORY_FILE_MAX_LINES", 5)
    cli.init()
    for i in range(12):
        cli.append_history(f"cmd{i}")

    lines = cli.HISTORY_FILE.read_text().splitlines()
    assert len(lines) == 5
    assert lines[0] == "cmd7"
    assert lines[-1] == "cmd11"


def test_append_history_preserves_prior_sessions(isolated_env):
    """Existing content is kept, not overwritten on the next command."""
    cli.init()
    cli.HISTORY_FILE.write_text("GET https://api.example.com/old\n")
    cli.append_history("GET https://api.example.com/new")

    assert cli.HISTORY_FILE.read_text().splitlines() == [
        "GET https://api.example.com/old",
        "GET https://api.example.com/new",
    ]


def test_repl_does_not_truncate_the_history_file(isolated_env, monkeypatch):
    """A session longer than history_size still leaves every line on disk."""
    from unittest.mock import patch

    cli.init()
    cli.CONFIG_FILE.write_text(
        '{"environments": {"default": {"base_url": "", "headers": {}}}, '
        '"active_env": "default", "history_size": 3}'
    )

    iterator = iter([f"var K{i}=v{i}" for i in range(8)] + ["quit"])

    def fake_input(prompt=""):
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)
    # read_history_file is patched out, add_history and the file write are not:
    # the truncation this guards against happened inside readline.
    with patch.object(cli.readline, "read_history_file"):
        cli.main()

    lines = cli.HISTORY_FILE.read_text().splitlines()
    assert len(lines) == 9
    assert lines[0] == "var K0=v0"
    assert "var K7=v7" in lines


def test_history_size_still_bounds_the_in_memory_buffer(isolated_env,
                                                        monkeypatch):
    """`history_size` keeps its documented job: how far back recall reaches."""
    cli.init()
    cli.CONFIG_FILE.write_text(
        '{"environments": {"default": {"base_url": "", "headers": {}}}, '
        '"active_env": "default", "history_size": 4}'
    )
    _run_repl(["quit"], monkeypatch)

    assert cli.readline.get_history_length() == 4