"""Project document store — backed by the database. A document uploaded by a
client is visible to the admin in the project workspace, across browsers/devices.
The actual file bytes are stored in the `data` column so uploads persist and can
be downloaded again."""
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Document, DocumentComment

# Serialises "read the highest id, then insert" so two overlapping uploads can
# never be handed the same doc-N. See create_document_with_next_id.
_id_lock = threading.Lock()

# How many times to re-allocate an id that lost a race to another process before
# giving up (a single retry is normally enough; several means real contention).
_ID_RETRIES = 10

# Callers that store the file bytes on the row itself use the document's own id
# as the "a downloadable file is stored" marker — but that id isn't known until
# it has been allocated. Pass this sentinel as driveFileId and
# create_document_with_next_id swaps it for the real id at insert time.
SELF_ID = "__self__"


def list_by_project(project_id: str) -> List[Dict]:
    with SessionLocal() as db:
        return [d.to_dict() for d in db.query(Document).filter(Document.projectId == project_id).all()]


def get_document(document_id: str) -> Optional[Dict]:
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        return d.to_dict() if d else None


def get_documents_meta(document_ids: List[str]) -> Dict[str, Dict]:
    """Lightweight metadata (no bytes, no analysis) for a set of document ids,
    keyed by id. Ids that no longer exist are simply absent from the result, so
    callers holding references to deleted documents drop them automatically.
    """
    ids = [d for d in dict.fromkeys(document_ids) if d]
    if not ids:
        return {}
    with SessionLocal() as db:
        rows = (
            db.query(
                Document.id, Document.name, Document.type,
                Document.uploadedAt, Document.uploadedBy, Document.sizeKB,
            )
            .filter(Document.id.in_(ids))
            .all()
        )
    return {
        r[0]: {
            "id": r[0],
            "name": r[1] or "file",
            "type": r[2] or "Other",
            "uploadedAt": r[3] or "",
            "uploadedBy": r[4] or "",
            "sizeKB": r[5] or 0,
        }
        for r in rows
    }


def _highest_doc_number(db) -> int:
    """The highest existing doc-N sequence number, using an open session."""
    highest = 0
    for (doc_id,) in db.query(Document.id).all():
        if doc_id and doc_id.startswith("doc-"):
            suffix = doc_id[4:]
            # Ignore legacy timestamp ids (13-digit ms) — only count real
            # sequence numbers so they don't poison the counter.
            if suffix.isdigit() and int(suffix) < 1_000_000:
                highest = max(highest, int(suffix))
    return highest


def create_document_with_next_id(data: Dict) -> Dict:
    """Insert a new document, allocating its sequential id atomically.

    Uploads used to read the highest id in one transaction and insert in another.
    Two uploads overlapping in that window both read the same highest id, and
    because create_document upserts by id, the second silently overwrote the
    first — selecting five files could leave three rows, with no error anywhere.
    Allocating and inserting under one lock closes that window; an id that still
    loses a race (a second worker process) retries with a fresh number rather
    than clobbering the row that got there first.
    """
    payload = dict(data)
    # Resolved once: a retry re-stamps both fields, so checking the sentinel
    # again after it has already been replaced would leave a stale id behind.
    mirror_drive_id = payload.get("driveFileId") == SELF_ID
    for _ in range(_ID_RETRIES):
        with _id_lock, SessionLocal() as db:
            doc_id = f"doc-{_highest_doc_number(db) + 1}"
            payload["id"] = doc_id
            if mirror_drive_id:
                payload["driveFileId"] = doc_id
            d = Document(**payload)
            db.add(d)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return d.to_dict()
    raise RuntimeError("Could not allocate a free document id — too many concurrent uploads.")


def list_with_data(project_id: str) -> List[Tuple[str, str, str, Optional[bytes]]]:
    """Return (id, name, type, bytes) for every document in a project.

    Used by AI extraction so it can read each file's text. Bytes may be None for
    metadata-only records that never had a file uploaded.
    """
    with SessionLocal() as db:
        rows = db.query(Document).filter(Document.projectId == project_id).all()
        return [
            (d.id, d.name or "file", d.type or "Other", bytes(d.data) if d.data is not None else None)
            for d in rows
        ]


def list_text_sources(project_id: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """Return (id, name, type, cachedText) for each document — WITHOUT loading the
    file bytes. Event extraction uses the cached text where present and only falls
    back to loading bytes for documents that haven't been analysed yet."""
    with SessionLocal() as db:
        rows = db.query(Document).filter(Document.projectId == project_id).all()
        return [(d.id, d.name or "file", d.type or "Other", d.extractedText) for d in rows]


def list_analyses(project_id: str) -> List[Dict]:
    """Return {name, type, analysis} for each document — the data-room register.

    Contractor-admissibility scoring reads this rather than `list_text_sources`:
    the stored analysis is an order of magnitude smaller than the extracted text,
    so the whole room fits in one cached request instead of being split into parts
    that each have to be re-read for every batch of events. `analysis` is {} for a
    document that hasn't been analysed yet.
    """
    with SessionLocal() as db:
        rows = (
            db.query(Document.name, Document.type, Document.analysis)
            .filter(Document.projectId == project_id)
            .order_by(Document.uploadedAt)
            .all()
        )
    return [
        {"name": name or "file", "type": doc_type or "Other", "analysis": analysis or {}}
        for name, doc_type, analysis in rows
    ]


def get_document_file(document_id: str) -> Optional[Tuple[bytes, str, str]]:
    """Return (bytes, filename, mime) for a stored document, or None."""
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if not d or d.data is None:
            return None
        return bytes(d.data), d.name or "file", d.mime or "application/octet-stream"


def create_document(data: Dict) -> Dict:
    """Create a document record (idempotent upsert by id)."""
    with SessionLocal() as db:
        d = db.get(Document, data["id"])
        if d:
            for key, value in data.items():
                setattr(d, key, value)
        else:
            d = Document(**data)
            db.add(d)
        db.commit()
        return d.to_dict()


def mark_unpacked(document_id: str, member_count: int) -> Optional[Dict]:
    """Flag an archive row whose contents were unpacked into documents of their own.

    The archive itself is kept — deleting an uploaded file is the user's call, not
    ours — but it is taken out of the analysis queue: an archive has no text to
    read, so any classification of it would be guesswork off the filename.
    """
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if not d:
            return None
        d.status = "Unpacked"
        d.note = f"Unpacked into {member_count} document(s) — safe to delete this archive."
        d.analysisStatus = "done"
        d.analysisError = None
        db.commit()
        return d.to_dict()


def set_analysis(document_id: str, analysis: Dict) -> Optional[Dict]:
    """Store the cached AI analysis for a document and mark it done."""
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if not d:
            return None
        d.analysis = analysis
        d.status = "Analysed"
        d.analysisStatus = "done"
        d.analysisError = None
        db.commit()
        return d.to_dict()


def set_extracted_text(document_id: str, text: str) -> None:
    """Cache the (possibly OCR'd) extracted text for reuse by event extraction."""
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if d:
            d.extractedText = text
            db.commit()


def get_extracted_text(document_id: str) -> Optional[str]:
    """Return the cached extracted text for a document, or None if not computed."""
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        return d.extractedText if d else None


def set_analysis_status(document_id: str, status: str, error: Optional[str] = None) -> Optional[Dict]:
    """Update only the background-analysis lifecycle status (pending/analyzing/failed)."""
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if not d:
            return None
        d.analysisStatus = status
        d.analysisError = error
        db.commit()
        return d.to_dict()


def list_pending_ids(project_id: str) -> List[str]:
    """Document ids in a project that still need analysis (never run or failed)."""
    with SessionLocal() as db:
        rows = (
            db.query(Document.id)
            .filter(Document.projectId == project_id)
            .filter(Document.data.isnot(None))
            .filter(Document.analysis.is_(None))
            .all()
        )
        return [r[0] for r in rows]


def list_unfinished_analysis_ids() -> List[str]:
    """Across all projects, document ids marked pending/analyzing that have no
    cached analysis yet — used to resume work interrupted by a restart."""
    with SessionLocal() as db:
        rows = (
            db.query(Document.id)
            .filter(Document.data.isnot(None))
            .filter(Document.analysis.is_(None))
            .filter(Document.analysisStatus.in_(("pending", "analyzing")))
            .all()
        )
        return [r[0] for r in rows]


def delete_document(document_id: str) -> bool:
    with SessionLocal() as db:
        d = db.get(Document, document_id)
        if not d:
            return False
        # Remove any comments attached to this document too.
        db.query(DocumentComment).filter(DocumentComment.documentId == document_id).delete()
        db.delete(d)
        db.commit()
        return True


# ── Document comments ──────────────────────────────────────────────────────

def list_comments(document_id: str) -> List[Dict]:
    """All comments for a document, oldest first."""
    with SessionLocal() as db:
        rows = (
            db.query(DocumentComment)
            .filter(DocumentComment.documentId == document_id)
            .order_by(DocumentComment.createdAt.asc())
            .all()
        )
        return [c.to_dict() for c in rows]


def add_comment(
    document_id: str,
    body: str,
    author: str = "",
    author_id: Optional[str] = None,
    anchor_text: Optional[str] = None,
    anchor_start: Optional[int] = None,
    anchor_length: Optional[int] = None,
) -> Dict:
    """Attach a new comment to a document and return it. May carry a text anchor."""
    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        comment = DocumentComment(
            id=f"cmt-{uuid.uuid4().hex[:12]}",
            documentId=document_id,
            projectId=doc.projectId if doc else "",
            body=body,
            author=author or "",
            authorId=author_id,
            createdAt=datetime.now(timezone.utc).isoformat(),
            anchorText=anchor_text,
            anchorStart=anchor_start,
            anchorLength=anchor_length,
        )
        db.add(comment)
        db.commit()
        return comment.to_dict()


def update_comment(comment_id: str, body: str) -> Optional[Dict]:
    """Update a comment's text and return it, or None if not found."""
    with SessionLocal() as db:
        c = db.get(DocumentComment, comment_id)
        if not c:
            return None
        c.body = body
        db.commit()
        return c.to_dict()


def delete_comment(comment_id: str) -> bool:
    with SessionLocal() as db:
        c = db.get(DocumentComment, comment_id)
        if not c:
            return False
        db.delete(c)
        db.commit()
        return True
