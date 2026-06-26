"""Durable runner execution loop (ADR-006).

The API/control plane enqueues jobs. A runner claims one job at a time, drives
the existing Engine from persisted run state, and records completion back into
the primary store. Embedded and external runners should use this same contract.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import traceback
from typing import Callable

from .backends.base import BackendRegistry
from .engine import Engine
from .models import Event, GateDecision, Pipeline, Status
from .persistence import RunStore

log = logging.getLogger("moira.runner")

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
            if not store.mark_job_running(job["job_id"], self.worker_id):
                # Lease was lost between claim and start (another worker reclaimed an
                # expired lease) — don't execute a job we no longer own.
                log.warning("lost lease for job %s before start; skipping", job["job_id"])
                return True
            store.heartbeat_worker(self.worker_id, job["job_id"])
            log.info("job.claim worker=%s job=%s run=%s kind=%s attempt=%s",
                     self.worker_id, job["job_id"], job["run_id"], job["kind"], job.get("attempt"))
            started = time.time()
            self._execute_job(store, job)
            log.info("job.complete worker=%s job=%s run=%s dur=%.2fs",
                     self.worker_id, job["job_id"], job["run_id"], time.time() - started)
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
            registry = self.registry_factory()
            engine = Engine(store, registry, owner=run.get("owner", "unknown"))

            # Keep the lease alive while the (potentially long) engine drive runs, so a
            # second worker never steals and double-executes this run (ADR-006:110). The
            # same heartbeat thread also watches for a cancellation request and kills the
            # active backend subprocess (B1) — the engine's should_cancel then stops at the
            # next node boundary. Heartbeat uses its OWN store connection on a daemon thread.
            stop_heartbeat = threading.Event()
            hb = threading.Thread(target=self._heartbeat_loop,
                                  args=(job["job_id"], run_id, registry, stop_heartbeat),
                                  daemon=True, name=f"hb-{job['job_id']}")
            hb.start()
            # cooperative cancellation: the engine polls this between node batches
            should_cancel = lambda: store.cancellation_requested(run_id)  # noqa: E731
            try:
                if job["kind"] == "drive_run":
                    result = engine.drive_existing(run_id, pipeline, context, should_cancel)
                elif job["kind"] == "resume_run":
                    decision_data = payload.get("decision") or {}
                    decision = GateDecision(**decision_data)
                    result = engine.resume(run_id, pipeline, context, decision, should_cancel)
                else:
                    store.complete_job(job["job_id"], self.worker_id, "failed",
                                       f"unknown job kind: {job['kind']}")
                    return
            finally:
                stop_heartbeat.set()
                hb.join(timeout=2)

            status = result.status.value
            if status == Status.CANCELLED.value:
                store.honor_cancellation(run_id)
            if not store.complete_job(job["job_id"], self.worker_id, status):
                # 0 rows updated => our lease was stolen mid-run despite heartbeat (e.g. a
                # DB stall longer than the lease). The current owner is authoritative; log
                # loudly rather than silently believing we succeeded.
                log.warning("lost lease for job %s during execution; completion not recorded "
                            "(another worker owns it)", job["job_id"])
        except Exception as e:  # noqa: BLE001
            try:
                store.update_run_status(run_id, Status.FAILED.value)
                store.append_event(Event(run_id=run_id, kind="run.end",
                                         message="Run failed in durable runner"))
            finally:
                store.complete_job(job["job_id"], self.worker_id, "failed",
                                   f"{e}\n{traceback.format_exc()}"[:4000])

    def _heartbeat_loop(self, job_id: str, run_id: str, registry, stop: threading.Event) -> None:
        """Renew the job lease AND watch for cancellation. Own store connection.

        Ticks at <=2s (capped below lease/3) so a cancellation request is acted on
        promptly: on the first request we kill the active backend (subprocess), which
        unblocks the long node so the engine's should_cancel can stop the run (B1).
        """
        interval = max(0.2, min(2.0, self.lease_seconds / 3.0))
        store = self.store_factory()
        killed = False
        try:
            store.heartbeat_job(job_id, self.worker_id, self.lease_seconds)
            while not stop.wait(interval):
                try:
                    store.heartbeat_job(job_id, self.worker_id, self.lease_seconds)
                    if not killed and store.cancellation_requested(run_id):
                        killed = True
                        log.info("cancellation requested for run %s — killing active backend", run_id)
                        try:
                            registry.cancel_active()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001 — a transient DB hiccup must not kill the thread
                    pass
        finally:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass

    def _cancel_run(self, store: RunStore, job: dict) -> None:
        run_id = job["run_id"]
        store.update_run_status(run_id, Status.CANCELLED.value)
        store.append_event(Event(run_id=run_id, kind="run.cancel",
                                 message="Run cancelled before next runner step"))
        store.honor_cancellation(run_id)
        store.complete_job(job["job_id"], self.worker_id, Status.CANCELLED.value)
