from __future__ import annotations

import uvicorn

from .app import app, config


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
