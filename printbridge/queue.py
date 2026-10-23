import asyncio
import hashlib
import json
import logging
from uuid import uuid4

from fastapi import HTTPException
from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.CRITICAL)


def validate_pdf(path):
    try:
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError()
            stream.seek(0)
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted or not len(reader.pages):
                raise ValueError()
    except Exception:
        raise HTTPException(400, "请上传有效、未加密且至少包含一页的 PDF") from None


class PrintQueue:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    async def submit(self, chunks, key, name, copies, sides, owner=None):
        job_id = str(uuid4())
        part = self.settings.runtime / (job_id + ".part")
        target = self.settings.runtime / (job_id + ".pdf")
        retained = False
        digest = hashlib.sha256()
        size = 0
        try:
            with part.open("xb") as stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "文件超出上传大小限制")
                    stream.write(chunk)
                    digest.update(chunk)
            await asyncio.to_thread(validate_pdf, part)
            fingerprint = hashlib.sha256(json.dumps([digest.hexdigest(), copies, sides], separators=(",", ":")).encode()).hexdigest()
            part.replace(target)
            safe_name = name.replace("\\", "/").split("/")[-1]
            safe_name = "".join(c for c in safe_name if c.isprintable())[:200] or "document.pdf"
            try:
                job, retained = self.store.add(job_id, key, fingerprint, safe_name, copies, sides, owner=owner)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            return job
        finally:
            part.unlink(missing_ok=True)
            if not retained:
                target.unlink(missing_ok=True)
