"""Task IDs, registry, and lifecycle for Agent Bridge Master."""
from __future__ import annotations

import threading
import os
import platform
import uuid
from datetime import datetime
from typing import Any, Callable

from nexus.bridge.errors import (
    BridgeError,
    PERMISSION_DENIED,
    TASK_NOT_FOUND,
    UNKNOWN_TASK,
)
from nexus.bridge.models import TaskCreateRequest, TaskRecord
from nexus.bridge.outcomes import evaluate
from nexus.bridge.permissions import (
    TASK_PERMISSIONS,
    PermissionLevel,
    allows,
    process_level,
)
from nexus.bridge.storage import TaskStorage

_INSTANCE_ID = str(uuid.uuid4())

REGISTERED_TASKS = tuple(TASK_PERMISSIONS.keys())

ExecutorFn = Callable[[dict[str, Any], TaskRecord], dict[str, Any]]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_task_id(counter: int) -> str:
    day = datetime.now().strftime("%Y%m%d")
    return f"NX-{day}-{counter:04d}"


class TaskManager:
    def __init__(self, storage: TaskStorage | None = None) -> None:
        self.storage = storage or TaskStorage()
        self._lock = threading.RLock()
        self._seq = 0
        self._day = datetime.now().strftime("%Y%m%d")
        self._executors: dict[str, ExecutorFn] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._archive_old_terminal_tasks()
        self._recover_orphaned_running_tasks()

    def _archive_old_terminal_tasks(self, max_age_days: int = 30) -> None:
        """Move old, terminal task records out of the live scan directory.

        The persist dir accumulates one JSON file per task forever, and
        __init__ reads every file on every boot to recover orphaned
        'running' tasks. With thousands of files this makes every restart
        slower. Terminal (non-running) records older than max_age_days are
        moved verbatim (never deleted) into an archive subfolder so future
        boots stay fast; nothing already running or recent is touched.
        """
        import time
        try:
            paths = list(self.storage.persist_dir.glob('NX-*.json'))
        except Exception:
            return
        if len(paths) < 500:
            return  # not worth the extra I/O pass yet
        cutoff = time.time() - max_age_days * 86400
        archive_dir = self.storage.persist_dir.parent / "tasks_archive"
        for fp in paths:
            try:
                if fp.stat().st_mtime >= cutoff:
                    continue
                rec = self.storage.get(fp.stem)
                if not rec or rec.status == 'running':
                    continue
                archive_dir.mkdir(parents=True, exist_ok=True)
                target = archive_dir / fp.name
                if target.exists():
                    continue
                os.replace(fp, target)
            except Exception:
                continue  # never let archival block boot

    def _recover_orphaned_running_tasks(self) -> None:
        """Terminalize persisted running tasks owned by a previous bridge instance.

        A bridge/process restart makes the old in-process synchronous executor
        impossible to resume. Only records carrying a different instance_id are
        touched; current-instance work is never inferred dead from age alone.
        """
        try:
            paths=list(self.storage.persist_dir.glob('NX-*.json'))
        except Exception:
            return
        for fp in paths:
            rec=self.storage.get(fp.stem)
            if not rec or rec.status != 'running':
                continue
            execution=rec.execution or {}
            prior_instance=execution.get('instance_id')
            if not prior_instance or prior_instance == _INSTANCE_ID:
                continue
            now=_now_iso()
            rec.status='failed'
            rec.error_code='TASK_ORPHANED_RESTART'
            rec.error='executor process ended with previous bridge instance; terminal result unavailable'
            rec.updated_at=now
            rec.log.append(f'{now} recovered orphaned running task previous_instance={prior_instance}')
            self.storage.put(rec)

    def register(self, name: str, fn: ExecutorFn) -> None:
        self._executors[name] = fn

    def _next_id(self) -> str:
        with self._lock:
            day = datetime.now().strftime("%Y%m%d")
            if day != self._day:
                self._day = day
                self._seq = 0
            # A restart must not overwrite today's persisted task evidence.
            while True:
                self._seq += 1
                candidate = new_task_id(self._seq)
                if self.storage.get(candidate) is None:
                    return candidate

    def create_and_run(self, req: TaskCreateRequest) -> TaskRecord:
        name = (req.task or "").strip()
        if not name or name not in TASK_PERMISSIONS:
            raise BridgeError(
                UNKNOWN_TASK,
                f"Unknown task '{name}'. Registered: {', '.join(REGISTERED_TASKS)}",
                http_status=400,
                details={"registered": list(REGISTERED_TASKS)},
            )
        required = TASK_PERMISSIONS[name]
        granted = process_level()

        # Maintenance drain: block new orders before any approval/side effect.
        from nexus.maintenance import MaintenanceActive, maintenance
        if maintenance().is_draining():
            raise BridgeError(
                "MAINTENANCE_ACTIVE",
                "maintenance drain active: new tasks are blocked until restart",
                http_status=503,
            )

        # PRIVILEGED: require consumed approval_id (local UI)
        approval_id = req.approval_id or (req.params or {}).get("approval_id")
        if required == PermissionLevel.PRIVILEGED:
            from nexus.bridge.approvals import approvals
            # Will raise APPROVAL_REQUIRED / DENIED / EXPIRED
            # Consume after we have task id — first validate pending/approved
            # Use consume at start only if already approved; else request flow via API
            appr = None
            if approval_id:
                # peek status
                appr = approvals().get(str(approval_id))
                if appr.status == "pending":
                    raise BridgeError(
                        "APPROVAL_REQUIRED",
                        "approval still pending — open local /approvals UI",
                        http_status=403,
                        details={"approval_id": approval_id, "ui": "/approvals"},
                    )
            else:
                # auto-create pending approval and return awaiting
                rec_pending_id = self._next_id()
                now = _now_iso()
                created = approvals().request(name, dict(req.params or {}), reason=f"PRIVILEGED {name}")
                rec = TaskRecord(
                    id=rec_pending_id,
                    task=name,
                    status="awaiting_approval",
                    permission=required.value,
                    created_at=now,
                    updated_at=now,
                    params=dict(req.params or {}),
                    source=req.source,
                    client=req.client,
                    session_id=req.session_id,
                    approval_id=created.approval_id,
                    log=[f"{now} awaiting_approval approval_id={created.approval_id}"],
                    result={
                        "approval_required": True,
                        "approval_id": created.approval_id,
                        "ui": "/approvals",
                        "message": "Approve locally then re-POST with approval_id",
                    },
                )
                self.storage.put(rec)
                return rec
            # approved path: consume one-shot
            approvals().consume(str(approval_id), name)

        # Approval elevates a single PRIVILEGED execution; otherwise enforce process_level.
        privileged_approved = (
            required == PermissionLevel.PRIVILEGED and bool(approval_id)
        )
        from nexus.safety import gate as _gate  # Phase 2 Schritt 4 (Schalter NEXUS_PHASE2_GATE)
        _ok = _gate.allows_level(required, granted) if _gate.enabled() else allows(required, granted)
        if not privileged_approved and not _ok:
            raise BridgeError(
                PERMISSION_DENIED,
                f"Task '{name}' requires {required.value}; process_level={granted.value}",
                http_status=403,
            )

        fn = self._executors.get(name)
        if fn is None:
            raise BridgeError(
                UNKNOWN_TASK,
                f"Task '{name}' registered but no executor bound",
                http_status=500,
            )

        now = _now_iso()
        rec = TaskRecord(
            id=self._next_id(),
            task=name,
            status="running",
            permission=required.value,
            created_at=now,
            updated_at=now,
            params=dict(req.params or {}),
            source=req.source,
            client=req.client,
            session_id=req.session_id,
            approval_id=str(approval_id) if approval_id else None,
            log=[f"{now} created task={name}"],
            execution={"host": platform.node(), "pid": os.getpid(),
                       "instance_id": _INSTANCE_ID,
                       "executor": f"{fn.__module__}.{fn.__qualname__}"},
        )
        self.storage.put(rec)
        self._cancel_flags[rec.id] = False

        # session touch
        if req.session_id:
            try:
                from nexus.bridge.sessions import sessions
                sessions().touch(req.session_id, task_id=rec.id)
            except Exception:
                pass

        try:
            if self._cancel_flags.get(rec.id):
                rec.status = "cancelled"
                rec.error_code = "TASK_CANCELLED"
                rec.error = "cancelled before run"
            else:
                blocked = False
                try:
                    with maintenance().work(f"task:{name}"):
                        result = fn(dict(req.params or {}), rec)
                except MaintenanceActive:
                    blocked = True
                    result = None
                    rec.result = {
                        "success": False,
                        "error": "MAINTENANCE_ACTIVE",
                        "message": "maintenance drain started before execution",
                    }
                if blocked:
                    rec.status = "blocked_maintenance"
                    rec.error_code = "MAINTENANCE_ACTIVE"
                    rec.error = "maintenance drain started before execution"
                elif self._cancel_flags.get(rec.id):
                    rec.status = "cancelled"
                    rec.error_code = "TASK_CANCELLED"
                    rec.error = "cancelled during run"
                    rec.result = result
                else:
                    outcome = evaluate(name, rec.params, result)
                    rec.status = outcome.status
                    rec.result = outcome.data
                    rec.error_code = outcome.error_code
                    rec.error = outcome.reason
        except BridgeError as e:
            rec.status = "failed"
            rec.error_code = e.code
            rec.error = e.message
            rec.result = e.to_dict()
        except Exception as e:
            rec.status = "failed"
            rec.error_code = "TASK_FAILED"
            rec.error = str(e)
        finally:
            rec.updated_at = _now_iso()
            rec.log.append(f"{rec.updated_at} status={rec.status}")
            self.storage.put(rec)

        return rec

    def get(self, task_id: str) -> TaskRecord:
        rec = self.storage.get(task_id)
        if not rec:
            raise BridgeError(TASK_NOT_FOUND, f"Task not found: {task_id}", http_status=404)
        return rec

    def cancel(self, task_id: str) -> TaskRecord:
        rec = self.get(task_id)
        self._cancel_flags[task_id] = True
        if rec.status in ("queued", "running", "awaiting_approval"):
            rec.status = "cancelled"
            rec.error_code = "TASK_CANCELLED"
            rec.error = "cancelled by client"
            rec.updated_at = _now_iso()
            rec.log.append(f"{rec.updated_at} cancelled")
            self.storage.put(rec)
        return rec

    def list(self, limit: int = 50) -> list[TaskRecord]:
        return self.storage.list(limit=limit)
