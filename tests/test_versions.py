"""Vixl versions: what PyPI offers, installing into separate environments, the tools environment and removal.

The commands uv would run are recorded by a fake runner; PyPI and GitHub are fakes. ``test_a_real_install`` (marker
``network``) installs a real release with the real uv; run it with ``pytest -m network``.
"""

import json
from pathlib import Path
import sys
import time

import pytest

from vixl_sandbox import versions as versions_module
from vixl_sandbox.versions import VersionError, Versions, env_python, version_key

PYPI = {"releases": {
    "0.9.0": [{"yanked": False}], "0.10.0": [{"yanked": False}], "0.36.0": [{"yanked": False}],
    "0.35.0": [{"yanked": True}], "0.20.0": [], "0.37.0rc1": [{"yanked": False}], "1.0.dev1": [{"yanked": False}],
}}


class Response:
    def __init__(self, data=None, text=""):
        self.data, self.text = data, text

    def json(self):
        return self.data


def fake_fetch(url):
    if "pypi.org" in url:
        return Response(PYPI)
    if "0.9.0" in url:
        raise OSError("404")
    return Response(text=f"# skill from {url}")


class FakeRunner:
    """Records commands; ``uv venv`` makes the environment's python file so later steps find it."""

    def __init__(self, fail_on=None):
        self.commands, self.fail_on = [], fail_on

    def __call__(self, command, log, env):
        command = [str(part) for part in command]
        self.commands.append(command)
        log.append(" ".join(command))
        if self.fail_on and any(self.fail_on in part for part in command):
            raise VersionError("simulated failure")
        if command[1:2] == ["venv"]:
            python = env_python(command[-1])
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
        assert env["UV_PYTHON_INSTALL_DIR"].endswith("python") and "ANTHROPIC_API_KEY" not in env


def wait(versions, job, timeout=10):
    deadline = time.time() + timeout
    while versions.jobs[job.id].status == "running" and time.time() < deadline:
        time.sleep(0.02)
    return versions.jobs[job.id]


@pytest.fixture
def versions(tmp_path, monkeypatch):
    monkeypatch.setenv("VIXL_SANDBOX_UV", "uv-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    instance = Versions(tmp_path, runner=FakeRunner(), fetch=fake_fetch)
    return instance


def test_version_order():
    assert sorted(["0.36.0", "0.9.0", "0.10.0", "0.37.0rc1", "0.37.0"], key=version_key) == [
        "0.9.0", "0.10.0", "0.36.0", "0.37.0rc1", "0.37.0"]


def test_releases_skip_yanked_empty_and_odd_versions(versions):
    assert versions.releases() == ["0.37.0rc1", "0.36.0", "0.10.0", "0.9.0"]


def test_install_makes_its_own_environment_and_fetches_the_skill(versions, tmp_path):
    job = wait(versions, versions.install("0.36.0"))
    assert job.status == "done", job.log
    commands = versions.runner.commands
    assert commands[0][:4] == ["uv-test", "venv", "--python", versions_module.PYTHON]
    assert Path(commands[0][4]) == tmp_path / "versions" / "0.36.0" / "env"
    assert commands[1][:2] == ["uv-test", "pip"] and commands[1][-1] == "vixl-engine==0.36.0"
    assert "import vixl" in commands[2][-1]
    assert versions.ready() == ["0.36.0"]
    assert versions.python("0.36.0") == env_python(tmp_path / "versions" / "0.36.0" / "env")
    assert "skill from" in versions.skill("0.36.0") and "v0.36.0" in versions.skill("0.36.0")


def test_a_release_without_a_skill_still_installs(versions):
    job = wait(versions, versions.install("0.9.0"))
    assert job.status == "done" and versions.skill("0.9.0") == ""
    assert any("runs without it" in line for line in job.log)


def test_a_failed_install_is_recorded_and_not_offered_as_an_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("VIXL_SANDBOX_UV", "uv-test")
    versions = Versions(tmp_path, runner=FakeRunner(fail_on="vixl-engine"), fetch=fake_fetch)
    job = wait(versions, versions.install("0.36.0"))
    assert job.status == "failed" and "simulated failure" in job.error
    assert versions.installed()[0]["status"] == "failed" and versions.ready() == []
    with pytest.raises(VersionError, match="not installed"):
        versions.python("0.36.0")


def test_versions_must_look_like_versions(versions):
    with pytest.raises(VersionError, match="not a version"):
        versions.install("../../etc")


def test_update_available(versions):
    assert versions.update_available() == "0.37.0rc1"  # nothing installed yet: the newest is offered
    versions.register("0.37.0rc1", sys.executable)
    assert versions.update_available() is None


def test_offline_is_reported_not_raised_elsewhere(tmp_path):
    def offline(url):
        raise OSError("no network")

    versions = Versions(tmp_path, fetch=offline)
    with pytest.raises(VersionError, match="could not reach PyPI"):
        versions.releases()
    assert versions.update_available() is None


def test_remove(versions):
    wait(versions, versions.install("0.36.0"))
    versions.remove("0.36.0")
    assert versions.installed() == []
    with pytest.raises(VersionError, match="not installed"):
        versions.remove("0.36.0")


def test_tools_environment(versions, tmp_path):
    assert versions.tools_python() is None and versions.tools_status()["status"] == "missing"
    job = wait(versions, versions.install_tools())
    assert job.status == "done", job.log
    install = versions.runner.commands[1]
    assert "matplotlib" in install and "vixl-engine" not in install
    assert versions.tools_python() == env_python(tmp_path / "tools-env")


def test_one_install_at_a_time_per_version(tmp_path, monkeypatch):
    monkeypatch.setenv("VIXL_SANDBOX_UV", "uv-test")
    gate = []

    def slow(command, log, env):
        while not gate:
            time.sleep(0.01)
        FakeRunner()(command, log, env)

    versions = Versions(tmp_path, runner=slow, fetch=fake_fetch)
    first = versions.install("0.36.0")
    assert versions.install("0.36.0") is first
    gate.append(True)
    assert wait(versions, first).status == "done"


def test_versions_through_the_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vixl_sandbox.server import create_app

    monkeypatch.setenv("VIXL_SANDBOX_UV", "uv-test")
    versions = Versions(tmp_path, runner=FakeRunner(), fetch=fake_fetch)
    client = TestClient(create_app(tmp_path, token="t", versions=versions), base_url="http://localhost")
    client.headers["X-Lab-Token"] = "t"
    listing = client.get("/api/versions").json()
    assert listing["releases"][0] == "0.37.0rc1" and listing["update"] == "0.37.0rc1"
    job = client.post("/api/versions", json={"version": "0.36.0"}).json()
    wait(versions, versions.jobs[job["id"]])
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    arms = {a["id"]: a for a in client.get("/api/config").json()["arms"]}
    assert arms["vixl@0.36.0"]["available"] and not arms["code"]["available"]
    assert client.post("/api/versions", json={"version": "nope"}).status_code == 400
    assert client.delete("/api/versions/0.36.0").json() == {"ok": True}
    assert "vixl@0.36.0" not in {a["id"] for a in client.get("/api/config").json()["arms"]}
    tools = client.post("/api/tools").json()
    wait(versions, versions.jobs[tools["id"]])
    assert client.get("/api/versions").json()["tools"]["status"] == "ready"


@pytest.mark.network
def test_a_real_install(tmp_path):
    """Install a real release with the real uv (downloads Python and Vixl), then talk to its MCP server."""
    from vixl_sandbox.mcp_client import VixlServer

    versions = Versions(tmp_path)
    newest = versions.releases()[0]
    job = wait(versions, versions.install(newest), timeout=900)
    assert job.status == "done", "\n".join(job.log[-30:])
    workspace = tmp_path / "w"
    workspace.mkdir()
    server = VixlServer(versions.python(newest), workspace, env=versions_module.scrubbed_env(tmp_path))
    try:
        assert {"vixl_apply", "vixl_export"} <= {t["name"] for t in server.tools}
        failed, blocks = server.call("vixl_document", {"action": "create", "path": "a.vixl", "size": "1080p"})
        assert not failed, blocks
    finally:
        server.close()
    assert json.loads((tmp_path / "versions" / newest / "meta.json").read_text())["status"] == "ready"


def test_a_moved_data_folder_still_finds_each_version(tmp_path, monkeypatch):
    """Paths are found from the data folder at run time, so a relative or stale recorded path does not matter."""
    monkeypatch.setenv("VIXL_SANDBOX_UV", "uv-test")
    monkeypatch.chdir(tmp_path)
    versions = Versions("relative-home", runner=FakeRunner(), fetch=fake_fetch)
    assert versions.home.is_absolute()
    wait(versions, versions.install("0.36.0"))
    moved = tmp_path / "moved-home"
    (tmp_path / "relative-home").rename(moved)
    monkeypatch.chdir("/")
    again = Versions(moved, fetch=fake_fetch)
    assert again.ready() == ["0.36.0"] and again.python("0.36.0") == env_python(moved / "versions" / "0.36.0" / "env")


def test_a_version_whose_environment_vanished_is_not_offered(versions, tmp_path):
    import shutil

    wait(versions, versions.install("0.36.0"))
    shutil.rmtree(tmp_path / "versions" / "0.36.0" / "env")
    assert versions.ready() == []
    with pytest.raises(VersionError, match="reinstall"):
        versions.python("0.36.0")
