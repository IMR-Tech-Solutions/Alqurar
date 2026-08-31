import { api } from "./client";
import type {
  AdmissibilityAssessment,
  AdmissibilityContent,
  ContractorAdmissibilityAssessment,
  ContractorAdmissibilityContent,
} from "@/types";

/** Fetch the project's admissibility matrix (and generation status). */
export async function getAdmissibilityApi(projectId: string): Promise<AdmissibilityAssessment> {
  const { data } = await api.get(`/admissibility/project/${projectId}`);
  return data;
}

/** Queue AI generation of the admissibility matrix; returns immediately (poll GET). */
export async function generateAdmissibilityApi(projectId: string): Promise<{ status: string }> {
  const { data } = await api.post(`/admissibility/project/${projectId}/generate`);
  return data;
}

/** Persist an analyst-edited admissibility matrix. */
export async function saveAdmissibilityApi(
  projectId: string,
  content: AdmissibilityContent,
): Promise<AdmissibilityAssessment> {
  const { data } = await api.put(`/admissibility/project/${projectId}`, { content });
  return data;
}

// ── Contractor admissibility — the matrix scored against each delay event ──

/** Fetch the project's contractor scoring (and generation status). */
export async function getContractorAdmissibilityApi(
  projectId: string,
): Promise<ContractorAdmissibilityAssessment> {
  const { data } = await api.get(`/admissibility/project/${projectId}/contractor`);
  return data;
}

/** Queue AI scoring of every delay event; returns immediately (poll GET). */
export async function generateContractorAdmissibilityApi(
  projectId: string,
): Promise<{ status: string }> {
  const { data } = await api.post(`/admissibility/project/${projectId}/contractor/generate`);
  return data;
}

/** Persist an analyst-edited contractor scoring. */
export async function saveContractorAdmissibilityApi(
  projectId: string,
  content: ContractorAdmissibilityContent,
): Promise<ContractorAdmissibilityAssessment> {
  const { data } = await api.put(`/admissibility/project/${projectId}/contractor`, { content });
  return data;
}
