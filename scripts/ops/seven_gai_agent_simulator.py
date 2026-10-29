"""Minimal local 7GAI Agent simulator for OTA contract and recovery tests.

Usage: python scripts/ops/seven_gai_agent_simulator.py --base-url http://127.0.0.1:8000/api/v1 \
  --enrollment-token <token> --agent-id sim-01

The simulator is deliberately an HTTP client, not a fake server.  It can
replay a download from a persisted chunk boundary and exercise the failure
branches that a real Agent must handle before a production rollout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from typing import Any


def _request(url: str, *, method: str = "GET", payload=None, token: str | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Seven-GAI-Agent-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return response.headers, (json.loads(raw) if raw else None)


def _download_chunk(base: str, agent_id: str, release_id: int, index: int, token: str) -> bytes:
    req = urllib.request.Request(
        f"{base}/edge-agents/{agent_id}/ota/{release_id}/chunks/{index}",
        headers={"X-Seven-GAI-Agent-Token": token},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


class DownloadInterrupted(ConnectionError):
    def __init__(self, message: str, partial: bytearray, last_index: int):
        super().__init__(message)
        self.partial = partial
        self.last_index = last_index


def download_release(
    base: str,
    *,
    agent_id: str,
    release_id: int,
    token: str,
    chunk_count: int,
    start_index: int = 0,
    disconnect_after: int | None = None,
    chunk_loader=_download_chunk,
) -> tuple[bytearray, int]:
    """Download chunks and return ``(bytes, last_index)``.

    ``disconnect_after`` intentionally raises after the requested number of
    chunks.  The caller can invoke this function again with ``last_index + 1``
    to model a network interruption and resumable OTA download.
    """
    if start_index < 0 or start_index > chunk_count:
        raise ValueError("start_index is outside the release")
    if disconnect_after is not None and disconnect_after < 1:
        raise ValueError("disconnect_after must be positive")
    blob = bytearray()
    last_index = start_index - 1
    downloaded = 0
    for index in range(start_index, chunk_count):
        blob.extend(chunk_loader(base, agent_id, release_id, index, token))
        last_index = index
        downloaded += 1
        if disconnect_after is not None and downloaded >= disconnect_after:
            raise DownloadInterrupted(
                f"simulated disconnect after chunk {index}", bytearray(blob), last_index
            )
    return blob, last_index


def _json_error(exc: Exception) -> dict[str, Any]:
    """Keep CLI failure output structured without leaking request contents."""
    return {"error": type(exc).__name__, "message": str(exc)[:500]}


def run(args) -> int:
    base = args.base_url.rstrip("/")
    _, registration = _request(
        f"{base}/edge-agents/register",
        method="POST",
        payload={"agent_id": args.agent_id, "name": "local-simulator", "enrollment_token": args.enrollment_token, "agent_version": "sim-1", "platform": "simulator", "capabilities": {"ota": True}},
    )
    agent = registration["data"]["agent"]
    token = registration["data"]["agent_token"]
    _request(
        f"{base}/edge-agents/{args.agent_id}/heartbeat",
        method="POST",
        token=token,
        payload={"reported_state": {"simulator": True}},
    )
    if args.release_id is None:
        print(json.dumps({"agent_id": agent["agent_id"], "agent_token": token}, ensure_ascii=False))
        return 0
    _, manifest = _request(f"{base}/edge-agents/{args.agent_id}/ota/{args.release_id}/manifest", token=token)
    manifest = manifest["data"]
    release = manifest["release"]
    start_index = int(args.resume_from)
    try:
        blob, last_index = download_release(
            base,
            agent_id=args.agent_id,
            release_id=args.release_id,
            token=token,
            chunk_count=int(release["chunk_count"]),
            start_index=start_index,
            disconnect_after=args.disconnect_after,
        )
    except DownloadInterrupted as exc:
        if not args.auto_resume:
            print(json.dumps(_json_error(exc), ensure_ascii=False))
            return 2
        # A reconnect starts at the next chunk.  The server-side deployment
        # record keeps the highest downloaded byte/index as the durable resume
        # cursor; retain the partial buffer and append only the missing chunks.
        resume_index = exc.last_index + 1
        resumed, last_index = download_release(
            base,
            agent_id=args.agent_id,
            release_id=args.release_id,
            token=token,
            chunk_count=int(release["chunk_count"]),
            start_index=resume_index,
        )
        blob = exc.partial
        blob.extend(resumed)
    digest = hashlib.sha256(blob).hexdigest()
    verify_digest = ("0" * 64) if args.fail_verify else digest
    try:
        _request(
            f"{base}/edge-agents/{args.agent_id}/ota/{args.release_id}/verify",
            method="POST",
            token=token,
            payload={"downloaded_sha256": verify_digest, "downloaded_bytes": len(blob)},
        )
    except Exception as exc:
        if not args.rollback_on_failure:
            print(json.dumps(_json_error(exc), ensure_ascii=False))
            return 2
        _, rolled_back = _request(
            f"{base}/edge-agents/{args.agent_id}/ota/{args.release_id}/rollback",
            method="POST",
            token=token,
            payload={"reason": "simulator verify failure"},
        )
        print(json.dumps({"phase": "verify", "rollback": rolled_back}, ensure_ascii=False))
        return 0
    active_version = release["version"] if not args.fail_activate else f"{release['version']}-mismatch"
    try:
        _, activated = _request(
            f"{base}/edge-agents/{args.agent_id}/ota/{args.release_id}/activate",
            method="POST",
            token=token,
            payload={"active_version": active_version},
        )
    except Exception as exc:
        if not args.rollback_on_failure:
            print(json.dumps(_json_error(exc), ensure_ascii=False))
            return 2
        _, rolled_back = _request(
            f"{base}/edge-agents/{args.agent_id}/ota/{args.release_id}/rollback",
            method="POST",
            token=token,
            payload={"reason": "simulator activation failure"},
        )
        print(json.dumps({"phase": "activate", "rollback": rolled_back}, ensure_ascii=False))
        return 0
    print(json.dumps({"phase": "activate", "last_chunk_index": last_index, "deployment": activated}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--enrollment-token", required=True)
    parser.add_argument("--agent-id", default="sim-local")
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--resume-from", type=int, default=0)
    parser.add_argument("--disconnect-after", type=int)
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--fail-verify", action="store_true")
    parser.add_argument("--fail-activate", action="store_true")
    parser.add_argument("--rollback-on-failure", action="store_true")
    sys.exit(run(parser.parse_args()))
