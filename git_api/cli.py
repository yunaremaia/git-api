#!/usr/bin/env python3
"""Git-native API REPL - interact with APIs, persist everything as JSON."""

import json
import pathlib
import readline
import shlex
import sys
import urllib.request
import urllib.error

GAPI_DIR = pathlib.Path(".git-api")
CONFIG_FILE = GAPI_DIR / "config.json"
HISTORY_FILE = GAPI_DIR / "history.txt"


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


def get_env(config: dict) -> dict:
    env_name = config["active_env"]
    return config["environments"][env_name]


def resolve_url(url: str, env: dict) -> str:
    if url.startswith(("http://", "https://", "/")):
        return url
    base = env.get("base_url", "").rstrip("/")
    return f"{base}/{url}"


def save_request(name: str, method: str, url: str, headers: dict,
                 body: str | None, response_status: int,
                 response_headers: dict, response_body: str):
    """Persist request + response as a clean JSON file."""
    req_dir = GAPI_DIR / "requests"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
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


def main():
    init()
    config = load_config()
    env = get_env(config)
    last_request = None

    print("🌿  Git-API REPL 0.1.0")
    print("    Type 'help' for commands, 'quit' to exit.\n")

    readline.set_history_length(config.get("history_size", 100))
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
        readline.write_history_file(str(HISTORY_FILE))

        parts = shlex.split(line)
        cmd = parts[0].upper()

        # Replay shortcuts
        if cmd == "!!":
            hist = readline.get_current_history_length()
            if hist >= 2:
                line = readline.get_history_item(hist - 2)
                parts = shlex.split(line)
                cmd = parts[0].upper()
            else:
                print("No previous command.")
                continue
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
            url = resolve_url(req["url"], env)
            for k, v in env.items():
                if isinstance(v, str):
                    url = url.replace(f"{{{{{k}}}}}", v)
            print(f"\n↻  {req['method']} {url}")
            status, hdrs, body = execute_request(req["method"], url,
                                                 req.get("headers", {}),
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
                         resp_hdrs, resp_body.decode(errors="replace"))
        elif cmd in ("GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"):
            if len(parts) < 2:
                print(f"Usage: {cmd} <url> [body]")
                continue
            url_raw = parts[1]
            body = parts[2] if len(parts) > 2 else None
            url = resolve_url(url_raw, env)
            # variable substitution
            for k, v in env.items():
                if isinstance(v, str):
                    url = url.replace(f"{{{{{k}}}}}", v)
                    if body:
                        body = body.replace(f"{{{{{k}}}}}", v)
            headers = env.get("headers", {})
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
