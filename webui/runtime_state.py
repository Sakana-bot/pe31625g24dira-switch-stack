"""Thread-safe runtime state for PE31625G24DIRA Switch Manager."""

import binascii
import itertools
import os
import queue
import threading
import time
import uuid


class RuntimeState:
    """Own mutable process state without depending on HTTP or hardware code."""

    def __init__(self, config, config_path=None, session_seconds=12 * 60 * 60):
        self.config = config
        self.config_path = config_path
        self.session_seconds = session_seconds
        self.config_lock = threading.Lock()
        self.jobs = {}
        self.jobs_lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.operation_queue = queue.PriorityQueue(maxsize=6)
        self.operation_queue_lock = threading.Lock()
        self.operation_sequence = itertools.count()
        self.operation_keys = {}
        self.active_operation_id = None
        self.auth_failures = {}
        self.auth_failures_lock = threading.Lock()
        self.sessions = {}
        self.sessions_lock = threading.Lock()
        self.telemetry_lock = threading.Lock()
        self.cpu_sample = None
        self.net_sample = {}
        self.switch_sample = None
        self.sensor_cache = {
            "state": "pending",
            "sampled": None,
            "temperatures": [],
            "voltages": [],
            "optics": {"state": "pending", "modules": []},
        }
        self.optics_cache = {"state": "pending", "sampled": None, "modules": []}
        self.l2_lock = threading.Lock()
        self.lldp_monitor = None
        self.lldp_mac_to_endpoint = {}
        threading.Thread(target=self._operation_dispatcher, daemon=True).start()

    def new_session(self, username):
        now = int(time.time())
        token = binascii.hexlify(os.urandom(32)).decode("ascii")
        session = {
            "username": username,
            "csrf": binascii.hexlify(os.urandom(32)).decode("ascii"),
            "expires": now + self.session_seconds,
        }
        with self.sessions_lock:
            self.sessions = {
                key: value for key, value in self.sessions.items() if value["expires"] > now
            }
            self.sessions[token] = session
        return token, dict(session)

    def get_session(self, token):
        if not token:
            return None
        now = int(time.time())
        with self.sessions_lock:
            session = self.sessions.get(token)
            if not session or session["expires"] <= now:
                self.sessions.pop(token, None)
                return None
            return dict(session)

    def revoke_session(self, token):
        with self.sessions_lock:
            self.sessions.pop(token, None)

    def revoke_all_sessions(self):
        with self.sessions_lock:
            self.sessions.clear()

    def login_allowed(self, address, now=None, window=60, limit=10):
        now = time.time() if now is None else now
        with self.auth_failures_lock:
            recent = [
                attempt
                for attempt in self.auth_failures.get(address, [])
                if now - attempt < window
            ]
            self.auth_failures[address] = recent
            return len(recent) < limit

    def record_login_failure(self, address, now=None):
        now = time.time() if now is None else now
        with self.auth_failures_lock:
            self.auth_failures.setdefault(address, []).append(now)

    def clear_login_failures(self, address):
        with self.auth_failures_lock:
            self.auth_failures.pop(address, None)

    def new_job(self, kind):
        now = int(time.time())
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": kind,
            "state": "queued",
            "message": "任务已排队",
            "created": now,
            "updated": now,
        }
        with self.jobs_lock:
            if len(self.jobs) >= 30:
                terminal = [
                    value for value in self.jobs.values() if value["state"] in {"done", "failed"}
                ]
                oldest = min(terminal or self.jobs.values(), key=lambda value: value["created"])
                self.jobs.pop(oldest["id"], None)
            self.jobs[job_id] = job
        return dict(job)

    def update_job(self, job_id, **values):
        with self.jobs_lock:
            self.jobs[job_id].update(values)
            self.jobs[job_id]["updated"] = int(time.time())

    def get_job(self, job_id):
        with self.jobs_lock:
            value = self.jobs.get(job_id)
            return dict(value) if value else None

    def start_operation(
        self,
        kind,
        target,
        *args,
        priority=0,
        coalesce_key=None,
    ):
        """Queue one hardware/SDK operation for serialized execution.

        Interactive requests use the default priority. Recurring background reads
        use a larger priority value and a coalescing key, so they cannot flood the
        queue or jump ahead of a user action.
        """
        with self.operation_queue_lock:
            if coalesce_key and coalesce_key in self.operation_keys:
                return self.get_job(self.operation_keys[coalesce_key])

            job = self.new_job(kind)
            try:
                ahead = self.operation_queue.qsize() + (
                    1 if self.active_operation_id is not None else 0
                )
                self.update_job(
                    job["id"],
                    message="SDK 操作已排队" if ahead else "正在准备 SDK 操作",
                    queue_ahead=ahead,
                )
                item = (
                    priority,
                    next(self.operation_sequence),
                    job["id"],
                    target,
                    args,
                    coalesce_key,
                )
                self.operation_queue.put_nowait(item)
                if coalesce_key:
                    self.operation_keys[coalesce_key] = job["id"]
            except queue.Full:
                self.update_job(
                    job["id"],
                    state="failed",
                    message="SDK 操作队列已满",
                    error="请等待当前操作完成后重试",
                )
                return None
        return self.get_job(job["id"])

    def operation_busy(self):
        return (
            self.active_operation_id is not None
            or self.operation_lock.locked()
            or not self.operation_queue.empty()
        )

    def _operation_dispatcher(self):
        while True:
            _, _, job_id, target, args, coalesce_key = self.operation_queue.get()
            self.active_operation_id = job_id
            try:
                self.update_job(job_id, message="等待当前 SDK 操作完成", queue_ahead=0)
                with self.operation_lock:
                    target(self, job_id, *args)
            except Exception as exc:
                self.update_job(
                    job_id,
                    state="failed",
                    message="SDK 操作异常退出",
                    error=str(exc),
                )
            finally:
                self.active_operation_id = None
                if coalesce_key:
                    with self.operation_queue_lock:
                        if self.operation_keys.get(coalesce_key) == job_id:
                            self.operation_keys.pop(coalesce_key, None)
                self.operation_queue.task_done()
