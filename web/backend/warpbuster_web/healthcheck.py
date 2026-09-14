"""Container health probe for the same HTTP surface Traefik uses."""

import http.client
import os
from urllib.parse import urlsplit


def main() -> int:
    origin = urlsplit(os.environ.get("WARPBUSTER_WEB_ORIGIN", "http://127.0.0.1:8000"))
    try:
        port = int(os.environ.get("WARPBUSTER_WEB_PORT", "8080"))
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/health", headers={"Host": origin.netloc})
        response = connection.getresponse()
        response.read()
        connection.close()
    except OSError, ValueError, http.client.HTTPException:
        return 1
    return 0 if response.status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
