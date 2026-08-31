"""SQLAlchemy models — persisted equivalents of the former in-memory stores.

Column names mirror the dict keys the services/schemas already use (incl.
camelCase), so the API responses are unchanged.
"""
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="Client View")
    status: Mapped[str] = mapped_column(String, default="Active")
    phone: Mapped[str] = mapped_column(String, default="")
    lastActive: Mapped[str] = mapped_column(String, default="")

    def to_dict(self, with_password: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "phone": self.phone,
            "lastActive": self.lastActive,
        }
        if with_password:
            d["password"] = self.password
        return d


class ClientProfile(Base):
    """Company details for a client, keyed by the client's user id."""

    __tablename__ = "client_profiles"

    userId: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[str] = mapped_column(String, default="")
    crNo: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    roleOnProject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contactName: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="", index=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    projectId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Secret token for the client's passwordless upload portal link
    # (/portal/{accessToken}). Unique per client; how they access without a login.
    accessToken: Mapped[Optional[str]] = mapped_column(String, unique=True, index=True, nullable=True)
    # Client lifecycle stage: "Temporary" (just onboarded — upload only) or
    # "Permanent" (promoted later — richer portal in future). New clients start
    # Temporary.
    clientType: Mapped[str] = mapped_column(String, default="Temporary")

    _FIELDS = (
        "userId", "company", "crNo", "country", "roleOnProject",
        "contactName", "email", "phone", "projectId", "createdAt", "accessToken",
        "clientType",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    code: Mapped[str] = mapped_column(String, default="")
    employer: Mapped[str] = mapped_column(String, default="")
    contractor: Mapped[str] = mapped_column(String, default="")
    standard: Mapped[str] = mapped_column(String, default="")
    value: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String, default="OMR")
    startDate: Mapped[str] = mapped_column(String, default="")
    completionDate: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="Active")
    riskLevel: Mapped[str] = mapped_column(String, default="Moderate")
    source: Mapped[str] = mapped_column(String, default="created")
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    engineer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    loaRef: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commencementDate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    timeForCompletionDays: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dataDate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    baselineProgramme: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Distinguishes an ordinary project ("project") from a standalone proposal
    # ("proposal"). Proposals reuse the same documents / delay-event pipeline but
    # live in their own Proposals area, not the Projects list.
    kind: Mapped[str] = mapped_column(String, default="project")
    # For proposals (kind="proposal"): the service line this proposal is for —
    # one of "claims_support" | "quantum_expert" | "eot_claims" |
    # "arbitration_expert" | "quantum_claims". Drives the costing defaults and the
    # generated proposal template. Empty for ordinary projects.
    proposalType: Mapped[str] = mapped_column(String, default="")
    # The Knowledge-Center contract book chosen as this project's base clause set
    # (its clauses are copied into the project's Clause Library). Null until picked.
    clauseBookId: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "id", "name", "code", "employer", "contractor", "standard", "value", "currency",
        "startDate", "completionDate", "status", "riskLevel", "source", "location", "engineer",
        "loaRef", "commencementDate", "timeForCompletionDays", "dataDate", "baselineProgramme", "createdAt",
        "kind", "proposalType", "clauseBookId",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    client_user_id: Mapped[str] = mapped_column(String, index=True)
    assigned_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "client_user_id": self.client_user_id,
            "assigned_by": self.assigned_by,
            "created_at": self.created_at,
        }


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    projectId: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, default="Other")
    sizeKB: Mapped[int] = mapped_column(Integer, default=0)
    uploadedAt: Mapped[str] = mapped_column(String, default="")
    uploadedBy: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="Uploaded")
    claimRef: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    driveFileId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The actual file bytes are stored in the database so uploads persist and can
    # be downloaded again (no external storage needed). DEFERRED: a file can be
    # tens of MB, so it is NOT loaded by ordinary list/get queries (e.g. the data
    # room list that the UI polls). It loads only when the bytes are accessed
    # (download / text extraction), keeping list queries small and the event loop
    # responsive.
    data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True, deferred=True)
    mime: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Client user id when uploaded via the client portal — lets a client see only
    # their own uploads ("client folder"). Null for admin/staff uploads.
    uploadedById: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Cached AI analysis of this document (DocumentAnalysis shape) so the data room
    # shows what each file is about without re-running the model on every view.
    analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Background-analysis lifecycle: "" (never run), "pending", "analyzing",
    # "done", "failed". Lets the UI poll instead of blocking on the model call.
    analysisStatus: Mapped[str] = mapped_column(String, default="")
    analysisError: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Cached extracted text (OCR'd if the file was scanned). Computed once during
    # analysis and reused by delay-event extraction so files aren't re-read/re-OCR'd.
    # Deferred + kept out of _FIELDS: never sent to the frontend or loaded by lists.
    extractedText: Mapped[Optional[str]] = mapped_column(Text, nullable=True, deferred=True)

    _FIELDS = ("id", "projectId", "name", "type", "sizeKB", "uploadedAt", "uploadedBy", "status", "claimRef", "note", "driveFileId", "uploadedById", "analysis", "analysisStatus", "analysisError")

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class DocumentComment(Base):
    """A free-text comment/note attached to an uploaded document. Multiple
    comments per document; shown beside the file in the in-app viewer."""

    __tablename__ = "document_comments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    documentId: Mapped[str] = mapped_column(String, index=True)
    projectId: Mapped[str] = mapped_column(String, default="", index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String, default="")
    authorId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    createdAt: Mapped[str] = mapped_column(String, default="")
    # Optional text anchor (for Word/text documents): the exact selected text and
    # its character offset within the rendered content, so it can be highlighted.
    anchorText: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    anchorStart: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anchorLength: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    _FIELDS = (
        "id", "documentId", "projectId", "body", "author", "authorId", "createdAt",
        "anchorText", "anchorStart", "anchorLength",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class DelayEvent(Base):
    """A reviewable delay event for a project (Project Workspace → Delay Events).

    Plain data storage — no AI involved. Events are created manually by the
    analyst (or seeded), reviewed (Accept / Merge / Reject), edited and deleted.
    `chronology`, `chronologyNarrative` and `sources` are stored whole as JSON
    columns so they match the frontend shape exactly.
    """

    __tablename__ = "delay_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    projectId: Mapped[str] = mapped_column(String, index=True)
    ref: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="")
    narrative: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(String, default="Employer")
    clause: Mapped[str] = mapped_column(String, default="")
    startDate: Mapped[str] = mapped_column(String, default="")
    endDate: Mapped[str] = mapped_column(String, default="")
    daysImpact: Mapped[int] = mapped_column(Integer, default=0)
    criticalPath: Mapped[bool] = mapped_column(Boolean, default=False)
    admissibility: Mapped[str] = mapped_column(String, default="Not assessed")
    aiConfidence: Mapped[int] = mapped_column(Integer, default=0)
    reviewStatus: Mapped[str] = mapped_column(String, default="Pending")
    chronology: Mapped[list] = mapped_column(JSON, default=list)
    # The submission-style write-up that frames the chronology — introduction,
    # delay event timeline, cause & effect and contractual entitlement. Written by
    # the AI chronology generator alongside `chronology`; null until generated.
    chronologyNarrative: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "id", "projectId", "ref", "title", "category", "narrative", "cause", "clause",
        "startDate", "endDate", "daysImpact", "criticalPath", "admissibility",
        "aiConfidence", "reviewStatus", "chronology", "chronologyNarrative", "sources",
    )

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["chronology"] = self.chronology or []
        d["sources"] = self.sources or []
        return d


class ProjectQuery(Base):
    """A query / RFI raised against a project (Project Workspace → Queries tab).

    A register of the questions (RFIs) put to the client / insurer (GIC) about an
    EOT or delay matter, together with the response received and the query status
    (Open / Closed). Rows are added manually by the analyst, who also records the
    GIC response and closes the query once answered. Plain data storage — no AI.
    Column names mirror the frontend `ProjectQuery` shape exactly.
    """

    __tablename__ = "project_queries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    projectId: Mapped[str] = mapped_column(String, index=True)
    dateOfRfi: Mapped[str] = mapped_column(String, default="")
    eotDescription: Mapped[str] = mapped_column(Text, default="")
    queryDescription: Mapped[str] = mapped_column(Text, default="")
    responseFromGic: Mapped[str] = mapped_column(Text, default="")
    dateOfResponse: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="Open")
    remarks: Mapped[str] = mapped_column(Text, default="")
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "id", "projectId", "dateOfRfi", "eotDescription", "queryDescription",
        "responseFromGic", "dateOfResponse", "status", "remarks",
        "createdAt", "updatedAt",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class ProjectClause(Base):
    """A clause in a single project's own Clause Library.

    Unlike a global reference list, clauses here belong to ONE project
    (projectId): each project has its own library, built from that project's
    uploaded contract. Today rows are added manually; when the Anthropic key is
    enabled, AI reads the uploaded contract and fills this same table, and later
    matches delay events to these rows. The columns mirror the former global
    clause shape plus projectId, so the frontend mapping is unchanged.
    """

    __tablename__ = "project_clauses"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    projectId: Mapped[str] = mapped_column(String, index=True)
    contract_standard: Mapped[str] = mapped_column(String, default="")
    clause_number: Mapped[str] = mapped_column(String, default="")
    clause_title: Mapped[str] = mapped_column(String, default="")
    clause_description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # How the row got here: "manual" (added by hand), "book" (copied from a
    # selected Knowledge-Center contract book) or "ai" (extracted from an uploaded
    # contract). Book-sourced rows are the ones a PCC upload can amend.
    source: Mapped[str] = mapped_column(String, default="manual")
    # Set when a Particular Conditions (PCC) upload amends this base clause. The
    # card then shows a "Modified" tag; `modification_note` says what changed.
    modified: Mapped[bool] = mapped_column(Boolean, default=False)
    modification_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The General-Conditions (base standard-form) wording, kept when a PCC
    # amendment overwrites `clause_description` with the amended wording. Lets a
    # modified clause be shown as base clause → PCC amendment → clause as amended.
    # NULL on unmodified clauses (the base wording IS `clause_description`).
    base_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # What the amendment means in practice — which party it favours and how it
    # changes the claim position. Written by Claude for modified clauses only.
    interpretation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    _FIELDS = (
        "id", "projectId", "contract_standard", "clause_number", "clause_title",
        "clause_description", "tags", "created_by", "created_at", "updated_at",
        "source", "modified", "modification_note", "base_description",
        "interpretation",
    )

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["tags"] = self.tags or []
        return d


class EOTClaim(Base):
    """The AI-generated Extension of Time claim document for a project (Proposal
    tab). One row per project. `content` holds the structured claim
    ({title, sections:[{heading, body}]}); `status` drives background generation
    polling (""/"running"/"done"/"failed")."""

    __tablename__ = "eot_claims"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def to_dict(self) -> dict:
        return {
            "projectId": self.projectId,
            "content": self.content,
            "model": self.model,
            "status": self.status or "",
            "error": self.error,
            "updatedAt": self.updatedAt,
        }


class AdmissibilityAssessment(Base):
    """The AI-generated admissibility scoring matrix for a project (Admissibility
    tab). One row per project. `content` holds the structured matrix:
    {clauses: [{id, clauseRef, label, marks, source, note,
                criteria: [{id, category, subClause, description, overallWtg}]}],
     summary}. The clause `marks` split 100 across the selected clauses; each
    criterion's `overallWtg` is a percentage within its clause. `status` drives
    background generation polling (""/"running"/"done"/"failed"); the analyst can
    also edit the matrix, which saves straight to `content`."""

    __tablename__ = "admissibility_assessments"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = ("projectId", "content", "model", "status", "error", "createdAt", "updatedAt")

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["status"] = self.status or ""
        return d


class ContractorAdmissibility(Base):
    """The AI-generated CONTRACTOR ADMISSIBILITY scoring for a project
    (Admissibility tab → Contractor admissibility). One row per project.

    Each of the admissibility matrix's criteria is scored against EVERY delay
    event: is the clause applicable to that event, did the Contractor comply, and
    what evidence shows it. `content` snapshots the criteria (so the scoring
    still renders if the matrix is later regenerated) alongside the per-event
    judgements:
    {criteria: [{id, clauseRef, clauseLabel, category, subClause, description,
                 weightage}],
     events:   [{eventId, eventRef, title, remarks,
                 rows: [{criterionId, applicable, complied, evidence}]}],
     summary, matrixUpdatedAt}
    `weightage` is the criterion's effective weight (its % share of its clause's
    marks), so the weightages across all criteria sum to 100. Score is derived,
    not stored: weightage when applicable AND complied, else 0. `status` drives
    background-generation polling (""/"running"/"done"/"failed")."""

    __tablename__ = "contractor_admissibility"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = ("projectId", "content", "model", "status", "error", "createdAt", "updatedAt")

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["status"] = self.status or ""
        return d


class ClientProposal(Base):
    """The AI-generated client-facing costed proposal for a proposal record
    (Proposals → New Proposal → Proposal tab). One row per proposal. `content`
    holds the structured proposal ({title, sections:[{heading, body}],
    costing:[{item, description, amount}], currency, total}); `status` drives
    background-generation polling (""/"running"/"done"/"failed")."""

    __tablename__ = "client_proposals"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Admin-entered fields that steer generation (client address, attention,
    # reference, date, discount, special notes, …). Kept so the form persists and
    # the same inputs are reused on regeneration.
    inputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # "Send to Client" — marks a finished proposal as shared with the client via
    # their portal. The portal only surfaces proposals where this flag is True.
    sentToClient: Mapped[bool] = mapped_column(Boolean, default=False)
    sentAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def to_dict(self) -> dict:
        return {
            "projectId": self.projectId,
            "content": self.content,
            "model": self.model,
            "status": self.status or "",
            "error": self.error,
            "inputs": self.inputs or {},
            "updatedAt": self.updatedAt,
            "sentToClient": self.sentToClient,
            "sentAt": self.sentAt,
        }


class ContractBook(Base):
    """A standard-form contract book in the Knowledge Center (FIDIC Red/Yellow/
    Silver, NEC4, …), uploaded once by an admin and read end-to-end by Claude to
    populate `book_clauses`.

    This is deliberately NOT the same table as ProjectClause. A project's Clause
    Library holds the clauses of THAT project's own contract; a ContractBook is
    the published standard form, shared across every project as reference.

    The file bytes live on this row (DEFERRED — a book is tens of MB and must
    never be pulled into the card-list query). They are kept so a book can be
    re-analysed later without asking the admin to upload it again.
    """

    __tablename__ = "contract_books"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="", index=True)
    edition: Mapped[str] = mapped_column(String, default="")
    publisher: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fileName: Mapped[str] = mapped_column(String, default="")
    sizeKB: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True, deferred=True)
    uploadedAt: Mapped[str] = mapped_column(String, default="")
    uploadedBy: Mapped[str] = mapped_column(String, default="")
    # Extraction lifecycle: "pending" (queued), "processing", "done", "failed".
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clauseCount: Mapped[int] = mapped_column(Integer, default=0)
    # A book is read in chunks (a whole book's verbatim clauses exceed one model
    # response), so progress is reported as chunks completed out of total.
    processedChunks: Mapped[int] = mapped_column(Integer, default=0)
    totalChunks: Mapped[int] = mapped_column(Integer, default=0)

    _FIELDS = (
        "id", "name", "edition", "publisher", "fileName", "sizeKB", "mime",
        "uploadedAt", "uploadedBy", "status", "error", "clauseCount",
        "processedChunks", "totalChunks",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class BookClause(Base):
    """One clause / sub-clause extracted from a ContractBook by Claude.

    Unlike ProjectClause (which stores only a short `clause_description`), a book
    clause keeps the VERBATIM `clause_text` alongside an AI `summary`, because the
    Knowledge Center is a reference library people read from, not a claim worksheet.
    """

    __tablename__ = "book_clauses"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bookId: Mapped[str] = mapped_column(String, index=True)
    clause_number: Mapped[str] = mapped_column(String, default="", index=True)
    clause_title: Mapped[str] = mapped_column(String, default="")
    clause_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Preserves the order clauses appear in the book (chunk index, then position),
    # so the reader lists them in document order rather than alphabetically.
    sortIndex: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "id", "bookId", "clause_number", "clause_title", "clause_text",
        "summary", "tags", "sortIndex", "createdAt",
    )

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in self._FIELDS}
        d["tags"] = self.tags or []
        return d


class Employee(Base):
    """Master-data record for a team member, used to price proposal costings.

    This is the single source of truth for who can be costed and at what hourly
    rate. A costing row COPIES the rate off this record at the time it is saved
    (see CostingEntry.rate) — so editing an employee's rate here never rewrites the
    numbers on a proposal that was already costed.
    """

    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="", index=True)
    designation: Mapped[str] = mapped_column(String, default="", index=True)
    hourlyRate: Mapped[float] = mapped_column(Float, default=0)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="Active")  # "Active" | "Inactive"
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "id", "name", "designation", "hourlyRate", "department", "status",
        "createdAt", "updatedAt",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class CostingActivity(Base):
    """One activity/line on a proposal's costing sheet (e.g. "Programme
    Preparation"), owning a set of CostingEntry rows. Scoped to the proposal's
    project row (projectId, kind="proposal"). One sheet per proposal = the set of
    activities sharing a projectId.
    """

    __tablename__ = "costing_activities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    projectId: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(String, default="")
    sortIndex: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = ("id", "projectId", "description", "sortIndex", "createdAt", "updatedAt")

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class CostingEntry(Base):
    """A single employee line within a CostingActivity: a role, optionally a named
    employee, the hours, and the hourly rate.

    `rate` and `employeeName` are SNAPSHOTS taken from the Employee master when the
    entry is saved — deliberately denormalised so a later change to the employee's
    master rate (or their deletion) does not alter an already-costed proposal. Total
    cost is derived (hours × rate), never stored.
    """

    __tablename__ = "costing_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    activityId: Mapped[str] = mapped_column(String, index=True)
    # Denormalised so entries can be scoped/queried per proposal (e.g. future
    # commercial reports) without joining through the activity.
    projectId: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, default="")
    employeeId: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    employeeName: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    hours: Mapped[float] = mapped_column(Float, default=0)
    rate: Mapped[float] = mapped_column(Float, default=0)  # snapshot of hourlyRate
    sortIndex: Mapped[int] = mapped_column(Integer, default=0)

    _FIELDS = (
        "id", "activityId", "projectId", "role", "employeeId", "employeeName",
        "hours", "rate", "sortIndex",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class CostingSettings(Base):
    """The editable percentage markups for a proposal's costing sheet (one row per
    proposal). Drives the Cost Summary waterfall: Total → +Contingency → +Overheads
    → +Profit → +Income Tax → +VAT → Suggested Pricing. Percentages are whole
    numbers (10 = 10%)."""

    __tablename__ = "costing_settings"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    contingencyPct: Mapped[float] = mapped_column(Float, default=10)
    overheadsPct: Mapped[float] = mapped_column(Float, default=15)
    profitPct: Mapped[float] = mapped_column(Float, default=25)
    incomeTaxPct: Mapped[float] = mapped_column(Float, default=15)
    # Excel leaves VAT blank, so it defaults to 0 (kept configurable) — see
    # costing_service.compute_summary for the exact Excel-parity waterfall.
    vatPct: Mapped[float] = mapped_column(Float, default=0)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    _FIELDS = (
        "projectId", "contingencyPct", "overheadsPct", "profitPct",
        "incomeTaxPct", "vatPct", "updatedAt",
    )

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self._FIELDS}


class ProposalChecklist(Base):
    """The EOT-documentation checklist for a proposal (Proposals → Checklist tab).

    One row per proposal. The 27 checklist items themselves are canonical and live
    in the service (proposal_checklist_service.CANONICAL_ITEMS); this row stores only
    the editable state per item — the status (Yes / No / N/A), any remarks, and the
    ids of the documents attached to it — as one JSON list keyed by item `no`:

        [{"no": 1, "status": "yes", "remarks": "...", "docIds": ["doc-3"]}, ...]

    Rows numbered 28+ are user-added "additional document" rows and carry their own
    `item` label. Attached files are ordinary project documents (they also appear in
    the proposal's Documents tab); only their ids are referenced here.

    Filled collaboratively by the client and the analyst; plain data storage, no AI.
    """

    __tablename__ = "proposal_checklists"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def to_dict(self) -> dict:
        return {
            "projectId": self.projectId,
            "items": self.items or [],
            "updatedAt": self.updatedAt,
        }


class MethodologyAssessment(Base):
    """The delay-analysis method-selection model for a proposal (Proposals →
    Methodology & Approach tab). One row per proposal.

    Either the analyst already knows the method (knowMethodology = True →
    manualSelection holds one of the 4 method ids), or the weighted scoring model
    recommends one (knowMethodology = False). The 17 factors, their fixed
    per-method Requirements and their weights are canonical and live in the service
    (methodology_service); this row stores only the editable state:

      * availability — factorNo → recordIndex → methodId → "yes"/"no"/"" for the
        10 detailed factors (the yellow "Availability / Applicable / Suitable /
        Purpose" cells).
      * directScores — factorNo → methodId → "yes"/"no" for the 7 factors that have
        no sub-table (a single Yes/No per method instead of a records checklist).

    Plain data storage; the suitability / final / total scores and the recommended
    method are computed from this state (see methodology_service.compute_summary).
    """

    __tablename__ = "methodology_assessments"

    projectId: Mapped[str] = mapped_column(String, primary_key=True)
    knowMethodology: Mapped[bool] = mapped_column(Boolean, default=False)
    manualSelection: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)
    directScores: Mapped[dict] = mapped_column(JSON, default=dict)
    updatedAt: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    def to_dict(self) -> dict:
        return {
            "projectId": self.projectId,
            "knowMethodology": bool(self.knowMethodology),
            "manualSelection": self.manualSelection,
            "availability": self.availability or {},
            "directScores": self.directScores or {},
            "updatedAt": self.updatedAt,
        }


class PortalOTP(Base):
    """The active one-time code for a client portal link (one row per link).
    The code itself is stored hashed; rows expire and are deleted on use."""

    __tablename__ = "portal_otps"

    portalToken: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, default="")
    codeHash: Mapped[str] = mapped_column(String, default="")
    expiresAt: Mapped[str] = mapped_column(String, default="")  # ISO datetime
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class PortalSession(Base):
    """A verified browser session for a client portal link, created after a
    successful OTP. The client isn't asked for OTP again until this expires."""

    __tablename__ = "portal_sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    portalToken: Mapped[str] = mapped_column(String, index=True)
    userId: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    createdAt: Mapped[str] = mapped_column(String, default="")
    expiresAt: Mapped[str] = mapped_column(String, default="")  # ISO datetime
