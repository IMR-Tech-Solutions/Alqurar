import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.schemas.admissibility import AdmissibilitySave, ContractorAdmissibilitySave
from app.services import admissibility_service, contractor_admissibility_service, project_service
from app.api.v1.deps import get_current_user

router = APIRouter()

_NOT_CONFIGURED = (
    "AI analysis is not configured — set ANTHROPIC_API_KEY in apps/backend/.env "
    "and restart the backend."
)


@router.get("/project/{project_id}")
async def get_assessment(project_id: str, _=Depends(get_current_user)):
    """The project's admissibility scoring matrix + generation status."""
    return admissibility_service.get(project_id)


@router.post("/project/{project_id}/generate")
async def generate_assessment(
    project_id: str, background: BackgroundTasks, current_user=Depends(get_current_user),
):
    """Queue AI generation of the project's admissibility matrix (background) and
    return immediately. The client polls GET /project/{id} for progress + result."""
    if current_user.get("role") == "Client View":
        raise HTTPException(status_code=403, detail="Clients cannot generate the admissibility matrix.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    if admissibility_service.get(project_id).get("status") == "running":
        return {"status": "running"}

    admissibility_service.mark_running(project_id)
    background.add_task(admissibility_service.run_generation, project_id)
    return {"status": "running"}


@router.put("/project/{project_id}")
async def save_assessment(
    project_id: str, body: AdmissibilitySave, current_user=Depends(get_current_user),
):
    """Save an analyst-edited admissibility matrix."""
    if current_user.get("role") == "Client View":
        raise HTTPException(status_code=403, detail="Clients cannot edit the admissibility matrix.")
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return admissibility_service.save_content(project_id, body.content.model_dump())


# ── Contractor admissibility — the matrix scored against each delay event ──


@router.get("/project/{project_id}/contractor")
async def get_contractor(project_id: str, _=Depends(get_current_user)):
    """The project's contractor-admissibility scoring + generation status."""
    return contractor_admissibility_service.get(project_id)


@router.post("/project/{project_id}/contractor/generate")
async def generate_contractor(
    project_id: str, background: BackgroundTasks, current_user=Depends(get_current_user),
):
    """Queue AI scoring of every delay event against the admissibility matrix
    (background) and return immediately. The client polls GET
    /project/{id}/contractor for progress + result."""
    if current_user.get("role") == "Client View":
        raise HTTPException(status_code=403, detail="Clients cannot generate the contractor admissibility scoring.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    if contractor_admissibility_service.get(project_id).get("status") == "running":
        return {"status": "running"}

    contractor_admissibility_service.mark_running(project_id)
    background.add_task(contractor_admissibility_service.run_generation, project_id)
    return {"status": "running"}


@router.put("/project/{project_id}/contractor")
async def save_contractor(
    project_id: str, body: ContractorAdmissibilitySave, current_user=Depends(get_current_user),
):
    """Save an analyst-edited contractor-admissibility scoring."""
    if current_user.get("role") == "Client View":
        raise HTTPException(status_code=403, detail="Clients cannot edit the contractor admissibility scoring.")
    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return contractor_admissibility_service.save_content(project_id, body.content.model_dump())
