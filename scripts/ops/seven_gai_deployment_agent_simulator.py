"""Local 7GAI edge-device simulator for model rollout callbacks.

This client exercises the device side of the deployment control plane without
touching a production service.  The operator still owns rollout transitions
(``approve/canary/promote/rollback``); the simulator only sends scoped Agent
callbacks authenticated with ``X-Seven-GAI-Agent-Token``.

Example::

    python scripts/ops/seven_gai_deployment_agent_simulator.py \
      --base-url http://127.0.0.1:9888/api/v1 \
      --enrollment-token local-enroll --agent-id edge-sim-01 \
      --release-id 12 --bundle-sha256 <64-hex> --healthy

Use ``--fail`` to report a failed activation and ``--rollback`` with an
operator JWT to request control-plane rollback after the failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


class SimulatorRequestError(RuntimeError):
    """A structured HTTP failure from the local API."""


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    agent_token: str | None = None,
    user_token: str | None = None,
) -> dict[str, Any] | None:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if agent_token:
        headers["X-Seven-GAI-Agent-Token"] = agent_token
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw).get("message") or json.loads(raw).get("detail")
        except (TypeError, ValueError, json.JSONDecodeError):
            detail = raw.decode(errors="replace")[:500]
        raise SimulatorRequestError(f"HTTP {exc.code}: {detail}") from exc


def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    registration = _request(
        f"{base}/edge-agents/register",
        method="POST",
        payload={
            "agent_id": args.agent_id,
            "name": "local-deployment-simulator",
            "enrollment_token": args.enrollment_token,
            "agent_version": "sim-deploy-1",
            "platform": "simulator",
            "capabilities": {"deployment_callbacks": True},
            "project_id": args.project_id,
        },
    )
    token = registration["data"]["agent_token"]
    _request(
        f"{base}/edge-agents/{args.agent_id}/heartbeat",
        method="POST",
        agent_token=token,
        payload={"reported_state": {"simulator": True, "deployment": True}},
    )

    if args.release_id is None:
        print(json.dumps({"agent_id": args.agent_id, "agent_token": token}, ensure_ascii=False))
        return 0

    release_id = int(args.release_id)
    report_url = f"{base}/edge-agents/{args.agent_id}/deployments/{release_id}/report"
    observe_url = f"{base}/edge-agents/{args.agent_id}/deployments/{release_id}/observe"
    transitions: list[dict[str, Any]] = []

    def control(operation: str, reason: str) -> dict[str, Any] | None:
        if not args.user_token:
            return None
        response = _request(
            f"{base}/deployment-releases/{release_id}/{operation}",
            method="POST",
            user_token=args.user_token,
            payload={"reason": reason},
        )
        transitions.append({"operation": operation, "response": response})
        return response

    # When an operator token is supplied, drive the control-plane phases too;
    # otherwise leave those actions to the UI/operator and only emit Agent
    # callbacks.  This makes the same script useful for both contract tests
    # and a complete local canary rehearsal.
    if args.user_token:
        control("approve", "local simulator approval")
        control("start", "local simulator precheck")
        control("precheck", "local simulator stage")
        control("canary", "local simulator canary")
    if args.fail:
        report = _request(
            report_url,
            method="POST",
            agent_token=token,
            payload={
                "reported_state": "FAILED",
                "error_message": args.failure_reason,
                "metrics": {"simulated": True},
            },
        )
        result: dict[str, Any] = {"phase": "failed", "report": report, "transitions": transitions}
        if args.rollback and args.user_token:
            result["rollback"] = control("rollback", "edge simulator failure")
            result["transitions"] = transitions
            # A real Agent acknowledges the stop/rollback desired state after
            # it has switched away from the failed bundle.
            result["rolled_back"] = _request(
                report_url,
                method="POST",
                agent_token=token,
                payload={"reported_state": "ROLLED_BACK", "error_message": "edge simulator rollback"},
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    bundle_sha256 = args.bundle_sha256
    if not bundle_sha256 or len(bundle_sha256) != 64:
        raise SimulatorRequestError("--bundle-sha256 is required for a successful rollout")
    staged = _request(
        report_url,
        method="POST",
        agent_token=token,
        payload={
            "reported_state": "STAGED",
            "reported_bundle_sha256": bundle_sha256,
            "agent_version": "sim-deploy-1",
            "capabilities": {"deployment_callbacks": True},
            "metrics": {"simulated": True},
        },
    )
    active = _request(
        report_url,
        method="POST",
        agent_token=token,
        payload={
            "reported_state": "ACTIVE",
            "reported_bundle_sha256": bundle_sha256,
            "agent_version": "sim-deploy-1",
            "metrics": {"simulated": True},
        },
    )
    observation = None
    if args.healthy:
        observation = _request(
            observe_url,
            method="POST",
            agent_token=token,
            payload={
                "observation_key": "sim-healthy-1",
                "status": "HEALTHY",
                "fps": args.fps,
                "latency_p95_ms": args.latency_p95_ms,
                "metrics": {"simulated": True},
            },
        )
    if args.user_token and args.healthy:
        control("promote", "local simulator healthy canary")
        control("expand", "local simulator expand")
        control("complete", "local simulator complete")
    print(
        json.dumps(
            {
                "phase": "active",
                "staged": staged,
                "active": active,
                "observation": observation,
                "transitions": transitions,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--enrollment-token", required=True)
    parser.add_argument("--agent-id", default="edge-sim-local")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--bundle-sha256")
    parser.add_argument("--healthy", action="store_true")
    parser.add_argument("--fail", action="store_true")
    parser.add_argument("--failure-reason", default="simulated activation failure")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--user-token")
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--latency-p95-ms", type=float, default=50.0)
    try:
        sys.exit(run(parser.parse_args()))
    except (SimulatorRequestError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        sys.exit(2)
