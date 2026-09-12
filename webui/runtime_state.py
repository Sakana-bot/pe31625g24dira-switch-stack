"""Thread-safe runtime state for PE31625G24DIRA Switch Manager."""

import binascii
import itertools
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta


DEFAULT_TELEMETRY_DATABASE = "/var/lib/pe31625g24dira/telemetry.sqlite3"


class TelemetryHistory:
    """Small in-memory, multi-resolution history for dashboard metrics."""

    VALUE_KEYS = ("cpu", "memory", "memoryUsed", "rx", "tx")

    def __init__(self):
        self.lock = threading.Lock()
        self.tiers = (
            {"resolution": 5, "retention": 15 * 60, "points": deque(maxlen=15 * 12)},
            {"resolution": 30, "retention": 6 * 60 * 60, "points": deque(maxlen=6 * 60 * 2)},
            {"resolution": 300, "retention": 7 * 24 * 60 * 60, "points": deque(maxlen=7 * 24 * 12)},
        )
        self.buckets = [None for _ in self.tiers]

    @staticmethod
    def _number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None

    def _sample(self, payload):
        traffic = (payload.get("port_status") or {}).get("traffic") or {}
        return {
            "timestamp": int(payload.get("sampled") or time.time()),
            "cpu": self._number((payload.get("cpu") or {}).get("usage_percent")),
            "memory": self._number((payload.get("memory") or {}).get("usage_percent")),
            "memoryUsed": self._number((payload.get("memory") or {}).get("used")),
            "rx": self._number(traffic.get("rx_bps")),
            "tx": self._number(traffic.get("tx_bps")),
        }

    @classmethod
    def _new_bucket(cls, timestamp, resolution):
        return {
            "timestamp": timestamp - timestamp % resolution,
            "sums": {key: 0.0 for key in cls.VALUE_KEYS},
            "counts": {key: 0 for key in cls.VALUE_KEYS},
        }

    @classmethod
    def _accumulate(cls, bucket, sample):
        for key in cls.VALUE_KEYS:
            if sample[key] is not None:
                bucket["sums"][key] += sample[key]
                bucket["counts"][key] += 1

    @classmethod
    def _finish_bucket(cls, bucket):
        result = {"timestamp": bucket["timestamp"]}
        for key in cls.VALUE_KEYS:
            count = bucket["counts"][key]
            result[key] = round(bucket["sums"][key] / count, 2) if count else None
        return result

    def record(self, payload):
        with self.lock:
            sample = self._sample(payload)
            for index, tier in enumerate(self.tiers):
                resolution = tier["resolution"]
                bucket_time = sample["timestamp"] - sample["timestamp"] % resolution
                bucket = self.buckets[index]
                if bucket is not None and bucket["timestamp"] != bucket_time:
                    tier["points"].append(self._finish_bucket(bucket))
                    bucket = None
                if bucket is None:
                    bucket = self._new_bucket(sample["timestamp"], resolution)
                    self.buckets[index] = bucket
                self._accumulate(bucket, sample)

    def query(self, seconds):
        seconds = max(60, min(int(seconds), self.tiers[-1]["retention"]))
        tier_index = next(
            (index for index, tier in enumerate(self.tiers) if tier["retention"] >= seconds),
            len(self.tiers) - 1,
        )
        cutoff = int(time.time()) - seconds
        with self.lock:
            tier = self.tiers[tier_index]
            points = [dict(point) for point in tier["points"] if point["timestamp"] >= cutoff]
            current = self.buckets[tier_index]
            if current is not None and current["timestamp"] >= cutoff:
                points.append(self._finish_bucket(current))
        return {
            "range_seconds": seconds,
            "resolution_seconds": tier["resolution"],
            "samples": points,
        }


class TelemetryPersistence:
    """Optional minute-level metric and per-port traffic history."""

    # One 100G port can transfer at most about 750 GB per minute. Values beyond
    # this guard are counter discontinuities, not traffic.
    MAX_PORT_BYTES_PER_MINUTE = 1_000_000_000_000

    def __init__(self, path, enabled=False, retention_days=30):
        self.path = path
        self.enabled = bool(enabled)
        self.retention_days = self._retention(retention_days)
        self.lock = threading.Lock()
        self.metric_bucket = None
        self.traffic_bucket = None
        self.previous_ports = {}
        self.last_prune_day = None
        if self.enabled:
            try:
                self._initialize()
                self._prune(force=True)
            except (OSError, sqlite3.Error) as exc:
                print(f"Telemetry persistence unavailable: {exc}", flush=True)

    @staticmethod
    def _retention(value):
        value = int(value)
        return value if value in {7, 30, 90} else 30

    @staticmethod
    def _port_samples(payload):
        result = {}
        ports = ((payload.get("port_status") or {}).get("ports") or {})
        for logical, port in ports.items():
            try:
                epl, lane = int(port["epl"]), int(port["lane"])
                rx = int(port["statistics"]["rx"]["good_bytes"])
                tx = int(port["statistics"]["tx"]["good_bytes"])
            except (KeyError, TypeError, ValueError):
                continue
            key = f"epl{epl}.lane{lane}"
            result[key] = {
                "logical": int(logical), "epl": epl, "lane": lane,
                "rx": rx, "tx": tx, "mode": (port.get("speed"), port.get("type")),
            }
        return result

    def configure(self, enabled, retention_days):
        with self.lock:
            enabled = bool(enabled)
            self.retention_days = self._retention(retention_days)
            if enabled and not self.enabled:
                self.previous_ports = {}
                self.metric_bucket = None
                self.traffic_bucket = None
            if not enabled:
                self.metric_bucket = None
                self.traffic_bucket = None
                self.previous_ports = {}
            self.enabled = enabled
            if enabled:
                self._initialize()
                self._prune(force=True)

    def settings(self):
        with self.lock:
            return {"enabled": self.enabled, "retention_days": self.retention_days}

    def clear(self):
        with self.lock:
            self.metric_bucket = None
            self.traffic_bucket = None
            self.previous_ports = {}
            if not os.path.exists(self.path):
                return
            try:
                with self._database() as connection:
                    connection.execute("DELETE FROM metric_minutes")
                    connection.execute("DELETE FROM traffic_minutes")
            except (OSError, sqlite3.Error) as exc:
                raise RuntimeError(f"无法清除历史数据：{exc}") from None

    def _connect(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._database() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metric_minutes (
                    timestamp INTEGER PRIMARY KEY,
                    cpu REAL, memory REAL, memory_used REAL, rx REAL, tx REAL
                );
                CREATE TABLE IF NOT EXISTS traffic_minutes (
                    timestamp INTEGER NOT NULL,
                    port_key TEXT NOT NULL,
                    logical INTEGER NOT NULL,
                    epl INTEGER NOT NULL,
                    lane INTEGER NOT NULL,
                    rx_bytes INTEGER NOT NULL,
                    tx_bytes INTEGER NOT NULL,
                    PRIMARY KEY (timestamp, port_key)
                );
                CREATE INDEX IF NOT EXISTS traffic_minutes_timestamp
                    ON traffic_minutes(timestamp);
                """
            )

    @staticmethod
    def _metric_values(payload):
        traffic = (payload.get("port_status") or {}).get("traffic") or {}
        return {
            "cpu": TelemetryHistory._number((payload.get("cpu") or {}).get("usage_percent")),
            "memory": TelemetryHistory._number((payload.get("memory") or {}).get("usage_percent")),
            "memory_used": TelemetryHistory._number((payload.get("memory") or {}).get("used")),
            "rx": TelemetryHistory._number(traffic.get("rx_bps")),
            "tx": TelemetryHistory._number(traffic.get("tx_bps")),
        }

    @staticmethod
    def _new_metric_bucket(timestamp):
        return {"timestamp": timestamp, "sums": {}, "counts": {}}

    def _write_buckets(self, metric, traffic):
        if metric is None and not traffic:
            return
        with self._database() as connection:
            if metric is not None:
                values = []
                for key in ("cpu", "memory", "memory_used", "rx", "tx"):
                    count = metric["counts"].get(key, 0)
                    values.append(round(metric["sums"].get(key, 0) / count, 2) if count else None)
                connection.execute(
                    "INSERT OR REPLACE INTO metric_minutes VALUES (?, ?, ?, ?, ?, ?)",
                    (metric["timestamp"], *values),
                )
            if traffic:
                connection.executemany(
                    "INSERT OR REPLACE INTO traffic_minutes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (traffic["timestamp"], key, value["logical"], value["epl"],
                         value["lane"], value["rx_bytes"], value["tx_bytes"])
                        for key, value in traffic["ports"].items()
                    ],
                )

    def _prune(self, force=False):
        today = datetime.now().date().isoformat()
        if not force and today == self.last_prune_day:
            return
        cutoff = int(time.time()) - self.retention_days * 86400
        with self._database() as connection:
            connection.execute("DELETE FROM metric_minutes WHERE timestamp < ?", (cutoff,))
            connection.execute("DELETE FROM traffic_minutes WHERE timestamp < ?", (cutoff,))
        self.last_prune_day = today

    def record(self, payload):
        with self.lock:
            if not self.enabled:
                return
            timestamp = int(payload.get("sampled") or time.time())
            minute = timestamp - timestamp % 60
            metric_to_write = traffic_to_write = None
            if self.metric_bucket is not None and self.metric_bucket["timestamp"] != minute:
                metric_to_write = self.metric_bucket
                traffic_to_write = self.traffic_bucket
                self.metric_bucket = None
                self.traffic_bucket = None
            if self.metric_bucket is None:
                self.metric_bucket = self._new_metric_bucket(minute)
                self.traffic_bucket = {"timestamp": minute, "ports": {}}
            for key, value in self._metric_values(payload).items():
                if value is not None:
                    self.metric_bucket["sums"][key] = self.metric_bucket["sums"].get(key, 0) + value
                    self.metric_bucket["counts"][key] = self.metric_bucket["counts"].get(key, 0) + 1
            current_ports = self._port_samples(payload)
            for key, current in current_ports.items():
                previous = self.previous_ports.get(key)
                if (previous and current["mode"] == previous["mode"] and
                        current["rx"] >= previous["rx"] and current["tx"] >= previous["tx"]):
                    value = self.traffic_bucket["ports"].setdefault(key, {
                        "logical": current["logical"], "epl": current["epl"], "lane": current["lane"],
                        "rx_bytes": 0, "tx_bytes": 0,
                    })
                    value["logical"] = current["logical"]
                    rx_delta = current["rx"] - previous["rx"]
                    tx_delta = current["tx"] - previous["tx"]
                    if rx_delta <= self.MAX_PORT_BYTES_PER_MINUTE:
                        value["rx_bytes"] += rx_delta
                    if tx_delta <= self.MAX_PORT_BYTES_PER_MINUTE:
                        value["tx_bytes"] += tx_delta
            self.previous_ports = current_ports
            try:
                self._write_buckets(metric_to_write, traffic_to_write)
                self._prune()
            except (OSError, sqlite3.Error) as exc:
                print(f"Telemetry persistence skipped: {exc}", flush=True)

    def history(self, seconds):
        with self.lock:
            if not self.enabled:
                return None
            cutoff = int(time.time()) - max(60, min(int(seconds), self.retention_days * 86400))
            try:
                with self._database() as connection:
                    rows = connection.execute(
                        "SELECT timestamp, cpu, memory, memory_used, rx, tx "
                        "FROM metric_minutes WHERE timestamp >= ? ORDER BY timestamp", (cutoff,)
                    ).fetchall()
            except (OSError, sqlite3.Error) as exc:
                print(f"Telemetry history read skipped: {exc}", flush=True)
                return None
            return [{"timestamp": row[0], "cpu": row[1], "memory": row[2],
                     "memoryUsed": row[3], "rx": row[4], "tx": row[5]} for row in rows]

    def traffic_statistics(self, port_key=None):
        with self.lock:
            settings = {"enabled": self.enabled, "retention_days": self.retention_days}
            if not self.enabled:
                return {**settings, "summary": {}, "series": {}, "top_days": [], "ports": []}
            now = int(time.time())
            today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            month = today[:7]
            yesterday = (datetime.fromtimestamp(now).date() - timedelta(days=1)).isoformat()
            valid_clause = (
                f" AND timestamp <= {now}"
                f" AND rx_bytes BETWEEN 0 AND {self.MAX_PORT_BYTES_PER_MINUTE}"
                f" AND tx_bytes BETWEEN 0 AND {self.MAX_PORT_BYTES_PER_MINUTE} "
            )
            try:
                with self._database() as connection:
                    summary = {}
                    port_clause = " AND port_key=?" if port_key else ""
                    for label, pattern in (("today", today), ("yesterday", yesterday),
                                           ("month", month + "%")):
                        row = connection.execute(
                            "SELECT COALESCE(SUM(rx_bytes),0), COALESCE(SUM(tx_bytes),0) "
                            "FROM traffic_minutes WHERE strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') LIKE ?" +
                            valid_clause + port_clause,
                            (pattern, port_key) if port_key else (pattern,),
                        ).fetchone()
                        summary[label] = {"rx_bytes": row[0], "tx_bytes": row[1]}
                    row = connection.execute(
                        "SELECT COALESCE(SUM(rx_bytes),0), COALESCE(SUM(tx_bytes),0) "
                        "FROM traffic_minutes WHERE 1=1" + valid_clause +
                        (" AND port_key=?" if port_key else ""),
                        (port_key,) if port_key else (),
                    ).fetchone()
                    summary["total"] = {"rx_bytes": row[0], "tx_bytes": row[1]}

                    def samples(rows, label):
                        return [
                            {
                                "label": label(row[0]),
                                "timestamp": row[0] if isinstance(row[0], int) else None,
                                "rx_bytes": row[1],
                                "tx_bytes": row[2],
                                "average_bps": round((row[1] + row[2]) * 8 / max(row[3], 60)),
                            }
                            for row in rows
                        ]

                    def fixed_series(seconds, cutoff):
                        rows = connection.execute(
                            "SELECT timestamp-(timestamp % ?), SUM(rx_bytes), SUM(tx_bytes), "
                            "COUNT(DISTINCT timestamp)*60 FROM traffic_minutes WHERE timestamp>=?" +
                            valid_clause + port_clause + " GROUP BY 1 ORDER BY 1",
                            (seconds, cutoff, port_key) if port_key else (seconds, cutoff),
                        ).fetchall()
                        pattern = "%m-%d %H:%M" if seconds == 300 else "%m-%d %H:00"
                        return samples(rows, lambda value: datetime.fromtimestamp(value).strftime(pattern))

                    daily_rows = connection.execute(
                        "SELECT strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime'), "
                        "SUM(rx_bytes), SUM(tx_bytes), COUNT(DISTINCT timestamp)*60 FROM traffic_minutes" +
                        " WHERE 1=1" + valid_clause + (" AND port_key=?" if port_key else "") +
                        " GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
                        (port_key, self.retention_days) if port_key else (self.retention_days,),
                    ).fetchall()[::-1]
                    daily = samples(daily_rows, str)
                    monthly_rows = connection.execute(
                        "SELECT strftime('%Y-%m', timestamp, 'unixepoch', 'localtime'), "
                        "SUM(rx_bytes), SUM(tx_bytes), COUNT(DISTINCT timestamp)*60 FROM traffic_minutes" +
                        " WHERE 1=1" + valid_clause + (" AND port_key=?" if port_key else "") +
                        " GROUP BY 1 ORDER BY 1 DESC LIMIT 24",
                        (port_key,) if port_key else (),
                    ).fetchall()[::-1]
                    monthly = samples(monthly_rows, str)
                    five_minute = fixed_series(300, now - 24 * 60 * 60)
                    hourly = fixed_series(3600, now - 48 * 60 * 60)
                    ports = [{"key": row[0], "logical": row[1], "epl": row[2], "lane": row[3],
                              "rx_bytes": row[4], "tx_bytes": row[5]} for row in connection.execute(
                        "SELECT port_key, MAX(logical), MAX(epl), MAX(lane), SUM(rx_bytes), SUM(tx_bytes) "
                        "FROM traffic_minutes WHERE strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime')=? " +
                        valid_clause +
                        "GROUP BY port_key HAVING SUM(rx_bytes)+SUM(tx_bytes)>0 "
                        "ORDER BY SUM(rx_bytes)+SUM(tx_bytes) DESC", (today,)
                    ).fetchall()]
            except (OSError, sqlite3.Error) as exc:
                return {**settings, "error": str(exc), "summary": {}, "series": {}, "top_days": [], "ports": []}
            top_days = sorted(
                daily, key=lambda item: item["rx_bytes"] + item["tx_bytes"], reverse=True
            )[:10]
            return {
                **settings,
                "selected_port": port_key,
                "summary": summary,
                "series": {
                    "five_minute": five_minute,
                    "hourly": hourly,
                    "daily": daily,
                    "monthly": monthly,
                },
                "top_days": top_days,
                "ports": ports,
            }

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
        self.telemetry_latest = None
        self.telemetry_history = TelemetryHistory()
        self.telemetry_persistence = TelemetryPersistence(
            config.get("telemetry_database", DEFAULT_TELEMETRY_DATABASE),
            config.get("telemetry_persistence", False),
            config.get("telemetry_retention_days", 30),
        )
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

    def record_telemetry(self, payload):
        self.telemetry_history.record(payload)
        self.telemetry_persistence.record(payload)
        with self.telemetry_lock:
            self.telemetry_latest = payload

    def latest_telemetry(self):
        with self.telemetry_lock:
            return self.telemetry_latest

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
