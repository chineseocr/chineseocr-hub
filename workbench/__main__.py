import argparse
import os
import sys
from pathlib import Path

import uvicorn

from . import PRODUCT_NAME


def main():
    """Start one application process for the configured workspace.

    Binding defaults to loopback; production reverse-proxy/TLS and persistence are explicit deployment choices."""
    if len(sys.argv) > 1 and sys.argv[1] in {"ocr", "documents", "tables"}:
        from .recognition_cli import main as recognize_main

        return recognize_main(sys.argv[1:])
    parser = argparse.ArgumentParser(
        description=PRODUCT_NAME,
        epilog="Recognition CLI: chineseocr-hub ocr FILE or chineseocr-hub documents FILE",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", default=os.environ.get("WORKBENCH_DATA_DIR", "data"))
    parser.add_argument(
        "--mode", choices=["community", "saas"], default=os.environ.get("WORKBENCH_MODE", "community")
    )
    args = parser.parse_args()
    os.environ["WORKBENCH_DATA_DIR"] = str(Path(args.data_dir).resolve())
    print("Workspace: " + os.environ["WORKBENCH_DATA_DIR"], flush=True)
    if args.mode == "saas":
        from .saas import create_saas_app

        print(
            "Online mode: initial administrator credentials are stored privately under <workspace>/saas/initial-admin.json.",
            flush=True,
        )
        uvicorn.run(create_saas_app(), host=args.host, port=args.port, workers=1)
    else:
        print(
            "Access token: read <workspace>/access-token, or set WORKBENCH_TOKEN before startup.", flush=True
        )
        uvicorn.run("workbench.app:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
