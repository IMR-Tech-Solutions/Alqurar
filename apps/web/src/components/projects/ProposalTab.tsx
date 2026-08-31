import { useMemo } from "react";
import { jsPDF } from "jspdf";
import { AlertTriangle, Download, FileSignature, Loader2, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { useGenerateProposal, useProposal } from "@/hooks/useProposal";
import type {
  ClaimBlock,
  ClaimContent,
  ClaimSection,
  ClaimStatementLabel,
  ClaimSubsection,
} from "@/api/proposals";
import { formatDate } from "@/lib/utils";

// ── Shared presentation ────────────────────────────────────────────────────

type RGB = [number, number, number];

const INK: RGB = [17, 24, 39];
const NAVY: RGB = [15, 42, 76];
const NAVY_MID: RGB = [30, 58, 95];
const SLATE: RGB = [71, 85, 105];
const FAINT: RGB = [120, 133, 150];
const RULE: RGB = [203, 213, 225];
const TABLE_HEAD: RGB = [241, 245, 249];

/** Accent for each statement label, so fact and inference never look alike. */
const STATEMENT_RGB: Record<ClaimStatementLabel, RGB> = {
  Fact: [30, 58, 95],
  "Contractor's position": [21, 94, 117],
  "Engineer / Employer's position": [146, 64, 14],
  Analysis: [91, 33, 182],
  "Missing evidence": [180, 35, 24],
  Assessment: [22, 101, 52],
};

/** Tailwind classes mirroring STATEMENT_RGB for the on-screen rendering. */
const STATEMENT_CLASS: Record<ClaimStatementLabel, string> = {
  Fact: "border-navy-400 bg-navy-50/60 text-navy-800",
  "Contractor's position": "border-info bg-info-bg/50 text-info",
  "Engineer / Employer's position": "border-amber-400 bg-amber-50 text-amber-800",
  Analysis: "border-violet-400 bg-violet-50 text-violet-800",
  "Missing evidence": "border-error bg-error-bg/50 text-error",
  Assessment: "border-success bg-success-bg/50 text-success",
};

const subsectionsOf = (s: ClaimSection): ClaimSubsection[] => s.subsections ?? [];

/**
 * Number every table in document order.
 *
 * Keyed on block identity rather than position so the screen and the PDF agree:
 * both walk the same object graph, so "Table 7" means the same table in each.
 */
function numberTables(doc: ClaimContent | null): Map<ClaimBlock, number> {
  const map = new Map<ClaimBlock, number>();
  let n = 0;
  const visit = (blocks?: ClaimBlock[]) => {
    for (const b of blocks ?? []) if (b.type === "table") map.set(b, ++n);
  };
  for (const section of doc?.sections ?? []) {
    visit(section.blocks);
    for (const sub of subsectionsOf(section)) {
      visit(sub.blocks);
      for (const part of sub.parts ?? []) visit(part.blocks);
    }
  }
  return map;
}

const captionFor = (block: ClaimBlock, tables: Map<ClaimBlock, number>) => {
  const n = tables.get(block);
  const caption = block.type === "table" ? block.caption?.trim() : "";
  if (!n) return caption ?? "";
  return caption ? `Table ${n}: ${caption}` : `Table ${n}`;
};

// ── PDF export ─────────────────────────────────────────────────────────────
// Laid out in two passes: the first measures the body on a throwaway document to
// learn which page each heading starts on, the second builds the real file with a
// cover and contents page in front and running headers applied once the total
// page count is known. Page numbers are body-relative, so "page 12" in the
// contents is the twelfth page of the claim proper.

const MARGIN = 54;
const BODY_SIZE = 9.5;
const TABLE_SIZE = 8;
const TOC_ROW_H = 14;

type TocEntry = { number: string; heading: string; page: number; depth: 0 | 1 };

function renderBody(
  pdf: jsPDF,
  doc: ClaimContent,
  tables: Map<ClaimBlock, number>,
): TocEntry[] {
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const maxW = pageW - MARGIN * 2;
  const toc: TocEntry[] = [];
  let y = MARGIN + 24; // leave room for the running header

  const ensure = (space: number) => {
    if (y + space > pageH - MARGIN - 18) {
      pdf.addPage();
      y = MARGIN + 24;
    }
  };

  const write = (
    text: string,
    size: number,
    style: "bold" | "normal" | "italic",
    gapAfter: number,
    color: RGB = INK,
    indent = 0,
  ) => {
    pdf.setFont("helvetica", style);
    pdf.setFontSize(size);
    pdf.setTextColor(...color);
    const lineH = size * 1.45;
    for (const raw of text.split("\n")) {
      if (raw.trim() === "") {
        y += lineH * 0.5;
        continue;
      }
      for (const line of pdf.splitTextToSize(raw, maxW - indent) as string[]) {
        ensure(lineH);
        pdf.text(line, MARGIN + indent, y);
        y += lineH;
      }
    }
    y += gapAfter;
  };

  /** Column widths weighted by the longest cell, clamped so no column collapses. */
  const columnWidths = (columns: string[], rows: string[][]) => {
    const weights = columns.map((c, i) => {
      const longest = rows.reduce((m, r) => Math.max(m, (r[i] ?? "").length), c.length);
      return Math.min(Math.max(longest, 6), 60);
    });
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    return weights.map((w) => (w / total) * maxW);
  };

  const drawTable = (caption: string, columns: string[], rows: string[][]) => {
    if (!columns.length) return;
    if (caption) write(caption, 8.5, "bold", 3, NAVY_MID);

    const widths = columnWidths(columns, rows);
    const pad = 4;
    const lineH = TABLE_SIZE * 1.35;

    const drawRow = (cells: string[], header: boolean) => {
      pdf.setFont("helvetica", header ? "bold" : "normal");
      pdf.setFontSize(TABLE_SIZE);
      // Wrap every cell first so the row height fits the tallest one.
      const wrapped = cells.map(
        (c, i) => pdf.splitTextToSize(String(c ?? ""), widths[i] - pad * 2) as string[],
      );
      const rowH = Math.max(...wrapped.map((w) => w.length)) * lineH + pad * 2;
      ensure(rowH);

      if (header) {
        pdf.setFillColor(...TABLE_HEAD);
        pdf.rect(MARGIN, y, maxW, rowH, "F");
      }
      pdf.setDrawColor(...RULE);
      pdf.setLineWidth(0.5);
      pdf.rect(MARGIN, y, maxW, rowH);

      let x = MARGIN;
      pdf.setTextColor(...INK);
      wrapped.forEach((lines, i) => {
        if (i > 0) pdf.line(x, y, x, y + rowH);
        lines.forEach((line, li) => {
          pdf.text(line, x + pad, y + pad + (li + 1) * lineH - lineH * 0.25);
        });
        x += widths[i];
      });
      y += rowH;
    };

    drawRow(columns, true);
    rows.forEach((r) => drawRow(r, false));
    y += 11;
  };

  const drawStatement = (label: ClaimStatementLabel | undefined, text: string) => {
    const key = (label ?? "Analysis") as ClaimStatementLabel;
    const color = STATEMENT_RGB[key] ?? STATEMENT_RGB.Analysis;
    const indent = 12;
    const top = y;
    write(key.toUpperCase(), 7.5, "bold", 2, color, indent);
    write(text, BODY_SIZE, "normal", 8, INK, indent);
    // Rule down the left edge, only where the statement stayed on one page.
    if (y > top) {
      pdf.setDrawColor(...color);
      pdf.setLineWidth(1.6);
      pdf.line(MARGIN + 3, top - 6, MARGIN + 3, Math.min(y - 6, pageH - MARGIN));
      pdf.setLineWidth(0.5);
    }
  };

  const drawBlocks = (blocks?: ClaimBlock[]) => {
    for (const b of blocks ?? []) {
      if (b.type === "paragraph") {
        if (b.text) write(b.text, BODY_SIZE, "normal", 8);
      } else if (b.type === "statement") {
        if (b.text) drawStatement(b.label, b.text);
      } else if (b.type === "bullets") {
        for (const item of b.items ?? []) write(`•  ${item}`, BODY_SIZE, "normal", 2);
        y += 6;
      } else if (b.type === "evidence") {
        write("Documents relied on", 8, "bold", 2, SLATE);
        for (const item of b.items ?? []) write(`—  ${item}`, 8.5, "italic", 1, SLATE);
        y += 8;
      } else if (b.type === "table") {
        drawTable(captionFor(b, tables), b.columns ?? [], b.rows ?? []);
      }
    }
  };

  doc.sections?.forEach((section, i) => {
    // Every top-level section opens a page, as a printed claim would.
    if (i > 0) {
      pdf.addPage();
      y = MARGIN + 24;
    }
    toc.push({
      number: section.number,
      heading: section.heading,
      page: pdf.getNumberOfPages(),
      depth: 0,
    });
    write(`${section.number}.  ${section.heading}`, 14, "bold", 4, NAVY);
    pdf.setDrawColor(...NAVY);
    pdf.setLineWidth(1);
    pdf.line(MARGIN, y - 6, pageW - MARGIN, y - 6);
    pdf.setLineWidth(0.5);
    y += 8;
    drawBlocks(section.blocks);

    for (const sub of subsectionsOf(section)) {
      ensure(46);
      toc.push({
        number: sub.number,
        heading: sub.heading,
        page: pdf.getNumberOfPages(),
        depth: 1,
      });
      write(`${sub.number}  ${sub.heading}`, 11, "bold", 6, NAVY_MID);
      drawBlocks(sub.blocks);

      for (const part of sub.parts ?? []) {
        ensure(34);
        write(`${part.number}.  ${part.heading}`, 9.5, "bold", 4, SLATE);
        drawBlocks(part.blocks);
      }
    }
  });

  return toc;
}

/** Running header and footer on every body page, once the total is known. */
function decorate(pdf: jsPDF, doc: ClaimContent, bodyStart: number) {
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const total = pdf.getNumberOfPages();
  const bodyTotal = total - bodyStart + 1;

  for (let p = bodyStart; p <= total; p++) {
    pdf.setPage(p);
    pdf.setFont("helvetica", "normal");
    pdf.setFontSize(7.5);
    pdf.setTextColor(...FAINT);

    pdf.text(doc.title || "Extension of Time Claim", MARGIN, MARGIN - 12, {
      maxWidth: pageW - MARGIN * 2 - 140,
    });
    if (doc.reference) {
      pdf.text(doc.reference, pageW - MARGIN, MARGIN - 12, { align: "right" });
    }
    pdf.setDrawColor(...RULE);
    pdf.setLineWidth(0.5);
    pdf.line(MARGIN, MARGIN - 7, pageW - MARGIN, MARGIN - 7);

    pdf.line(MARGIN, pageH - MARGIN + 4, pageW - MARGIN, pageH - MARGIN + 4);
    pdf.text(
      "AI-generated draft — verify against the contract and source documents before submission.",
      MARGIN,
      pageH - MARGIN + 16,
    );
    pdf.text(
      `Page ${p - bodyStart + 1} of ${bodyTotal}`,
      pageW - MARGIN,
      pageH - MARGIN + 16,
      { align: "right" },
    );
  }
}

function drawCover(pdf: jsPDF, doc: ClaimContent, generatedAt?: string | null) {
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const maxW = pageW - MARGIN * 2;

  pdf.setFillColor(...NAVY);
  pdf.rect(0, 0, pageW, 8, "F");

  let y = 230;
  pdf.setFont("helvetica", "bold");
  pdf.setFontSize(24);
  pdf.setTextColor(...NAVY);
  for (const line of pdf.splitTextToSize(
    doc.title || "Extension of Time Claim",
    maxW,
  ) as string[]) {
    pdf.text(line, MARGIN, y);
    y += 30;
  }

  if (doc.reference) {
    pdf.setFont("helvetica", "normal");
    pdf.setFontSize(12.5);
    pdf.setTextColor(...SLATE);
    for (const line of pdf.splitTextToSize(doc.reference, maxW) as string[]) {
      pdf.text(line, MARGIN, y + 6);
      y += 18;
    }
  }

  pdf.setDrawColor(...NAVY);
  pdf.setLineWidth(2);
  pdf.line(MARGIN, y + 22, MARGIN + 130, y + 22);
  pdf.setLineWidth(0.5);

  pdf.setFont("helvetica", "normal");
  pdf.setFontSize(9.5);
  pdf.setTextColor(...FAINT);
  pdf.text(
    `AI-generated draft${generatedAt ? ` · ${formatDate(generatedAt)}` : ""}`,
    MARGIN,
    y + 50,
  );
  pdf.text(
    "This draft is prepared from the documents supplied to the project data room.",
    MARGIN,
    pageH - MARGIN - 26,
  );
  pdf.text(
    "Review and verify against the contract and source records before submission.",
    MARGIN,
    pageH - MARGIN - 13,
  );
}

function drawContents(pdf: jsPDF, entries: TocEntry[]) {
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const maxW = pageW - MARGIN * 2;

  pdf.setFont("helvetica", "bold");
  pdf.setFontSize(15);
  pdf.setTextColor(...NAVY);
  pdf.text("CONTENTS", MARGIN, MARGIN + 12);
  let y = MARGIN + 44;

  for (const e of entries) {
    if (y > pageH - MARGIN) {
      pdf.addPage();
      y = MARGIN + 12;
    }
    const top = e.depth === 0;
    pdf.setFont("helvetica", top ? "bold" : "normal");
    pdf.setFontSize(top ? 9.5 : 9);
    pdf.setTextColor(...(top ? INK : SLATE));
    const indent = top ? 0 : 20;
    const label = `${e.number}${top ? "." : ""}  ${e.heading}`;
    pdf.text(label, MARGIN + indent, y, { maxWidth: maxW - 46 - indent });
    pdf.text(String(e.page), pageW - MARGIN, y, { align: "right" });
    y += top ? TOC_ROW_H + 3 : TOC_ROW_H;
  }
}

function downloadClaimPdf(
  doc: ClaimContent,
  tables: Map<ClaimBlock, number>,
  generatedAt?: string | null,
) {
  // Pass 1 — measure the body to learn each heading's page.
  const probe = new jsPDF({ unit: "pt", format: "a4" });
  const entries = renderBody(probe, doc, tables);

  // Pass 2 — the real document.
  const pdf = new jsPDF({ unit: "pt", format: "a4" });
  drawCover(pdf, doc, generatedAt);
  pdf.addPage();
  drawContents(pdf, entries);
  pdf.addPage();
  const bodyStart = pdf.getNumberOfPages();
  renderBody(pdf, doc, tables);
  decorate(pdf, doc, bodyStart);

  const safe =
    (doc.title || "EOT Claim").replace(/[^\w\-. ]+/g, "").trim().slice(0, 80) || "EOT Claim";
  pdf.save(`${safe}.pdf`);
}

// ── On-screen rendering ────────────────────────────────────────────────────

function BlockView({
  block,
  tables,
}: {
  block: ClaimBlock;
  tables: Map<ClaimBlock, number>;
}) {
  if (block.type === "paragraph") {
    return <p className="text-sm text-ink/90 leading-relaxed whitespace-pre-wrap">{block.text}</p>;
  }

  if (block.type === "statement") {
    const key = (block.label ?? "Analysis") as ClaimStatementLabel;
    const tone = STATEMENT_CLASS[key] ?? STATEMENT_CLASS.Analysis;
    return (
      <div className={`border-l-[3px] rounded-r-md px-3 py-2 ${tone}`}>
        <p className="text-[10px] font-bold uppercase tracking-wider opacity-80">{key}</p>
        <p className="mt-0.5 text-sm leading-relaxed text-ink/90 whitespace-pre-wrap">
          {block.text}
        </p>
      </div>
    );
  }

  if (block.type === "bullets") {
    return (
      <ul className="list-disc pl-5 space-y-1">
        {(block.items ?? []).map((it, i) => (
          <li key={i} className="text-sm text-ink/90 leading-relaxed">
            {it}
          </li>
        ))}
      </ul>
    );
  }

  if (block.type === "evidence") {
    return (
      <div className="rounded-lg border border-border bg-navy-50/30 px-3 py-2">
        <p className="text-[11px] font-semibold text-muted uppercase tracking-wide">
          Documents relied on
        </p>
        <ul className="mt-1 space-y-0.5">
          {(block.items ?? []).map((it, i) => (
            <li key={i} className="text-xs text-ink/80 italic">
              — {it}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const caption = captionFor(block, tables);
  return (
    <figure className="space-y-1.5">
      {caption && (
        <figcaption className="text-xs font-semibold text-navy-700">{caption}</figcaption>
      )}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="bg-navy-50/70">
              {(block.columns ?? []).map((c, i) => (
                <th
                  key={i}
                  className="text-left font-semibold text-navy-800 px-2.5 py-2 border-b border-border align-top"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(block.rows ?? []).map((row, ri) => (
              <tr key={ri} className="even:bg-navy-50/25">
                {(block.columns ?? []).map((_, ci) => (
                  <td key={ci} className="px-2.5 py-1.5 border-b border-border align-top text-ink/90">
                    {row[ci] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function Blocks({
  blocks,
  tables,
}: {
  blocks?: ClaimBlock[];
  tables: Map<ClaimBlock, number>;
}) {
  return (
    <>
      {(blocks ?? []).map((b, i) => (
        <BlockView key={i} block={b} tables={tables} />
      ))}
    </>
  );
}

function SectionView({
  section,
  tables,
}: {
  section: ClaimSection;
  tables: Map<ClaimBlock, number>;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-bold uppercase tracking-wide text-navy-800 pb-1.5 border-b-2 border-navy-800">
        {section.number}. {section.heading}
      </h2>
      <Blocks blocks={section.blocks} tables={tables} />

      {subsectionsOf(section).map((sub, i) => (
        <div key={i} className="space-y-2 pt-2">
          <h3 className="text-[13px] font-semibold text-navy-700">
            {sub.number} {sub.heading}
          </h3>
          <Blocks blocks={sub.blocks} tables={tables} />

          {(sub.parts ?? []).map((part, pi) => (
            <div key={pi} className="space-y-2 pl-3 border-l border-border">
              <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
                {part.number}. {part.heading}
              </h4>
              <Blocks blocks={part.blocks} tables={tables} />
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}

/**
 * EOT Report tab — the AI-drafted Extension of Time claim, assembled from every
 * project module: delay events, the Clause Library, admissibility, methodology,
 * queries and the data room. Generation runs in several passes and the document
 * is rendered as it builds, so sections appear before the whole claim is done.
 */
export function ProposalTab({ projectId }: { projectId: string }) {
  const { data: proposal, isLoading } = useProposal(projectId);
  const generate = useGenerateProposal(projectId);

  const running = proposal?.status === "running" || generate.isPending;
  const failed = proposal?.status === "failed";
  const doc = proposal?.content ?? null;
  const tables = useMemo(() => numberTables(doc), [doc]);

  function handleGenerate() {
    if (doc && !window.confirm("Regenerate the EOT report? This replaces the current draft.")) return;
    generate.mutate();
  }

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink">EOT report</h3>
          <p className="text-xs text-muted mt-0.5">
            Drafted from this project's delay events, clause library, admissibility, methodology,
            queries and data room.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {doc && (
            <button
              className="btn btn-outline btn-sm"
              onClick={() => downloadClaimPdf(doc, tables, proposal?.updatedAt)}
            >
              <Download className="size-4" /> Download PDF
            </button>
          )}
          <button className="btn btn-primary btn-sm" onClick={handleGenerate} disabled={running}>
            {running ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            {running ? "Generating…" : doc ? "Regenerate" : "Generate with AI"}
          </button>
        </div>
      </div>

      {failed && !running && (
        <div className="flex items-start gap-2 rounded-lg bg-error-bg/60 px-3 py-2.5 text-xs text-error">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{proposal?.error || "Failed to generate the EOT report."}</span>
        </div>
      )}

      {running && doc && (
        <div className="flex items-start gap-2 rounded-lg bg-navy-50/70 px-3 py-2.5 text-xs text-navy-700">
          <Loader2 className="size-4 shrink-0 mt-px animate-spin" />
          <span>
            Still drafting — sections appear here as they are completed. The delay events and the
            analysis are written last.
          </span>
        </div>
      )}

      {isLoading ? (
        <Card className="p-10 text-center text-sm text-muted inline-flex items-center justify-center gap-2 w-full">
          <Loader2 className="size-4 animate-spin" /> Loading…
        </Card>
      ) : running && !doc ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <Loader2 className="size-6 animate-spin" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Drafting the EOT report with AI…</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Claude is working through the contract, clauses and admissibility first, then each delay
            event in turn. The first sections will appear here shortly.
          </p>
        </Card>
      ) : !doc ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <FileSignature className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">No EOT report generated yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            The report is only as complete as the tabs behind it — analyse the Data Room, extract the
            Delay Events, load the Clause Library and run Admissibility first, then generate here.
          </p>
          <button className="btn btn-primary btn-sm mt-4 inline-flex" onClick={handleGenerate} disabled={running}>
            <Sparkles className="size-4" /> Generate with AI
          </button>
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <article className="px-6 py-6 sm:px-10 sm:py-8 max-w-4xl mx-auto">
            <header className="border-b-2 border-navy-800 pb-4 mb-7">
              <p className="text-[11px] uppercase tracking-wide text-faint inline-flex items-center gap-1.5">
                <Sparkles className="size-3.5 text-amber-500" /> AI-generated draft
                {proposal?.updatedAt ? ` · ${formatDate(proposal.updatedAt)}` : ""}
              </p>
              <h1 className="mt-2 text-xl font-bold text-ink leading-snug">{doc.title}</h1>
              {doc.reference && <p className="mt-1 text-sm text-muted">{doc.reference}</p>}
            </header>

            <div className="space-y-9">
              {(doc.sections ?? []).map((s, i) => (
                <SectionView key={i} section={s} tables={tables} />
              ))}
            </div>

            <p className="mt-9 pt-4 border-t border-border text-[11px] text-faint">
              AI-generated draft{proposal?.model ? ` · ${proposal.model}` : ""}. Review and verify
              against the contract and source documents before submission.
            </p>
          </article>
        </Card>
      )}
    </div>
  );
}
