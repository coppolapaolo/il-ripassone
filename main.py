"""Entry point: lancia Il Ripassone con uvicorn.

    uv run main.py
"""
import sys
from pathlib import Path

# rendere importabile src/ripassone senza installare il pacchetto
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from ripassone import config


def main() -> None:
    print(f"\n  ▸ Il Ripassone su http://{config.HOST}:{config.PORT}\n")
    uvicorn.run(
        "ripassone.app:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.DEV_RELOAD,
        reload_dirs=["src", "templates", "static"],
    )


if __name__ == "__main__":
    main()
