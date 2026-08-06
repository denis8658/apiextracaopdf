import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from app.core.errors import pdf_error


class StorageBackend(Protocol):
    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> Path: ...
    async def open(self, key: str) -> Path: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def delete_prefix(self, prefix: str) -> None: ...


class LocalStorageBackend:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise pdf_error("storage_error", 500)
        return path

    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> Path:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                async for chunk in chunks:
                    await asyncio.to_thread(stream.write, chunk)
            return path
        except Exception:
            if path.exists():
                await asyncio.to_thread(path.unlink)
            raise

    async def open(self, key: str) -> Path:
        path = self._path(key)
        if not await asyncio.to_thread(path.is_file):
            raise pdf_error("storage_error", 500)
        return path

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if await asyncio.to_thread(path.is_dir):
            await asyncio.to_thread(shutil.rmtree, path)
