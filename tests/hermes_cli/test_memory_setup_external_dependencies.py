from __future__ import annotations

import subprocess

from hermes_cli.memory_setup import _safe_dependency_check_argv


def test_safe_dependency_check_splits_simple_command():
    assert _safe_dependency_check_argv("brv --version") == ["brv", "--version"]


def test_safe_dependency_check_rejects_shell_syntax():
    assert _safe_dependency_check_argv("brv --version; rm -rf /tmp/x") is None
    assert _safe_dependency_check_argv("curl https://example.com/install.sh | sh") is None
    assert _safe_dependency_check_argv("echo $(whoami)") is None


def test_safe_dependency_check_rejects_explicit_shell():
    assert _safe_dependency_check_argv(["sh", "-c", "brv --version"]) is None


def test_dependency_check_argv_runs_without_shell(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = _safe_dependency_check_argv("brv --version")
    subprocess.run(argv, shell=False, capture_output=True, timeout=5, check=True)

    assert calls == [(["brv", "--version"], {"shell": False, "capture_output": True, "timeout": 5, "check": True})]
