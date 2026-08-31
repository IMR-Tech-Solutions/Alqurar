"""Per-project Clause Library API.

Each project has its own clause library (project_clauses), built from that
project's uploaded contract. Routes are nested under a project:
    GET    /projects/{project_id}/clauses
    POST   /projects/{project_id}/clauses
    PATCH  /projects/{project_id}/clauses/{clause_id}
    DELETE /projects/{project_id}/clauses/{clause_id}
    POST   /projects/{project_id}/clauses/extract          (upload the contract)
    GET    /projects/{project_id}/clauses/extract-status   (poll AI extraction)

`extract` stores the uploaded contract in the project's data room and, when the
Anthropic key is configured, kicks off background AI extraction that fills the
project's clause library. The client polls `extract-status` for progress.
"""
import os
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.schemas.project_clause import (
    ProjectClauseCreate,
    ProjectClauseOut,
    ProjectClauseUpdate,
)
from app.services import clause_extraction, contract_book_service, pcc_comparison
from app.services.document_service import SELF_ID, create_document_with_next_id
from app.services.project_clause_service import (
    add_clause,
    clear_book_clauses,
    delete_clause,
    get_clause,
    get_clause_book_id,
    list_by_project,
    set_book_clauses,
    update_clause,
)
from app.api.v1.deps import get_current_user, require_permission

router = APIRouter()

# Adding/editing/removing clauses is a contract-management action — the same
# permission the global clause library used.
_MANAGE = require_permission("contracts.manage")

_EXT_TYPE = {
    "pdf": "PDF", "doc": "DOCX", "docx": "DOCX", "xls": "XLSX", "xlsx": "XLSX",
}


def _doc_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_TYPE.get(ext, "Other")


@router.get("/{project_id}/clauses", response_model=List[ProjectClauseOut])
async def get_project_clauses(project_id: str, _=Depends(get_current_user)):
    """This project's clause library (all authenticated users can read it)."""
    return list_by_project(project_id)


@router.post("/{project_id}/clauses", response_model=ProjectClauseOut, status_code=status.HTTP_201_CREATED)
async def create_project_clause(
    project_id: str,
    payload: ProjectClauseCreate,
    current_user=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    if not payload.contract_standard.strip():
        raise HTTPException(status_code=400, detail="Contract standard is required.")
    if not payload.clause_number.strip():
        raise HTTPException(status_code=400, detail="Clause number is required.")
    if not payload.clause_title.strip():
        raise HTTPException(status_code=400, detail="Clause title is required.")
    return add_clause(project_id, payload.model_dump(), created_by=current_user["id"])


@router.patch("/{project_id}/clauses/{clause_id}", response_model=ProjectClauseOut)
async def patch_project_clause(
    project_id: str,
    clause_id: str,
    payload: ProjectClauseUpdate,
    _=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    updated = update_clause(clause_id, payload.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Clause not found")
    return updated


@router.delete("/{project_id}/clauses/book")
async def clear_project_clause_book(
    project_id: str,
    _=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    """Unselect the base contract book.

    Removes the clauses copied from it along with anything a PCC comparison added
    or amended on top, and clears the comparison state. Clauses added by hand or
    extracted from an uploaded contract are kept."""
    removed = clear_book_clauses(project_id)
    pcc_comparison.mark_idle(project_id)
    return {"bookId": None, "removed": removed}


@router.delete("/{project_id}/clauses/{clause_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_clause(
    project_id: str,
    clause_id: str,
    _=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    if not get_clause(clause_id):
        raise HTTPException(status_code=404, detail="Clause not found")
    delete_clause(clause_id)


@router.post("/{project_id}/clauses/extract", status_code=status.HTTP_201_CREATED)
async def upload_contract_for_clauses(
    project_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    """Upload this project's contract and (when AI is configured) extract its clauses.

    The contract is stored in the project's data room, then Claude reads it in the
    background and fills the clause library. The client polls `extract-status`.
    If the Anthropic key isn't set, the contract is saved and the response says so.
    """
    content = await file.read()
    record = {
        "projectId": project_id,
        "name": file.filename or "contract",
        "type": _doc_type(file.filename or ""),
        "sizeKB": max(1, round(len(content) / 1024)),
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "uploadedBy": current_user.get("name") or current_user.get("email") or "",
        "status": "Uploaded",
        "claimRef": "Contract",
        "note": "Uploaded for clause extraction",
        "driveFileId": SELF_ID,
        "data": content,
        "mime": file.content_type or "application/octet-stream",
    }
    doc_id = create_document_with_next_id(record)["id"]

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {
            "stored": True,
            "documentId": doc_id,
            "status": "idle",
            "message": "Contract saved. AI clause extraction will run once the Anthropic key is enabled.",
        }

    clause_extraction.mark_running(project_id)
    background.add_task(clause_extraction.run_extraction, project_id, doc_id)
    return {
        "stored": True,
        "documentId": doc_id,
        "status": "running",
        "message": "Contract saved. Claude is reading it and extracting the clauses…",
    }


@router.get("/{project_id}/clauses/extract-status")
async def clause_extraction_status(project_id: str, _=Depends(get_current_user)):
    """Current state of background clause extraction for a project."""
    return clause_extraction.get_status(project_id)


# ── Base contract book (from the Knowledge Center) ───────────────────────────

class SelectBookPayload(BaseModel):
    bookId: str


@router.get("/{project_id}/clauses/book")
async def get_project_clause_book(project_id: str, _=Depends(get_current_user)):
    """The Knowledge-Center book chosen as this project's base clause set."""
    return {"bookId": get_clause_book_id(project_id)}


@router.post("/{project_id}/clauses/book")
async def select_project_clause_book(
    project_id: str,
    payload: SelectBookPayload,
    _=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    """Pick a base FIDIC/standard-form book and copy its clauses into this
    project's library. Replaces the previously book-sourced clauses (manual and
    AI-extracted ones are kept) and clears any prior PCC modifications."""
    book = contract_book_service.get_book(payload.bookId)
    if not book:
        raise HTTPException(status_code=404, detail="Contract book not found.")
    if book.get("status") != "done":
        raise HTTPException(
            status_code=400,
            detail="This book hasn't finished extracting its clauses yet.",
        )
    book_clauses = contract_book_service.list_book_clauses(payload.bookId)
    if not book_clauses:
        raise HTTPException(status_code=400, detail="This book has no extracted clauses to copy.")
    count = set_book_clauses(project_id, book, book_clauses)
    # A new base book invalidates any earlier PCC comparison.
    pcc_comparison.mark_idle(project_id)
    return {"bookId": payload.bookId, "count": count}


# Unselecting the book (DELETE .../clauses/book) is declared with the clause CRUD
# routes above — it has to precede DELETE .../clauses/{clause_id}, which would
# otherwise match first and take "book" for a clause id.


# ── Particular Conditions of Contract (PCC) ─────────────────────────────────

@router.post("/{project_id}/clauses/pcc", status_code=status.HTTP_201_CREATED)
async def upload_pcc(
    project_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    __=Depends(_MANAGE),
):
    """Upload the project's Particular Conditions of Contract. Claude compares it
    against the selected base book's clauses and flags the ones it amends.

    The PCC is stored in the project's data room, then compared in the background;
    the client polls `pcc-status`. Requires a base book to have been selected."""
    if not get_clause_book_id(project_id):
        raise HTTPException(
            status_code=400,
            detail="Select a base contract book before uploading the Particular Conditions.",
        )

    content = await file.read()
    record = {
        "projectId": project_id,
        "name": file.filename or "particular-conditions",
        "type": _doc_type(file.filename or ""),
        "sizeKB": max(1, round(len(content) / 1024)),
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "uploadedBy": current_user.get("name") or current_user.get("email") or "",
        "status": "Uploaded",
        "claimRef": "Particular Conditions",
        "note": "Uploaded for PCC comparison",
        "driveFileId": SELF_ID,
        "data": content,
        "mime": file.content_type or "application/octet-stream",
    }
    doc_id = create_document_with_next_id(record)["id"]

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {
            "stored": True,
            "documentId": doc_id,
            "status": "idle",
            "message": "Particular Conditions saved. AI comparison will run once the Anthropic key is enabled.",
        }

    pcc_comparison.mark_running(project_id)
    background.add_task(pcc_comparison.run_comparison, project_id, doc_id)
    return {
        "stored": True,
        "documentId": doc_id,
        "status": "running",
        "message": "Particular Conditions saved. Claude is comparing them against the base clauses…",
    }


@router.get("/{project_id}/clauses/pcc-status")
async def pcc_comparison_status(project_id: str, _=Depends(get_current_user)):
    """Current state of background PCC comparison for a project."""
    return pcc_comparison.get_status(project_id)
