import { api } from "./client";
import type { ClauseRef } from "@/types";

/**
 * Raw clause row from the per-project `project_clauses` table. The card/table UI
 * works in terms of {@link ClauseRef} (book/clause/title/summary), so responses
 * are mapped to that shape here.
 */
interface ProjectClauseRow {
  id: string;
  projectId: string;
  contract_standard: string;
  clause_number: string;
  clause_title: string;
  clause_description: string;
  tags: string[];
  source?: string;
  modified?: boolean;
  modification_note?: string | null;
  base_description?: string | null;
  interpretation?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectClauseCreatePayload {
  contract_standard: string;
  clause_number: string;
  clause_title: string;
  clause_description?: string;
  tags?: string[];
}

export type ProjectClauseUpdatePayload = Partial<ProjectClauseCreatePayload>;

export interface ClauseExtractStatus {
  status: "idle" | "running" | "done" | "failed";
  error?: string;
  count?: number;
  /** PCC comparison: base clauses amended and brand-new clauses added. */
  modified?: number;
  added?: number;
}

export interface ContractExtractResult {
  stored: boolean;
  documentId: string;
  status: ClauseExtractStatus["status"];
  message: string;
}

function toClauseRef(row: ProjectClauseRow): ClauseRef {
  return {
    id: row.id,
    book: row.contract_standard,
    clause: row.clause_number,
    title: row.clause_title,
    summary: row.clause_description,
    tags: row.tags ?? [],
    source: row.source ?? "manual",
    modified: row.modified ?? false,
    modificationNote: row.modification_note ?? undefined,
    baseDescription: row.base_description ?? undefined,
    interpretation: row.interpretation ?? undefined,
  };
}

export async function listProjectClausesApi(projectId: string): Promise<ClauseRef[]> {
  const { data } = await api.get<ProjectClauseRow[]>(`/projects/${projectId}/clauses`);
  return data.map(toClauseRef);
}

export async function createProjectClauseApi(
  projectId: string,
  payload: ProjectClauseCreatePayload,
): Promise<ClauseRef> {
  const { data } = await api.post<ProjectClauseRow>(`/projects/${projectId}/clauses`, payload);
  return toClauseRef(data);
}

export async function updateProjectClauseApi(
  projectId: string,
  id: string,
  patch: ProjectClauseUpdatePayload,
): Promise<ClauseRef> {
  const { data } = await api.patch<ProjectClauseRow>(`/projects/${projectId}/clauses/${id}`, patch);
  return toClauseRef(data);
}

export async function deleteProjectClauseApi(projectId: string, id: string): Promise<void> {
  await api.delete(`/projects/${projectId}/clauses/${id}`);
}

/** Upload the project's contract; the backend extracts its clauses with AI in
 * the background. Returns quickly — poll {@link getClauseExtractStatusApi}. */
export async function extractProjectClausesApi(
  projectId: string,
  file: File,
): Promise<ContractExtractResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<ContractExtractResult>(
    `/projects/${projectId}/clauses/extract`,
    form,
    { headers: { "Content-Type": "multipart/form-data" }, timeout: 60_000 },
  );
  return data;
}

/** Current state of background clause extraction for a project. */
export async function getClauseExtractStatusApi(projectId: string): Promise<ClauseExtractStatus> {
  const { data } = await api.get<ClauseExtractStatus>(`/projects/${projectId}/clauses/extract-status`);
  return data;
}

/** The Knowledge-Center book chosen as this project's base clause set (or null). */
export async function getClauseBookApi(projectId: string): Promise<string | null> {
  const { data } = await api.get<{ bookId: string | null }>(`/projects/${projectId}/clauses/book`);
  return data.bookId;
}

/** Pick a base contract book; its clauses are copied into the project's library. */
export async function selectClauseBookApi(
  projectId: string,
  bookId: string,
): Promise<{ bookId: string; count: number }> {
  const { data } = await api.post<{ bookId: string; count: number }>(
    `/projects/${projectId}/clauses/book`,
    { bookId },
  );
  return data;
}

/** Unselect the base contract book — its copied clauses (and anything the PCC
 * comparison added or amended on top) are removed from the project's library. */
export async function clearClauseBookApi(
  projectId: string,
): Promise<{ bookId: null; removed: number }> {
  const { data } = await api.delete<{ bookId: null; removed: number }>(
    `/projects/${projectId}/clauses/book`,
  );
  return data;
}

/** Upload the project's Particular Conditions; the backend compares them against
 * the base book's clauses in the background. Poll {@link getPccStatusApi}. */
export async function uploadPccApi(
  projectId: string,
  file: File,
): Promise<ContractExtractResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<ContractExtractResult>(
    `/projects/${projectId}/clauses/pcc`,
    form,
    // A PCC can be a large (scanned) file — the comparison itself runs in the
    // background, but the upload of the bytes needs a generous timeout.
    { headers: { "Content-Type": "multipart/form-data" }, timeout: 120_000 },
  );
  return data;
}

/** Current state of the background PCC comparison for a project. */
export async function getPccStatusApi(projectId: string): Promise<ClauseExtractStatus> {
  const { data } = await api.get<ClauseExtractStatus>(`/projects/${projectId}/clauses/pcc-status`);
  return data;
}
