import { api } from "./client";

/**
 * What a statement is: documentary fact, one party's asserted case, the
 * analyst's own inference, a gap in the record, or a concluded position. Keeping
 * these apart is the point of the claim — an inference presented as fact is the
 * failure mode that gets a submission rejected.
 */
export type ClaimStatementLabel =
  | "Fact"
  | "Contractor's position"
  | "Engineer / Employer's position"
  | "Analysis"
  | "Missing evidence"
  | "Assessment";

/**
 * One piece of content inside a claim section. Registers and contract
 * particulars come back as tables; `evidence` lists the source documents a
 * passage rests on, so any assertion can be traced back to the data room.
 */
export type ClaimBlock =
  | { type: "paragraph"; text?: string }
  | { type: "bullets"; items?: string[] }
  | { type: "statement"; label?: ClaimStatementLabel; text?: string }
  | { type: "evidence"; items?: string[] }
  | { type: "table"; caption?: string; columns?: string[]; rows?: string[][] };

/** A lettered part within a delay event (e.g. "C. Chronology"). */
export interface ClaimPart {
  number: string;
  heading: string;
  blocks: ClaimBlock[];
}

/** A numbered sub-heading within a section (e.g. "2.3 Summary of Relief Sought"). */
export interface ClaimSubsection {
  number: string;
  heading: string;
  blocks: ClaimBlock[];
  /** Populated for delay events, which follow the A–K template. */
  parts?: ClaimPart[];
}

/** A top-level numbered section of the generated EOT claim document. */
export interface ClaimSection {
  number: string;
  heading: string;
  blocks: ClaimBlock[];
  subsections?: ClaimSubsection[];
}

/** The generated claim document itself. */
export interface ClaimContent {
  title: string;
  reference: string;
  sections: ClaimSection[];
}

/** The generated EOT claim document + its generation status. */
export interface Proposal {
  projectId: string;
  content: ClaimContent | null;
  model: string | null;
  status: "" | "running" | "done" | "failed";
  error: string | null;
  updatedAt: string | null;
}

/** Fetch the project's generated EOT claim document (and status). */
export async function getProposalApi(projectId: string): Promise<Proposal> {
  const { data } = await api.get(`/proposals/project/${projectId}`);
  return data;
}

/** Queue AI generation of the EOT claim document; returns immediately (poll GET). */
export async function generateProposalApi(projectId: string): Promise<{ status: string }> {
  const { data } = await api.post(`/proposals/project/${projectId}/generate`);
  return data;
}
