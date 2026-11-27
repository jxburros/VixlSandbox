"""Vixl versions, each installed into its own environment, plus the code-tools environment.

Every version lives in ``<home>/versions/<version>/``:

    env/        a virtual environment with ``vixl-engine==<version>`` and nothing else of ours
    SKILL.md    that release's agent skill, fetched from the Vixl repository at tag ``v<version>`` (optional)
    meta.json   {version, installed, python, status, error}

The code-tools arm runs model-written Python in ``<home>/tools-env/``, which holds the drawing and document
libraries an agent without Vixl would use.

Environments are made with ``uv``: the one bundled with the app, else the ``uv`` Python package, else ``uv`` on the
PATH. uv downloads a Python for them when the computer has none, into ``<home>/python``.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

PACKAGE = "vixl-engine"
PYPI = f"https://pypi.org/pypi/{PACKAGE}/json"
SKILL_URL = "https://raw.githubusercontent.com/jxburros/Vixl/v{version}/skills/vixl/SKILL.md"
PYTHON = "3.12"
TOOLS_PACKAGES = ["Pillow", "numpy", "matplotlib", "reportlab", "fpdf2", "python-pptx", "python-docx", "openpyxl",
                  "svgwrite", "pypdf", "fonttools"]
VERSION_PATTERN = re.compile(r"^\d+(\.\d+){1,3}([a-z]+\d+)?$")


class VersionError(RuntimeError):
    pass


def version_key(text):
    """Sort key for release numbers: 0.9.0 < 0.10.0 < 0.36.0; pre-releases before their release."""
    match = re.match(r"^(\d+(?:\.\d+)*)([a-z]+)?(\d+)?", text)
    if not match:
        return ((0,), 0, "", 0)
    numbers = tuple(int(part) for part in match.group(1).split("."))
    numbers += (0,) * (4 - len(numbers))
    return (numbers, 0 if match.group(2) else 1, match.group(2) or "", int(match.group(3) or 0))


def env_python(env):
    env = Path(env)
    return env / "Scripts" / "python.exe" if os.name == "nt" else env / "bin" / "python"


def find_uv():
    """The uv executable: bundled with the app, the uv package's, or on the PATH."""
    if os.environ.get("VIXL_SANDBOX_UV"):
        return os.environ["VIXL_SANDBOX_UV"]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "uv" / ("uv.exe" if os.name == "nt" else "uv")
        if candidate.is_file():
            return str(candidate)
    try:
        from uv import find_uv_bin

        return find_uv_bin()
    except (ImportError, FileNotFoundError):
        pass
    found = shutil.which("uv")
    if found:
        return found
    raise VersionError("uv was not found; reinstall VixlSandbox or install uv (https://docs.astral.sh/uv/)")


def scrubbed_env(home):
    """Environment for uv and for the Vixl servers: no API keys, caches kept inside the app's folder."""
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
            "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
            "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "UV_INDEX_URL", "PIP_INDEX_URL")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update({"UV_CACHE_DIR": str(Path(home) / "uv-cache"), "UV_PYTHON_INSTALL_DIR": str(Path(home) / "python"),
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return env


def run(command, log, env, timeout=1800):
    """Run a command, streaming its output into ``log`` (a list of lines). Raises VersionError on failure."""
    log.append("$ " + " ".join(str(part) for part in command))
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen([str(part) for part in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", env=env, **options)
    started = time.time()
    for line in process.stdout:
        log.append(line.rstrip())
        if time.time() - started > timeout:
            process.kill()
            raise VersionError(f"stopped after {timeout} s")
    if process.wait() != 0:
        raise VersionError(f"{Path(str(command[0])).name} exited with code {process.returncode}")


@dataclass
class Job:
    """A running or finished install, shown in the Versions tab."""

    id: str
    kind: str
    target: str
    status: str = "running"
    log: list = field(default_factory=list)
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def public(self, tail=200):
        return {"id": self.id, "kind": self.kind, "target": self.target, "status": self.status, "error": self.error,
                "started": self.started, "finished": self.finished, "log": self.log[-tail:]}


class Versions:
    """Installed Vixl versions and the tools environment, under ``home``."""

    def __init__(self, home, runner=run, fetch=None):
        self.home = Path(home).resolve()
        self.root = self.home / "versions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.fetch = fetch or self._fetch
        self.jobs = {}
        self.lock = threading.Lock()
        self._releases = (0.0, [])

    # --- what exists --------------------------------------------------------------------------------------------

    def meta(self, version):
        path = self.root / version / "meta.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def installed(self):
        """Every version folder with its status, newest version first."""
        found = []
        for folder in self.root.iterdir() if self.root.is_dir() else []:
            meta = self.meta(folder.name)
            if meta:
                meta["skill"] = (folder / "SKILL.md").is_file()
                found.append(meta)
        return sorted(found, key=lambda m: version_key(m["version"]), reverse=True)

    def ready(self):
        """Versions that can run now: installed and with their interpreter in place."""
        found = []
        for meta in self.installed():
            if meta.get("status") == "ready":
                try:
                    self.python(meta["version"])
                except VersionError:
                    continue
                found.append(meta["version"])
        return found

    def python(self, version):
        """The Python of an installed, ready version: its own environment's, found from its folder so the data
        folder can move; a registered interpreter (``register``) when the version has no environment of its own."""
        meta = self.meta(version)
        if not meta or meta.get("status") != "ready":
            raise VersionError(f"Vixl {version} is not installed")
        own = env_python(self.root / version / "env")
        if own.is_file():
            return own
        recorded = Path(meta.get("python") or "")
        if recorded.is_absolute() and recorded.is_file():
            return recorded
        raise VersionError(f"Vixl {version}'s environment is missing; reinstall it in the Versions tab")

    def skill(self, version):
        path = self.root / version / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def tools_python(self):
        meta = self._read(self.home / "tools-env.json")
        if not meta or meta.get("status") != "ready":
            return None
        own = env_python(self.home / "tools-env")
        if own.is_file():
            return own
        recorded = Path(meta.get("python") or "")
        return recorded if recorded.is_absolute() and recorded.is_file() else None

    def tools_status(self):
        meta = self._read(self.home / "tools-env.json") or {"status": "missing"}
        return {**meta, "packages": TOOLS_PACKAGES}

    @staticmethod
    def _read(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # --- what PyPI has ------------------------------------------------------------------------------------------

    @staticmethod
    def _fetch(url):
        import httpx

        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response

    def releases(self, refresh=False):
        """Published versions, newest first, without yanked ones. Cached for ten minutes."""
        stamp, cached = self._releases
        if cached and not refresh and time.time() - stamp < 600:
            return cached
        try:
            data = self.fetch(PYPI).json()
        except Exception as exc:  # noqa: BLE001 - offline is a normal state for a desktop app
            raise VersionError(f"could not reach PyPI: {exc}") from None
        found = [version for version, files in data.get("releases", {}).items()
                 if files and not all(f.get("yanked") for f in files) and VERSION_PATTERN.match(version)]
        found.sort(key=version_key, reverse=True)
        self._releases = (time.time(), found)
        return found

    def update_available(self):
        """The newest published version when it is newer than every installed one, else None."""
        try:
            newest = self.releases()[0]
        except (VersionError, IndexError):
            return None
        installed = self.ready()
        if installed and version_key(newest) <= version_key(installed[0]):
            return None
        return newest

    # --- installing ---------------------------------------------------------------------------------------------

    def _start(self, kind, target, work):
        with self.lock:
            for job in self.jobs.values():
                if job.status == "running" and (job.kind, job.target) == (kind, target):
                    return job
            job = Job(id=f"{kind}-{target}-{int(time.time() * 1000)}", kind=kind, target=target)
            self.jobs[job.id] = job

        def body():
            try:
                work(job)
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - shown in the Versions tab
                job.status, job.error = "failed", str(exc)
                job.log.append(f"Failed: {exc}")
            job.finished = time.time()

        threading.Thread(target=body, daemon=True).start()
        return job

    def install(self, version):
        """Install ``vixl-engine==version`` into its own environment in the background; return the job."""
        version = str(version).strip()
        if not VERSION_PATTERN.match(version):
            raise VersionError(f"{version!r} is not a version number")
        folder = self.root / version

        def work(job):
            folder.mkdir(parents=True, exist_ok=True)
            meta = {"version": version, "status": "installing", "installed": None, "python": None, "error": ""}
            (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            env = scrubbed_env(self.home)
            uv = find_uv()
            try:
                if (folder / "env").exists():
                    shutil.rmtree(folder / "env")
                self.runner([uv, "venv", "--python", PYTHON, folder / "env"], job.log, env)
                python = env_python(folder / "env")
                self.runner([uv, "pip", "install", "--python", python, f"{PACKAGE}=={version}"], job.log, env)
                self.runner([python, "-c", "import vixl; print('vixl', vixl.__version__)"], job.log, env)
            except Exception as exc:
                meta.update(status="failed", error=str(exc))
                (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
                raise
            try:
                response = self.fetch(SKILL_URL.format(version=version))
                (folder / "SKILL.md").write_text(response.text, encoding="utf-8")
                job.log.append("Fetched the agent skill for this release.")
            except Exception as exc:  # noqa: BLE001 - the skill is optional; older releases may not have it
                job.log.append(f"No agent skill for this release ({exc}); the Vixl arm runs without it.")
            meta.update(status="ready", installed=time.time(), python=str(python))
            (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            job.log.append(f"Vixl {version} is ready.")

        return self._start("install", version, work)

    def install_tools(self):
        """Create or repair the code-tools environment in the background; return the job."""
        target = self.home / "tools-env"
        record = self.home / "tools-env.json"

        def work(job):
            record.write_text(json.dumps({"status": "installing"}), encoding="utf-8")
            env = scrubbed_env(self.home)
            uv = find_uv()
            try:
                if target.exists():
                    shutil.rmtree(target)
                self.runner([uv, "venv", "--python", PYTHON, target], job.log, env)
                python = env_python(target)
                self.runner([uv, "pip", "install", "--python", python, *TOOLS_PACKAGES], job.log, env)
            except Exception as exc:
                record.write_text(json.dumps({"status": "failed", "error": str(exc)}), encoding="utf-8")
                raise
            record.write_text(json.dumps({"status": "ready", "python": str(python), "installed": time.time()}),
                              encoding="utf-8")
            job.log.append("The code-tools environment is ready.")

        return self._start("tools", "tools", work)

    def remove(self, version):
        if any(j.status == "running" and j.target == version for j in self.jobs.values()):
            raise VersionError(f"Vixl {version} is still installing")
        folder = self.root / version
        if not (folder / "meta.json").is_file():
            raise VersionError(f"Vixl {version} is not installed")
        shutil.rmtree(folder)

    def register(self, version, python, skill=""):
        """Record an existing environment as an installed version (tests, and a developer's own checkout)."""
        folder = self.root / version
        folder.mkdir(parents=True, exist_ok=True)
        if skill:
            (folder / "SKILL.md").write_text(skill, encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({"version": version, "status": "ready", "installed": time.time(),
                                                      "python": str(python), "error": ""}), encoding="utf-8")

    def register_tools(self, python):
        (self.home / "tools-env.json").write_text(
            json.dumps({"status": "ready", "python": str(python), "installed": time.time()}), encoding="utf-8")
