import hmac
import ipaddress
import secrets
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def admin_router(access):
    csrf_token = secrets.token_urlsafe(32)

    async def local_only(request: Request):
        try:
            peer = ipaddress.ip_address(request.client.host)
            local = peer.is_loopback or (isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped and peer.ipv4_mapped.is_loopback)
        except (ValueError, AttributeError):
            local = False
        if not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, "管理页仅允许在服务端本机使用 localhost 或 127.0.0.1 访问")
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "不允许跨站管理请求")

    async def mutation(request: Request, x_admin_csrf: str = Header(default="")):
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("origin") != expected or not hmac.compare_digest(x_admin_csrf.encode(), csrf_token.encode()):
            raise HTTPException(403, "管理会话无效，请刷新本机管理页")

    router = APIRouter(prefix="/admin", dependencies=[Depends(local_only)])

    @router.get("")
    async def page():
        return FileResponse(Path(__file__).resolve().parent.parent / "templates" / "admin.html")

    @router.get("/api/session")
    async def session():
        return {"csrf": csrf_token}

    @router.get("/api/keys")
    async def keys():
        return access.keys()

    @router.get("/api/clients")
    async def clients():
        return access.clients()

    @router.post("/api/keys", dependencies=[Depends(mutation)], status_code=201)
    async def issue(body: KeyRequest):
        name = body.name.strip()
        if not name or not all(c.isprintable() for c in name):
            raise HTTPException(400, "请输入有效设备名称")
        return access.issue(name)

    @router.delete("/api/keys/{key_id}", dependencies=[Depends(mutation)])
    async def revoke(key_id: UUID):
        if not access.revoke(str(key_id)):
            raise HTTPException(404, "令牌不存在")
        return {"ok": True}

    return router
