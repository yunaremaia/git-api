"""Tests for git-api save_request function."""
import json
from pathlib import Path
from unittest.mock import patch

from git_api.cli import save_request


def test_save_request_basic(tmp_path):
    """Test saving a request with normal name."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name="test-request",
            method="GET",
            url="https://api.example.com/users",
            headers={"Authorization": "Bearer token"},
            body=None,
            response_status=200,
            response_headers={"Content-Type": "application/json"},
            response_body='{"users": []}',
        )

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["name"] == "test-request"
    assert data["request"]["method"] == "GET"
    assert data["request"]["url"] == "https://api.example.com/users"
    assert data["response"]["status"] == 200


def test_save_request_special_chars(tmp_path):
    """Test that special characters in name are sanitized."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name="my request/with:special*chars",
            method="POST",
            url="https://api.example.com/data",
            headers={},
            body="payload",
            response_status=201,
            response_headers={},
            response_body="created",
        )

    assert path.exists()
    assert "my_request_with_special_chars" in path.name


def test_save_request_empty_name(tmp_path):
    """Test that empty name gets a fallback."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name="",
            method="GET",
            url="https://api.example.com/test",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body="ok",
        )

    assert path.exists()
    assert path.name.endswith(".json")


def test_save_request_long_name(tmp_path):
    """Test that very long names are truncated."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    long_name = "a" * 300
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name=long_name,
            method="GET",
            url="https://api.example.com/test",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body="ok",
        )

    assert path.exists()
    assert len(path.stem) <= 200


def test_save_request_bytes_body(tmp_path):
    """Test saving a request with bytes response body."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name="bytes-test",
            method="GET",
            url="https://api.example.com/raw",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body="binary data",
        )

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["response"]["body"] == "binary data"


def test_save_request_response_body_str(tmp_path):
    """Test saving a request with string response body (not bytes)."""
    (tmp_path / "requests").mkdir(parents=True, exist_ok=True)
    with patch("git_api.cli.GAPI_DIR", tmp_path):
        path = save_request(
            name="str-test",
            method="GET",
            url="https://api.example.com/text",
            headers={},
            body=None,
            response_status=200,
            response_headers={},
            response_body="plain text response",
        )

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["response"]["body"] == "plain text response"
