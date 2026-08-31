// The "Timeline of Key Events" view of a delay event's chronology — a Gantt
// chart with one bar per dated step, colour-coded by the party that took it,
// over a month/year axis, with the calendar-day durations attributable to each
// side. Laid out from the shared model in lib/chronologyGantt so the PDF export
// renders the identical chart.
import { AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ACTOR_COLORS,
  captionPlacement,
  dayLabel,
  type GanttModel,
  type GanttRow,
} from "@/lib/chronologyGantt";

const MONTH_W = 54; // px per month column — the chart scrolls when it overflows
const ROW_H = 26;
const LABEL_W = 300;
// Rendered width of a date caption ("15-Aug-24" at 8px tabular-nums), used to
// work out which side of a bar it fits on.
const CAPTION_W = 44;

export function ChronologyGantt({
  model,
  title,
  statusAsOf,
}: {
  model: GanttModel;
  title: string;
  statusAsOf?: string;
}) {
  const timelineW = Math.max(model.months.length * MONTH_W, 320);
  const actorsUsed = [...new Set(model.rows.map((r) => r.actor))];

  return (
    <div className="space-y-3">
      {/* ── Banner ── */}
      <div className="rounded-lg bg-navy-900 px-4 py-3 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-navy-200">
            Timeline of key events
          </p>
          <h4 className="text-sm font-bold text-white mt-0.5 leading-snug">{title}</h4>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-navy-200">Status as on</p>
          <p className="text-xs font-bold text-white tabular-nums">
            {statusAsOf ?? dayLabel(model.end)}
          </p>
        </div>
      </div>

      {/* ── Summary strip ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <SummaryCell label="Start" value={dayLabel(model.start)} />
        <SummaryCell
          label="Overall period covered"
          value={`${model.totalDays.toLocaleString()} calendar days`}
          hint={`${dayLabel(model.start)} → ${dayLabel(model.end)}`}
        />
        <SummaryCell label="Finish" value={dayLabel(model.end)} />
      </div>

      {/* Per-party actual durations, the way the claim reports them */}
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${Math.min(model.durations.length + 1, 4)}, minmax(0, 1fr))` }}
      >
        {model.durations.map((d) => (
          <div
            key={d.actor}
            className="rounded-lg px-3 py-2 text-white"
            style={{ backgroundColor: ACTOR_COLORS[d.actor].hex }}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide opacity-80">
              {d.actor}&apos;s actual duration
            </p>
            <p className="text-sm font-bold tabular-nums mt-0.5">
              {d.days.toLocaleString()} calendar days
            </p>
          </div>
        ))}
        <div className="rounded-lg px-3 py-2 bg-navy-50 border border-border">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">
            Total actual duration
          </p>
          <p className="text-sm font-bold tabular-nums mt-0.5 text-ink">
            {model.totalDays.toLocaleString()} calendar days
          </p>
        </div>
      </div>

      {/* ── Chart ── */}
      <div className="rounded-lg border border-border overflow-x-auto scroll-thin">
        <div style={{ minWidth: LABEL_W + timelineW }}>
          {/* Year header */}
          <div className="flex border-b border-border bg-navy-50/60">
            <div
              className="shrink-0 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wide text-faint border-r border-border"
              style={{ width: LABEL_W }}
            >
              Key events
            </div>
            <div className="flex" style={{ width: timelineW }}>
              {model.years.map((y) => (
                <div
                  key={y.year}
                  className="text-[10px] font-bold text-navy-700 text-center py-1.5 border-r border-border last:border-r-0"
                  style={{ width: y.span * MONTH_W }}
                >
                  {y.year}
                </div>
              ))}
            </div>
          </div>

          {/* Month header */}
          <div className="flex border-b border-border bg-white">
            <div className="shrink-0 border-r border-border" style={{ width: LABEL_W }} />
            <div className="flex" style={{ width: timelineW }}>
              {model.months.map((m) => (
                <div
                  key={m.key}
                  className="text-[10px] font-medium text-muted text-center py-1 border-r border-border/60 last:border-r-0"
                  style={{ width: MONTH_W }}
                >
                  {m.label}
                </div>
              ))}
            </div>
          </div>

          {/* Rows */}
          {model.rows.map((row, i) => (
            <GanttBarRow key={row.id} row={row} timelineW={timelineW} monthCount={model.months.length} zebra={i % 2 === 1} />
          ))}
        </div>
      </div>

      {/* ── Legend ── */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
        {actorsUsed.map((a) => (
          <span key={a} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-6 rounded-sm" style={{ backgroundColor: ACTOR_COLORS[a].hex }} />
            {ACTOR_COLORS[a].label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span
            className="size-2.5 rotate-45 rounded-[1px]"
            style={{ backgroundColor: ACTOR_COLORS.Engineer.hex }}
          />
          Milestone / single-day event
        </span>
      </div>

      {model.hasInferredEnds && (
        <div className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
          <Info className="size-3.5 shrink-0 mt-px" />
          <span>
            Some steps have no end date in the record. Those bars are drawn to the next recorded step — they show
            when the step was overtaken, not a documented completion. Regenerate the chronology with AI to capture
            end dates from the documents.
          </span>
        </div>
      )}
    </div>
  );
}

function GanttBarRow({
  row,
  timelineW,
  monthCount,
  zebra,
}: {
  row: GanttRow;
  timelineW: number;
  monthCount: number;
  zebra: boolean;
}) {
  const color = ACTOR_COLORS[row.actor].hex;
  const leftPct = row.offset * 100;
  const widthPct = row.width * 100;
  // Date captions sit outside the bar where there's room on that side, and
  // inside it (in white) where there isn't, so they never land on the bar in its
  // own colour or on top of each other.
  const caps = captionPlacement(row, timelineW, CAPTION_W);

  return (
    <div className={cn("flex border-b border-border/60 last:border-b-0", zebra && "bg-navy-50/30")}>
      <div
        className="shrink-0 flex items-center gap-2 px-3 border-r border-border"
        style={{ width: LABEL_W, height: ROW_H }}
      >
        <span className="text-[10px] tabular-nums text-faint w-4 shrink-0">{row.no}</span>
        <span className="text-[11px] text-ink truncate" title={row.label}>
          {row.label}
        </span>
      </div>
      <div className="relative" style={{ width: timelineW, height: ROW_H }}>
        {/* Month gridlines */}
        {Array.from({ length: monthCount }, (_, i) => (
          <span
            key={i}
            className="absolute top-0 bottom-0 w-px bg-border/50"
            style={{ left: ((i + 1) / monthCount) * 100 + "%" }}
          />
        ))}

        {row.milestone ? (
          <span
            className="absolute top-1/2 size-2.5 -translate-y-1/2 -translate-x-1/2 rotate-45 rounded-[1px]"
            style={{ left: `${leftPct + widthPct / 2}%`, backgroundColor: color }}
            title={`${row.label} — ${row.startLabel}`}
          />
        ) : (
          <span
            className="absolute top-1/2 h-2.5 -translate-y-1/2 rounded-sm"
            style={{ left: `${leftPct}%`, width: `${Math.max(widthPct, 0.6)}%`, backgroundColor: color }}
            title={`${row.label} — ${row.startLabel} → ${row.endLabel}`}
          />
        )}

        {/* Start / end captions */}
        {caps.start !== "hidden" && (
          <span
            className={cn(
              "absolute top-1/2 -translate-y-1/2 text-[8px] tabular-nums whitespace-nowrap",
              caps.start === "before" ? "-translate-x-full pr-1" : "pl-1",
            )}
            style={{
              left: `${caps.start === "after" ? leftPct + widthPct : leftPct}%`,
              color: caps.start === "inside" ? "#fff" : color,
            }}
          >
            {row.startLabel}
          </span>
        )}
        {caps.end !== "hidden" && (
          <span
            className={cn(
              "absolute top-1/2 -translate-y-1/2 text-[8px] tabular-nums whitespace-nowrap",
              caps.end === "after" ? "pl-1" : "-translate-x-full pr-1",
            )}
            style={{
              left: `${leftPct + widthPct}%`,
              color: caps.end === "inside" ? "#fff" : color,
            }}
          >
            {row.endLabel}
          </span>
        )}
      </div>
    </div>
  );
}

function SummaryCell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">{label}</p>
      <p className="text-sm font-bold text-ink tabular-nums mt-0.5">{value}</p>
      {hint && <p className="text-[10px] text-muted tabular-nums mt-0.5">{hint}</p>}
    </div>
  );
}

/** Shown when an event has no dated chronology to plot. */
export function GanttEmpty({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-navy-50/40 px-4 py-3 text-sm text-muted">
      <AlertTriangle className="size-4 shrink-0 mt-0.5 text-amber-500" />
      <span>{message}</span>
    </div>
  );
}
