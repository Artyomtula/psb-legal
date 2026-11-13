from __future__ import annotations

import asyncio
import random
import sys

import httpx

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


_clients: dict[tuple[str, float, int], httpx.AsyncClient] = {}


def client(base_url: str, timeout: float) -> httpx.AsyncClient:
    key = (base_url, timeout, id(asyncio.get_running_loop()))
    c = _clients.get(key)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=min(15.0, timeout)),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
        _clients[key] = c
    return c


async def close_all() -> None:
    for c in list(_clients.values()):
        if not c.is_closed:
            await c.aclose()
    _clients.clear()


async def post_json(base_url: str, path: str, key: str, payload: dict, timeout: float, attempts: int = 4) -> dict:
    if not base_url:
        raise ApiError(0, "base url is not configured")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = await client(base_url, timeout).post(path, json=payload, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            err = ApiError(resp.status_code, resp.text)
            if resp.status_code not in RETRY_STATUS:
                raise err
            last = err
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
        if attempt + 1 == attempts:
            break
        delay = min(20.0, (2 ** attempt) + random.random())
        print(f"{path}: попытка {attempt + 1} не удалась ({type(last).__name__}: {str(last)[:120]}), повтор через {delay:.1f}с", file=sys.stderr)
        await asyncio.sleep(delay)
    raise last or ApiError(0, "no attempts were made")
