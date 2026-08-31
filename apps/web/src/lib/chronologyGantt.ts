// Turns a delay event's chronology into the "Timeline of Key Events" Gantt model:
// one dated bar per chronology step, a month/year axis spanning the whole event,
// and the calendar-day durations attributable to each party.
//
// Pure computation with no rendering — both the on-screen chart
// (components/projects/ChronologyGantt.tsx) and the PDF export
// (lib/chronologyGanttPdf.ts) lay themselves out from this model, so the two
// always agree.
import {
  differenceInCalendarDays,
  endOfMonth,
  format,
  isValid,
  parseISO,
  startOfMonth,
} from "date-fns";
import type { ChronologyItem, ProjectDelayEvent } from "@/types";

export type GanttActor = ChronologyItem["actor"];

/** One chronology step placed on the timeline. */
export interface GanttRow {
  id: string;
  /** 1-based row number shown in the "No." column. */
  no: number;
  label: string;
  actor: GanttActor;
  start: Date;
  end: Date;
  /** A single-day step — drawn as a diamond rather than a bar. */
  milestone: boolean;
  /** True when `end` was inferred from the next step rather than recorded. */
  inferredEnd: boolean;
  startLabel: string;
  endLabel: string;
  /** Fraction of the axis (0–1) where the bar starts and how wide it runs. */
  offset: number;
  width: number;
}

/** One month column on the axis. */
export interface GanttMonth {
  key: string;
  label: string;
  year: number;
}

/** Calendar days a party was active — overlapping steps counted once. */
export interface GanttDuration {
  actor: GanttActor;
  days: number;
}

export interface GanttModel {
  rows: GanttRow[];
  months: GanttMonth[];
  /** Month-column spans for the year header strip. */
  years: { year: number; span: number }[];
  /** First and last dated step (the period the chart covers). */
  start: Date;
  end: Date;
  /** Inclusive calendar days from `start` to `end`. */
  totalDays: number;
  durations: GanttDuration[];
  /** Any bar's end date was inferred rather than recorded. */
  hasInferredEnds: boolean;
}

/** Parse an ISO date, returning null for empty or malformed values. */
function parse(iso?: string | null): Date | null {
  if (!iso) return null;
  try {
    const d = parseISO(iso);
    return isValid(d) ? d : null;
  } catch {
    return null;
  }
}

const dayLabel = (d: Date) => format(d, "d-MMM-yy");

/** Calendar days covered by a set of ranges, counting overlaps once. */
function unionDays(ranges: [number, number][]): number {
  if (ranges.length === 0) return 0;
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  let total = 0;
  let [curStart, curEnd] = sorted[0];
  for (const [s, e] of sorted.slice(1)) {
    // Ranges are inclusive day indices, so touching ranges (e.g. 5–9 and 10–12)
    // merge rather than double-counting the boundary day.
    if (s <= curEnd + 1) {
      curEnd = Math.max(curEnd, e);
    } else {
      total += curEnd - curStart + 1;
      curStart = s;
      curEnd = e;
    }
  }
  return total + (curEnd - curStart + 1);
}

/**
 * Build the Gantt model for one delay event, or null when it has no dated
 * chronology to plot.
 *
 * Each step's end date is used when the generator recorded one. Where it didn't
 * (chronology written before end dates were captured), the step is drawn to the
 * next recorded step — the forensic reading that a step ran until the next thing
 * happened — and flagged via `inferredEnd` so the chart can say so.
 */
export function buildGanttModel(event: ProjectDelayEvent): GanttModel | null {
  const dated = (event.chronology ?? [])
    .map((c) => ({ item: c, start: parse(c.date) }))
    .filter((x): x is { item: ChronologyItem; start: Date } => x.start !== null)
    .sort((a, b) => a.start.getTime() - b.start.getTime());

  if (dated.length === 0) return null;

  const eventEnd = parse(event.endDate);

  const placed = dated.map(({ item, start }, i) => {
    const recorded = parse(item.endDate);
    let end: Date;
    let inferredEnd = false;
    if (recorded && recorded >= start) {
      end = recorded;
    } else {
      // Fall back to the next step; for the last step, the event's own end date.
      const next = dated[i + 1]?.start ?? (eventEnd && eventEnd > start ? eventEnd : null);
      end = next ?? start;
      inferredEnd = next !== null;
    }
    return { item, start, end, inferredEnd };
  });

  const axisStart = startOfMonth(placed[0].start);
  const lastEnd = placed.reduce((m, p) => (p.end > m ? p.end : m), placed[0].end);
  const axisEnd = endOfMonth(lastEnd);
  const axisDays = differenceInCalendarDays(axisEnd, axisStart) + 1;

  const rows: GanttRow[] = placed.map((p, i) => {
    const offsetDays = differenceInCalendarDays(p.start, axisStart);
    const spanDays = differenceInCalendarDays(p.end, p.start) + 1;
    return {
      id: p.item.id,
      no: i + 1,
      label: p.item.title || "(untitled step)",
      actor: p.item.actor,
      start: p.start,
      end: p.end,
      milestone: spanDays <= 1,
      inferredEnd: p.inferredEnd,
      startLabel: dayLabel(p.start),
      endLabel: dayLabel(p.end),
      offset: offsetDays / axisDays,
      width: spanDays / axisDays,
    };
  });

  // Month columns across the whole axis, plus the year spans above them.
  const months: GanttMonth[] = [];
  for (let d = axisStart; d <= axisEnd; d = startOfMonth(new Date(d.getFullYear(), d.getMonth() + 1, 1))) {
    months.push({
      key: format(d, "yyyy-MM"),
      label: format(d, "MMM"),
      year: d.getFullYear(),
    });
  }
  const years: { year: number; span: number }[] = [];
  for (const m of months) {
    const last = years[years.length - 1];
    if (last && last.year === m.year) last.span += 1;
    else years.push({ year: m.year, span: 1 });
  }

  // Per-party duration: calendar days that party was active, overlaps counted once.
  const byActor = new Map<GanttActor, [number, number][]>();
  for (const r of rows) {
    const range: [number, number] = [
      differenceInCalendarDays(r.start, axisStart),
      differenceInCalendarDays(r.end, axisStart),
    ];
    const list = byActor.get(r.actor);
    if (list) list.push(range);
    else byActor.set(r.actor, [range]);
  }
  const durations: GanttDuration[] = [...byActor.entries()]
    .map(([actor, ranges]) => ({ actor, days: unionDays(ranges) }))
    .sort((a, b) => b.days - a.days);

  const start = placed[0].start;
  return {
    rows,
    months,
    years,
    start,
    end: lastEnd,
    totalDays: differenceInCalendarDays(lastEnd, start) + 1,
    durations,
    hasInferredEnds: rows.some((r) => r.inferredEnd),
  };
}

/** Where a bar's date caption sits relative to the bar. */
export type CaptionSide = "before" | "after" | "inside" | "hidden";

/**
 * Decide where a row's start and end date captions go.
 *
 * Captions are drawn in the bar's own colour, so one placed over its bar is
 * invisible, and two placed on the same side land on top of each other. Both
 * happen on bars that touch an edge of the axis, where there is no room outside
 * — the first and last steps of a chronology always do. Preference is
 * start-before / end-after, falling back to inside the bar (drawn in white, so
 * it reads against the fill) where the bar is wide enough, and hidden where it
 * is not.
 *
 * `capW` is the rendered width of a caption, in the same units as `timelineW`.
 */
export function captionPlacement(
  row: Pick<GanttRow, "offset" | "width" | "milestone">,
  timelineW: number,
  capW: number,
  gap = 4,
): { start: CaptionSide; end: CaptionSide } {
  const barLeft = row.offset * timelineW;
  const barRight = (row.offset + row.width) * timelineW;
  const roomBefore = barLeft >= capW + gap;
  const roomAfter = timelineW - barRight >= capW + gap;

  // A milestone is a diamond carrying one caption, so it may use either side.
  if (row.milestone) {
    return { start: roomBefore ? "before" : roomAfter ? "after" : "hidden", end: "hidden" };
  }

  // Only the captions with no room outside go inside, so a bar needs to hold
  // both only when it is boxed in at both ends.
  const needBoth = !roomBefore && !roomAfter;
  const fitsInside = barRight - barLeft >= (needBoth ? 2 * capW + 3 * gap : capW + 2 * gap);
  return {
    start: roomBefore ? "before" : fitsInside ? "inside" : "hidden",
    end: roomAfter ? "after" : fitsInside ? "inside" : "hidden",
  };
}

/** Bar colour per party, as hex (screen) and RGB (PDF). */
export const ACTOR_COLORS: Record<GanttActor, { hex: string; rgb: [number, number, number]; label: string }> = {
  Engineer: { hex: "#1f4a7d", rgb: [31, 74, 125], label: "Engineer activities" },
  Contractor: { hex: "#158650", rgb: [21, 134, 80], label: "Contractor activities" },
  Employer: { hex: "#e8920c", rgb: [232, 146, 12], label: "Employer activities" },
  System: { hex: "#64748b", rgb: [100, 116, 139], label: "Other / recorded events" },
};

/** "DE-08" → "08"; falls back to the event's position in the register. */
export function eventNumber(event: ProjectDelayEvent, index: number): string {
  return (event.ref?.match(/(\d+)\s*$/)?.[1] ?? String(index + 1)).padStart(2, "0");
}

export { dayLabel };
