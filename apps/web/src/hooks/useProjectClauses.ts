import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearClauseBookApi,
  createProjectClauseApi,
  deleteProjectClauseApi,
  extractProjectClausesApi,
  getClauseBookApi,
  getClauseExtractStatusApi,
  getPccStatusApi,
  listProjectClausesApi,
  selectClauseBookApi,
  updateProjectClauseApi,
  uploadPccApi,
  type ProjectClauseCreatePayload,
  type ProjectClauseUpdatePayload,
} from "@/api/projectClauses";

export const projectClausesKey = (projectId: string) => ["project-clauses", projectId] as const;

/** A single project's own clause library. */
export function useProjectClausesQuery(projectId: string) {
  return useQuery({
    queryKey: projectClausesKey(projectId),
    queryFn: () => listProjectClausesApi(projectId),
    enabled: !!projectId,
    staleTime: 5 * 60_000,
  });
}

export function useCreateProjectClause(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProjectClauseCreatePayload) => createProjectClauseApi(projectId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: projectClausesKey(projectId) }),
  });
}

export function useUpdateProjectClause(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ProjectClauseUpdatePayload }) =>
      updateProjectClauseApi(projectId, id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: projectClausesKey(projectId) }),
  });
}

export function useDeleteProjectClause(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProjectClauseApi(projectId, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: projectClausesKey(projectId) }),
  });
}

export const clauseExtractStatusKey = (projectId: string) =>
  ["clause-extract-status", projectId] as const;

/** Upload the contract; the backend extracts clauses with AI in the background.
 * Seeds the status query so {@link useClauseExtractStatus} starts polling. */
export function useExtractProjectClauses(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => extractProjectClausesApi(projectId, file),
    onSuccess: (res) =>
      qc.setQueryData(clauseExtractStatusKey(projectId), {
        status: res.status,
        // carry the "not configured" message through as an error when idle
        error: res.status === "idle" ? res.message : undefined,
      }),
  });
}

/** Polls background clause-extraction status; when it finishes, refreshes the
 * project's clause list so the newly extracted clauses appear. */
export function useClauseExtractStatus(projectId: string) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: clauseExtractStatusKey(projectId),
    queryFn: () => getClauseExtractStatusApi(projectId),
    enabled: !!projectId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });

  const prev = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = query.data?.status;
    if (prev.current === "running" && (status === "done" || status === "failed")) {
      qc.invalidateQueries({ queryKey: projectClausesKey(projectId) });
    }
    prev.current = status;
  }, [query.data?.status, projectId, qc]);

  return query;
}

// ── Base contract book (Knowledge Center → project) ──────────────────────────

export const clauseBookKey = (projectId: string) => ["clause-book", projectId] as const;

/** The Knowledge-Center book chosen as this project's base clause set. */
export function useClauseBookQuery(projectId: string) {
  return useQuery({
    queryKey: clauseBookKey(projectId),
    queryFn: () => getClauseBookApi(projectId),
    enabled: !!projectId,
  });
}

/** Pick a base book; its clauses are copied into the project's library. */
export function useSelectClauseBook(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: string) => selectClauseBookApi(projectId, bookId),
    onSuccess: (res) => {
      qc.setQueryData(clauseBookKey(projectId), res.bookId);
      qc.invalidateQueries({ queryKey: projectClausesKey(projectId) });
      // A new base book clears any prior PCC comparison state.
      qc.setQueryData(pccStatusKey(projectId), { status: "idle" });
    },
  });
}

/** Unselect the base book; its copied clauses (and the PCC work on top of them)
 * are removed from the project's library. */
export function useClearClauseBook(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearClauseBookApi(projectId),
    onSuccess: () => {
      qc.setQueryData(clauseBookKey(projectId), null);
      qc.invalidateQueries({ queryKey: projectClausesKey(projectId) });
      // Without a base book there is nothing left for a PCC comparison to show.
      qc.setQueryData(pccStatusKey(projectId), { status: "idle" });
    },
  });
}

// ── Particular Conditions (PCC) comparison ───────────────────────────────────

export const pccStatusKey = (projectId: string) => ["pcc-status", projectId] as const;

/** Upload the project's Particular Conditions; the backend compares them against
 * the base book in the background. Seeds the status query so polling starts. */
export function useUploadPcc(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadPccApi(projectId, file),
    onSuccess: (res) =>
      qc.setQueryData(pccStatusKey(projectId), {
        status: res.status,
        error: res.status === "idle" ? res.message : undefined,
      }),
  });
}

/** Polls background PCC-comparison status; when it finishes, refreshes the
 * clause list so the newly-flagged "Modified" clauses appear. */
export function usePccStatus(projectId: string) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: pccStatusKey(projectId),
    queryFn: () => getPccStatusApi(projectId),
    enabled: !!projectId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });

  const prev = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = query.data?.status;
    if (prev.current === "running" && (status === "done" || status === "failed")) {
      qc.invalidateQueries({ queryKey: projectClausesKey(projectId) });
    }
    prev.current = status;
  }, [query.data?.status, projectId, qc]);

  return query;
}
