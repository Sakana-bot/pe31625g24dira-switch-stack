"""Thread-safe runtime state for PE31625G24DIRA Switch Manager."""

import binascii
import os
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
        }
        self.transceiver_cache = {"state": "pending", "sampled": None, "modules": []}
        self.l2_lock = threading.Lock()
        self.lldp_monitor = None
        self.lldp_mac_to_endpoint = {}

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

    def start_operation(self, kind, target, *args):
        """Start one serialized hardware/SDK operation, or return None when busy."""
        if not self.operation_lock.acquire(False):
            return None
        job = self.new_job(kind)
        try:
            thread = threading.Thread(target=target, args=(self, job["id"], *args), daemon=True)
            thread.start()
        except Exception:
            self.operation_lock.release()
            raise
        return job
