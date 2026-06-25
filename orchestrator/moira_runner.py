"""Standalone durable runner entrypoint (ADR-006).

The API sidecar can host an embedded runner, but team/self-hosted deployments
should run one or more external runner processes against the same primary store.
"""
from __future__ import annotations

import argparse
import logging
import os

from moira_core import BackendRegistry, ClaudeCodeBackend, DurableRunner, LiteLLMBackend, MockBackend, make_run_store


def registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register(MockBackend())
    reg.register(ClaudeCodeBackend())
    reg.register(LiteLLMBackend())
    return reg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Moira durable runner")
    parser.add_argument("--db", default=os.environ.get("MOIRA_DB", ".moira/moira.sqlite"))
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--mode", default="external", choices=["embedded", "external"])
    parser.add_argument("--lease-seconds", type=int,
                        default=int(os.environ.get("MOIRA_RUNNER_LEASE_SECONDS", "300")))
    parser.add_argument("--poll-seconds", type=float,
                        default=float(os.environ.get("MOIRA_RUNNER_POLL_SECONDS", "0.5")))
    parser.add_argument("--once", action="store_true",
                        help="claim and execute at most one job, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # External runners are for team/self-hosted deployments where several processes
    # share ONE primary store. SQLite is not safe as that shared store across
    # processes/hosts — team mode requires Postgres (ADR-005/ADR-006).
    primary = os.environ.get("MOIRA_PRIMARY", "sqlite").lower()
    if args.mode == "external" and primary != "postgres":
        parser.error("--mode external requires a shared Postgres primary "
                     "(set MOIRA_PRIMARY=postgres and MOIRA_PG_DSN); SQLite is not safe "
                     "for multi-process execution. Use --mode embedded for single-process/desktop.")

    def open_store():
        return make_run_store(args.db)

    runner = DurableRunner(open_store, registry, worker_id=args.worker_id,
                           mode=args.mode, lease_seconds=args.lease_seconds,
                           poll_seconds=args.poll_seconds)
    logging.info("Moira durable runner started: worker_id=%s mode=%s db=%s",
                 runner.worker_id, args.mode, args.db)
    if args.once:
        runner.run_once()
        return 0
    runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
