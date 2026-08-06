import asyncio
import os

import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.structure_worker import structure_worker_loop


async def serve() -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            "app.structure_main:app",
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8001")),
            log_config=None,
        )
    )
    await server.serve()


async def run() -> None:
    await asyncio.gather(serve(), structure_worker_loop())


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
