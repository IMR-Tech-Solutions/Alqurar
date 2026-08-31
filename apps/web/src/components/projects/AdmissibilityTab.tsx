import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  HardHat,
  Loader2,
  Plus,
  Save,
  Scale,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Tabs } from "@/components/ui/Tabs";
import { ContractorAdmissibilityTab } from "@/components/projects/ContractorAdmissibilityTab";
import { DelayEventSummaryTab } from "@/components/projects/DelayEventSummaryTab";
import { apiErrorMessage } from "@/api/client";
import {
  useAdmissibility,
  useGenerateAdmissibility,
  useSaveAdmissibility,
} from "@/hooks/useAdmissibility";
import { cn } from "@/lib/utils";
import type {
  AdmissibilityClause,
  AdmissibilityClauseSource,
  AdmissibilityCriterion,
} from "@/types";

const SOURCE_META: Record<
  AdmissibilityClauseSource,
  { label: string; sel: string; badge: string; accent: string }
> = {
  book: { label: "FIDIC book", sel: "text-navy-700", badge: "bg-navy-100 text-navy-700", accent: "border-l-navy-400" },
  modified: { label: "Modified (PCC)", sel: "text-warning", badge: "bg-warning-bg text-warning", accent: "border-l-amber-400" },
  new: { label: "New clause", sel: "text-success", badge: "bg-success-bg text-success", accent: "border-l-emerald-400" },
  manual: { label: "Manual", sel: "text-muted", badge: "bg-navy-50 text-muted", accent: "border-l-border" },
};
const SOURCE_KEYS = Object.keys(SOURCE_META) as AdmissibilityClauseSource[];

const num = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
/** Round to at most 2 dp for display. */
const fmt = (n: number) => String(Math.round(n * 100) / 100);
const uid = (p: string) =>
  `${p}-${(globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2))}`;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

/** Effective weight = the criterion's % share of its clause's marks. */
const effective = (c: AdmissibilityCriterion, marks: number) => (num(c.overallWtg) / 100) * num(marks);

const SUB_TABS = [
  { id: "matrix", label: "Admissibility matrix", icon: Scale },
  { id: "contractor", label: "Contractor admissibility", icon: HardHat },
  { id: "delay-summary", label: "Delay event summary", icon: CalendarClock },
] as const;

type SubTabId = (typeof SUB_TABS)[number]["id"];

export function AdmissibilityTab({ projectId }: { projectId: string }) {
  const [view, setView] = useState<SubTabId>("matrix");

  return (
    <div className="space-y-4">
      <Tabs tabs={[...SUB_TABS]} active={view} onChange={(id) => setView(id as SubTabId)} />

      {view === "matrix" && <AdmissibilityMatrixView projectId={projectId} />}
      {view === "contractor" && <ContractorAdmissibilityTab projectId={projectId} />}
      {view === "delay-summary" && <DelayEventSummaryTab projectId={projectId} />}
    </div>
  );
}

function AdmissibilityMatrixView({ projectId }: { projectId: string }) {
  const { data, isLoading } = useAdmissibility(projectId);
  const generate = useGenerateAdmissibility(projectId);
  const save = useSaveAdmissibility(projectId);

  const [clauses, setClauses] = useState<AdmissibilityClause[]>([]);
  const [summary, setSummary] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState("");
  const syncedAt = useRef<string | null>(null);

  // Sync local editable state from the server whenever a new version arrives
  // (after generation completes or a save) — local edits never bump updatedAt,
  // so in-progress editing is not clobbered.
  useEffect(() => {
    const stamp = data?.updatedAt ?? null;
    if (data?.content && stamp !== syncedAt.current) {
      setClauses(clone(data.content.clauses ?? []));
      setSummary(data.content.summary ?? "");
      setDirty(false);
      syncedAt.current = stamp;
    }
  }, [data]);

  const isRunning = data?.status === "running" || generate.isPending;
  const genError = data?.status === "failed" ? data.error || "AI generation failed." : "";
  const hasContent = clauses.length > 0;

  const totalMarks = useMemo(() => clauses.reduce((s, c) => s + num(c.marks), 0), [clauses]);
  const marksOff = hasContent && Math.abs(totalMarks - 100) > 0.01;

  // ── mutators (immutable) ──
  const mutate = (fn: (draft: AdmissibilityClause[]) => void) => {
    setClauses((prev) => {
      const next = clone(prev);
      fn(next);
      return next;
    });
    setDirty(true);
  };
  const patchClause = (id: string, patch: Partial<AdmissibilityClause>) =>
    mutate((d) => {
      const c = d.find((x) => x.id === id);
      if (c) Object.assign(c, patch);
    });
  const patchCriterion = (clauseId: string, critId: string, patch: Partial<AdmissibilityCriterion>) =>
    mutate((d) => {
      const cr = d.find((x) => x.id === clauseId)?.criteria.find((y) => y.id === critId);
      if (cr) Object.assign(cr, patch);
    });
  const addCriterion = (clauseId: string) =>
    mutate((d) => {
      d.find((x) => x.id === clauseId)?.criteria.push({
        id: uid("acr"),
        category: "",
        subClause: "",
        description: "",
        overallWtg: 0,
      });
    });
  const removeCriterion = (clauseId: string, critId: string) =>
    mutate((d) => {
      const c = d.find((x) => x.id === clauseId);
      if (c) c.criteria = c.criteria.filter((y) => y.id !== critId);
    });
  const addClause = () =>
    mutate((d) => {
      d.push({
        id: uid("acl"),
        clauseRef: "",
        label: "New clause",
        marks: 0,
        source: "manual",
        note: "",
        criteria: [],
      });
    });
  const removeClause = (id: string) => {
    if (!window.confirm("Remove this clause group from the matrix?")) return;
    mutate((d) => {
      const i = d.findIndex((x) => x.id === id);
      if (i >= 0) d.splice(i, 1);
    });
  };

  function handleGenerate() {
    if (
      hasContent &&
      !window.confirm(
        "Regenerate the admissibility matrix with AI? This replaces the current matrix and any edits.",
      )
    )
      return;
    generate.mutate();
  }

  async function handleSave() {
    setSaveError("");
    try {
      await save.mutateAsync({ clauses, summary });
    } catch (err) {
      setSaveError(apiErrorMessage(err, "Could not save the matrix."));
    }
  }

  return (
    <div className="space-y-4">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-base font-semibold text-ink">Admissibility matrix</h3>
          <p className="text-xs text-muted mt-0.5">
            The applicable clauses split 100 marks; each clause's criteria are weighted, from the delay events and clause library. AI-generated and editable.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-primary btn-sm" onClick={handleGenerate} disabled={isRunning}>
            {isRunning ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            {isRunning ? "Generating…" : hasContent ? "Regenerate" : "Generate with AI"}
          </button>
          {hasContent && (
            <>
              <button className="btn btn-outline btn-sm" onClick={addClause}>
                <Plus className="size-4" /> Add clause
              </button>
              <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={!dirty || save.isPending}>
                {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                Save changes
              </button>
            </>
          )}
        </div>
      </div>

      {isRunning && (
        <div className="flex items-start gap-2 rounded-lg bg-navy-50/60 px-3 py-2.5 text-xs text-navy-700">
          <Sparkles className="size-4 shrink-0 mt-px text-amber-500" />
          <span>Claude is selecting the applicable clauses and weighting the admissibility criteria from the delay events and clause library.</span>
        </div>
      )}
      {genError && (
        <div className="flex items-start gap-2 rounded-lg bg-error-bg/60 px-3 py-2.5 text-xs text-error">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{genError}</span>
        </div>
      )}
      {saveError && (
        <div className="flex items-start gap-2 rounded-lg bg-error-bg/60 px-3 py-2.5 text-xs text-error">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{saveError}</span>
        </div>
      )}
      {save.isSuccess && !dirty && !saveError && (
        <div className="flex items-start gap-2 rounded-lg bg-success-bg/60 px-3 py-2.5 text-xs text-success">
          <CheckCircle2 className="size-4 shrink-0 mt-px" /> Matrix saved.
        </div>
      )}

      {isLoading ? (
        <Card className="p-10 text-center text-sm text-muted inline-flex items-center justify-center gap-2 w-full">
          <Loader2 className="size-4 animate-spin" /> Loading admissibility matrix…
        </Card>
      ) : !hasContent && isRunning ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <Loader2 className="size-6 animate-spin" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Building the admissibility matrix with AI…</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Claude is deciding which clauses govern admissibility and weighting the criteria.
          </p>
        </Card>
      ) : !hasContent ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <Scale className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">No admissibility matrix yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Generate a weighted admissibility matrix from this project's delay events and clause library — the applicable
            clauses (e.g. Clause 20, 8.4, 13) split 100 marks, each with weighted compliance criteria. You can then edit everything.
          </p>
          <button className="btn btn-primary btn-sm mt-4 inline-flex" onClick={handleGenerate} disabled={isRunning}>
            <Sparkles className="size-4" /> Generate with AI
          </button>
        </Card>
      ) : (
        <>
          {/* ── Overview cards (one per clause) ── */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {clauses.map((c) => {
              const meta = SOURCE_META[c.source] ?? SOURCE_META.manual;
              return (
                <Card key={c.id} className={cn("p-4 border-l-4", meta.accent)}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-ink truncate">{c.label || "Untitled clause"}</p>
                      <p className="text-xs text-muted mt-0.5">
                        {c.clauseRef ? `Clause ${c.clauseRef} · ` : ""}{c.criteria.length} criteria
                      </p>
                    </div>
                    <span className={cn("text-[11px] font-semibold rounded px-1.5 py-0.5 shrink-0", meta.badge)}>
                      {meta.label}
                    </span>
                  </div>
                  <p className="mt-2 text-2xl font-bold font-display tabular-nums text-ink leading-none">
                    {fmt(num(c.marks))} <span className="text-sm font-medium text-muted">marks</span>
                  </p>
                </Card>
              );
            })}
          </div>

          {/* Total-marks banner */}
          <div
            className={cn(
              "flex items-center justify-between gap-3 rounded-lg px-4 py-2.5 text-sm",
              marksOff ? "bg-warning-bg/60 text-warning" : "bg-navy-50/60 text-navy-700",
            )}
          >
            <span className="font-medium inline-flex items-center gap-1.5">
              <Scale className="size-4" /> Total marks{" "}
              <span className="font-bold tabular-nums">{fmt(totalMarks)}</span> / 100
            </span>
            {marksOff && (
              <span className="text-xs inline-flex items-center gap-1">
                <AlertTriangle className="size-3.5" /> Clause marks should sum to 100.
              </span>
            )}
          </div>

          {summary && <p className="text-sm text-muted leading-relaxed">{summary}</p>}

          {clauses.map((clause) => (
            <ClauseCard
              key={clause.id}
              clause={clause}
              onPatch={(patch) => patchClause(clause.id, patch)}
              onPatchCriterion={(critId, patch) => patchCriterion(clause.id, critId, patch)}
              onAddCriterion={() => addCriterion(clause.id)}
              onRemoveCriterion={(critId) => removeCriterion(clause.id, critId)}
              onRemove={() => removeClause(clause.id)}
            />
          ))}
        </>
      )}
    </div>
  );
}

function ClauseCard({
  clause,
  onPatch,
  onPatchCriterion,
  onAddCriterion,
  onRemoveCriterion,
  onRemove,
}: {
  clause: AdmissibilityClause;
  onPatch: (patch: Partial<AdmissibilityClause>) => void;
  onPatchCriterion: (critId: string, patch: Partial<AdmissibilityCriterion>) => void;
  onAddCriterion: () => void;
  onRemoveCriterion: (critId: string) => void;
  onRemove: () => void;
}) {
  const marks = num(clause.marks);
  const wtgSum = clause.criteria.reduce((s, c) => s + num(c.overallWtg), 0);
  const effSum = clause.criteria.reduce((s, c) => s + effective(c, marks), 0);
  const wtgOff = clause.criteria.length > 0 && Math.abs(wtgSum - 100) > 0.01;
  const meta = SOURCE_META[clause.source] ?? SOURCE_META.manual;

  return (
    <Card className={cn("overflow-hidden border-l-4", meta.accent)}>
      {/* Clause header */}
      <div className="px-4 py-3 bg-navy-50/40 border-b border-border">
        <div className="flex items-center gap-2 flex-wrap">
          <input
            className="cell font-semibold text-ink flex-1 min-w-[180px] text-[15px]"
            value={clause.label}
            placeholder="Clause label (e.g. Clause 20 related)"
            onChange={(e) => onPatch({ label: e.target.value })}
          />
          <label className="text-xs text-muted inline-flex items-center gap-1">
            Ref
            <input
              className="cell w-20 text-center"
              value={clause.clauseRef}
              placeholder="20"
              onChange={(e) => onPatch({ clauseRef: e.target.value })}
            />
          </label>
          <select
            className={cn("cell w-36 font-medium", meta.sel)}
            value={clause.source}
            onChange={(e) => onPatch({ source: e.target.value as AdmissibilityClauseSource })}
          >
            {SOURCE_KEYS.map((k) => (
              <option key={k} value={k}>{SOURCE_META[k].label}</option>
            ))}
          </select>
          <label className="inline-flex items-center gap-1.5 rounded-lg bg-navy-100 px-2 py-1 text-xs font-medium text-navy-700">
            Marks
            <input
              type="number"
              className="w-14 bg-transparent text-center font-bold tabular-nums text-navy-900 outline-none"
              value={clause.marks}
              onChange={(e) => onPatch({ marks: num(e.target.value) })}
            />
          </label>
          <button
            className="btn btn-ghost btn-sm px-2 text-error"
            onClick={onRemove}
            aria-label="Remove clause group"
            title="Remove clause group"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
        {(clause.source === "modified" || clause.source === "new" || clause.note) && (
          <input
            className="cell w-full mt-2 text-xs"
            value={clause.note}
            placeholder={clause.source === "modified" ? "What the Particular Conditions change…" : "Note…"}
            onChange={(e) => onPatch({ note: e.target.value })}
          />
        )}
      </div>

      {/* Criteria table */}
      <div className="overflow-x-auto scroll-thin">
        <table className="w-full text-sm border-collapse min-w-[720px]">
          <thead>
            <tr className="text-left text-xs font-semibold text-muted uppercase tracking-wide border-b border-border">
              <th className="px-3 py-2 w-44">Category</th>
              <th className="px-3 py-2 w-28">Sub-clause</th>
              <th className="px-3 py-2">Criteria</th>
              <th className="px-3 py-2 w-28 text-right bg-info-bg/40">Overall Wtg</th>
              <th className="px-3 py-2 w-28 text-right bg-info-bg/40">Effective Wtg</th>
              <th className="px-3 py-2 w-10" />
            </tr>
          </thead>
          <tbody>
            {clause.criteria.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-center text-sm text-muted">
                  No criteria. Use “Add criterion” below.
                </td>
              </tr>
            )}
            {clause.criteria.map((c, i) => {
              const prev = clause.criteria[i - 1];
              const groupStart = !prev || prev.category !== c.category;
              return (
                <tr
                  key={c.id}
                  className={cn(
                    "align-top border-b border-border/60",
                    groupStart && i > 0 && "border-t-2 border-t-navy-100",
                  )}
                >
                  <td className="px-3 py-1.5">
                    <input
                      className={cn("cell w-full", groupStart ? "font-medium text-ink" : "text-faint")}
                      value={c.category}
                      placeholder="Delay Notice"
                      onChange={(e) => onPatchCriterion(c.id, { category: e.target.value })}
                    />
                  </td>
                  <td className="px-3 py-1.5">
                    <input className="cell w-full tabular-nums" value={c.subClause} placeholder="20.1A(1)" onChange={(e) => onPatchCriterion(c.id, { subClause: e.target.value })} />
                  </td>
                  <td className="px-3 py-1.5">
                    <input className="cell w-full" value={c.description} placeholder="Notice within 14 days" onChange={(e) => onPatchCriterion(c.id, { description: e.target.value })} />
                  </td>
                  <td className="px-3 py-1.5 bg-info-bg/25">
                    <div className="flex items-center justify-end gap-1">
                      <input
                        type="number"
                        className="cell w-16 text-right tabular-nums"
                        value={c.overallWtg}
                        onChange={(e) => onPatchCriterion(c.id, { overallWtg: num(e.target.value) })}
                      />
                      <span className="text-muted">%</span>
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink font-medium bg-info-bg/25">
                    {fmt(effective(c, marks))}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      className="btn btn-ghost btn-sm px-1.5 text-error"
                      onClick={() => onRemoveCriterion(c.id)}
                      aria-label="Remove criterion"
                      title="Remove"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-border font-semibold text-ink">
              <td className="px-3 py-2" colSpan={3}>
                <button className="btn btn-ghost btn-sm px-1 text-navy-700" onClick={onAddCriterion}>
                  <Plus className="size-3.5" /> Add criterion
                </button>
              </td>
              <td className={cn("px-3 py-2 text-right tabular-nums bg-info-bg/40", wtgOff && "text-warning")}>
                {fmt(wtgSum)}%{wtgOff && <span title="Should total 100%"> ⚠</span>}
              </td>
              <td className="px-3 py-2 text-right tabular-nums bg-info-bg/40">{fmt(effSum)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}
