"""Run with python -m warpbuster_web; public origin is configured independently of bind."""

import argparse
import os
from ipaddress import IPv4Address

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="WarpBuster local web service")
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 address or localhost")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    try:
        host = "127.0.0.1" if args.host == "localhost" else str(IPv4Address(args.host))
    except ValueError:
        parser.error("--host must be an IPv4 address or localhost")
    os.umask(0o077)
    uvicorn.run(create_app(), host=host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
