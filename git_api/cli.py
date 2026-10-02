#!/usr/bin/env python3
"""Git-native API REPL - interact with APIs, persist everything as JSON."""

import json
import pathlib
import readline
import re
import shlex
import sys
import urllib.request
import urllib.error

GAPI_DIR = pathlib.Path(".git-api")
CONFIG_FILE = GAPI_DIR / "config.json"
HISTORY_FILE = GAPI_DIR / "history.txt"

# In-memory readline buffer. `history_size` from the config bounds this; it is
# what `!!`, `!N` and `history` can reach. It is deliberately NOT the disk cap:
# write_history_file() serialises the capped in-memory buffer, so using the
# same number for both truncated history.txt to a moving N-line window.
MEMORY_HISTORY_DEFAULT = 100

# How many lines history.txt keeps on disk. Separate from the in-memory cap
# because the two are different limits: recall depth and retention.
HISTORY_FILE_MAX_LINES = 10_000


def init():
    """Create .git-api directory structure."""
    GAPI_DIR.mkdir(exist_ok=True)
    (GAPI_DIR / "requests").mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps({
            "environments": {
                "default": {
                    "base_url": "",
                    "headers": {},
                }
            },
            "active_env": "default",
            "history_size": 100,
        }, indent=2))
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("")


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def append_history(line: str) -> None:
    """Append *line* to ``history.txt``, trimming to ``HISTORY_FILE_MAX_LINES``.

    ``readline.write_history_file()`` cannot be used here: it serialises the
    in-memory buffer, which ``set_history_length()`` has already capped, so on
    every command it rewrote the file with only the most recent entries. The
    buffer cap and the file cap are different limits -- recall depth versus
    retention -- so the file is written directly.

    The file is only rewritten when it grows past the cap, so the common case
    is a single append.
    """
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = HISTORY_FILE.read_text().splitlines() if HISTORY_FILE.exists() \
        else []
    existing.append(line)
    if len(existing) > HISTORY_FILE_MAX_LINES:
        existing = existing[-HISTORY_FILE_MAX_LINES:]
        HISTORY_FILE.write_text("\n".join(existing) + "\n")
    else:
        with HISTORY_FILE.open("a") as fh:
            fh.write(line + "\n")


def get_env(config: dict) -> dict:
    env_name = config["active_env"]
    return config["environments"][env_name]


def resolve_url(url: str, env: dict) -> str:
    base = env.get("base_url", "").rstrip("/")
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        if not base:
            raise ValueError(
                f"relative URL {url!r} requires base_url to be set in the active environment"
            )
        return f"{base}{url}"
    return f"{base}/{url}"


def _interpolate(text: str, env: dict) -> str:
    """Replace every ``{{var}}`` in *text* with the matching string in *env*.

    Non-string environment entries (``base_url``'s siblings such as the
    ``headers`` dict) are skipped: only strings are substitution values.
    """
    for key, value in env.items():
        if isinstance(value, str):
            text = text.replace(f"{{{{{key}}}}}", value)
    return text


def _interpolate_headers(headers: dict, env: dict) -> dict:
    """Return *headers* with every value passed through ``_interpolate``."""
    return {k: _interpolate(str(v), env) for k, v in headers.items()}


def _unresolved_placeholders(text: str, env: dict) -> list[str]:
    """Names of ``{{var}}`` placeholders in *text* that *env* cannot resolve.

    Reporting them turns an opaque 401 into an actionable message: the request
    was about to be sent with the placeholder still in it.
    """
    found = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", text)
    seen: list[str] = []
    for name in found:
        name = name.strip()
        if name and name not in seen and not isinstance(env.get(name), str):
            seen.append(name)
    return seen


def _warn_unresolved(text: str, env: dict, where: str) -> None:
    """Warn about placeholders left in *text* after substitution."""
    missing = _unresolved_placeholders(text, env)
    if missing:
        print(f"⚠️  Unresolved variable(s) in {where}: "
              + ", ".join(f"{{{{{n}}}}}" for n in missing))


def build_request_parts(parts: list[str], env: dict):
    """Build ``(url, headers, body)`` from a parsed request line.

    Substitution happens in all three places -- the help text promises URLs,
    headers and bodies -- and the body guard is hoisted out so an empty-string
    body is treated the same as any other.
    """
    url = resolve_url(parts[1], env)
    raw_body = parts[2] if len(parts) > 2 else None
    body = _interpolate(raw_body, env) if raw_body is not None else None
    headers = _interpolate_headers(env.get("headers", {}), env)
    url = _interpolate(url, env)
    _warn_unresolved(url, env, "URL")
    for name, value in headers.items():
        _warn_unresolved(value, env, f"header {name}")
    if body is not None:
        _warn_unresolved(body, env, "body")
    return url, headers, body


def save_request(name: str, method: str, url: str, headers: dict,
                 body: str | None, response_status: int,
                 response_headers: dict, response_body: str):
    """Persist request + response as a clean JSON file."""
    req_dir = GAPI_DIR / "requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename: keep alphanumeric, dash, underscore; replace others with _
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    # Ensure the filename is not empty and not just dots/underscores
    if not safe_name or safe_name.strip("._") == "":
        safe_name = "request_" + "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    # Limit length to avoid filesystem issues
    if len(safe_name) > 200:
        safe_name = safe_name[:200]
    out = {
        "name": name,
        "request": {
            "method": method.upper(),
            "url": url,
            "headers": headers,
            "body": body,
        },
        "response": {
            "status": response_status,
            "headers": response_headers,
            "body": response_body,
        },
    }
    path = req_dir / f"{safe_name}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n💾  Saved to .git-api/requests/{safe_name}.json")
    return path


def cmd_help():
    print("""
Git-API REPL — commands:
  GET <url>           Send GET request
  POST <url> [body]   Send POST request
  PUT <url> [body]    Send PUT request
  DELETE <url>        Send DELETE request
  HEAD <url>          Send HEAD request

  env [name]          Show / switch active environment
  var KEY=VALUE       Set variable in active environment
  save NAME           Save last request as NAME
  list                List saved requests
  history             Show request history
  replay NAME         Replay a saved request
  curl NAME           Export a saved request to curl format
  config              Show current config
  quit / exit         Leave the REPL

Variables: {{var_name}} in URLs/headers/body are substituted.
Shortcuts: !<N> repeats history entry N, !! repeats last request.
""")


def execute_request(method: str, url: str, headers: dict,
                    body: str | None) -> tuple[int, dict, bytes]:
    """Execute HTTP request and return (status, headers_dict, body_bytes)."""
    data = body.encode() if body else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except urllib.error.URLError as e:
        return 0, {}, str(e.reason).encode()
    except ValueError as e:
        return 0, {}, str(e).encode()


def main():
    init()
    config = load_config()
    env = get_env(config)
    last_request = None

    print("🌿  Git-API REPL 0.1.0")
    print("    Type 'help' for commands, 'quit' to exit.\n")

    # `history_size` bounds the in-memory buffer only. It is not the disk cap:
    # write_history_file() serialises the capped buffer, so using it for both
    # rewrote history.txt with only the last N commands, on every command.
    readline.set_history_length(config.get("history_size",
                                          MEMORY_HISTORY_DEFAULT))
    if HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > 0:
        readline.read_history_file(str(HISTORY_FILE))

    while True:
        try:
            prompt = f"{config['active_env']}> "
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not line:
            continue

        # Save to history
        readline.add_history(line)
        append_history(line)

        parts = shlex.split(line)
        cmd = parts[0].upper()

        # Replay shortcuts
        if cmd == "!!":
            # add_history() above already appended the "!!" line itself, so it
            # occupies the last slot and the command to repeat is the one
            # before it. Indexing hist - 2 skipped one entry too far back,
            # which silently re-sent an older request.
            #
            # A previous "!!" is skipped rather than replayed: bash expands to
            # the last *command*, so `!!` twice runs the same command twice
            # instead of the second one trying to expand a literal "!!".
            previous = None
            for i in range(readline.get_current_history_length() - 1, 0, -1):
                candidate = readline.get_history_item(i)
                if candidate and candidate.strip() != "!!":
                    previous = candidate
                    break
            if not previous:
                print("No previous command.")
                continue
            line = previous
            parts = shlex.split(line)
            cmd = parts[0].upper()
        elif cmd.startswith("!"):
            try:
                n = int(cmd[1:])
                line = readline.get_history_item(n)
                if line:
                    parts = shlex.split(line)
                    cmd = parts[0].upper()
                else:
                    print(f"History item {n} not found.")
                    continue
            except ValueError:
                pass

        if cmd in ("QUIT", "EXIT", "Q"):
            print("Bye.")
            break
        elif cmd == "HELP":
            cmd_help()
        elif cmd == "CONFIG":
            print(json.dumps(config, indent=2))
        elif cmd == "ENV":
            if len(parts) > 1:
                name = parts[1]
                if name in config["environments"]:
                    config["active_env"] = name
                    env = config["environments"][name]
                    CONFIG_FILE.write_text(json.dumps(config, indent=2))
                    print(f"Switched to environment: {name}")
                else:
                    print(f"Unknown environment: {name}")
                    print(f"Available: {list(config['environments'].keys())}")
            else:
                print(f"Active: {config['active_env']}")
                for name, e in config["environments"].items():
                    marker = " *" if name == config["active_env"] else ""
                    print(f"  {name}: {e.get('base_url', '(no base_url)')}{marker}")
        elif cmd == "VAR":
            if len(parts) > 1:
                assignment = parts[1]
                if "=" not in assignment:
                    print("Usage: var KEY=VALUE")
                    continue
                key, value = assignment.split("=", 1)
                env[key.strip()] = value.strip()
                CONFIG_FILE.write_text(json.dumps(config, indent=2))
                print(f"Set {key.strip()} = {value.strip()}")
            else:
                for k, v in env.items():
                    if k not in ("headers", "base_url"):
                        print(f"  {k} = {v}")
        elif cmd == "LIST":
            req_dir = GAPI_DIR / "requests"
            req_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(req_dir.glob("*.json"))
            if not files:
                print("No saved requests. Use 'save NAME' to save one.")
                continue
            for f in files:
                data = json.loads(f.read_text())
                req = data["request"]
                resp = data["response"]
                print(f"  {f.stem}: {req['method']} {req['url']} → {resp['status']}")
        elif cmd == "HISTORY":
            for i in range(1, readline.get_current_history_length() + 1):
                item = readline.get_history_item(i)
                if item:
                    print(f"  {i}: {item}")
        elif cmd == "REPLAY":
            if len(parts) < 2:
                print("Usage: replay NAME")
                continue
            name = parts[1]
            req_file = GAPI_DIR / "requests" / f"{name}.json"
            if not req_file.exists():
                print(f"No saved request: {name}")
                continue
            data = json.loads(req_file.read_text())
            req = data["request"]
            url = resolve_url(_interpolate(req["url"], env), env)
            headers = _interpolate_headers(req.get("headers", {}), env)
            _warn_unresolved(url, env, "URL")
            for hname, hvalue in headers.items():
                _warn_unresolved(hvalue, env, f"header {hname}")
            print(f"\n↻  {req['method']} {url}")
            status, hdrs, body = execute_request(req["method"], url,
                                                 headers,
                                                 req.get("body"))
            print(f"←  {status}")
            try:
                parsed = json.loads(body)
                print(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(body.decode(errors="replace")[:500])
        elif cmd == "CURL":
            if len(parts) < 2:
                print("Usage: curl NAME")
                continue
            name = parts[1]
            req_file = GAPI_DIR / "requests" / f"{name}.json"
            if not req_file.exists():
                print(f"No saved request: {name}")
                continue
            data = json.loads(req_file.read_text())
            req = data["request"]
            parts_curl = ["curl", "-X", req["method"]]
            for k, v in req.get("headers", {}).items():
                parts_curl += ["-H", f"{k}: {v}"]
            if req.get("body"):
                parts_curl += ["-d", req["body"]]
            parts_curl.append(req["url"])
            print(" \\\n   ".join(parts_curl))
        elif cmd == "SAVE":
            if len(parts) < 2:
                print("Usage: save NAME")
                continue
            if last_request is None:
                print("No request to save. Run a GET/POST/etc first.")
                continue
            name = parts[1]
            method, url, headers, body, status, resp_hdrs, resp_body = last_request
            save_request(name, method, url, headers, body, status,
                         resp_hdrs, resp_body.decode(errors="replace") if isinstance(resp_body, bytes) else resp_body)
        elif cmd in ("GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"):
            if len(parts) < 2:
                print(f"Usage: {cmd} <url> [body]")
                continue
            url_raw = parts[1]
            url, headers, body = build_request_parts(parts, env)
            print(f"\n→  {cmd} {url}")
            status, hdrs, resp_body = execute_request(cmd, url, headers, body)
            print(f"←  {status}")
            try:
                parsed = json.loads(resp_body)
                print(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(resp_body.decode(errors="replace")[:500])
            last_request = (cmd, url, headers, body, status, hdrs, resp_body)
            print(f"\n  (use 'save {cmd.lower()}-{url_raw.replace('/', '-')}' to save)")
        else:
            print(f"Unknown command: {cmd}. Type 'help' for help.")


if __name__ == "__main__":
    main()
