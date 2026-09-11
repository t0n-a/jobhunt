"""One-shot messages carried across a redirect in a cookie.

A POST handler calls `flash` on its RedirectResponse; the next full-page GET
renders it and the middleware in app.py clears the cookie. HTMX partials never
read it: they replace an element in place, which is already the feedback.
"""

import json
from typing import Literal
from urllib.parse import quote, unquote

from fastapi import Request, Response

FLASH_COOKIE = "jobhunt_flash"
Kind = Literal["ok", "warn", "err"]


def flash(response: Response, text: str, kind: Kind = "ok") -> Response:
    payload = quote(json.dumps({"text": text, "kind": kind}))
    response.set_cookie(FLASH_COOKIE, payload, max_age=30, path="/", samesite="lax", httponly=True)
    return response


def read_flash(request: Request) -> tuple[str, Kind] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        data = json.loads(unquote(raw))
        return str(data["text"]), data.get("kind", "ok")
    except (ValueError, KeyError, TypeError):
        return None
