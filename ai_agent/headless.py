"""Synchronous command line adapter for the VC News Agent.

The CLI intentionally contains no business orchestration.  It translates command
line controls to ``DailyRunOptions`` and emits one machine-readable JSON object
as its final stdout line so a scheduler can locate the run manifest reliably.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .orchestration import (
    EXIT_PIPELINE,
    EXIT_PREFLIGHT,
    DailyRunOptions,
    DailyRunOrchestrator,
    RunResult,
    _safe_error,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ai_agent.headless", description="VC News Agent headless runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="check local runtime prerequisites")

    daily = subparsers.add_parser("daily", help="run one synchronous daily report")
    daily.add_argument("--date", dest="target_date", help="target date in Asia/Shanghai, YYYY-MM-DD")
    daily.add_argument("--force", action="store_true", help="create a new run even if the date already succeeded")
    daily.add_argument("--skip-crawl", action="store_true", help="use the existing database snapshot only")
    daily.add_argument(
        "--output-dir",
        "--output-root",
        dest="output_root",
        help="optional runtime root override (for tests or one-off exports)",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, orchestrator: DailyRunOrchestrator | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runner = orchestrator or DailyRunOrchestrator()

    if args.command == "health":
        try:
            payload = runner.health()
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
            return int(payload.get("exit_code", 0 if payload.get("healthy") else EXIT_PREFLIGHT))
        except Exception as exc:  # noqa: BLE001
            return _emit_cli_error("health", EXIT_PREFLIGHT, exc)

    options = DailyRunOptions(
        target_date=args.target_date,
        force=bool(args.force),
        skip_crawl=bool(args.skip_crawl),
        output_root=args.output_root,
    )
    try:
        result = runner.run(options)
    except KeyboardInterrupt:
        return _emit_cli_error("daily", EXIT_PIPELINE, "interrupted")
    except Exception as exc:  # noqa: BLE001
        return _emit_cli_error("daily", EXIT_PIPELINE, exc)

    payload = result.to_dict() if isinstance(result, RunResult) else _result_payload(result)
    # Keep this as the final stdout operation.  Codex uses it as the primary
    # pointer to the manifest and does not need to parse progress logs.
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
    return int(payload.get("exit_code", EXIT_PIPELINE))


def _result_payload(result) -> dict:
    if isinstance(result, dict):
        payload = dict(result)
    elif hasattr(result, "to_dict"):
        payload = dict(result.to_dict())
    else:
        payload = {
            key: getattr(result, key)
            for key in ("run_id", "status", "exit_code", "target_date", "manifest_path", "artifacts", "warnings", "error")
            if hasattr(result, key)
        }
    if payload.get("manifest_path") is not None:
        payload["manifest_path"] = str(payload["manifest_path"])
    return payload


def _emit_cli_error(command: str, exit_code: int, error) -> int:
    text = _safe_error(error)
    payload = {"status": "preflight_failed" if exit_code == EXIT_PREFLIGHT else "pipeline_failed", "exit_code": exit_code, "stage": command, "error": text}
    # A minimal stderr line is useful when the runtime directory itself cannot
    # be written.  It intentionally contains no configuration or request data.
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str), file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
    return exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
