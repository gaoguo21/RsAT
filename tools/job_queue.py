import importlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    import redis
    from rq import Queue
except Exception:  # pragma: no cover - optional dependency
    redis = None
    Queue = None


def _redis_job_key(job_id):
    return f"rna:job:{job_id}"


def _redis_jobs_set():
    return "rna:jobs"


def _serialize_result(result):
    try:
        return json.dumps(result)
    except TypeError:
        return json.dumps({"error": "Result not serializable."})


def _deserialize_result(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _import_callable(path):
    if callable(path):
        return path
    module_name, func_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def run_job_wrapper(job_id, func_path, args, kwargs, redis_url):
    if redis is None:
        raise RuntimeError("redis module is required to run background jobs.")
    conn = redis.Redis.from_url(redis_url)
    _redis_set_status(conn, job_id, "running")
    try:
        func = _import_callable(func_path)
        result = func(*args, **kwargs)
        if result is not None:
            _redis_set_result(conn, job_id, result)
        _redis_set_status(conn, job_id, "finished")
        return True
    except Exception as exc:
        _redis_set_error(conn, job_id, str(exc))
        _redis_set_status(conn, job_id, "failed")
        raise


def _redis_set_status(conn, job_id, status):
    key = _redis_job_key(job_id)
    conn.hset(key, mapping={"status": status, "updated_ts": time.time()})


def _redis_set_result(conn, job_id, result):
    key = _redis_job_key(job_id)
    conn.hset(key, mapping={"result": _serialize_result(result), "updated_ts": time.time()})


def _redis_set_error(conn, job_id, error):
    key = _redis_job_key(job_id)
    conn.hset(key, mapping={"error": error, "updated_ts": time.time()})


class JobQueue:
    def __init__(self, app, max_concurrent=2, job_ttl_hours=24, base_dir=None, redis_url=None):
        self._app = app
        self._logger = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._jobs = {}
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._ttl_seconds = max(1, int(job_ttl_hours * 3600))
        self._base_dir = base_dir or tempfile.gettempdir()
        os.makedirs(self._base_dir, exist_ok=True)
        self._redis_url = redis_url
        self._redis = None
        self._queue = None
        if redis_url and redis and Queue:
            try:
                self._redis = redis.Redis.from_url(redis_url)
                self._queue = Queue(connection=self._redis)
            except Exception as exc:
                self._logger.exception("Failed to connect to Redis: %s", exc)
        elif redis_url:
            self._logger.warning("Redis URL set but redis/rq not installed; falling back to thread pool.")
        self._start_cleanup_thread()

    def create_job(self, kind):
        job_id = uuid.uuid4().hex
        job_dir = tempfile.mkdtemp(prefix="rna_job_", dir=self._base_dir)
        now = time.time()
        record = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "created_ts": now,
            "updated_ts": now,
            "job_dir": job_dir,
            "error": None,
            "result": None,
        }
        if self._queue:
            key = _redis_job_key(job_id)
            try:
                self._redis.hset(
                    key,
                    mapping={
                        "id": job_id,
                        "kind": kind,
                        "status": "queued",
                        "created_ts": now,
                        "updated_ts": now,
                        "job_dir": job_dir,
                        "error": "",
                        "result": "",
                    },
                )
                self._redis.sadd(_redis_jobs_set(), job_id)
            except Exception as exc:
                self._logger.exception("Failed to register job in Redis: %s", exc)
        else:
            with self._lock:
                self._jobs[job_id] = record
        return job_id, job_dir

    def submit(self, job_id, func_path, *args, **kwargs):
        if self._queue:
            self._queue.enqueue(
                run_job_wrapper,
                job_id,
                func_path,
                args,
                kwargs,
                self._redis_url,
                job_id=job_id,
            )
            return
        self._executor.submit(self._run_job, job_id, func_path, *args, **kwargs)

    def _run_job(self, job_id, func_path, *args, **kwargs):
        self._set_status(job_id, "running")
        try:
            func = _import_callable(func_path)
            result = func(*args, **kwargs)
            if result is not None:
                self._set_result(job_id, result)
            self._set_status(job_id, "finished")
        except Exception as exc:
            self._logger.exception("Job %s failed: %s", job_id, exc)
            self._set_error(job_id, str(exc))
            self._set_status(job_id, "failed")

    def get_job(self, job_id):
        if self._queue:
            try:
                data = self._redis.hgetall(_redis_job_key(job_id))
            except Exception:
                data = {}
            if not data:
                return None
            decoded = {k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()}
            return {
                "id": decoded.get("id"),
                "kind": decoded.get("kind"),
                "status": decoded.get("status"),
                "created_ts": float(decoded.get("created_ts") or 0),
                "updated_ts": float(decoded.get("updated_ts") or 0),
                "job_dir": decoded.get("job_dir"),
                "error": decoded.get("error") or None,
                "result": _deserialize_result(decoded.get("result")) if decoded.get("result") else None,
            }
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def get_public_status(self, job_id):
        job = self.get_job(job_id)
        if not job:
            return None
        return {
            "job_id": job["id"],
            "status": job["status"],
            "error": job.get("error"),
            "result": job.get("result"),
        }

    def finalize_job(self, job_id):
        job = self.get_job(job_id)
        if not job:
            return False
        self._safe_rmtree(job.get("job_dir"))
        if self._queue:
            try:
                self._redis.delete(_redis_job_key(job_id))
                self._redis.srem(_redis_jobs_set(), job_id)
            except Exception:
                pass
        else:
            with self._lock:
                self._jobs.pop(job_id, None)
        return True

    def cleanup_expired(self):
        now = time.time()
        if self._queue:
            try:
                job_ids = self._redis.smembers(_redis_jobs_set())
            except Exception:
                job_ids = []
            for raw_id in job_ids:
                job_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
                job = self.get_job(job_id)
                if not job:
                    continue
                if now - job.get("created_ts", 0) > self._ttl_seconds:
                    self.finalize_job(job_id)
            return
        expired = []
        with self._lock:
            for job_id, job in self._jobs.items():
                if now - job["created_ts"] > self._ttl_seconds:
                    expired.append(job_id)
        for job_id in expired:
            self.finalize_job(job_id)

    def _set_status(self, job_id, status):
        if self._queue:
            try:
                _redis_set_status(self._redis, job_id, status)
            except Exception:
                pass
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = status
                job["updated_ts"] = time.time()

    def _set_result(self, job_id, result):
        if self._queue:
            try:
                _redis_set_result(self._redis, job_id, result)
            except Exception:
                pass
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["result"] = result
                job["updated_ts"] = time.time()

    def _set_error(self, job_id, error):
        if self._queue:
            try:
                _redis_set_error(self._redis, job_id, error)
            except Exception:
                pass
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["error"] = error
                job["updated_ts"] = time.time()

    def _safe_rmtree(self, path):
        if not path:
            return
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    def _start_cleanup_thread(self):
        thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        thread.start()

    def _cleanup_loop(self):
        while True:
            time.sleep(30 * 60)
            try:
                self.cleanup_expired()
            except Exception as exc:
                self._logger.exception("Job cleanup failed: %s", exc)
