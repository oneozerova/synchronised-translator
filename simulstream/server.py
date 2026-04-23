"""
server.py – entry point for the SimulStreaming backend service.

Run:
    python server.py
    python server.py --host 0.0.0.0 --port 8000 --reload
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="SimulStreaming backend server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
