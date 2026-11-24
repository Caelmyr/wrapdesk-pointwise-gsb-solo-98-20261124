"""Run the cylindrical label inspection desk with a bounded number of analysis workers."""

import os
from pathlib import Path

from waitress import serve

from .web import create_app


if __name__ == "__main__":
    data = Path(os.environ.get("WRAPDESK_DATA", "runtime-data")).resolve()
    serve(create_app(data), host=os.environ.get("HOST", "127.0.0.1"),
          port=int(os.environ.get("PORT", "8000")), threads=4, max_request_body_size=24 * 1024 * 1024)
