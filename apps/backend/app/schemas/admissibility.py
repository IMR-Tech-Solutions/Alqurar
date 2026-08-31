from typing import List, Optional

from pydantic import BaseModel


class AdmissibilityCriterion(BaseModel):
    """One compliance check within a clause group."""

    id: Optional[str] = None
    category: str = ""
    subClause: str = ""
    description: str = ""
    overallWtg: float = 0


class AdmissibilityClause(BaseModel):
    """One clause group in the matrix, allocated a share of the 100 marks."""

    id: Optional[str] = None
    clauseRef: str = ""
    label: str = ""
    marks: float = 0
    # book | modified | new | manual
    source: str = "book"
    note: str = ""
    criteria: List[AdmissibilityCriterion] = []


class AdmissibilityContent(BaseModel):
    clauses: List[AdmissibilityClause] = []
    summary: str = ""


class AdmissibilitySave(BaseModel):
    """PUT body — the analyst-edited matrix."""

    content: AdmissibilityContent


# ── Contractor admissibility (Admissibility tab → Contractor admissibility) ──
# The matrix's criteria scored against every delay event. Weights are snapshot
# from the matrix at generation time so the scoring keeps rendering after the
# matrix is regenerated; the score itself is derived (weightage when applicable
# AND complied, else 0) and therefore not stored.


class ContractorCriterion(BaseModel):
    """One scored requirement, snapshot from the admissibility matrix."""

    id: Optional[str] = None
    clauseRef: str = ""
    clauseLabel: str = ""
    category: str = ""
    subClause: str = ""
    description: str = ""
    # Effective weight — the criterion's % share of its clause's marks. These sum
    # to 100 across all criteria.
    weightage: float = 0


class ContractorRow(BaseModel):
    """One criterion's verdict for one delay event."""

    criterionId: str = ""
    # "Y" | "N"
    applicable: str = "N"
    complied: str = "N"
    evidence: str = ""


class ContractorEvent(BaseModel):
    """One delay event's column of verdicts."""

    eventId: str = ""
    eventRef: str = ""
    title: str = ""
    remarks: str = ""
    rows: List[ContractorRow] = []


class ContractorAdmissibilityContent(BaseModel):
    criteria: List[ContractorCriterion] = []
    events: List[ContractorEvent] = []
    summary: str = ""
    # The matrix version this was scored against — lets the UI flag stale scoring.
    matrixUpdatedAt: Optional[str] = None


class ContractorAdmissibilitySave(BaseModel):
    """PUT body — the analyst-edited contractor scoring."""

    content: ContractorAdmissibilityContent
