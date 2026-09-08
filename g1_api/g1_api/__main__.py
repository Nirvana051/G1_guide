"""Entry point: ``python -m g1_api`` serves the API on port 1448."""

from __future__ import annotations

import os
import sys


def main() -> None:
    import uvicorn

    from g1_api.config import load_config

    config = load_config()
    uvicorn.run(
        "g1_api.gateway.app:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
