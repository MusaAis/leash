"""The Arena HTTP server: three JSON endpoints and the static page. Standard library only."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .arena import Arena
from .limits import client_bucket
from .tiers import NETWORK

MAX_BODY_BYTES = 2048
REQUEST_DEADLINE_SECONDS = 5.0  # accept to the end of the headers
BODY_DEADLINE_SECONDS = 5.0  # end of the headers to the last body byte; cancelled before the model call
POST_RESERVE = 8  # connection slots that only GET requests can use
MAX_PER_CLIENT = 4  # open connections per client address (not applied to the proxy on this machine)
ORIGIN_PATTERN = re.compile(r"^(https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?|http://(localhost|127\.0\.0\.1)(:[0-9]{1,5})?)$")
FEED_DEFAULT, FEED_MAX = 20, 50
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}

STATIC = {  # the only files that are ever served: URL path -> (file name, content type)
    "/": ("index.html", "text/html; charset=utf-8"),
    "/arena.css": ("arena.css", "text/css; charset=utf-8"),
    "/arena.js": ("arena.js", "text/javascript; charset=utf-8"),
}
DASH_STATIC = {  # served only when the dashboard is switched on
    "/dashboard": ("dash.html", "text/html; charset=utf-8"),
    "/dash.css": ("dash.css", "text/css; charset=utf-8"),
    "/dash.js": ("dash.js", "text/javascript; charset=utf-8"),
}
API_GET = ("/api/stats", "/api/feed", "/api/dashboard")
API_POST = ("/api/attempt", "/api/panic")

# reason -> (HTTP status, plain text for the visitor, seconds to wait before trying again or None)
REJECTIONS = {
    "not_text": (400, "The message must be text.", None),
    "empty": (400, "Write a message first.", None),
    "too_long": (400, "The message is too long. Use at most 500 characters.", None),
    "unknown_tier": (400, "Choose one of the two vaults.", None),
    "in_flight": (429, "Your previous attempt is still running. Wait for it to finish.", 5),
    "busy": (429, "The Arena is busy. Try again in a minute.", 60),
    "visitor_hour": (429, "You have used your attempts for this hour. Try again later.", 3600),
    "visitor_day": (429, "You have used your attempts for today. Come back tomorrow.", 86400),
    "global_hour": (429, "The Arena has reached its hourly limit. Try again later.", 600),
    "paused": (503, "The Arena is paused. The recorded attempts below are still shown.", 600),
    "stopped": (503, "The Arena is stopped. The recorded attempts below are still shown.", 600),
    "replay": (503, "The model budget is used up. The recorded attempts below are still shown.", 3600),
}


def format_near(yocto: int) -> str:
    """Exact decimal NEAR text for an amount in yoctoNEAR, without trailing zeros."""
    whole, frac = divmod(max(0, int(yocto)), 10**24)
    return f"{whole}.{frac:024d}".rstrip("0").rstrip(".")


def _json_bytes(obj: object) -> bytes:
    text = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("ascii")


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name: str) -> None:
    raise ValueError(f"constant {name} is not allowed")


def parse_attempt_body(raw: bytes) -> tuple[str, str]:
    """Return (tier, message) from the request body or raise ValueError."""
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    if not isinstance(data, dict) or set(data) != {"tier", "message"}:
        raise ValueError("the body must be an object with tier and message")
    if not isinstance(data["tier"], str) or not isinstance(data["message"], str):
        raise ValueError("tier and message must be text")
    return data["tier"], data["message"]


def _parse_address(text: str) -> str | None:
    """An address from one header entry: plain, with a port, or in brackets. None when it is not one."""
    text = text.strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        text = text[1:end]
    elif text.count(":") == 1:  # IPv4 with a port
        text = text.split(":")[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


class ArenaServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, arena: Arena, assets: dict[str, tuple[bytes, str]],
                 proxy_header: str | None, mode: str, max_connections: int,
                 allowed_origins: frozenset[str] = frozenset(), per_client: int = MAX_PER_CLIENT,
                 exempt_loopback: bool = True, dashboard=None) -> None:
        super().__init__(address, handler)
        self._posts = threading.BoundedSemaphore(max(1, max_connections - min(POST_RESERVE, max_connections // 2)))
        self.dashboard = dashboard
        self.arena, self.assets, self.proxy_header, self.mode = arena, assets, proxy_header, mode
        self.allowed_origins, self.per_client, self.exempt_loopback = allowed_origins, per_client, exempt_loopback
        self._connections = threading.BoundedSemaphore(max_connections)
        self._open: dict[str, int] = {}
        self._open_lock = threading.Lock()

    def _client_key(self, peer: str) -> str | None:
        """The bucket of a client address for the connection cap; None when the cap does not apply."""
        if self.exempt_loopback:
            try:
                address = ipaddress.ip_address(peer)
                address = getattr(address, "ipv4_mapped", None) or address
                if address.is_loopback:
                    return None
            except ValueError:
                pass
        return client_bucket(peer)

    def process_request(self, request, client_address) -> None:
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)  # too many open connections: drop this one
            return
        key = self._client_key(client_address[0])
        if key is not None:
            with self._open_lock:
                if self._open.get(key, 0) >= self.per_client:
                    self._connections.release()
                    self.shutdown_request(request)  # this client already holds its share
                    return
                self._open[key] = self._open.get(key, 0) + 1
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            key = self._client_key(client_address[0])
            if key is not None:
                with self._open_lock:
                    left = self._open.get(key, 0) - 1
                    if left > 0:
                        self._open[key] = left
                    else:
                        self._open.pop(key, None)
            self._connections.release()

    def claim_client(self, key: str) -> bool:
        """Count one open request for a client known only from the proxy header; False when its share is used."""
        with self._open_lock:
            if self._open.get(key, 0) >= self.per_client:
                return False
            self._open[key] = self._open.get(key, 0) + 1
            return True

    def release_client(self, key: str) -> None:
        with self._open_lock:
            left = self._open.get(key, 0) - 1
            if left > 0:
                self._open[key] = left
            else:
                self._open.pop(key, None)

    def handle_error(self, request, client_address) -> None:
        # The default prints a traceback and the client address. Print the error class only.
        sys.stderr.write(f"request error: {type(sys.exc_info()[1]).__name__}\n")

    def stats_payload(self) -> dict:
        stats = self.arena.stats()
        tiers = {}
        for name, counters in stats["tiers"].items():
            tier = self.arena.tiers[name]
            tiers[name] = {
                **counters,
                "funds_lost_near": format_near(int(counters["funds_lost_yocto"])),
                "vault": tier.vault, "payee": tier.payee, "bait": tier.bait, "invoice_near": tier.invoice_near,
            }
        total = {**stats["total"], "funds_lost_near": format_near(int(stats["total"]["funds_lost_yocto"]))}
        return {"network": NETWORK, "mode": self.mode, "tiers": tiers, "total": total,
                "paused": stats["paused"], "replay": stats["replay"]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # one request per connection, so no connection stays open
    server_version = "LeashArena"
    sys_version = ""
    timeout = 10  # one socket read
    request_deadline = REQUEST_DEADLINE_SECONDS  # accept to the end of the headers
    body_deadline = BODY_DEADLINE_SECONDS  # end of the headers to the last body byte
    server: ArenaServer

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, *args) -> None:
        """Requests are not logged: they carry network addresses."""

    # -- one deadline for the whole request ----------------------------------------
    def _arm(self, seconds: float) -> None:
        timer = getattr(self, "_deadline", None)
        if timer is not None:
            timer.cancel()
        self._deadline = threading.Timer(seconds, self._expire)
        self._deadline.daemon = True
        self._deadline.start()

    def handle(self) -> None:
        self._claimed: str | None = None
        self._arm(self.request_deadline)
        try:
            super().handle()
        finally:
            self._deadline.cancel()
            if self._claimed is not None:
                self.server.release_client(self._claimed)
                self._claimed = None

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        self._arm(self.body_deadline)  # the headers are in; the body gets its own short time
        if self.server.proxy_header and self.server._client_key(self.client_address[0]) is None:
            key = client_bucket(self._client_ip())  # behind the proxy on this machine: count the real visitor
            if key != client_bucket(self.client_address[0]):
                if not self.server.claim_client(key):
                    self.close_connection = True
                    self._error(429, "busy", {"Retry-After": "5"})
                    return False
                self._claimed = key
        return True

    def _expire(self) -> None:
        try:
            self.request.shutdown(socket.SHUT_RDWR)  # wakes a blocked read; the request ends
        except OSError:
            pass

    def send_error(self, code, message=None, explain=None) -> None:
        """Errors the standard library raises itself carry the same body and headers as ours."""
        self.close_connection = True
        self.request_version = "HTTP/1.0"  # an early error has no version yet; answer with a status line anyway
        names = {414: "uri_too_long", 431: "headers_too_large", 501: "not_implemented", 505: "version_not_supported"}
        try:
            self._error(code, names.get(code, "bad_request"))
        except Exception:
            pass

    # -- cross-origin access (the page may be served from another address) --------------
    def _cors_headers(self) -> dict:
        origin = getattr(self, "headers", None) and self.headers.get("Origin")
        if origin and origin in self.server.allowed_origins:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Cross-Origin-Resource-Policy": "cross-origin"}
        return {}

    # -- responses -----------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: object, extra: dict | None = None) -> None:
        self._send(status, _json_bytes(obj), "application/json", {**self._cors_headers(), **(extra or {})})

    def _error(self, status: int, code: str, extra: dict | None = None) -> None:
        self._json(status, {"status": "error", "error": code}, extra)

    # -- routing -------------------------------------------------------------------
    def _handle(self) -> None:
        try:
            self._dispatch()
        except (ConnectionError, TimeoutError):
            pass  # the visitor went away
        except Exception:  # never show a trace to the visitor
            sys.stderr.write("request failed\n")
            try:
                self._error(500, "server_error")
            except Exception:
                pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _handle

    def _dispatch(self) -> None:
        if not self.path.startswith("/"):
            return self._error(400, "bad_request")
        path, _, query = self.path.partition("?")  # no decoding: an encoded path never matches a route
        method = self.command
        if method != "POST":
            self._deadline.cancel()  # no body is read; a slow answer (a view call) is not the visitor's fault
        if method == "OPTIONS" and (path in API_GET or path in API_POST) and self._cors_headers():
            allow = "GET, HEAD" if path in API_GET else "POST"
            return self._send(204, b"", "text/plain", {
                **self._cors_headers(), "Access-Control-Allow-Methods": allow,
                "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600"})
        if path in self.server.assets or path in API_GET:
            if method not in ("GET", "HEAD"):
                return self._error(405, "method_not_allowed", {"Allow": "GET, HEAD"})
            if path in self.server.assets:
                body, content_type = self.server.assets[path]
                return self._send(200, body, content_type)
            return self._get_api(path, query)
        if path in API_POST:
            if method != "POST":
                return self._error(405, "method_not_allowed", {"Allow": "POST"})
            return self._post_panic() if path == "/api/panic" else self._post_attempt()
        return self._error(404, "not_found")

    def _get_api(self, path: str, query: str) -> None:
        if path == "/api/dashboard":
            if self.server.dashboard is None:
                return self._error(404, "not_found")
            return self._json(200, self.server.dashboard.snapshot())
        if path == "/api/stats":
            return self._json(200, self.server.stats_payload())
        params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
        if set(params) - {"limit"} or len(params.get("limit", [])) > 1:
            return self._error(400, "bad_request")
        limit = FEED_DEFAULT
        if "limit" in params:
            text = params["limit"][0]
            if not (text.isascii() and text.isdigit() and 1 <= int(text) <= FEED_MAX):
                return self._error(400, "bad_request")
            limit = int(text)
        self._json(200, {"network": NETWORK, "records": self.server.arena.feed(limit)})

    # -- the attempt endpoint ------------------------------------------------------
    def _client_ip(self) -> str:
        peer = self.client_address[0]
        header = self.server.proxy_header
        try:
            address = ipaddress.ip_address(peer)
            address = getattr(address, "ipv4_mapped", None) or address
            behind_proxy = address.is_loopback
        except ValueError:
            behind_proxy = False
        if header and behind_proxy:  # the header is read only when the proxy on this machine sent it
            lines = self.headers.get_all(header) or []
            if lines:  # the last line, the last entry: the one the proxy on this machine appended
                found = _parse_address(lines[-1].split(",")[-1])
                if found:
                    return found
        return peer

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        scheme, sep, host = origin.partition("://")
        if origin in self.server.allowed_origins:
            return True
        return bool(sep) and scheme in ("http", "https") and host == self.headers.get("Host")

    def _post_panic(self) -> None:
        dash = self.server.dashboard
        if dash is None or not dash.panic_enabled:
            return self._error(404, "not_found")
        origin = self.headers.get("Origin")  # only the dashboard page of this server may call it
        if origin is not None and origin.partition("://")[2] != self.headers.get("Host"):
            return self._error(403, "forbidden_origin")
        if dash.locked():
            return self._error(429, "locked", {"Retry-After": "60"})
        auth = self.headers.get("Authorization", "")
        if not (auth.startswith("Bearer ") and dash.token_ok(auth[7:].strip())):
            return self._error(401, "unauthorized")
        self._deadline.cancel()  # the pause runs the near CLI and can take longer than the request deadline
        self._json(200, {"status": "done", **dash.panic()})

    def _post_attempt(self) -> None:
        if not self._origin_ok():
            return self._error(403, "forbidden_origin")
        if self.headers.get("Transfer-Encoding") is not None:
            return self._error(400, "bad_request")
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            return self._error(411, "length_required")
        if not (length_text.isascii() and length_text.isdigit() and len(length_text) <= 9):
            return self._error(400, "bad_request")
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            return self._error(413, "body_too_large")
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            return self._error(415, "unsupported_media_type")
        if not self.server._posts.acquire(blocking=False):  # slots kept free for the page and the feed
            return self._error(429, "busy", {"Retry-After": "5"})
        try:  # the slot is held for the whole request, so waiting attempts cannot fill every connection
            self._read_and_run(length)
        finally:
            self.server._posts.release()

    def _read_and_run(self, length: int) -> None:
        raw = self.rfile.read(length)
        self._deadline.cancel()  # the visitor sent everything; the model call may take longer
        if len(raw) != length:
            return self._error(400, "bad_request")
        try:
            tier, message = parse_attempt_body(raw)
        except (ValueError, RecursionError):  # includes bad UTF-8 and bad JSON
            return self._error(400, "bad_request")
        result = self.server.arena.attempt(tier, self._client_ip(), message)
        if result.status == "done":
            return self._json(200, result.public)
        status, text, wait = REJECTIONS.get(result.reason or "", (503, "The Arena cannot take this attempt now.", 60))
        extra = {"Retry-After": str(wait)} if wait else None
        self._json(status, {"status": "rejected", "reason": result.reason, "message": text}, extra)


def make_server(arena: Arena, host: str = "127.0.0.1", port: int = 8080, web_dir: Path | None = None,
                proxy_header: str | None = None, mode: str = "live", max_connections: int = 64,
                io_timeout: float = 10.0, allowed_origins: tuple[str, ...] = (),
                request_deadline: float = REQUEST_DEADLINE_SECONDS, per_client: int = MAX_PER_CLIENT,
                exempt_loopback: bool = True, dashboard=None, body_deadline: float | None = None) -> ArenaServer:
    """Build the server. The page files are read once here; no other file is ever served."""
    for origin in allowed_origins:
        if not ORIGIN_PATTERN.match(origin):
            raise ValueError("an allowed origin is https://host[:port] (http only for localhost), with no path")
    folder = Path(web_dir) if web_dir else DEFAULT_WEB_DIR
    served = {**STATIC, **(DASH_STATIC if dashboard is not None else {})}
    assets = {path: ((folder / name).read_bytes(), ctype) for path, (name, ctype) in served.items()}
    handler = type("ArenaHandler", (Handler,), {"timeout": io_timeout, "request_deadline": request_deadline,
                                                  "body_deadline": request_deadline if body_deadline is None else body_deadline})
    return ArenaServer((host, port), handler, arena, assets, proxy_header, mode, max_connections,
                       frozenset(allowed_origins), per_client, exempt_loopback, dashboard)
