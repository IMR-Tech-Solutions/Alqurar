import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Loader2,
  Save,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { apiErrorMessage } from "@/api/client";
import {
  useContractorAdmissibility,
  useSaveContractorAdmissibility,
} from "@/hooks/useAdmissibility";
import { useDelayEvents } from "@/hooks/useDelayEvents";
import {
  ADMISSIBILITY_BANDS,
  bandFor,
  fmt,
  totalsFor,
  type EventTotals,
} from "@/lib/admissibility";
import { cn, formatDate } from "@/lib/utils";
import type {
  ContractorAdmissibilityContent,
  ContractorEventScore,
  ProjectDelayEvent,
} from "@/types";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

type SortKey = "event" | "score" | "date";

const SORTS: { id: SortKey; label: string }[] = [
  { id: "event", label: "Delay event" },
  { id: "score", label: "Admissibility (highest first)" },
  { id: "date", label: "Start date" },
];

/** One consolidated line: the scoring joined to the delay event it came from. */
interface SummaryLine {
  scored: ContractorEventScore;
  event?: ProjectDelayEvent;
  totals: EventTotals;
  /** Does the event bite on the completion date? */
  timeImpact: boolean;
  startDate: string;
}

export function DelayEventSummaryTab({ projectId }: { projectId: string }) {
  const { data, isLoading } = useContractorAdmissibility(projectId);
  const { data: events = [] } = useDelayEvents(projectId);
  const save = useSaveContractorAdmissibility(projectId);

  const [content, setContent] = useState<ContractorAdmissibilityContent | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [sort, setSort] = useState<SortKey>("event");
  const syncedAt = useRef<string | null>(null);

  // Remarks are the same field the Contractor admissibility sheet edits, so the
  // two views never drift — sync from the server the same way that tab does.
  useEffect(() => {
    const stamp = data?.updatedAt ?? null;
    if (data?.content && stamp !== syncedAt.current) {
      setContent(clone(data.content));
      setDirty(false);
      syncedAt.current = stamp;
    }
  }, [data]);

  const criteria = useMemo(() => content?.criteria ?? [], [content]);
  const scored = useMemo(() => content?.events ?? [], [content]);
  const hasContent = criteria.length > 0 && scored.length > 0;

  const lines = useMemo<SummaryLine[]>(() => {
    const byId = new Map(events.map((e) => [e.id, e]));
    const byRef = new Map(events.map((e) => [e.ref, e]));
    const rows = scored.map((s) => {
      const event = byId.get(s.eventId) ?? byRef.get(s.eventRef);
      return {
        scored: s,
        event,
        totals: totalsFor(s, criteria),
        timeImpact: !!event && (event.criticalPath || event.daysImpact > 0),
        startDate: event?.startDate ?? "",
      };
    });
    const collator = new Intl.Collator(undefined, { numeric: true });
    return rows.sort((a, b) => {
      if (sort === "score") return b.totals.pct - a.totals.pct;
      if (sort === "date") return (a.startDate || "￿").localeCompare(b.startDate || "￿");
      return collator.compare(a.scored.eventRef, b.scored.eventRef);
    });
  }, [scored, events, criteria, sort]);

  /** How many events landed in each band — the consolidated read. */
  const bandCounts = useMemo(
    () =>
      ADMISSIBILITY_BANDS.map((b) => ({
        band: b,
        count: lines.filter((l) => bandFor(l.totals.pct).label === b.label).length,
      })),
    [lines],
  );

  const setRemarks = (eventId: string, remarks: string) => {
    setContent((prev) => {
      if (!prev) return prev;
      const next = clone(prev);
      const ev = next.events.find((e) => e.eventId === eventId);
      if (ev) ev.remarks = remarks;
      return next;
    });
    setDirty(true);
  };

  async function handleSave() {
    if (!content) return;
    setSaveError("");
    try {
      await save.mutateAsync(content);
    } catch (err) {
      setSaveError(apiErrorMessage(err, "Could not save the remarks."));
    }
  }

  return (
    <div className="space-y-4">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-base font-semibold text-ink">Delay event summary</h3>
          <p className="text-xs text-muted mt-0.5">
            Contractual admissibility of the delay events, consolidated — each event's percentage and
            category carried straight from the contractor scoring.
          </p>
        </div>
        {hasContent && (
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted inline-flex items-center gap-1.5">
              Sort by
              <select
                className="cell w-52 font-medium text-ink"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
              >
                {SORTS.map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </label>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={!dirty || save.isPending}
            >
              {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              Save remarks
            </button>
          </div>
        )}
      </div>

      {saveError && (
        <div className="flex items-start gap-2 rounded-lg bg-error-bg/60 px-3 py-2.5 text-xs text-error">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{saveError}</span>
        </div>
      )}
      {save.isSuccess && !dirty && !saveError && (
        <div className="flex items-start gap-2 rounded-lg bg-success-bg/60 px-3 py-2.5 text-xs text-success">
          <CheckCircle2 className="size-4 shrink-0 mt-px" /> Remarks saved.
        </div>
      )}

      {isLoading ? (
        <Card className="p-10 text-center text-sm text-muted inline-flex items-center justify-center gap-2 w-full">
          <Loader2 className="size-4 animate-spin" /> Loading delay event summary…
        </Card>
      ) : !hasContent ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <CalendarClock className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Nothing to summarise yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            {data?.status === "running"
              ? "The contractor scoring is still running — this list fills in as soon as it finishes."
              : "Score the delay events on the Contractor admissibility tab first. This list then consolidates each event's admissibility percentage and category."}
          </p>
        </Card>
      ) : (
        <>
          {/* ── Band tally across the register ── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {bandCounts.map(({ band, count }) => (
              <Card key={band.label} className="p-4">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">{band.label}</p>
                <p className={cn("mt-1 text-2xl font-bold font-display tabular-nums leading-none", band.text)}>
                  {count}
                  <span className="text-sm font-medium text-muted"> of {lines.length}</span>
                </p>
              </Card>
            ))}
          </div>

          <Card className="overflow-hidden">
            <div className="overflow-x-auto scroll-thin">
              <table className="w-full text-sm border-collapse min-w-[980px]">
                <thead>
                  <tr className="text-xs font-semibold text-muted uppercase tracking-wide bg-navy-50 border-b border-border">
                    <th className="px-3 py-2 w-14 text-center">Sl.no</th>
                    <th className="px-3 py-2 w-32 text-left">Delay event #</th>
                    <th className="px-3 py-2 text-left">Description</th>
                    <th className="px-3 py-2 w-28 text-right">Admissibility</th>
                    <th className="px-3 py-2 w-36 text-left">Category</th>
                    <th className="px-3 py-2 w-24 text-center">Time impact</th>
                    <th className="px-3 py-2 w-28 text-left">Start date</th>
                    <th className="px-3 py-2 w-72 text-left">Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => {
                    const band = bandFor(l.totals.pct);
                    return (
                      <tr key={l.scored.eventId} className="align-top border-b border-border/60">
                        <td className="px-3 py-2.5 text-center tabular-nums text-muted">{i + 1}</td>
                        <td className="px-3 py-2.5 font-medium text-ink">{l.scored.eventRef}</td>
                        <td className="px-3 py-2.5 text-ink">
                          {l.scored.title || l.event?.title || "—"}
                          {l.event?.category && (
                            <span className="block text-xs text-muted mt-0.5">{l.event.category}</span>
                          )}
                        </td>
                        <td
                          className={cn(
                            "px-3 py-2.5 text-right tabular-nums font-bold",
                            band.text,
                          )}
                          title={`${fmt(l.totals.achieved)} of ${fmt(l.totals.applicableWeight)} applicable weight`}
                        >
                          {fmt(l.totals.pct)}%
                        </td>
                        <td className="px-3 py-2.5">
                          <span className={cn("text-[11px] font-semibold rounded px-1.5 py-0.5", band.badge)}>
                            {band.label}
                          </span>
                        </td>
                        <td
                          className={cn(
                            "px-3 py-2.5 text-center font-medium",
                            l.timeImpact ? "text-ink" : "text-faint",
                          )}
                        >
                          {l.timeImpact ? "Yes" : "No"}
                          {!!l.event?.daysImpact && (
                            <span className="block text-xs text-muted tabular-nums">
                              {l.event.daysImpact} days
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 tabular-nums text-ink">
                          {l.startDate ? formatDate(l.startDate) : "—"}
                        </td>
                        <td className="px-2 py-2">
                          <input
                            className="cell w-full text-xs"
                            value={l.scored.remarks}
                            placeholder="Remarks…"
                            onChange={(e) => setRemarks(l.scored.eventId, e.target.value)}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
