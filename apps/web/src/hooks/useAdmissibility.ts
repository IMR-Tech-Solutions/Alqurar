import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  generateAdmissibilityApi,
  generateContractorAdmissibilityApi,
  getAdmissibilityApi,
  getContractorAdmissibilityApi,
  saveAdmissibilityApi,
  saveContractorAdmissibilityApi,
} from "@/api/admissibility";
import type { AdmissibilityContent, ContractorAdmissibilityContent } from "@/types";

export const admissibilityKey = (projectId: string) => ["admissibility", projectId] as const;

/** The project's admissibility matrix; polls while generation runs. */
export function useAdmissibility(projectId: string) {
  return useQuery({
    queryKey: admissibilityKey(projectId),
    queryFn: () => getAdmissibilityApi(projectId),
    enabled: !!projectId,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  });
}

/** Queue AI generation of the admissibility matrix. */
export function useGenerateAdmissibility(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => generateAdmissibilityApi(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: admissibilityKey(projectId) }),
  });
}

/** Save an analyst-edited admissibility matrix. */
export function useSaveAdmissibility(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: AdmissibilityContent) => saveAdmissibilityApi(projectId, content),
    onSuccess: (data) => qc.setQueryData(admissibilityKey(projectId), data),
  });
}

// ── Contractor admissibility — the matrix scored against each delay event ──

export const contractorAdmissibilityKey = (projectId: string) =>
  ["contractor-admissibility", projectId] as const;

/** The project's contractor scoring; polls while generation runs. */
export function useContractorAdmissibility(projectId: string) {
  return useQuery({
    queryKey: contractorAdmissibilityKey(projectId),
    queryFn: () => getContractorAdmissibilityApi(projectId),
    enabled: !!projectId,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  });
}

/** Queue AI scoring of every delay event against the matrix. */
export function useGenerateContractorAdmissibility(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => generateContractorAdmissibilityApi(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: contractorAdmissibilityKey(projectId) }),
  });
}

/** Save an analyst-edited contractor scoring. */
export function useSaveContractorAdmissibility(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: ContractorAdmissibilityContent) =>
      saveContractorAdmissibilityApi(projectId, content),
    onSuccess: (data) => qc.setQueryData(contractorAdmissibilityKey(projectId), data),
  });
}
