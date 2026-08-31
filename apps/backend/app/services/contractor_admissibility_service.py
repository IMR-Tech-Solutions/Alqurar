"""Contractor-admissibility store + background generation.

Takes the project's ADMISSIBILITY MATRIX (the weighted criteria) and scores it
against EVERY delay event: for each criterion, is the clause applicable to that
event, did the Contractor comply, and which document evidences it. Scoring every
event against the data room takes far too long for an HTTP request, so generation
runs in the background and the row's `status` is polled by the UI.

The criteria are SNAPSHOT into `content` at generation time (with each one's
effective weightage) so the scoring keeps rendering after the matrix is
regenerated or re-weighted; `matrixUpdatedAt` records which matrix version it was
scored against, so the UI can flag stale scoring. The score itself is derived
(weightage when applicable AND complied, else 0) and is never stored.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List

import anthropic

from app.db import SessionLocal
from app.models import ContractorAdmissibility
from app.services import (
    admissibility_service,
    delay_event_service,
    document_service,
    project_service,
)
from app.services.ai_service import (
    EXTRACTION_MODEL,
    generate_contractor_admissibility,
    provider_error_message,
)

_sem = asyncio.Semaphore(int(os.getenv("EXTRACTION_CONCURRENCY", "2")))

# projectId -> {"done": n, "total": n} — how many events have been scored so far.
# Kept in memory (like the other long jobs) purely to drive the tab's progress
# readout; the durable state is the row's `status`.
_progress: Dict[str, dict] = {}

_NOT_CONFIGURED = (
    "AI analysis is not configured — set ANTHROPIC_API_KEY in apps/backend/.env "
    "and restart the backend."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get(project_id: str) -> Dict:
    """Return the stored scoring + status (idle shape if none yet), with progress."""
    with SessionLocal() as db:
        row = db.get(ContractorAdmissibility, project_id)
        data = row.to_dict() if row else {
            "projectId": project_id, "content": None, "model": None,
            "status": "", "error": None, "updatedAt": None,
        }
    if data.get("status") == "running":
        data["progress"] = _progress.get(project_id)
    return data


def _set(project_id: str, **fields) -> None:
    with SessionLocal() as db:
        row = db.get(ContractorAdmissibility, project_id)
        if not row:
            row = ContractorAdmissibility(projectId=project_id, createdAt=_now())
            db.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        row.updatedAt = _now()
        db.commit()


def mark_running(project_id: str) -> None:
    _progress.pop(project_id, None)
    _set(project_id, status="running", error=None)


def fail_interrupted() -> int:
    """Mark scorings left "running" by a previous process as failed. Returns how many.

    A run holds its progress in memory, so a restart part-way through leaves the
    row claiming to be running with nothing driving it: the tab then polls a job
    that no longer exists, forever, and offers no way to start another. Resuming
    isn't possible — nothing partial is persisted — and re-running automatically
    would spend real money on every boot, so the row is failed with a message that
    invites a retry.
    """
    with SessionLocal() as db:
        rows = (
            db.query(ContractorAdmissibility)
            .filter(ContractorAdmissibility.status == "running")
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.error = "Scoring was interrupted when the server restarted. Run it again."
            row.updatedAt = _now()
        count = len(rows)
        if count:
            db.commit()
    _progress.clear()
    return count


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def flatten_criteria(matrix_content: Dict) -> List[Dict]:
    """Flatten the matrix's clause groups into the scored criteria list.

    Each criterion's `weightage` is its EFFECTIVE weight — its `overallWtg`
    percentage of its clause group's marks — so the weightages across all
    criteria sum to the matrix's 100 marks, exactly like the sheet.
    """
    criteria: List[Dict] = []
    for clause in (matrix_content or {}).get("clauses", []) or []:
        marks = _num(clause.get("marks"))
        for cr in clause.get("criteria", []) or []:
            criteria.append({
                "id": cr.get("id") or f"cac-{uuid.uuid4().hex[:8]}",
                "clauseRef": clause.get("clauseRef", ""),
                "clauseLabel": clause.get("label", ""),
                "category": cr.get("category", ""),
                "subClause": cr.get("subClause", ""),
                "description": cr.get("description", ""),
                "weightage": round(_num(cr.get("overallWtg")) / 100 * marks, 2),
            })
    return criteria


def _assign_ids(content: Dict) -> Dict:
    """Give every criterion a stable id and keep each event's rows pointing at it."""
    content = dict(content or {})
    criteria = []
    for c in content.get("criteria", []) or []:
        c = dict(c)
        c["id"] = c.get("id") or f"cac-{uuid.uuid4().hex[:8]}"
        criteria.append(c)
    content["criteria"] = criteria
    content["events"] = [dict(e) for e in content.get("events", []) or []]
    content["summary"] = content.get("summary", "")
    return content


def save_content(project_id: str, content: Dict) -> Dict:
    """Persist an analyst-edited scoring."""
    _set(project_id, content=_assign_ids(content), status="done", error=None)
    return get(project_id)


def _blank_rows(criteria: List[Dict]) -> List[Dict]:
    """An unscored column — used for any event a batch failed to return."""
    return [
        {"criterionId": c["id"], "applicable": "N", "complied": "N", "evidence": ""}
        for c in criteria
    ]


def _map_result(result: Dict, criteria: List[Dict]) -> List[Dict]:
    """Map one event's AI verdicts (keyed by 1-based slNo) onto criterion ids.

    Criteria the model skipped come back unscored rather than missing, so every
    row in the sheet is always present.
    """
    by_sl = {}
    for r in result.get("rows", []) or []:
        try:
            by_sl[int(r.get("slNo"))] = r
        except (TypeError, ValueError):
            continue
    rows = []
    for i, c in enumerate(criteria, start=1):
        r = by_sl.get(i) or {}
        applicable = "Y" if str(r.get("applicable", "")).upper().startswith("Y") else "N"
        complied = "Y" if str(r.get("complied", "")).upper().startswith("Y") else "N"
        rows.append({
            "criterionId": c["id"],
            "applicable": applicable,
            # A requirement that doesn't apply can't be complied with.
            "complied": complied if applicable == "Y" else "N",
            "evidence": (r.get("evidence") or "").strip(),
        })
    return rows


def _load(project_id: str):
    """Blocking: gather the project, its data-room register, delay events and matrix.

    The register is each document's stored analysis, not its extracted text — see
    `ai_service._contractor_digest` for why. It also means this loader touches no
    file bytes and runs no OCR, so a run starts in milliseconds.
    """
    project = project_service.get_project(project_id)
    documents = document_service.list_analyses(project_id)
    events = delay_event_service.list_by_project(project_id)
    matrix = admissibility_service.get(project_id)
    return project, documents, events, matrix


async def run_generation(project_id: str) -> None:
    """Score every delay event against the matrix in the background and store it."""
    async with _sem:
        _set(project_id, status="running", error=None)
        try:
            if not os.getenv("ANTHROPIC_API_KEY"):
                _set(project_id, status="failed", error=_NOT_CONFIGURED)
                return

            project, docs_for_ai, events, matrix = await asyncio.to_thread(_load, project_id)
            if not project:
                _set(project_id, status="failed", error="Project not found.")
                return

            criteria = flatten_criteria(matrix.get("content") or {})
            if not criteria:
                _set(
                    project_id,
                    status="failed",
                    error="Generate the admissibility matrix first — the contractor scoring is built from its criteria.",
                )
                return
            if not events:
                _set(
                    project_id,
                    status="failed",
                    error="No delay events yet — extract or add delay events first.",
                )
                return
            if not docs_for_ai:
                _set(
                    project_id,
                    status="failed",
                    error="No documents in the data room yet — upload documents first.",
                )
                return

            _progress[project_id] = {"done": 0, "total": len(events)}

            def _on_progress(done: int, total: int) -> None:
                _progress[project_id] = {"done": done, "total": total}

            # The model scores by 1-based row number, exactly as the sheet reads.
            numbered = [{**c, "slNo": i} for i, c in enumerate(criteria, start=1)]
            results = await generate_contractor_admissibility(
                events=events,
                criteria=numbered,
                documents=docs_for_ai,
                project_name=project.get("name"),
                standard=project.get("standard"),
                on_progress=_on_progress,
            )
            by_ref = {r.get("eventRef"): r for r in results}

            scored_events = []
            for ev in events:
                res = by_ref.get(ev.get("ref"))
                scored_events.append({
                    "eventId": ev.get("id", ""),
                    "eventRef": ev.get("ref", ""),
                    "title": ev.get("title", ""),
                    "remarks": (res or {}).get("remark", ""),
                    "rows": _map_result(res, criteria) if res else _blank_rows(criteria),
                })

            _set(
                project_id,
                content={
                    "criteria": criteria,
                    "events": scored_events,
                    "summary": "",
                    "matrixUpdatedAt": matrix.get("updatedAt"),
                },
                model=EXTRACTION_MODEL,
                status="done",
                error=None,
            )
        except anthropic.AuthenticationError:
            _set(project_id, status="failed", error=_NOT_CONFIGURED)
        except anthropic.APIStatusError as e:
            _set(project_id, status="failed", error=provider_error_message(e)[:500])
        except Exception as e:  # noqa: BLE001
            _set(project_id, status="failed", error=str(e)[:500])
        finally:
            _progress.pop(project_id, None)
