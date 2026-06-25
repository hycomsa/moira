"""Durable runner execution loop (ADR-006).

The API/control plane enqueues jobs. A runner claims one job at a time, drives
the existing Engine from persisted run state, and records completion back into
the primary store. Embedded and external runners should use this same contract.
"""
from __future__ import annotations

import json
import os
import socket
import time
import traceback
from typing import Callable

from .backends.base import BackendRegistry
from .engine import Engine
from .models import Event, GateDecision, Pipeline, Status
from .persistence import RunStore


StoreFactory = Callable[[], RunStore]
RegistryFactory = Callable[[], BackendRegistry]


class DurableRunner:
    def __init__(self, store_factory: StoreFactory, registry_factory: RegistryFactory, *,
                 worker_id: str | None = None, mode: str = "embedded",
                 lease_seconds: int = 300, poll_seconds: float = 0.5) -> None:
        self.store_factory = store_factory
        self.registry_factory = registry_factory
        self.worker_id = worker_id or f"{mode}-{socket.gethostname()}-{os.getpid()}"
        self.mode = mode
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.capabilities = ["mock", "claude_code", "litellm", "auto_check"]
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        while not self._stop:
            did_work = self.run_once()
            if not did_work:
                time.sleep(self.poll_seconds)

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True when a job was handled."""
        store = self.store_factory()
        try:
            self._upsert_worker(store)
            store.release_expired_leases()
            job = store.claim_next_job(self.worker_id, self.capabilities, self.lease_seconds)
            if not job:
                store.heartbeat_worker(self.worker_id, None)
                return False
            store.mark_job_running(job["job_id"], self.worker_id)
            store.heartbeat_worker(self.worker_id, job["job_id"])
            self._execute_job(store, job)
            return True
        finally:
            try:
                store.heartbeat_worker(self.worker_id, None)
            except Exception:  # noqa: BLE001
                pass
            store.close()

    def _upsert_worker(self, store: RunStore) -> None:
        store.upsert_worker({
            "worker_id": self.worker_id,
            "mode": self.mode,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "version": "0.1",
            "capabilities": self.capabilities,
            "status": "running",
        })

    def _execute_job(self, store: RunStore, job: dict) -> None:
        run_id = job["run_id"]
        try:
            if store.cancellation_requested(run_id):
                self._cancel_run(store, job)
                return

            run = store.get_run(run_id)
            if not run:
                store.complete_job(job["job_id"], self.worker_id, "failed", "run not found")
                return

            payload = job.get("payload") or {}
            pipeline = Pipeline.from_dict(json.loads(run["pipeline"]))
            context = payload.get("context") or {}
            engine = Engine(store, self.registry_factory(), owner=run.get("owner", "unknown"))

            if job["kind"] == "drive_run":
                result = engine.drive_existing(run_id, pipeline, context)
            elif job["kind"] == "resume_run":
                decision_data = payload.get("decision") or {}
                decision = GateDecision(**decision_data)
                result = engine.resume(run_id, pipeline, context, decision)
            else:
                store.complete_job(job["job_id"], self.worker_id, "failed",
                                   f"unknown job kind: {job['kind']}")
                return

            status = result.status.value
            store.complete_job(job["job_id"], self.worker_id, status)
        except Exception as e:  # noqa: BLE001
            try:
                store.update_run_status(run_id, Status.FAILED.value)
                store.append_event(Event(run_id=run_id, kind="run.end",
                                         message="Run failed in durable runner"))
            finally:
                store.complete_job(job["job_id"], self.worker_id, "failed",
                                   f"{e}\n{traceback.format_exc()}"[:4000])

    def _cancel_run(self, store: RunStore, job: dict) -> None:
        run_id = job["run_id"]
        store.update_run_status(run_id, Status.CANCELLED.value)
        store.append_event(Event(run_id=run_id, kind="run.cancel",
                                 message="Run cancelled before next runner step"))
        store.honor_cancellation(run_id)
        store.complete_job(job["job_id"], self.worker_id, Status.CANCELLED.value)
