// Landscape PDF export of the "Timeline of Key Events" Gantt chart, built with
// jsPDF from the same model as the on-screen chart (lib/chronologyGantt), so the
// exported sheet matches what the analyst reviewed.
//
// Each delay event gets its own sheet: a navy banner, the overall-period and
// per-party duration strips, the month/year axis with one bar per chronology
// step, and a legend. Long registers paginate with the axis header repeated.
import { jsPDF } from "jspdf";
import {
  ACTOR_COLORS,
  buildGanttModel,
  captionPlacement,
  dayLabel,
  eventNumber,
  type GanttModel,
} from "@/lib/chronologyGantt";
import type { ProjectDelayEvent } from "@/types";

type RGB = [number, number, number];

const NAVY: RGB = [10, 37, 64];
const NAVY_MID: RGB = [31, 74, 125];
const BODY: RGB = [35, 35, 35];
const GREY: RGB = [107, 114, 128];
const RULE: RGB = [217, 220, 227];
const SHADE: RGB = [238, 244, 251];
const WHITE: RGB = [255, 255, 255];

const CONFIDENTIAL =
  "This document is the sole property of Al Qarar Management Solutions. Any unauthorized use, reproduction, or distribution of this document is strictly prohibited.";

export interface GanttPdfOpts {
  projectName?: string;
  projectCode?: string;
  standard?: string;
  /** Filename stem; defaults from the project name. */
  fileName?: string;
}

/**
 * Build the Gantt PDF and trigger a download. Pass every delay event for a
 * whole-project export, or a single event for one sheet. Events with no dated
 * chronology are skipped; returns the number of sheets actually drawn.
 */
export function downloadChronologyGanttPdf(
  events: ProjectDelayEvent[],
  opts: GanttPdfOpts = {},
): number {
  const sheets = events
    .map((event, i) => ({ event, index: i, model: buildGanttModel(event) }))
    .filter((s): s is { event: ProjectDelayEvent; index: number; model: GanttModel } => s.model !== null);

  if (sheets.length === 0) return 0;

  const pdf = new jsPDF({ unit: "pt", format: "a4", orientation: "landscape" });
  const W = pdf.internal.pageSize.getWidth();
  const H = pdf.internal.pageSize.getHeight();
  const M = 30;
  const contentW = W - 2 * M;
  const contentBottom = H - 52;

  const NO_W = 22;
  const LABEL_W = 200;
  const gridX = M + NO_W + LABEL_W;
  const timelineW = contentW - NO_W - LABEL_W;
  const ROW_H = 13;

  const setFont = (b?: boolean, i?: boolean) =>
    pdf.setFont("helvetica", b && i ? "bolditalic" : b ? "bold" : i ? "italic" : "normal");
  const setColor = (c: RGB) => pdf.setTextColor(c[0], c[1], c[2]);
  const setFill = (c: RGB) => pdf.setFillColor(c[0], c[1], c[2]);
  const setDraw = (c: RGB) => pdf.setDrawColor(c[0], c[1], c[2]);

  const textAt = (t: string, x: number, y: number, size: number, color: RGB, bold = false) => {
    pdf.setFontSize(size);
    setFont(bold, false);
    setColor(color);
    pdf.text(t, x, y);
  };
  const centered = (t: string, cx: number, y: number, size: number, color: RGB, bold = false) => {
    pdf.setFontSize(size);
    setFont(bold, false);
    setColor(color);
    pdf.text(t, cx - pdf.getTextWidth(t) / 2, y);
  };
  /** Truncate to fit a column, appending an ellipsis. */
  const clip = (t: string, maxW: number, size: number) => {
    pdf.setFontSize(size);
    setFont(false, false);
    if (pdf.getTextWidth(t) <= maxW) return t;
    let s = t;
    while (s.length > 1 && pdf.getTextWidth(s + "…") > maxW) s = s.slice(0, -1);
    return s + "…";
  };

  let y = 0;

  function drawBanner(event: ProjectDelayEvent, index: number, model: GanttModel) {
    const h = 34;
    setFill(NAVY);
    pdf.rect(M, M, contentW, h, "F");
    textAt("TIMELINE OF KEY EVENTS", M + 12, M + 14, 7.5, [190, 210, 235], true);
    const title = `DELAY EVENT # ${eventNumber(event, index)} – ${event.title || "Untitled delay event"}`;
    textAt(clip(title, contentW - 170, 11), M + 12, M + 27, 11, WHITE, true);
    // Status-as-on stamp on the right
    const stamp = dayLabel(model.end);
    pdf.setFontSize(7);
    setFont(true, false);
    setColor([190, 210, 235]);
    const sw = Math.max(pdf.getTextWidth("STATUS AS ON"), 46);
    pdf.text("STATUS AS ON", M + contentW - 12 - sw, M + 14);
    textAt(stamp, M + contentW - 12 - sw, M + 27, 10, WHITE, true);
    y = M + h + 8;
  }

  function drawSummary(model: GanttModel) {
    const h = 26;
    const cells: [string, string][] = [
      ["START", dayLabel(model.start)],
      [
        "OVERALL PERIOD COVERED",
        `${dayLabel(model.start)} to ${dayLabel(model.end)}  ·  ${model.totalDays.toLocaleString()} calendar days`,
      ],
      ["FINISH", dayLabel(model.end)],
    ];
    const widths = [contentW * 0.2, contentW * 0.6, contentW * 0.2];
    let x = M;
    cells.forEach(([label, value], i) => {
      setFill(SHADE);
      pdf.rect(x, y, widths[i], h, "F");
      setDraw(RULE);
      pdf.setLineWidth(0.5);
      pdf.rect(x, y, widths[i], h);
      centered(label, x + widths[i] / 2, y + 10, 6.5, GREY, true);
      centered(value, x + widths[i] / 2, y + 21, 9, NAVY, true);
      x += widths[i];
    });
    y += h + 6;

    // Per-party actual durations + total
    const dur = model.durations;
    const total = { label: "TOTAL ACTUAL DURATION", days: model.totalDays };
    const count = dur.length + 1;
    const cw = contentW / count;
    const dh = 24;
    x = M;
    for (const d of dur) {
      setFill(ACTOR_COLORS[d.actor].rgb);
      pdf.rect(x, y, cw, dh, "F");
      centered(`${d.actor.toUpperCase()}'S ACTUAL DURATION`, x + cw / 2, y + 9, 6, [235, 242, 250], true);
      centered(`${d.days.toLocaleString()} CALENDAR DAYS`, x + cw / 2, y + 19, 8.5, WHITE, true);
      x += cw;
    }
    setFill(SHADE);
    pdf.rect(x, y, cw, dh, "F");
    setDraw(RULE);
    pdf.rect(x, y, cw, dh);
    centered(total.label, x + cw / 2, y + 9, 6, GREY, true);
    centered(`${total.days.toLocaleString()} CALENDAR DAYS`, x + cw / 2, y + 19, 8.5, NAVY, true);
    y += dh + 8;
  }

  /** Year + month axis header. Returns the y where rows start. */
  function drawAxis(model: GanttModel) {
    const monthW = timelineW / model.months.length;
    const yearH = 13;
    const monthH = 12;

    setFill(SHADE);
    pdf.rect(M, y, contentW, yearH, "F");
    setDraw(RULE);
    pdf.setLineWidth(0.5);
    pdf.rect(M, y, contentW, yearH);
    textAt("No.", M + 5, y + 9, 6.5, GREY, true);
    textAt("KEY EVENTS", M + NO_W + 4, y + 9, 6.5, GREY, true);
    let x = gridX;
    for (const yr of model.years) {
      const w = yr.span * monthW;
      pdf.line(x, y, x, y + yearH);
      centered(String(yr.year), x + w / 2, y + 9, 7, NAVY_MID, true);
      x += w;
    }
    y += yearH;

    pdf.rect(M, y, contentW, monthH);
    model.months.forEach((m, i) => {
      const mx = gridX + i * monthW;
      pdf.line(mx, y, mx, y + monthH);
      centered(m.label, mx + monthW / 2, y + 8, 6, GREY);
    });
    y += monthH;
    return monthW;
  }

  function drawRows(model: GanttModel, monthW: number, startIdx: number): number {
    let i = startIdx;
    for (; i < model.rows.length; i++) {
      if (y + ROW_H > contentBottom) break;
      const row = model.rows[i];
      const color = ACTOR_COLORS[row.actor].rgb;

      if (i % 2 === 1) {
        setFill([249, 251, 253]);
        pdf.rect(M, y, contentW, ROW_H, "F");
      }
      setDraw([236, 239, 244]);
      pdf.setLineWidth(0.4);
      pdf.line(M, y + ROW_H, M + contentW, y + ROW_H);
      // Column separators + month gridlines
      setDraw(RULE);
      pdf.line(M + NO_W, y, M + NO_W, y + ROW_H);
      pdf.line(gridX, y, gridX, y + ROW_H);
      setDraw([240, 242, 246]);
      for (let m = 1; m < model.months.length; m++) {
        const mx = gridX + m * monthW;
        pdf.line(mx, y, mx, y + ROW_H);
      }

      textAt(String(row.no), M + 6, y + 9, 6, GREY);
      textAt(clip(row.label, LABEL_W - 10, 7), M + NO_W + 4, y + 9, 7, BODY);

      const barX = gridX + row.offset * timelineW;
      const barW = Math.max(row.width * timelineW, 1.5);
      const cy = y + ROW_H / 2;
      setFill(color);
      if (row.milestone) {
        // Diamond for a single-day event
        const s = 3.2;
        const mx = barX + barW / 2;
        pdf.triangle(mx, cy - s, mx + s, cy, mx, cy + s, "F");
        pdf.triangle(mx, cy - s, mx - s, cy, mx, cy + s, "F");
      } else {
        pdf.rect(barX, cy - 2.6, barW, 5.2, "F");
      }

      // Date captions — outside the bar where there is room, inside it (in white)
      // where there isn't, so the two never land on the same spot.
      pdf.setFontSize(5.2);
      setFont(false, false);
      const startW = pdf.getTextWidth(row.startLabel);
      const endW = pdf.getTextWidth(row.endLabel);
      const caps = captionPlacement(row, timelineW, Math.max(startW, endW), 2);
      const capY = cy + 1.8;
      if (caps.start !== "hidden") {
        setColor(caps.start === "inside" ? WHITE : color);
        const sx =
          caps.start === "before"
            ? barX - startW - 2
            : caps.start === "after"
              ? barX + barW + 2
              : barX + 2;
        pdf.text(row.startLabel, sx, capY);
      }
      if (caps.end !== "hidden") {
        setColor(caps.end === "inside" ? WHITE : color);
        pdf.text(row.endLabel, caps.end === "after" ? barX + barW + 2 : barX + barW - endW - 2, capY);
      }
      y += ROW_H;
    }
    return i;
  }

  function drawLegend(model: GanttModel) {
    if (y + 30 > contentBottom) return;
    y += 8;
    const actors = [...new Set(model.rows.map((r) => r.actor))];
    let x = M + 2;
    for (const a of actors) {
      setFill(ACTOR_COLORS[a].rgb);
      pdf.rect(x, y, 16, 5, "F");
      textAt(ACTOR_COLORS[a].label, x + 20, y + 5, 6.5, BODY);
      pdf.setFontSize(6.5);
      x += 20 + pdf.getTextWidth(ACTOR_COLORS[a].label) + 16;
    }
    setFill(NAVY_MID);
    const dx = x + 4;
    pdf.triangle(dx, y - 0.5, dx + 3.2, y + 2.7, dx, y + 5.9, "F");
    pdf.triangle(dx, y - 0.5, dx - 3.2, y + 2.7, dx, y + 5.9, "F");
    textAt("Milestone / single-day event", x + 10, y + 5, 6.5, BODY);
    y += 12;

    if (model.hasInferredEnds) {
      textAt(
        "Note: steps with no recorded end date are drawn to the next recorded step.",
        M + 2,
        y + 5,
        6,
        GREY,
      );
    }
  }

  function drawFooter(page: number, total: number) {
    const fy = H - 34;
    setDraw(RULE);
    pdf.setLineWidth(0.5);
    pdf.line(M, fy, M + contentW, fy);
    pdf.setFontSize(6);
    setFont(false, false);
    setColor(GREY);
    pdf.text(CONFIDENTIAL, M, fy + 9);
    const meta = [opts.projectName, opts.projectCode].filter(Boolean).join("  ·  ");
    if (meta) pdf.text(meta, M, fy + 17);
    pdf.setFontSize(7);
    const s = `Page ${page} of ${total}`;
    pdf.text(s, M + contentW - pdf.getTextWidth(s), fy + 12);
  }

  // ── Draw every sheet ──────────────────────────────────────────────────────
  sheets.forEach(({ event, index, model }, sheetIdx) => {
    if (sheetIdx > 0) pdf.addPage();
    drawBanner(event, index, model);
    drawSummary(model);
    let monthW = drawAxis(model);
    let next = drawRows(model, monthW, 0);
    while (next < model.rows.length) {
      pdf.addPage();
      y = M;
      drawBanner(event, index, model);
      monthW = drawAxis(model);
      next = drawRows(model, monthW, next);
    }
    drawLegend(model);
  });

  const pageCount = pdf.getNumberOfPages();
  for (let p = 1; p <= pageCount; p++) {
    pdf.setPage(p);
    drawFooter(p, pageCount);
  }

  const stem =
    opts.fileName ||
    `${opts.projectName || "Project"} — Timeline of Key Events`.replace(/[^\w\-. ]+/g, "").trim() ||
    "Timeline of Key Events";
  pdf.save(`${stem.slice(0, 90)}.pdf`);
  return sheets.length;
}
