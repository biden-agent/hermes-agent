import json
import importlib
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_browser_cdp_url(monkeypatch):
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    yield
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)


def _ok_response():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    return response


def test_browser_connect_tool_is_registered_without_user_supplied_exec_args():
    import tools.browser_tool as browser_tool

    entry = browser_tool.registry.get_entry("browser_connect")

    assert entry is not None
    schema = entry.schema
    assert schema["parameters"]["properties"] == {}
    assert "executable" not in schema["parameters"]["properties"]
    assert "args" not in schema["parameters"]["properties"]


def test_browser_connect_reuses_reachable_default_local_endpoint(monkeypatch):
    import tools.browser_tool as browser_tool

    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    with patch("tools.browser_tool.urlopen", return_value=_ok_response()) as urlopen, \
         patch("hermes_cli.browser_connect.try_launch_chrome_debug") as launch:
        result = json.loads(browser_tool.browser_connect())

    assert result["connected"] is True
    assert result["url"] == "http://127.0.0.1:9222"
    assert result["launched"] is False
    assert browser_tool.os.environ["BROWSER_CDP_URL"] == "http://127.0.0.1:9222"
    launch.assert_not_called()
    assert urlopen.call_args_list[0].args[0] == "http://127.0.0.1:9222/json/version"


def test_browser_connect_launches_only_default_local_when_unreachable(monkeypatch):
    import tools.browser_tool as browser_tool

    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    calls = {"count": 0}

    def fake_urlopen(url, timeout):
        calls["count"] += 1
        if calls["count"] < 3:
            raise OSError("not listening")
        return _ok_response()

    with patch("tools.browser_tool.urlopen", side_effect=fake_urlopen), \
         patch("tools.browser_tool.time.sleep", return_value=None), \
         patch("hermes_cli.browser_connect.try_launch_chrome_debug", return_value=True) as launch:
        result = json.loads(browser_tool.browser_connect())

    assert result["connected"] is True
    assert result["url"] == "http://127.0.0.1:9222"
    assert result["launched"] is True
    assert browser_tool.os.environ["BROWSER_CDP_URL"] == "http://127.0.0.1:9222"
    launch.assert_called_once()
    assert launch.call_args.args[0] == 9222


def test_browser_connect_does_not_launch_remote_configured_endpoint(monkeypatch):
    import tools.browser_tool as browser_tool

    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    with patch("hermes_cli.config.read_raw_config", return_value={"browser": {"cdp_url": "http://remote.example:9222"}}), \
         patch("tools.browser_tool.urlopen", side_effect=OSError("no route")), \
         patch("hermes_cli.browser_connect.try_launch_chrome_debug") as launch:
        result = json.loads(browser_tool.browser_connect())

    assert result["connected"] is False
    assert "could not reach browser CDP" in result["error"]
    assert "BROWSER_CDP_URL" not in browser_tool.os.environ
    launch.assert_not_called()


def test_importing_browser_tool_does_not_launch_chrome(monkeypatch):
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    with patch("subprocess.Popen") as popen:
        import tools.browser_tool as browser_tool
        importlib.reload(browser_tool)

    popen.assert_not_called()


def test_browser_navigate_does_not_implicitly_launch_default_local_cdp(monkeypatch):
    import tools.browser_tool as browser_tool

    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    monkeypatch.setattr(browser_tool, "_active_sessions", {})
    monkeypatch.setattr(browser_tool, "_session_last_activity", {})
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)

    def fake_run_browser_command(task_id, command, args=None, timeout=None):
        if command == "open":
            return {"success": True, "data": {"title": "Example", "url": args[0]}}
        if command == "snapshot":
            return {"success": True, "data": {"snapshot": "Example page", "refs": {}}}
        raise AssertionError(f"unexpected browser command: {command}")

    with patch("tools.browser_tool._ensure_browser_cdp_connected") as ensure_cdp, \
         patch("hermes_cli.browser_connect.try_launch_chrome_debug") as launch, \
         patch("tools.browser_tool._run_browser_command", side_effect=fake_run_browser_command) as run_cmd:
        result = json.loads(browser_tool.browser_navigate("https://example.com", task_id="local-default"))

    assert result["success"] is True
    ensure_cdp.assert_not_called()
    launch.assert_not_called()
    assert run_cmd.call_args_list[0].args[0] == "local-default"
    assert "BROWSER_CDP_URL" not in browser_tool.os.environ
    assert browser_tool._active_sessions["local-default"]["cdp_url"] is None
