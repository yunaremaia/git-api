# Git-API — Git-native API REPL

[![CI](https://github.com/yunaremaia/git-api/actions/workflows/ci.yml/badge.svg)](https://github.com/yunaremaia/git-api/actions/workflows/ci.yml)

A local-first API REPL that persists requests as clean JSON files in your repo.

## Why

Postman stores data in proprietary blobs. curl doesn't persist. Bruno is closer but isn't a REPL. Git-API gives you:

- **REPL interactivity** with history and variable substitution
- **Git-friendly storage**: one JSON file per request, diffs are clean
- **Zero dependencies**: stdlib only, Python 3.11+
- **Local-first**: runs from any repo directory

## Install

```bash
pip install git+https://github.com/yunaremaia/git-api.git
```

> **Not yet on PyPI.** Install from git with the line above. The command you run
> is `git-api`; the distribution name on PyPI is `git-api-py`. The short
> `git-api` name on PyPI belongs to an unrelated project by another author, so
> it is not used here and never will be.

## Usage

```bash
cd your-project
git-api
```

```
🌿  Git-API REPL 0.1.0
    Type 'help' for commands, 'quit' to exit.

default> var base_url=https://api.example.com
Set base_url = https://api.example.com

default> GET {{base_url}}/users
→  GET https://api.example.com/users
←  200
[{"id":1,"name":"Yunare"}]

default> save get-users
💾  Saved to .git-api/requests/get-users.json

default> list
  get-users: GET https://api.example.com/users → 200

default> replay get-users
↻  GET https://api.example.com/users
←  200
```

## Data layout

```
.git-api/
├── config.json          # environments, active env
├── history.txt          # readline history
└── requests/
    ├── get-users.json   # request + response
    └── create-post.json
```

## License
MIT
