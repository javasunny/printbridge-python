import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout

from pydantic import BaseModel

from .access import AccessStore
from .admin import admin_router
from .config import Settings
from .printer import print_pdf
from .store import Store
from .queue import PrintQueue
from .ipp import ipp_router

STATIC = Path(__file__).resolve().parent.parent / "static"


def public(job):
    return {**{k: job[k] for k in ("id", "name", "copies", "sides", "status", "message")},
            "created_at": datetime.fromtimestamp(job["created_at"], timezone.utc).isoformat()}


class ClientPresence(BaseModel):
    client_id: UUID


def create_app(settings=None, printer=print_pdf):
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.runtime)
    access = AccessStore(store)
    queue = PrintQueue(settings, store)

    async def worker(stop):
        while not stop.is_set():
            job = store.claim()
            if job:
                try:
                    status, message = await asyncio.to_thread(printer, settings, job, settings.runtime / (job["id"] + ".pdf"))
                except Exception:
                    status, message = "uncertain", "处理异常，请核查打印情况；未自动重试。"
                store.finish(job["id"], status, message)
            else:
                try:
                    store.cleanup(settings.retention_hours)
                except OSError:
                    logging.getLogger(__name__).warning("运行时文件清理失败，请检查目录权限")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass

    @asynccontextmanager
    async def lifespan(app):
        settings.runtime.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(settings.runtime / "service.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise RuntimeError("同一运行时目录已有服务运行，请使用单进程启动") from None
        try:
            store.initialize()
            access.initialize()
            stop = asyncio.Event()
            task = asyncio.create_task(worker(stop))
            try:
                yield
            finally:
                stop.set()
                await task
        finally:
            lock.release()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.access = access
    app.include_router(admin_router(access))
    if settings.ipp_enabled:
        app.include_router(ipp_router(settings, store, access, queue))

    async def authorize(authorization: str = Header(default="")):
        secret = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        key_id = access.authenticate(secret) if secret else None
        if not key_id:
            raise HTTPException(401, "访问令牌无效或已撤销")
        return key_id

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.post("/api/heartbeat")
    async def heartbeat(body: ClientPresence, request: Request, key_id=Depends(authorize)):
        if not access.heartbeat(key_id, str(body.client_id), request.client.host):
            raise HTTPException(401, "访问令牌已撤销")
        return {"ok": True}

    @app.post("/api/disconnect")
    async def disconnect(body: ClientPresence, key_id=Depends(authorize)):
        access.disconnect(key_id, str(body.client_id))
        return {"ok": True}

    @app.get("/api/settings", dependencies=[Depends(authorize)])
    async def get_settings():
        return {"max_upload_mb": settings.max_upload_mb, "mode": settings.mode}

    @app.get("/api/jobs", dependencies=[Depends(authorize)])
    async def jobs():
        return [public(row) for row in store.list()]

    @app.post("/api/jobs", dependencies=[Depends(authorize)], status_code=201)
    async def upload(request: Request, copies: int = Query(default=1, ge=1, le=99),
                     sides: Literal["simplex", "duplexlong", "duplexshort"] = "simplex",
                     name: str = Query(default="document.pdf", min_length=1, max_length=200),
                     idempotency_key: str = Header(default="")):
        try:
            key = str(UUID(idempotency_key))
        except ValueError:
            raise HTTPException(400, "需要有效的 Idempotency-Key UUID") from None
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "仅支持 PDF")
        return public(await queue.submit(request.stream(), key, name, copies, sides))

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return app
