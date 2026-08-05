import asyncio
import os

import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.extraction_worker import worker_loop
from app.workers.structure_worker import structure_worker_loop


async def serve() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_config=None,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run() -> None:
    tasks = {
        asyncio.create_task(serve(), name="api"),
        asyncio.create_task(worker_loop(), name="worker"),
        asyncio.create_task(structure_worker_loop(), name="structure-worker"),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
