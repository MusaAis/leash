"""Read-only checks of wrap.near storage rules. No transaction, no key."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from .chain import is_valid_account_id
from .intents import _OPENER, MAX_RESPONSE_BYTES, USER_AGENT, IntentsError

DEFAULT_RPC = "https://free.rpc.fastnear.com"  # listed on docs.near.org/api/rpc/providers
CONTRACT = "wrap.near"


def rpc_url() -> str:
    url = os.environ.get("LEASH_RPC_URL") or DEFAULT_RPC
    if not url.startswith("https://"):
        raise IntentsError("LEASH_RPC_URL must start with https://")
    return url


def _post(url: str, body: dict) -> object:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with _OPENER.open(request, timeout=30) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise IntentsError(f"RPC request failed: {getattr(exc, 'code', None) or exc.reason}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise IntentsError("RPC answer is too large; refused")
    try:
        return json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise IntentsError("RPC answer is not JSON") from exc


def view_call(method: str, args: dict, *, post=_post, url: str | None = None) -> object:
    """A view call on wrap.near: the JSON the contract returned."""
    body = {"jsonrpc": "2.0", "id": "leash", "method": "query", "params": {
        "request_type": "call_function", "finality": "final", "account_id": CONTRACT, "method_name": method,
        "args_base64": base64.b64encode(json.dumps(args).encode()).decode()}}
    answer = post(url or rpc_url(), body)
    if not isinstance(answer, dict) or "result" not in answer:
        raise IntentsError(f"RPC answered an error for {method}: {json.dumps(answer)[:300]}")
    result = answer["result"]
    if not isinstance(result, dict) or not isinstance(result.get("result"), list):
        raise IntentsError(f"RPC answer for {method} has no result bytes")
    try:
        return json.loads(bytes(result["result"]).decode())
    except (ValueError, TypeError) as exc:
        raise IntentsError(f"contract answer for {method} is not JSON") from exc


def check_storage(accounts: list[str], *, post=_post) -> dict:
    """The storage bounds and the storage balance of each account, as the contract returned them."""
    for account in accounts:
        if not is_valid_account_id(account):
            raise IntentsError(f"not a valid account id: {account!r}")
    out = {"storage_balance_bounds": view_call("storage_balance_bounds", {}, post=post)}
    for account in accounts:
        out[f"storage_balance_of {account}"] = view_call("storage_balance_of", {"account_id": account}, post=post)
    return out
