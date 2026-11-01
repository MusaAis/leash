"""F-1 test: what kind of signature does the gateway give a model's answer? The signature is not verified here."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .brain import _OPENER, check_ai_url
from .chain import clean_text

EXTRA_MODEL = "z-ai/glm-5.3"
RETRIES, WAIT_S = 4, 3
PROMPT = "Reply with the single word: ok"


def _request(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=120) as resp:
            return resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(3000)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach NEAR AI Cloud: {exc.reason}") from exc


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return None


def check_signature(model: str, api_key: str, base_url: str, *, request=_request, sleep=time.sleep, log=None) -> dict:
    """One non-streaming call and its signature lookup. Failures are findings, recorded as they are."""
    base = check_ai_url(base_url).rstrip("/")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 300,
                       "stream": False, "temperature": 0}, separators=(",", ":")).encode()
    auth = {"Authorization": f"Bearer {api_key}"}
    result = {"model": model, "request_hash": hashlib.sha256(body).hexdigest()}
    status, raw = request("POST", base + "/chat/completions", {
        **auth, "Content-Type": "application/json", "Accept": "application/json",
        "Accept-Encoding": "identity", "x-no-aliasing": "true"}, body)
    if status != 200:
        result.update(stage="completion", status=status, detail=clean_text(raw.decode("utf-8", "replace"), 300))
    else:
        result["response_hash"] = hashlib.sha256(raw).hexdigest()
        data = _json(raw)
        chat_id = data.get("id") if isinstance(data, dict) else None
        returned = data.get("model") if isinstance(data, dict) else None
        result.update(stage="signature", chat_id=chat_id, returned_model=returned, aliased=returned != model)
        if not isinstance(chat_id, str) or not chat_id:
            result.update(signature_kind="unavailable", detail="the answer has no chat id")
        else:
            result.update(_fetch(base, chat_id, auth, result, request, sleep))
    if log is not None:
        log.append({"event": "signature_check", **result})
    return result


def _fetch(base, chat_id, auth, result, request, sleep) -> dict:
    url = f"{base}/signature/{urllib.parse.quote(chat_id, safe='')}?signing_algo=ecdsa"
    detail = "no signature after retries"
    for attempt in range(RETRIES):
        status, raw = request("GET", url, {**auth, "Accept": "application/json"}, None)
        data = _json(raw) if status == 200 else None
        if isinstance(data, dict) and "error_code" not in data and {"text", "signature", "signing_address"} <= set(data):
            kind = data.get("signature_kind")
            kind = kind if kind in ("provider_tee", "gateway") else "unfamiliar"
            req_h, resp_h = result["request_hash"], result["response_hash"]
            expected = {"provider_tee": f"{result['model']}:{req_h}:{resp_h}", "gateway": f"{req_h}:{resp_h}"}.get(kind)
            return {"signature_kind": kind, "signing_address": data["signing_address"],
                    "signing_algo": data.get("signing_algo"), "signature": data["signature"],
                    "text": data["text"], "text_matches": expected is not None and data["text"] == expected}
        detail = f"HTTP {status}: {clean_text(raw.decode('utf-8', 'replace'), 200)}"
        if attempt < RETRIES - 1:
            sleep(WAIT_S)
    return {"signature_kind": "unavailable", "detail": detail}


def describe(result: dict) -> str:
    if "signature_kind" not in result:
        return f"{result['model']}: FAILED at the completion, HTTP {result['status']}: {result['detail']}"
    lines = [f"{result['model']}:", f"  returned model: {result.get('returned_model')} (aliased: {result.get('aliased')})",
             f"  chat id: {result.get('chat_id')}", f"  request hash:  {result['request_hash']}",
             f"  response hash: {result['response_hash']}", f"  signature_kind: {result['signature_kind']}"]
    if result["signature_kind"] in ("provider_tee", "gateway", "unfamiliar"):
        lines += [f"  signer: {result['signing_address']} ({result['signing_algo']})",
                  f"  signed text matches our hashes: {result['text_matches']}"]
    else:
        lines.append(f"  unverified: {result.get('detail')}")
    return "\n".join(lines)
