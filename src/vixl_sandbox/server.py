"""The app's local web server: one page plus a small JSON API, bound to 127.0.0.1.

Requests must name a local host (a guard against DNS rebinding) and every ``/api`` call must carry the per-launch
token embedded in the page, so another site open in a browser cannot start runs on your key. Files the models made
are served with a sandbox Content-Security-Policy, so model-written HTML runs without access to the app.
"""

import hmac
from pathlib import Path
import secrets
import shutil
import threading

from . import __version__, arms, previews, sandbox, system
from .providers import PRESETS, ProviderConfig, ProviderError
from .runner import Lab
from .versions import VersionError, Versions

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
FILE_HEADERS = {"Content-Security-Policy": "sandbox allow-scripts", "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store"}


def host_name(header):
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.split(":")[0]


def create_app(home, chat_factory=None, token=None, versions=None, keystore=None, opener=None, exports=None,
               on_quit=None):
    """The app for data folder ``home``. Tests pass scripted models (``chat_factory``), a ``Versions`` with
    registered environments, a key store, an ``opener`` in place of the system's and an ``exports`` folder."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    home = Path(home)
    versions = versions or Versions(home)
    options = {"chat_factory": chat_factory} if chat_factory else {}
    lab = Lab(home / "results", versions, keystore=keystore, **options)
    opener = opener or system.open_path
    exports_folder = Path(exports) if exports else None
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title="VixlSandbox", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.lab, app.state.versions, app.state.token = lab, versions, token
    app.state.browser_mode = False

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if host_name(request.headers.get("host")) not in LOCAL_HOSTS:
            return JSONResponse({"error": "VixlSandbox only answers on localhost"}, status_code=403)
        if request.url.path.startswith("/api/"):
            sent = request.headers.get("x-lab-token", "")
            if not hmac.compare_digest(sent.encode(), token.encode()):
                return JSONResponse({"error": "missing or wrong token; reload the page"}, status_code=403)
        return await call_next(request)

    def fail(status, message):
        raise HTTPException(status_code=status, detail=message)

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        return page.replace("__LAB_TOKEN__", token)

    @app.get("/static/{name}")
    def static(name: str):
        path = STATIC / name
        if "/" in name or "\\" in name or not path.is_file():
            fail(404, "not found")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    # --- configuration ----------------------------------------------------------------------------------------

    def arm_list():
        available = set(lab.available_arms())
        listed = ["bare", "code"] + [f"{arms.VIXL_PREFIX}{v}" for v in versions.ready()]
        return [{"id": a, **arms.arm_info(a), "available": a in available} for a in sorted(listed, key=arms.arm_order)]

    @app.get("/api/config")
    def config():
        providers = {name: {"label": p["label"], "kind": p["kind"], "base_url": p["base_url"],
                            "key_required": p["key_required"], "env": p["env"],
                            "suggestions": p.get("suggestions", []), "key_known": lab.key_known(name),
                            "key_saved": lab.key_saved(name)}
                     for name, p in PRESETS.items()}
        tools = versions.tools_python()
        return {"app": __version__, "providers": providers, "arms": arm_list(),
                "browser_mode": app.state.browser_mode,
                "keystore": lab.keystore.available(),
                "capabilities": {"render_html": previews.render_html_available(),
                                 "network_isolation": bool(sandbox.network_isolation()),
                                 "python_libraries": sandbox.installed_libraries(str(tools)) if tools else []},
                "home": str(home)}

    @app.post("/api/quit")
    def quit_app():
        if on_quit is not None:
            # Stop a moment later, so this reply reaches the page first.
            timer = threading.Timer(0.5, on_quit)
            timer.daemon = True
            timer.start()
        return {"ok": True}

    @app.post("/api/keys/forget")
    async def forget_key(request: Request):
        data = await request.json()
        if data.get("provider") not in PRESETS:
            fail(400, "unknown provider")
        lab.forget_key(data["provider"], data.get("base_url", ""))
        return {"ok": True}

    # --- Vixl versions ----------------------------------------------------------------------------------------

    @app.get("/api/versions")
    def list_versions(refresh: int = 0):
        try:
            releases, offline = versions.releases(refresh=bool(refresh)), ""
        except VersionError as exc:
            releases, offline = [], str(exc)
        return {"installed": versions.installed(), "releases": releases, "offline": offline,
                "update": versions.update_available() if releases else None, "tools": versions.tools_status(),
                "jobs": [job.public(tail=0) for job in versions.jobs.values()]}

    @app.post("/api/versions")
    async def install_version(request: Request):
        data = await request.json()
        try:
            return versions.install(data.get("version", "")).public()
        except VersionError as exc:
            fail(400, str(exc))

    @app.delete("/api/versions/{version}")
    def remove_version(version: str):
        try:
            versions.remove(version)
        except VersionError as exc:
            fail(400, str(exc))
        return {"ok": True}

    @app.post("/api/tools")
    def install_tools():
        return versions.install_tools().public()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        found = versions.jobs.get(job_id)
        if found is None:
            fail(404, "no such job")
        return found.public()

    # --- runs -------------------------------------------------------------------------------------------------

    @app.post("/api/models")
    async def models(request: Request):
        data = await request.json()
        provider = data.get("provider")
        if provider not in PRESETS:
            fail(400, "unknown provider")
        try:
            key = lab.resolve_key(provider, data.get("base_url", ""), data.get("api_key", ""),
                                  bool(data.get("remember")))
            chat = lab.chat_factory(ProviderConfig(provider=provider, model="", base_url=data.get("base_url", ""),
                                                   api_key=key))
            return {"models": chat.list_models()}
        except (ValueError, ProviderError) as exc:
            fail(400, str(exc))

    @app.post("/api/runs")
    async def start(request: Request):
        data = await request.json()
        key = data.pop("api_key", "") or ""
        remember = bool(data.pop("remember", False))
        try:
            return {"run_id": lab.start(data, key, remember)}
        except (ValueError, TypeError) as exc:
            fail(400, str(exc))

    @app.get("/api/runs")
    def runs():
        return {"runs": lab.store.runs()}

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        found = lab.store.run(run_id)
        if found is None:
            fail(404, "no such run")
        found["judging"] = run_id in lab.judging
        found["busy"] = lab.busy(run_id)
        found["arm_labels"] = {a: arms.arm_info(a)["label"] for a in found["spec"]["arms"]}
        return found

    @app.post("/api/runs/{run_id}/rounds")
    async def follow_up(run_id: str, request: Request):
        data = await request.json()
        try:
            return {"round": lab.follow_up(run_id, data.get("prompt", ""), data.get("api_key", "") or "")}
        except KeyError:
            fail(404, "no such run")
        except ValueError as exc:
            fail(400, str(exc))

    @app.get("/api/sessions/{session_id}/events")
    def events(session_id: str, since: int = 0):
        return {"events": lab.session_events(session_id, max(0, since))}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        lab.cancel(run_id)
        return {"ok": True}

    @app.delete("/api/runs/{run_id}")
    def delete(run_id: str):
        try:
            lab.delete(run_id)
        except KeyError:
            fail(404, "no such run")
        except ValueError as exc:
            fail(409, str(exc))
        return {"ok": True}

    @app.post("/api/runs/{run_id}/votes")
    async def vote(run_id: str, request: Request):
        data = await request.json()
        found = lab.store.run(run_id)
        if found is None:
            fail(404, "no such run")
        repeat, winner, number = data.get("repeat"), data.get("winner"), data.get("round", 1)
        members = {s["id"] for s in found["sessions"] if s["repeat"] == repeat
                   and any(g["round"] == number for g in s["generations"])}
        if not members or (winner not in members and winner not in ("tie", "none")):
            fail(400, "winner must be a session of that repeat and round, 'tie' or 'none'")
        lab.store.vote(run_id, number, repeat, winner)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/judge")
    async def judge(run_id: str, request: Request):
        data = await request.json()
        key = data.pop("api_key", "") or ""
        try:
            return {"judging": lab.judge(run_id, data, key)}
        except KeyError:
            fail(404, "no such run")
        except ValueError as exc:
            fail(400, str(exc))

    @app.get("/api/stats")
    def stats():
        rows = lab.store.stats()
        for row in rows:
            row["arm_label"] = arms.arm_info(row["arm"])["label"]
        return {"rows": rows}

    # --- files ------------------------------------------------------------------------------------------------

    @app.post("/api/export")
    async def export_runs(request: Request):
        from .export import build

        data = await request.json()
        folder = exports_folder or system.exports_folder()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            path = build(lab, data.get("runs") or None, out=None)
        except ValueError as exc:
            fail(400, str(exc))
        target = folder / export_name(path)
        number = 2
        while target.exists():
            target = folder / f"{Path(export_name(path)).stem}-{number}.zip"
            number += 1
        shutil.move(str(path), str(target))
        return {"path": str(target), "folder": str(folder), "bytes": target.stat().st_size}

    @app.post("/api/reveal")
    async def reveal(request: Request):
        data = await request.json()
        folder = (exports_folder or system.exports_folder()).resolve()
        path = Path(data.get("path", "")).resolve()
        if folder not in path.parents or not path.is_file():
            fail(400, "only exported files can be shown")
        opener(path, reveal=True)
        return {"ok": True}

    def round_file(run_id, session_id, number, relative):
        session = lab.store.session(session_id)
        if session is None or session["run_id"] != run_id:
            fail(404, "not found")
        folder = lab.root / "runs" / run_id / session_id / "rounds" / str(number)
        try:
            path = arms.inside(folder / "files", relative) if relative is not None else folder / "preview.png"
        except arms.ToolError:
            fail(404, "not found")
        if not path.is_file():
            fail(404, "not found")
        return path

    @app.post("/api/open")
    async def open_file(request: Request):
        """Open a generated file with the computer's own app (a .pptx in PowerPoint, a .pdf in its viewer)."""
        data = await request.json()
        path = round_file(data.get("run", ""), data.get("session", ""), int(data.get("round", 1)), data.get("path"))
        opener(path, reveal=bool(data.get("reveal")))
        return {"ok": True}

    @app.get("/files/{run_id}/{session_id}/{number}/preview.png")
    def preview(run_id: str, session_id: str, number: int):
        return FileResponse(round_file(run_id, session_id, number, None), headers=FILE_HEADERS)

    @app.get("/files/{run_id}/{session_id}/{number}/files/{relative:path}")
    def round_files(run_id: str, session_id: str, number: int, relative: str):
        return FileResponse(round_file(run_id, session_id, number, relative), headers=FILE_HEADERS)

    return app


def export_name(path):
    """``vixl-sandbox-export-<stamp>.zip`` from the temporary file's name ``vixl-sandbox-export-<stamp>-<random>.zip``."""
    stem = Path(path).stem
    parts = stem.split("-")
    return "-".join(parts[:5]) + ".zip" if len(parts) > 5 else Path(path).name
