# """Approval workflow — pause a workflow after script/audio/blueprint, email the
# owners, and resume only when the FIRST owner acts.

# Core guarantees:
#   - First action wins. Whoever approves/declines first resolves the request.
#   - Idempotent. A second owner acting later does NOT re-trigger anything; they
#     get told it's already resolved (so no wasted tokens / no double generation).
#   - Durable. State is a JSON file so it survives a server restart.

# This module is deliberately storage-light (a JSON file) per the "simplest version"
# goal. The state shape is small and easy to migrate to Supabase later if wanted.
# """
# import json
# import logging
# import secrets
# import threading
# import time
# from datetime import datetime, timezone
# from pathlib import Path
# from typing import Callable

# logger = logging.getLogger(__name__)

# STORE_PATH = Path(__file__).resolve().parent.parent / "assets" / "approvals.json"
# STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

# _LOCK = threading.RLock()

# # In-process registry of resume callbacks, keyed by approval id. Callbacks are not
# # persisted (they're code); if the server restarts mid-wait, a pending approval can
# # be re-armed by the caller. The resolved STATE always persists.
# _RESUME_CALLBACKS: dict[str, Callable[[dict], None]] = {}


# # ── status values ─────────────────────────────────────────
# PENDING = "pending"
# APPROVED = "approved"
# DECLINED = "declined"


# def _now() -> str:
#     return datetime.now(timezone.utc).isoformat()


# def _read() -> dict:
#     if STORE_PATH.exists():
#         try:
#             return json.loads(STORE_PATH.read_text(encoding="utf-8"))
#         except Exception:  # noqa: BLE001
#             return {}
#     return {}


# def _write(data: dict):
#     tmp = STORE_PATH.with_suffix(".json.tmp")
#     tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
#     tmp.replace(STORE_PATH)


# # ── public API ────────────────────────────────────────────
# def create_request(*, workflow: str, render_id: str, owners: list[str],
#                    payload: dict | None = None) -> dict:
#     """Create a pending approval. Returns the record including a unique id and a
#     per-owner token (so each owner's link is distinct and auditable)."""
#     with _LOCK:
#         store = _read()
#         approval_id = secrets.token_urlsafe(9)
#         # one token per owner, plus a shared lookup
#         owner_tokens = {owner: secrets.token_urlsafe(12) for owner in owners}
#         record = {
#             "id": approval_id,
#             "workflow": workflow,
#             "render_id": render_id,
#             "owners": owners,
#             "owner_tokens": owner_tokens,
#             "status": PENDING,
#             "resolved_by": None,
#             "resolved_at": None,
#             "created_at": _now(),
#             "payload": payload or {},
#             "history": [],   # every action attempt, for audit
#         }
#         store[approval_id] = record
#         _write(store)
#         logger.info("Approval %s created for workflow=%s render=%s owners=%s",
#                     approval_id, workflow, render_id, owners)
#         return record


# def get(approval_id: str) -> dict | None:
#     with _LOCK:
#         return _read().get(approval_id)


# def resolve_by_token(token: str, action: str) -> dict:
#     """Resolve an approval via an owner's token. action: 'approve' | 'decline'.

#     Returns a result dict:
#       {"outcome": "resolved"|"already"|"invalid", "status": ..., "record": ...,
#        "message": ...}
#     'resolved' means THIS action resolved it (fire the next phase).
#     'already'  means it was already resolved earlier (do NOT re-trigger).
#     """
#     action = action.lower().strip()
#     if action not in ("approve", "decline"):
#         return {"outcome": "invalid", "message": "Unknown action"}

#     with _LOCK:
#         store = _read()
#         # find the record holding this token
#         record = None
#         owner = None
#         for rec in store.values():
#             for o, t in rec.get("owner_tokens", {}).items():
#                 if secrets.compare_digest(t, token):
#                     record, owner = rec, o
#                     break
#             if record:
#                 break
#         if not record:
#             return {"outcome": "invalid", "message": "Invalid or expired link"}

#         # Record the attempt for audit regardless of outcome
#         record["history"].append({"owner": owner, "action": action, "at": _now()})

#         if record["status"] != PENDING:
#             # Already resolved — idempotent no-op. This is the "second owner acts
#             # later" case: tell them, do NOT re-run anything.
#             _write(store)
#             who = record["resolved_by"]
#             return {
#                 "outcome": "already",
#                 "status": record["status"],
#                 "record": record,
#                 "message": (f"This {record['workflow']} request was already "
#                             f"{record['status']} by {who}. No further action taken."),
#             }

#         # First valid action — resolve it.
#         new_status = APPROVED if action == "approve" else DECLINED
#         record["status"] = new_status
#         record["resolved_by"] = owner
#         record["resolved_at"] = _now()
#         _write(store)
#         logger.info("Approval %s %s by %s", record["id"], new_status, owner)
#         return {
#             "outcome": "resolved",
#             "status": new_status,
#             "record": record,
#             "message": f"{record['workflow']} {new_status} by {owner}.",
#         }


# def register_resume(approval_id: str, callback: Callable[[dict], None]):
#     """Register the function to run when an approval is APPROVED. Called at most
#     once (first approval). Stored in-process only."""
#     with _LOCK:
#         _RESUME_CALLBACKS[approval_id] = callback


# def fire_resume_if_approved(result: dict) -> bool:
#     """If a resolve() result is a fresh approval, run the registered resume
#     callback exactly once and clear it. Returns True if it fired."""
#     if result.get("outcome") != "resolved" or result.get("status") != APPROVED:
#         return False
#     rec = result["record"]
#     cb = _RESUME_CALLBACKS.pop(rec["id"], None)
#     if cb is None:
#         logger.warning("Approval %s approved but no resume callback registered "
#                        "(server may have restarted). Caller must resume manually.", rec["id"])
#         return False
#     try:
#         cb(rec)
#         return True
#     except Exception as e:  # noqa: BLE001
#         logger.exception("Resume callback for %s failed: %s", rec["id"], e)
#         return False


# def list_pending() -> list[dict]:
#     with _LOCK:
#         return [r for r in _read().values() if r["status"] == PENDING]

"""Approval workflow — pause a workflow after script/audio/blueprint, email the
owners, and resume only when the FIRST owner acts.

Core guarantees:
  - First action wins. Whoever approves/declines first resolves the request.
  - Idempotent. A second owner acting later does NOT re-trigger anything; they
    get told it's already resolved (so no wasted tokens / no double generation).
  - Durable. State is a JSON file so it survives a server restart.

This module is deliberately storage-light (a JSON file) per the "simplest version"
goal. The state shape is small and easy to migrate to Supabase later if wanted.
"""
import json
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / "assets" / "approvals.json"
STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

_LOCK = threading.RLock()

# In-process registry of resume callbacks, keyed by approval id. Callbacks are not
# persisted (they're code); if the server restarts mid-wait, a pending approval can
# be re-armed by the caller. The resolved STATE always persists.
_RESUME_CALLBACKS: dict[str, Callable[[dict], None]] = {}


# ── status values ─────────────────────────────────────────
PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _write(data: dict):
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


# ── public API ────────────────────────────────────────────
def create_request(*, workflow: str, render_id: str, owners: list[str],
                   payload: dict | None = None) -> dict:
    """Create a pending approval. Returns the record including a unique id and a
    per-owner token (so each owner's link is distinct and auditable)."""
    with _LOCK:
        store = _read()
        approval_id = secrets.token_urlsafe(9)
        # one token per owner, plus a shared lookup
        owner_tokens = {owner: secrets.token_urlsafe(12) for owner in owners}
        record = {
            "id": approval_id,
            "workflow": workflow,
            "render_id": render_id,
            "owners": owners,
            "owner_tokens": owner_tokens,
            "status": PENDING,
            "resolved_by": None,
            "resolved_at": None,
            "created_at": _now(),
            "payload": payload or {},
            "history": [],   # every action attempt, for audit
        }
        store[approval_id] = record
        _write(store)
        logger.info("Approval %s created for workflow=%s render=%s owners=%s",
                    approval_id, workflow, render_id, owners)
        return record


def get(approval_id: str) -> dict | None:
    with _LOCK:
        return _read().get(approval_id)


def resolve_by_token(token: str, action: str) -> dict:
    """Resolve an approval via an owner's token. action: 'approve' | 'decline'.

    Returns a result dict:
      {"outcome": "resolved"|"already"|"invalid", "status": ..., "record": ...,
       "message": ...}
    'resolved' means THIS action resolved it (fire the next phase).
    'already'  means it was already resolved earlier (do NOT re-trigger).
    """
    action = action.lower().strip()
    if action not in ("approve", "decline"):
        return {"outcome": "invalid", "message": "Unknown action"}

    with _LOCK:
        store = _read()
        # find the record holding this token
        record = None
        owner = None
        for rec in store.values():
            for o, t in rec.get("owner_tokens", {}).items():
                if secrets.compare_digest(t, token):
                    record, owner = rec, o
                    break
            if record:
                break
        if not record:
            return {"outcome": "invalid", "message": "Invalid or expired link"}

        # Record the attempt for audit regardless of outcome
        record["history"].append({"owner": owner, "action": action, "at": _now()})

        if record["status"] != PENDING:
            # Already resolved — idempotent no-op. This is the "second owner acts
            # later" case: tell them, do NOT re-run anything.
            _write(store)
            who = record["resolved_by"]
            return {
                "outcome": "already",
                "status": record["status"],
                "record": record,
                "message": (f"This {record['workflow']} request was already "
                            f"{record['status']} by {who}. No further action taken."),
            }

        # First valid action — resolve it.
        new_status = APPROVED if action == "approve" else DECLINED
        record["status"] = new_status
        record["resolved_by"] = owner
        record["resolved_at"] = _now()
        _write(store)
        logger.info("Approval %s %s by %s", record["id"], new_status, owner)
        return {
            "outcome": "resolved",
            "status": new_status,
            "record": record,
            "message": f"{record['workflow']} {new_status} by {owner}.",
        }


def register_resume(approval_id: str, callback: Callable[[dict], None]):
    """Register the function to run when an approval is APPROVED. Called at most
    once (first approval). Stored in-process only."""
    with _LOCK:
        _RESUME_CALLBACKS[approval_id] = callback


def fire_resume_if_approved(result: dict) -> bool:
    """If a resolve() result is a fresh approval, run the registered resume
    callback exactly once and clear it. Returns True if it fired."""
    if result.get("outcome") != "resolved" or result.get("status") != APPROVED:
        return False
    rec = result["record"]
    cb = _RESUME_CALLBACKS.pop(rec["id"], None)
    if cb is None:
        logger.warning("Approval %s approved but no resume callback registered "
                       "(server may have restarted). Caller must resume manually.", rec["id"])
        return False
    try:
        cb(rec)
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("Resume callback for %s failed: %s", rec["id"], e)
        return False


def list_pending() -> list[dict]:
    with _LOCK:
        return [r for r in _read().values() if r["status"] == PENDING]