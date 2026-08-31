import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  HardHat,
  Loader2,
  Save,
  Sparkles,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { apiErrorMessage } from "@/api/client";
import {
  useAdmissibility,
  useContractorAdmissibility,
  useGenerateContractorAdmissibility,
  useSaveContractorAdmissibility,
} from "@/hooks/useAdmissibility";
import { cn } from "@/lib/utils";
import {
  ADMISSIBILITY_BANDS,
  bandFor,
  fmt,
  num,
  scoreOf,
  totalsFor,
  type EventTotals,
} from "@/lib/admissibility";
import type {
  ContractorAdmissibilityContent,
  ContractorCriterion,
  ContractorEventScore,
  ContractorRow,
  YesNo,
} from "@/types";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export function ContractorAdmissibilityTab({ projectId }: { projectId: string }) {
  const { data: matrix } = useAdmissibility(projectId);
  const { data, isLoading } = useContractorAdmissibility(projectId);
  const generate = useGenerateContractorAdmissibility(projectId);
  const save = useSaveContractorAdmissibility(projectId);

  const [content, setContent] = useState<ContractorAdmissibilityContent | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState("");
  /** "" = every event's columns; otherwise the single eventId being reviewed. */
  const [focus, setFocus] = useState("");
  const syncedAt = useRef<string | null>(null);

  // Sync local editable state from the server whenever a new version arrives
  // (after generation completes or a save) — local edits never bump updatedAt,
  // so in-progress editing is not clobbered.
  useEffect(() => {
    const stamp = data?.updatedAt ?? null;
    if (data?.content && stamp !== syncedAt.current) {
      setContent(clone(data.content));
      setDirty(false);
      syncedAt.current = stamp;
    }
  }, [data]);

  const isRunning = data?.status === "running" || generate.isPending;
  const genError = data?.status === "failed" ? data.error || "AI scoring failed." : "";
  const criteria = useMemo(() => content?.criteria ?? [], [content]);
  const allEvents = useMemo(() => content?.events ?? [], [content]);
  const hasContent = criteria.length > 0 && allEvents.length > 0;
  const hasMatrix = (matrix?.content?.clauses?.length ?? 0) > 0;
  // The matrix has been regenerated or re-weighted since this scoring was built.
  const staleMatrix =
    hasContent && !!matrix?.updatedAt && content?.matrixUpdatedAt !== matrix.updatedAt;

  const shown = useMemo(
    () => (focus ? allEvents.filter((e) => e.eventId === focus) : allEvents),
    [allEvents, focus],
  );

  const patchRow = (eventId: string, criterionId: string, patch: Partial<ContractorRow>) => {
    setContent((prev) => {
      if (!prev) return prev;
      const next = clone(prev);
      const row = next.events
        .find((e) => e.eventId === eventId)
        ?.rows.find((r) => r.criterionId === criterionId);
      if (row) {
        Object.assign(row, patch);
        // A requirement that doesn't apply can't be complied with.
        if (row.applicable === "N") row.complied = "N";
      }
      return next;
    });
    setDirty(true);
  };

  const patchEvent = (eventId: string, patch: Partial<ContractorEventScore>) => {
    setContent((prev) => {
      if (!prev) return prev;
      const next = clone(prev);
      const ev = next.events.find((e) => e.eventId === eventId);
      if (ev) Object.assign(ev, patch);
      return next;
    });
    setDirty(true);
  };

  function handleGenerate() {
    if (
      hasContent &&
      !window.confirm(
        "Re-score every delay event with AI? This replaces the current scoring and any edits.",
      )
    )
      return;
    generate.mutate();
  }

  async function handleSave() {
    if (!content) return;
    setSaveError("");
    try {
      await save.mutateAsync(content);
    } catch (err) {
      setSaveError(apiErrorMessage(err, "Could not save the scoring."));
    }
  }

  return (
    <div className="space-y-4">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-base font-semibold text-ink">Contractor admissibility</h3>
          <p className="text-xs text-muted mt-0.5">
            Every criterion in the admissibility matrix, scored against each delay event — is the clause
            applicable, did the Contractor comply, and what evidences it. AI-generated and editable.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-primary btn-sm"
            onClick={handleGenerate}
            disabled={isRunning || !hasMatrix}
            title={hasMatrix ? undefined : "Generate the admissibility matrix first"}
          >
            {isRunning ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            {isRunning ? "Scoring…" : hasContent ? "Re-score" : "Score with AI"}
          </button>
          {hasContent && (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={!dirty || save.isPending}
            >
              {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              Save changes
            </button>
          )}
        </div>
      </div>

      {isRunning && (
        <div className="flex items-start gap-2 rounded-lg bg-navy-50/60 px-3 py-2.5 text-xs text-navy-700">
          <Sparkles className="size-4 shrink-0 mt-px text-amber-500" />
          <span>
            Claude is reading the data room and scoring each delay event against every criterion in the
            matrix.
            {data?.progress ? ` ${data.progress.done} of ${data.progress.total} events scored.` : ""} This
            can take a few minutes.
          </span>
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
      {staleMatrix && !isRunning && (
        <div className="flex items-start gap-2 rounded-lg bg-warning-bg/60 px-3 py-2.5 text-xs text-warning">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>
            The admissibility matrix has changed since this scoring was built — the weightages below are
            the ones it was scored against. Re-score to pick up the current matrix.
          </span>
        </div>
      )}
      {save.isSuccess && !dirty && !saveError && (
        <div className="flex items-start gap-2 rounded-lg bg-success-bg/60 px-3 py-2.5 text-xs text-success">
          <CheckCircle2 className="size-4 shrink-0 mt-px" /> Scoring saved.
        </div>
      )}

      {isLoading ? (
        <Card className="p-10 text-center text-sm text-muted inline-flex items-center justify-center gap-2 w-full">
          <Loader2 className="size-4 animate-spin" /> Loading contractor admissibility…
        </Card>
      ) : !hasContent && isRunning ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <Loader2 className="size-6 animate-spin" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Scoring the delay events with AI…</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Claude is checking each event's correspondence against every admissibility criterion.
          </p>
        </Card>
      ) : !hasContent ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <HardHat className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Not scored yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            {hasMatrix
              ? "Score every delay event against the admissibility matrix — each criterion is judged applicable or not, complied or not, and evidenced from the data room, giving each event an admissibility percentage and category."
              : "Generate the admissibility matrix first, on the Admissibility matrix tab — the contractor scoring is built from its criteria."}
          </p>
          <button
            className="btn btn-primary btn-sm mt-4 inline-flex"
            onClick={handleGenerate}
            disabled={isRunning || !hasMatrix}
          >
            <Sparkles className="size-4" /> Score with AI
          </button>
        </Card>
      ) : (
        <>
          {/* ── Per-event result cards ── */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {allEvents.map((ev) => {
              const t = totalsFor(ev, criteria);
              const band = bandFor(t.pct);
              return (
                <Card
                  key={ev.eventId}
                  className={cn(
                    "p-4 cursor-pointer transition-shadow hover:shadow-sm",
                    focus === ev.eventId && "ring-2 ring-navy-300",
                  )}
                  onClick={() => setFocus(focus === ev.eventId ? "" : ev.eventId)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-ink truncate">
                        {ev.eventRef}
                        {ev.title ? ` — ${ev.title}` : ""}
                      </p>
                      <p className="text-xs text-muted mt-0.5 tabular-nums">
                        {fmt(t.achieved)} / {fmt(t.applicableWeight)} applicable weight
                      </p>
                    </div>
                    <span
                      className={cn(
                        "text-[11px] font-semibold rounded px-1.5 py-0.5 shrink-0",
                        band.badge,
                      )}
                    >
                      {band.label}
                    </span>
                  </div>
                  <p className="mt-2 text-2xl font-bold font-display tabular-nums text-ink leading-none">
                    {fmt(t.pct)}
                    <span className="text-sm font-medium text-muted"> % admissible</span>
                  </p>
                </Card>
              );
            })}
          </div>

          {/* ── Band legend + which events the sheet shows ── */}
          <div className="flex items-center justify-between gap-3 flex-wrap rounded-lg bg-navy-50/60 px-4 py-2.5">
            <div className="flex items-center gap-3 flex-wrap text-xs">
              <span className="font-medium text-navy-700">Admissibility category</span>
              {ADMISSIBILITY_BANDS.map((b, i) => (
                <span
                  key={b.label}
                  className={cn("font-semibold rounded px-1.5 py-0.5", b.badge)}
                >
                  {b.label}
                  {/* The catch-all band has no floor of its own, so it is labelled
                      from the band above it — hardcoding a number here would keep
                      claiming "<50%" after someone moved the band that defines it. */}
                  {b.min >= 0 ? ` ${b.min}%+` : ` <${ADMISSIBILITY_BANDS[i - 1].min}%`}
                </span>
              ))}
            </div>
            <label className="text-xs text-muted inline-flex items-center gap-1.5">
              Showing
              <select
                className="cell w-56 font-medium text-ink"
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
              >
                <option value="">All delay events ({allEvents.length})</option>
                {allEvents.map((ev) => (
                  <option key={ev.eventId} value={ev.eventId}>
                    {ev.eventRef}
                    {ev.title ? ` — ${ev.title}` : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <ScoringSheet
            criteria={criteria}
            events={shown}
            onPatchRow={patchRow}
            onPatchEvent={patchEvent}
          />
        </>
      )}
    </div>
  );
}

/** Column widths in px. The sheet uses a FIXED table layout so these are exact —
 *  the sticky left-hand offsets below are derived from them, so the identity
 *  columns stay aligned however long the requirement text runs. */
const W = {
  sl: 52,
  item: 150,
  clause: 90,
  requirement: 300,
  weightage: 90,
  applicable: 74,
  complied: 74,
  score: 70,
  evidence: 240,
} as const;

/** Left offset of each of the four sticky identity columns. */
const STICKY_LEFT = [0, W.sl, W.sl + W.item, W.sl + W.item + W.clause];
const EVENT_W = W.applicable + W.complied + W.score + W.evidence;

/** `span` is how many identity columns the cell covers — the cell that reaches
 *  the end of the sticky block carries the divider, so the line stays unbroken
 *  between the body rows and the footer's spanning totals. */
const stickyCell = (i: number, bg: string, span = 1) =>
  cn("sticky z-10", bg, i + span === STICKY_LEFT.length && "border-r border-border");

function ScoringSheet({
  criteria,
  events,
  onPatchRow,
  onPatchEvent,
}: {
  criteria: ContractorCriterion[];
  events: ContractorEventScore[];
  onPatchRow: (eventId: string, criterionId: string, patch: Partial<ContractorRow>) => void;
  onPatchEvent: (eventId: string, patch: Partial<ContractorEventScore>) => void;
}) {
  // criterionId -> row, per event, so every cell is a direct lookup.
  const rowsByEvent = useMemo(
    () => new Map(events.map((e) => [e.eventId, new Map(e.rows.map((r) => [r.criterionId, r]))])),
    [events],
  );
  const totals = useMemo(
    () => new Map(events.map((e) => [e.eventId, totalsFor(e, criteria)])),
    [events, criteria],
  );
  const tableWidth =
    W.sl + W.item + W.clause + W.requirement + W.weightage + events.length * EVENT_W;

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto scroll-thin">
        <table className="text-sm border-collapse table-fixed" style={{ width: tableWidth }}>
          <colgroup>
            <col style={{ width: W.sl }} />
            <col style={{ width: W.item }} />
            <col style={{ width: W.clause }} />
            <col style={{ width: W.requirement }} />
            <col style={{ width: W.weightage }} />
            {events.map((ev) => (
              <Fragment key={ev.eventId}>
                <col style={{ width: W.applicable }} />
                <col style={{ width: W.complied }} />
                <col style={{ width: W.score }} />
                <col style={{ width: W.evidence }} />
              </Fragment>
            ))}
          </colgroup>
          <thead>
            <tr className="text-xs font-semibold text-muted uppercase tracking-wide bg-navy-50">
              {(["Sl.no", "Item", "Clause", "Requirement"] as const).map((label, i) => (
                <th
                  key={label}
                  rowSpan={2}
                  style={{ left: STICKY_LEFT[i] }}
                  className={cn(
                    "px-3 py-2 align-bottom",
                    i === 0 ? "text-center" : "text-left",
                    stickyCell(i, "bg-navy-50"),
                  )}
                >
                  {label}
                </th>
              ))}
              <th className="px-3 py-2 text-right align-bottom" rowSpan={2}>
                Weightage
              </th>
              {events.map((ev) => (
                <th
                  key={ev.eventId}
                  className="px-3 py-2 text-center border-l-2 border-border text-navy-700 normal-case truncate"
                  colSpan={4}
                  title={`${ev.eventRef}${ev.title ? ` — ${ev.title}` : ""}`}
                >
                  {ev.eventRef}
                  {ev.title ? ` — ${ev.title}` : ""}
                </th>
              ))}
            </tr>
            <tr className="text-[11px] font-semibold text-muted uppercase tracking-wide bg-navy-50 border-b border-border">
              {events.map((ev) => (
                <Fragment key={ev.eventId}>
                  <th className="px-2 py-1.5 text-center leading-tight border-l-2 border-border">
                    Is the clause applicable
                  </th>
                  <th className="px-2 py-1.5 text-center leading-tight">Complied</th>
                  <th className="px-2 py-1.5 text-right">Score</th>
                  <th className="px-3 py-1.5 text-left">Evidence Ref / Remarks</th>
                </Fragment>
              ))}
            </tr>
          </thead>
          <tbody>
            {criteria.map((c, i) => {
              const prev = criteria[i - 1];
              const groupStart = !prev || prev.category !== c.category;
              return (
                <tr
                  key={c.id}
                  className={cn(
                    "align-top border-b border-border/60",
                    groupStart && i > 0 && "border-t-2 border-t-navy-100",
                  )}
                >
                  <td
                    style={{ left: STICKY_LEFT[0] }}
                    className={cn(
                      "px-2 py-2 text-center tabular-nums text-muted",
                      stickyCell(0, "bg-white"),
                    )}
                  >
                    {i + 1}
                  </td>
                  <td
                    style={{ left: STICKY_LEFT[1] }}
                    className={cn(
                      "px-3 py-2 break-words",
                      groupStart ? "font-medium text-ink" : "text-faint",
                      stickyCell(1, "bg-white"),
                    )}
                  >
                    {c.category}
                  </td>
                  <td
                    style={{ left: STICKY_LEFT[2] }}
                    className={cn(
                      "px-3 py-2 tabular-nums text-ink break-words",
                      stickyCell(2, "bg-white"),
                    )}
                  >
                    {c.subClause}
                  </td>
                  <td
                    style={{ left: STICKY_LEFT[3] }}
                    className={cn("px-3 py-2 text-ink break-words", stickyCell(3, "bg-white"))}
                  >
                    {c.description}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums font-medium text-ink bg-info-bg/25">
                    {fmt(num(c.weightage))}
                  </td>
                  {events.map((ev) => {
                    const row = rowsByEvent.get(ev.eventId)?.get(c.id);
                    const score = scoreOf(row, c);
                    return (
                      <Fragment key={ev.eventId}>
                        <td className="px-2 py-2 text-center border-l-2 border-border">
                          <YesNoCell
                            value={row?.applicable ?? "N"}
                            onChange={(v) => onPatchRow(ev.eventId, c.id, { applicable: v })}
                          />
                        </td>
                        <td className="px-2 py-2 text-center">
                          <YesNoCell
                            value={row?.complied ?? "N"}
                            disabled={row?.applicable !== "Y"}
                            onChange={(v) => onPatchRow(ev.eventId, c.id, { complied: v })}
                          />
                        </td>
                        <td
                          className={cn(
                            "px-2 py-2 text-right tabular-nums font-medium",
                            score > 0 ? "text-ink" : "text-faint",
                          )}
                        >
                          {fmt(score)}
                        </td>
                        <td className="px-2 py-1.5">
                          <input
                            className="cell w-full text-xs"
                            value={row?.evidence ?? ""}
                            placeholder="Letter ref & date…"
                            onChange={(e) => onPatchRow(ev.eventId, c.id, { evidence: e.target.value })}
                          />
                        </td>
                      </Fragment>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
          <tfoot className="text-sm">
            {/* Total weightage across the matrix — sums to the matrix's 100 marks. */}
            <tr className="border-t border-border font-semibold text-ink bg-navy-50">
              <td
                style={{ left: STICKY_LEFT[0] }}
                className={cn("px-3 py-2", stickyCell(0, "bg-navy-50"))}
              />
              <td
                style={{ left: STICKY_LEFT[1] }}
                className={cn("px-3 py-2", stickyCell(1, "bg-navy-50", 3))}
                colSpan={3}
              >
                Total weightage
              </td>
              <td className="px-3 py-2 text-right tabular-nums bg-info-bg/40">
                {fmt(criteria.reduce((s, c) => s + num(c.weightage), 0))}
              </td>
              {events.map((ev) => (
                <td key={ev.eventId} className="border-l-2 border-border" colSpan={4} />
              ))}
            </tr>
            {(
              [
                ["Applicable Weight", (t: EventTotals) => fmt(t.applicableWeight)],
                ["Achieved Score", (t: EventTotals) => fmt(t.achieved)],
                ["Admissibility %", (t: EventTotals) => `${fmt(t.pct)}%`],
              ] as const
            ).map(([label, value]) => (
              <tr key={label} className="border-t border-border text-ink bg-white">
                <td
                  style={{ left: STICKY_LEFT[0] }}
                  className={cn("px-3 py-2", stickyCell(0, "bg-white"))}
                />
                <td
                  style={{ left: STICKY_LEFT[1] }}
                  className={cn("px-3 py-2 font-semibold", stickyCell(1, "bg-white", 3))}
                  colSpan={3}
                >
                  {label}
                </td>
                <td />
                {events.map((ev) => (
                  <td
                    key={ev.eventId}
                    className="px-3 py-2 text-center font-bold tabular-nums border-l-2 border-border"
                    colSpan={4}
                  >
                    {value(totals.get(ev.eventId)!)}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-border text-ink bg-white">
              <td
                style={{ left: STICKY_LEFT[0] }}
                className={cn("px-3 py-2", stickyCell(0, "bg-white"))}
              />
              <td
                style={{ left: STICKY_LEFT[1] }}
                className={cn("px-3 py-2 font-semibold", stickyCell(1, "bg-white", 3))}
                colSpan={3}
              >
                Category
              </td>
              <td />
              {events.map((ev) => {
                const band = bandFor(totals.get(ev.eventId)!.pct);
                return (
                  <td
                    key={ev.eventId}
                    className={cn(
                      "px-3 py-2 text-center font-bold border-l-2 border-border",
                      band.text,
                    )}
                    colSpan={4}
                  >
                    {band.label}
                  </td>
                );
              })}
            </tr>
            <tr className="border-t border-border align-top bg-white">
              <td
                style={{ left: STICKY_LEFT[0] }}
                className={cn("px-3 py-2", stickyCell(0, "bg-white"))}
              />
              <td
                style={{ left: STICKY_LEFT[1] }}
                className={cn("px-3 py-2 font-semibold text-ink", stickyCell(1, "bg-white", 3))}
                colSpan={3}
              >
                Remarks
              </td>
              <td />
              {events.map((ev) => (
                <td key={ev.eventId} className="px-2 py-1.5 border-l-2 border-border" colSpan={4}>
                  <input
                    className="cell w-full text-xs"
                    value={ev.remarks}
                    placeholder="Overall compliance position for this event…"
                    onChange={(e) => onPatchEvent(ev.eventId, { remarks: e.target.value })}
                  />
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}

/** The sheet's Y/N cell — colour-coded, and editable like the rest of the matrix. */
function YesNoCell({
  value,
  disabled,
  onChange,
}: {
  value: YesNo;
  disabled?: boolean;
  onChange: (v: YesNo) => void;
}) {
  return (
    <select
      className={cn(
        "cell w-14 text-center font-semibold",
        value === "Y" ? "text-success" : "text-error",
        disabled && "opacity-40",
      )}
      value={value}
      disabled={disabled}
      title={disabled ? "Not applicable to this event" : undefined}
      onChange={(e) => onChange(e.target.value as YesNo)}
    >
      <option value="Y">Y</option>
      <option value="N">N</option>
    </select>
  );
}
