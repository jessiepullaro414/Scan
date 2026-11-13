"""The FastAPI app of OpenHandScan Studio: REST under ``/api``, the live WebSocket, the built UI.

:func:`create_app` implements ``software/docs/APP_API.md``:

* REST routes (blocking work runs in FastAPI's thread pool: the endpoints are plain functions),
  M1's plus M2's ``/api/setup/*``, ``/api/settings/camera`` and ``/api/calibration/*``
  (:mod:`ohs.server.setup`, :mod:`ohs.server.calibration`), and M3's processing studio
  ``/api/projects/{pid}/scans/{sid}/process/*`` and ``/api/recipes`` (:mod:`ohs.server.processing`);
* ``/api/ws/live``: ``hello``, then the current ``device`` / ``session`` / ``calib_session`` /
  ``setup`` / active ``job`` messages, then everything the :class:`~ohs.server.hub.Hub`
  publishes for this client; ``subscribe`` messages from the client set its options;
* errors are JSON ``{"detail": ..., "hint": ...}`` for every status (validation errors, unknown
  routes and unexpected exceptions included);
* the built UI is served from ``static_dir`` (default ``ohs/server/static/``, written by
  ``npm run build`` in ``software/ui``) with a single-page-app fallback to ``index.html`` for
  every non-``/api`` path that is not a file; without a build, a page explains how to build it;
* CORS for the Vite dev server (``http://localhost:5173``, ``http://127.0.0.1:5173``).

Local-app safety (:class:`LocalGuard`): requests must name this machine or an IP address in the
``Host`` header (blocks DNS-rebinding pages), and state-changing requests and WebSocket
connections that carry an ``Origin`` must come from the app itself or the dev server (blocks
other web pages from driving the scanner through the browser). Clients without an ``Origin``
(scripts, tests, ``curl``) are not affected.

The app's lifespan starts the :class:`~ohs.server.studio.Studio` threads and, on shutdown,
stops a running scan (the recording is finalised), switches the emitters off and disconnects.
"""
import asyncio
import contextlib
import ipaddress
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from ohs import __version__
from ohs.server.protocol import PROTOCOL_VERSION, ApiError, dumps, jsonable
from ohs.server.studio import Studio

__all__ = ["create_app", "CORS_ORIGINS", "LocalGuard"]

log = logging.getLogger(__name__)

CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# Windows maps some extensions from the registry (e.g. .js -> text/plain), which breaks module scripts
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                    (".svg", "image/svg+xml"), (".wasm", "application/wasm"), (".json", "application/json"),
                    (".map", "application/json"), (".woff2", "font/woff2"), (".ico", "image/x-icon")):
    mimetypes.add_type(_type, _ext)


# --------------------------------------------------------------------------- request bodies
class ConnectBody(BaseModel):
    source: Literal["camera", "replay", "sim"]
    recording: Optional[str] = None
    loop: Optional[bool] = None


class EmittersBody(BaseModel):
    vertical: Optional[float] = Field(None, ge=0, le=100)
    horizontal: Optional[float] = Field(None, ge=0, le=100)
    dots: Optional[float] = Field(None, ge=0, le=100)


class NameBody(BaseModel):
    name: str


class ScanStartBody(BaseModel):
    project_id: str
    mode: Literal["markerless", "markers"] = "markerless"
    name: Optional[str] = None


class RewindBody(BaseModel):
    seconds: float = Field(2.0, gt=0, le=3600)


class ProcessBody(BaseModel):
    quality: Literal["standard", "best", "preview", "full"] = "standard"  # "full": the old name of "standard"


class DismissBody(BaseModel):
    dismissed: bool


class CameraSettingsBody(BaseModel):
    index: Optional[int] = Field(None, ge=0, le=63)
    swap_lr: Optional[bool] = None


class PrintablesBody(BaseModel):
    paper: Optional[Literal["letter", "a4"]] = None


class PrintCheckBody(BaseModel):
    measured_mm: float = Field(..., gt=0, lt=1000)
    apply: bool = False


class CalibStartBody(BaseModel):
    kind: Literal["stereo", "lasers", "imu", "check"]


class AutoBody(BaseModel):
    enabled: bool


class SaveBody(BaseModel):
    force: bool = False


class TrackBody(BaseModel):
    quality: Literal["standard", "best", "preview", "full"] = "standard"  # "full": the old name of "standard"


class FuseBody(BaseModel):
    voxel_mm: Optional[float] = Field(None, ge=0.25, le=10.0)


class EditBody(BaseModel):
    op: Literal["remove_floaters", "fill_holes", "smooth", "decimate", "delete_faces", "keep_faces"]
    params: dict[str, Any] = Field(default_factory=dict)
    version: Optional[int] = None  # extra: the mesh version a selection was made on


class RecipeApplyBody(BaseModel):
    recipe_id: str
    scope: Literal["edits", "all"]


class FitBody(BaseModel):
    kind: str  # "plane" | "cylinder" | "hole" (checked by the engine, with a plain message)
    face: int = Field(ge=0)
    point: list[float] = Field(min_length=3, max_length=3)
    tolerance_mm: Optional[float] = Field(None, gt=0.0, le=10.0)
    version: Optional[int] = None  # the mesh version the click was made on


class ScanRef(BaseModel):
    project_id: str
    scan_id: str


class RecipeOpBody(BaseModel):
    op: Literal["remove_floaters", "fill_holes", "smooth", "decimate"]
    params: dict[str, Any] = Field(default_factory=dict)


class RecipeSettingsBody(BaseModel):
    quality: Literal["standard", "best", "preview", "full"] = "standard"  # "full": the old name of "standard"
    straighten: Literal["auto", "off"] = "auto"
    voxel_mm: Optional[float] = Field(None, ge=0.25, le=10.0)


class RecipeCreateBody(BaseModel):
    name: str
    description: Optional[str] = None
    from_scan: Optional[ScanRef] = None
    settings: Optional[RecipeSettingsBody] = None
    ops: Optional[list[RecipeOpBody]] = None


# --------------------------------------------------------------------------- helpers
def _json(x: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(jsonable(x), status_code=status)


def _err(status: int, detail: str, hint: Optional[str] = None) -> JSONResponse:
    body: dict = {"detail": detail}
    if hint:
        body["hint"] = hint
    return JSONResponse(body, status_code=status)


_NO_UI = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenHandScan Studio</title>
<style>
:root { --bg: #f6f7f9; --fg: #1d2330; --muted: #5b6475; --card: #ffffff; --code: #eef1f5; --accent: #2f6fde; }
@media (prefers-color-scheme: dark) { :root { --bg: #14171d; --fg: #e6e9ef; --muted: #9aa3b2; --card: #1d2129;
  --code: #262b35; --accent: #6ea0ff; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 640px; margin: 8vh auto; padding: 32px; background: var(--card); border-radius: 12px; }
h1 { margin-top: 0; font-size: 1.6em; } p { color: var(--muted); } a { color: var(--accent); }
pre, code { background: var(--code); border-radius: 6px; font: 14px ui-monospace, Consolas, monospace; }
pre { padding: 12px 16px; color: var(--fg); } code { padding: 1px 5px; }
</style></head><body><main>
<h1>OpenHandScan Studio</h1>
<p>The backend is running, but the user interface has not been built yet. Build it once:</p>
<pre>cd software/ui
npm install
npm run build</pre>
<p>The build is written to <code>software/ohs/server/static/</code>. Then reload this page.</p>
<p>For UI development, run <code>npm run dev</code> in <code>software/ui</code> and open
<a href="http://localhost:5173">http://localhost:5173</a>.</p>
<p>Backend: <a href="/api/health">/api/health</a> &middot; <a href="/api/docs">API docs</a></p>
</main></body></html>
"""


def _host_only(hostport: str) -> str:
    h = hostport.strip()
    if h.startswith("["):  # [::1]:8765
        return h[1 : h.find("]")] if "]" in h else h
    if h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h


class LocalGuard:
    """ASGI middleware: Host must be local or an IP; Origin (when sent) must be the app or the dev server."""

    def __init__(self, app: Any, allowed_origins: list[str], extra_hosts: tuple[str, ...] = ()) -> None:
        self.app = app
        self.allowed_origins = set(allowed_origins)
        self.extra_hosts = {h.lower() for h in extra_hosts if h}

    def host_ok(self, host: str) -> bool:
        h = _host_only(host).lower()
        if not h or h == "localhost" or h.endswith(".localhost") or h in self.extra_hosts:
            return True
        try:
            ipaddress.ip_address(h)
            return True
        except ValueError:
            return False

    def origin_ok(self, origin: str, host: str) -> bool:
        if origin in self.allowed_origins:
            return True
        netloc = urlsplit(origin).netloc
        return bool(netloc) and netloc.lower() == host.lower()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        origin = headers.get("origin")
        problem = None
        if not self.host_ok(host):
            problem = (f"requests for host {host!r} are refused", "open the app at http://127.0.0.1:<port>")
        elif origin and (kind == "websocket" or scope.get("method") in _UNSAFE) and not self.origin_ok(origin, host):
            problem = (f"cross-origin request from {origin} refused",
                       "use the app's own page (http://127.0.0.1:<port>) or the Vite dev server (port 5173)")
        if problem is None:
            await self.app(scope, receive, send)
            return
        log.warning("refused %s %s: %s", kind, scope.get("path"), problem[0])
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": problem[0], "hint": problem[1]}).encode("utf-8")
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# --------------------------------------------------------------------------- the app
def create_app(config: Any = None, *, projects_dir: Any = None, static_dir: Any = None, demo_dir: Any = None,
               data_dir: Any = None, config_path: Any = None, studio: Optional[Studio] = None, **studio_kw: Any) -> FastAPI:
    """Build the app. ``config`` defaults to :func:`ohs.app.config.load_config`; ``projects_dir``
    to ``paths.projects_dir``; ``static_dir`` to ``ohs/server/static``; ``demo_dir`` to ``~/.ohs/demo``;
    ``data_dir`` to ``paths.data_dir``; ``config_path`` (where settings are saved) to the loaded
    config file, else the default one. Other keywords go to :class:`~ohs.server.studio.Studio`
    (e.g. device factories for tests).

    The :class:`~ohs.server.studio.Studio` is at ``app.state.studio``; nothing starts before the
    app's lifespan does (uvicorn runs it).
    """
    st = studio or Studio(config, projects_dir=projects_dir, demo_dir=demo_dir, static_dir=static_dir,
                          data_dir=data_dir, config_path=config_path, **studio_kw)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        st.startup()
        try:
            yield
        finally:
            await asyncio.to_thread(st.shutdown)

    app = FastAPI(title="OpenHandScan Studio", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.studio = st
    extra_hosts = (str(getattr(getattr(st.cfg, "app", None), "host", "") or ""),)
    app.add_middleware(LocalGuard, allowed_origins=CORS_ORIGINS, extra_hosts=extra_hosts)
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["*"],
                       allow_headers=["*"])

    # ------------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(request: Any, exc: ApiError) -> JSONResponse:
        return _err(exc.status, exc.detail, exc.hint)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Any, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for e in exc.errors():
            loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
            parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
        return _err(422, "invalid request: " + "; ".join(parts), "check the request body against APP_API.md")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Any, exc: StarletteHTTPException) -> JSONResponse:
        hints = {404: "check the URL (the API is under /api, see APP_API.md)", 405: "check the HTTP method of this route"}
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _err(exc.status_code, detail, hints.get(exc.status_code))

    @app.exception_handler(Exception)
    async def _unexpected(request: Any, exc: Exception) -> JSONResponse:
        log.exception("unexpected error in %s %s", request.method, request.url.path)
        return _err(500, f"internal error: {type(exc).__name__}: {exc}",
                    "this is a bug: see the log (ohs -v app) and report it; your recordings are safe")

    # ------------------------------------------------------------------ REST
    @app.get("/api/health")
    def health() -> JSONResponse:
        return _json(st.health())

    @app.get("/api/settings")
    def settings() -> JSONResponse:
        return _json(st.settings())

    @app.get("/api/device")
    def device() -> JSONResponse:
        return _json(st.devices.state())

    @app.post("/api/device/connect")
    def device_connect(body: ConnectBody) -> JSONResponse:
        status, payload = st.connect(body.source, body.recording, body.loop)
        return _json(payload, status)

    @app.post("/api/device/disconnect")
    def device_disconnect() -> JSONResponse:
        return _json(st.disconnect())

    @app.post("/api/device/emitters")
    def device_emitters(body: EmittersBody) -> JSONResponse:
        return _json(st.set_emitters(body.model_dump(exclude_none=True)))

    @app.get("/api/projects")
    def projects_list() -> JSONResponse:
        return _json(st.projects.list_projects())

    @app.post("/api/projects")
    def projects_create(body: NameBody) -> JSONResponse:
        return _json(st.projects.create(body.name))

    @app.get("/api/projects/{pid}")
    def projects_get(pid: str) -> JSONResponse:
        return _json(st.projects.get(pid))

    @app.patch("/api/projects/{pid}")
    def projects_rename(pid: str, body: NameBody) -> JSONResponse:
        return _json(st.projects.rename(pid, body.name))

    @app.delete("/api/projects/{pid}")
    def projects_delete(pid: str) -> Response:
        st.delete_project(pid)
        return Response(status_code=204)

    @app.get("/api/scan")
    def scan_state() -> JSONResponse:
        return _json(st.session_state())

    @app.post("/api/scan/start")
    def scan_start(body: ScanStartBody) -> JSONResponse:
        return _json(st.start_scan(body.project_id, body.mode, body.name))

    @app.post("/api/scan/pause")
    def scan_pause() -> JSONResponse:
        return _json(st.pause_scan())

    @app.post("/api/scan/resume")
    def scan_resume() -> JSONResponse:
        return _json(st.resume_scan())

    @app.post("/api/scan/rewind")
    def scan_rewind(body: Optional[RewindBody] = None) -> JSONResponse:
        return _json(st.rewind_scan(body.seconds if body is not None else 2.0))

    @app.post("/api/scan/stop")
    def scan_stop() -> JSONResponse:
        return _json(st.stop_scan())

    @app.post("/api/projects/{pid}/scans/{sid}/process")
    def scan_process(pid: str, sid: str, body: Optional[ProcessBody] = None) -> JSONResponse:
        job = st.process_scan(pid, sid, body.quality if body is not None else "standard")
        return _json(job.to_dict())

    @app.get("/api/projects/{pid}/scans/{sid}/files/{name}")
    def scan_file(pid: str, sid: str, name: str) -> FileResponse:
        path, media = st.projects.file_path(pid, sid, name)
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------ M3: processing studio
    proc = st.processing

    @app.get("/api/projects/{pid}/scans/{sid}/process")
    def process_state(pid: str, sid: str) -> JSONResponse:
        return _json(proc.state(pid, sid))

    @app.post("/api/projects/{pid}/scans/{sid}/process/track")
    def process_track(pid: str, sid: str, body: Optional[TrackBody] = None) -> JSONResponse:
        return _json(proc.submit_track(pid, sid, body.quality if body is not None else "standard").to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/straighten")
    def process_straighten(pid: str, sid: str) -> JSONResponse:
        return _json(proc.submit_straighten(pid, sid).to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/spacetime")
    def process_spacetime(pid: str, sid: str) -> JSONResponse:
        return _json(proc.submit_spacetime(pid, sid).to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/refine")
    def process_refine(pid: str, sid: str) -> JSONResponse:
        return _json(proc.submit_refine(pid, sid).to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/fuse")
    def process_fuse(pid: str, sid: str, body: Optional[FuseBody] = None) -> JSONResponse:
        return _json(proc.submit_fuse(pid, sid, body.voxel_mm if body is not None else None).to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/edits")
    def process_edit(pid: str, sid: str, body: EditBody) -> JSONResponse:
        return _json(proc.submit_edit(pid, sid, body.op, body.params, body.version).to_dict())

    @app.post("/api/projects/{pid}/scans/{sid}/process/undo")
    def process_undo(pid: str, sid: str) -> JSONResponse:
        return _json(proc.undo(pid, sid))

    @app.post("/api/projects/{pid}/scans/{sid}/process/redo")
    def process_redo(pid: str, sid: str) -> JSONResponse:
        return _json(proc.redo(pid, sid))

    @app.post("/api/projects/{pid}/scans/{sid}/process/recipe")
    def process_recipe(pid: str, sid: str, body: RecipeApplyBody) -> JSONResponse:
        return _json(proc.submit_recipe(pid, sid, body.recipe_id, body.scope).to_dict())

    @app.get("/api/projects/{pid}/scans/{sid}/process/holes.json")
    def process_holes(pid: str, sid: str) -> JSONResponse:
        return _json(proc.holes(pid, sid))

    @app.get("/api/projects/{pid}/scans/{sid}/process/deviation.json")
    def process_deviation(pid: str, sid: str) -> JSONResponse:
        return _json(proc.deviation(pid, sid)[0])

    @app.get("/api/projects/{pid}/scans/{sid}/process/deviation.bin")
    def process_deviation_bin(pid: str, sid: str) -> Response:
        info, off = proc.deviation(pid, sid)
        return Response(off.astype("<f4", copy=False).tobytes(), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-cache", "X-OHS-Mesh-Version": str(info["version"])})

    @app.post("/api/projects/{pid}/scans/{sid}/fit")
    def scan_fit(pid: str, sid: str, body: FitBody) -> JSONResponse:
        return _json(proc.fit(pid, sid, body.kind, body.face, body.point, body.tolerance_mm, body.version))

    @app.get("/api/projects/{pid}/scans/{sid}/process/trajectory.json")
    def process_trajectory(pid: str, sid: str) -> JSONResponse:
        return _json(proc.trajectory(pid, sid))

    @app.get("/api/recipes")
    def recipes_list() -> JSONResponse:
        return _json(proc.recipes.list())

    @app.post("/api/recipes")
    def recipes_create(body: RecipeCreateBody) -> JSONResponse:
        settings = body.settings.model_dump() if body.settings is not None else None
        ops = [o.model_dump() for o in body.ops] if body.ops is not None else None
        ref = body.from_scan.model_dump() if body.from_scan is not None else None
        return _json(proc.save_recipe(body.name, body.description, ref, settings, ops))

    @app.delete("/api/recipes/{rid}")
    def recipes_delete(rid: str) -> Response:
        proc.recipes.delete(rid)
        return Response(status_code=204)

    @app.get("/api/jobs")
    def jobs_list() -> JSONResponse:
        return _json([j.to_dict() for j in st.jobs.list()])

    @app.get("/api/jobs/{jid}")
    def jobs_get(jid: str) -> JSONResponse:
        return _json(st.jobs.get(jid).to_dict())

    @app.post("/api/jobs/{jid}/cancel")
    def jobs_cancel(jid: str) -> JSONResponse:
        return _json(st.jobs.cancel(jid).to_dict())

    # ------------------------------------------------------------------ M2: setup
    @app.get("/api/setup")
    def setup_state() -> JSONResponse:
        return _json(st.setup.state())

    @app.post("/api/setup/dismiss")
    def setup_dismiss(body: DismissBody) -> JSONResponse:
        return _json(st.setup.dismiss(body.dismissed))

    @app.post("/api/setup/selftest")
    def setup_selftest() -> JSONResponse:
        return _json(st.setup.start_selftest().to_dict())

    @app.get("/api/setup/devices")
    def setup_devices() -> JSONResponse:
        return _json(st.setup.devices_probe())

    @app.post("/api/settings/camera")
    def settings_camera(body: CameraSettingsBody) -> JSONResponse:
        return _json(st.set_camera_settings(body.index, body.swap_lr))

    @app.get("/api/setup/printables")
    def printables_state() -> JSONResponse:
        return _json(st.setup.printables_state())

    @app.post("/api/setup/printables")
    def printables_generate(body: Optional[PrintablesBody] = None) -> JSONResponse:
        return _json(st.setup.start_printables(body.paper if body is not None else None).to_dict())

    @app.get("/api/setup/printables/files/{name}")
    def printables_file(name: str) -> FileResponse:
        path, media = st.setup.printable_file(name)
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache",
                                                              "X-Content-Type-Options": "nosniff"})

    @app.post("/api/setup/printables/open")
    def printables_open() -> JSONResponse:
        return _json(st.setup.open_printables())

    @app.post("/api/setup/print-check")
    def setup_print_check(body: PrintCheckBody) -> JSONResponse:
        return _json(st.setup.print_check(body.measured_mm, body.apply))

    # ------------------------------------------------------------------ M2: calibration
    @app.get("/api/calibration")
    def calib_state() -> JSONResponse:
        return _json(st.calibration.state())

    @app.post("/api/calibration/start")
    def calib_start(body: CalibStartBody) -> JSONResponse:
        return _json(st.calibration.start(body.kind))

    @app.post("/api/calibration/capture")
    def calib_capture() -> JSONResponse:
        return _json(st.calibration.capture())

    @app.delete("/api/calibration/views/{index}")
    def calib_delete_view(index: int) -> JSONResponse:
        return _json(st.calibration.delete_view(index))

    @app.post("/api/calibration/auto")
    def calib_auto(body: AutoBody) -> JSONResponse:
        return _json(st.calibration.set_auto(body.enabled))

    @app.post("/api/calibration/imu/start")
    def calib_imu_start() -> JSONResponse:
        return _json(st.calibration.imu_start())

    @app.post("/api/calibration/imu/stop")
    def calib_imu_stop() -> JSONResponse:
        return _json(st.calibration.imu_stop())

    @app.post("/api/calibration/solve")
    def calib_solve() -> JSONResponse:
        return _json(st.calibration.solve().to_dict())

    @app.post("/api/calibration/continue")
    def calib_continue() -> JSONResponse:
        return _json(st.calibration.continue_())

    @app.post("/api/calibration/save")
    def calib_save(body: Optional[SaveBody] = None) -> JSONResponse:
        return _json(st.calibration.save(body.force if body is not None else False))

    @app.post("/api/calibration/cancel")
    def calib_cancel() -> JSONResponse:
        return _json(st.calibration.cancel())

    @app.get("/api/calibration/views/{index}/thumb.jpg")
    def calib_thumb(index: int) -> Response:
        return Response(st.calibration.thumb(index), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.get("/api/calibration/report.json")
    def calib_report() -> JSONResponse:
        return _json(st.calibration.report())

    # ------------------------------------------------------------------ WebSocket
    @app.websocket("/api/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await ws.accept()
        hub = st.hub
        client = hub.register(asyncio.get_running_loop())
        sender: Optional[asyncio.Task] = None
        try:
            await ws.send_text(dumps({"type": "hello", "version": PROTOCOL_VERSION, "app": "openhandscan",
                                      "app_version": __version__}))
            st.seed_client(client)
            sender = asyncio.create_task(hub.serve(client, ws.send_text, ws.send_bytes))
            while True:
                recv = asyncio.create_task(ws.receive())
                done, _ = await asyncio.wait({recv, sender}, return_when=asyncio.FIRST_COMPLETED)
                if sender in done:
                    recv.cancel()
                    with contextlib.suppress(BaseException):
                        await recv
                    break
                msg = recv.result()
                if msg.get("type") == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text:
                    try:
                        data = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("type") == "subscribe":
                        hub.subscribe(client, data)
                        if data.get("mesh") is True:
                            st.seed_client(client)
        except Exception as e:  # a vanished client ends here (send/receive errors)
            log.debug("live client %d: %s", client.id, e)
        finally:
            hub.unregister(client)
            client.wake.set()
            if sender is not None:
                sender.cancel()
                with contextlib.suppress(BaseException):
                    await sender

    # ------------------------------------------------------------------ unknown API paths, then the UI
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_unknown(request: Request, rest: str) -> JSONResponse:
        path = request.url.path
        allowed = sorted({m for r in app.routes if isinstance(r, APIRoute) and r.name != "api_unknown"
                          and r.path.startswith("/api/") and r.path_regex.match(path) for m in (r.methods or ())})
        if allowed and request.method not in allowed:
            return _err(405, f"{request.method} is not allowed on {path}", f"use {' or '.join(allowed)}")
        return _err(404, f"unknown API route /api/{rest}", "see software/docs/APP_API.md for the routes")

    @app.get("/{full_path:path}", include_in_schema=False)
    def ui(full_path: str) -> Response:
        root = Path(st.static_dir)
        index = root / "index.html"
        if not index.is_file():
            return HTMLResponse(_NO_UI)
        if full_path:
            try:
                candidate = (root / full_path).resolve()
                inside = root.resolve() in candidate.parents
            except (OSError, ValueError):
                candidate, inside = root, False
            if inside and candidate.is_file():
                media = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
                return FileResponse(candidate, media_type=media, headers={"Cache-Control": cache})
            if "." in full_path.rsplit("/", 1)[-1]:  # a missing file, not an app route
                return _err(404, f"/{full_path} not found", "rebuild the UI (npm run build in software/ui)")
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})

    return app
