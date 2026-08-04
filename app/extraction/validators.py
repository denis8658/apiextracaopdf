import asyncio
import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.config import Settings
from app.core.errors import pdf_error

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class ValidatedUpload:
    original_filename: str
    safe_filename: str
    storage_key: str
    content_type: str
    size: int
    sha256: str


def sanitize_filename(filename: str) -> str:
    base = Path(filename).name
    cleaned = SAFE_NAME.sub("_", base).strip("._")
    return (cleaned or "documento.pdf")[-180:]


async def inspect_pdf(path: Path, settings: Settings) -> int:
    try:
        import pymupdf
    except ImportError as exc:
        raise pdf_error("pdf_engine_unavailable", 503) from exc

    try:
        document = await asyncio.to_thread(pymupdf.open, path)
        try:
            if document.needs_pass:
                raise pdf_error("encrypted_pdf_not_supported", 422)
            page_count = document.page_count
            if page_count > settings.max_pdf_pages:
                raise pdf_error("page_limit_exceeded", 422)
            return page_count
        finally:
            document.close()
    except Exception as exc:
        if hasattr(exc, "code"):
            raise
        raise pdf_error("corrupted_pdf", 422) from exc


async def validate_and_stream(
    upload: UploadFile, settings: Settings
) -> tuple[ValidatedUpload, AsyncIterator[bytes]]:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != ".pdf":
        raise pdf_error("invalid_file_extension", 415)
    if upload.content_type != "application/pdf":
        raise pdf_error("invalid_content_type", 415)

    first = await upload.read(min(settings.upload_chunk_size_bytes, 5))
    if not first:
        raise pdf_error("empty_file", 400)
    if not first.startswith(b"%PDF-"):
        raise pdf_error("invalid_pdf_signature", 422)

    digest = hashlib.sha256()
    digest.update(first)
    state = {"size": len(first)}

    async def chunks() -> AsyncIterator[bytes]:
        try:
            yield first
            while chunk := await upload.read(settings.upload_chunk_size_bytes):
                state["size"] += len(chunk)
                if state["size"] > settings.max_pdf_size_bytes:
                    raise pdf_error("file_too_large", 413)
                digest.update(chunk)
                yield chunk
        finally:
            await upload.close()

    internal = f"{uuid.uuid4()}.pdf"
    result = ValidatedUpload(
        original_filename=filename,
        safe_filename=sanitize_filename(filename),
        storage_key=f"{internal[:2]}/{internal}",
        content_type=upload.content_type,
        size=0,
        sha256="",
    )

    async def finalizing_chunks() -> AsyncIterator[bytes]:
        async for chunk in chunks():
            yield chunk
        result.size = state["size"]
        result.sha256 = digest.hexdigest()

    return result, finalizing_chunks()
