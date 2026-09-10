import { useMemo, useState } from "react";
import { AlertTriangle, BookOpen, ChevronDown, ListChecks, Loader2, Scale, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { useDelayEvents, useDelayEventsExtractor } from "@/hooks/useDelayEvents";
import { useProjectDocuments } from "@/hooks/useProjectDocuments";
import { useClauseBookQuery, useProjectClausesQuery } from "@/hooks/useProjectClauses";
import { useBooksQuery } from "@/hooks/useKnowledge";
import type { ClauseRef, ProjectDelayEvent } from "@/types";
import { cn } from "@/lib/utils";

/** Just the digits and dots of a clause reference — "Sub-Clause 8.5(a)" → "8.5".
 *  Lets an event's cited clause match a library row however either is worded. */
function clauseKey(ref: string): string {
  const m = (ref || "").match(/\d+(?:\.\d+)*/);
  return m ? m[0] : "";
}

/** An event's narrative as a clean description (whitespace normalised, not cut). */
function description(text: string): string {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  return clean || "No description available.";
}

/** Lookup keys for a filename — normalised, with and without its extension — so a
 *  source cited by name still matches an uploaded document. */
function nameKeys(name: string): string[] {
  const full = (name || "").toLowerCase().replace(/\s+/g, " ").trim();
  const base = full.replace(/\.[a-z0-9]{1,5}$/, "");
  return base && base !== full ? [full, base] : [full];
}

/**
 * Delay-event identification for a proposal — the SAME AI extraction pipeline as
 * a project, but shown as a simple register of event name + short description
 * (no full forensic analysis). Auto-extracts once the documents are analysed.
 */
export function ProposalDelayEventsTab({ proposalId }: { proposalId: string }) {
  const { data: events = [], isLoading } = useDelayEvents(proposalId);
  const { data: docs = [], isLoading: docsLoading } = useProjectDocuments(proposalId);
  const extract = useDelayEventsExtractor(proposalId, !isLoading, events.length);
  const [openId, setOpenId] = useState<string | null>(null);

  // The contract book chosen for this proposal, and the clauses copied from it —
  // the AI cites these when it identifies the events, so each event is shown
  // against the actual book clause it falls under.
  const { data: bookId } = useClauseBookQuery(proposalId);
  const { data: books = [] } = useBooksQuery();
  const { data: clauses = [] } = useProjectClausesQuery(proposalId);
  const book = books.find((b) => b.id === bookId);
  const bookName = book ? [book.name, book.edition].filter(Boolean).join(" ") : "";

  // Book-sourced clauses indexed by their bare number, for the lookup below.
  const clausesByNumber = useMemo(() => {
    const map = new Map<string, ClauseRef>();
    for (const c of clauses) {
      const key = clauseKey(c.clause);
      if (key && !map.has(key)) map.set(key, c);
    }
    return map;
  }, [clauses]);

  /** The library clause an event cites, when it resolves to one. */
  const clauseFor = (e: ProjectDelayEvent): ClauseRef | undefined =>
    e.clause ? clausesByNumber.get(clauseKey(e.clause)) : undefined;

  const toggle = (id: string) => setOpenId((cur) => (cur === id ? null : id));

  // Every uploaded document, by id and by filename.
  const docKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const d of docs) {
      keys.add(d.id);
      for (const k of nameKeys(d.name)) keys.add(k);
    }
    return keys;
  }, [docs]);

  /**
   * True when none of an event's cited sources resolve to a document in the data
   * room — the AI identified the event from context but the evidence for it was
   * never uploaded, so the analyst has to supply it.
   */
  const unevidenced = (e: ProjectDelayEvent) =>
    !docsLoading &&
    !(e.sources ?? []).some(
      (s) => docKeys.has(s.id) || nameKeys(s.name).some((k) => docKeys.has(k)),
    );

  const unevidencedCount = events.filter(unevidenced).length;

  function handleExtract() {
    if (
      events.length > 0 &&
      !window.confirm("Re-identify delay events from the documents with AI? This replaces the current list.")
    )
      return;
    extract.start();
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink">Delay events</h3>
          <p className="text-xs text-muted mt-0.5">
            AI reads the uploaded documents and identifies the delay events — name, a short description,
            and the contract clause each falls under.
          </p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={handleExtract} disabled={extract.isRunning}>
          {extract.isRunning ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          {extract.isRunning ? "Identifying…" : "Identify with AI"}
        </button>
      </div>

      {/* The Knowledge-Center book whose clauses the events are assessed against. */}
      {bookName ? (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-navy-50/40 px-3 py-2.5 text-xs text-muted">
          <BookOpen className="size-4 shrink-0 mt-px text-navy-600" />
          <span>
            Clauses taken from <span className="font-semibold text-ink">{bookName}</span>
            {clauses.length > 0 && ` — ${clauses.length} clauses in this proposal's library`}. Each event
            below is cited against this book.
          </span>
        </div>
      ) : (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-warning-bg/50 px-3 py-2.5 text-xs text-warning">
          <BookOpen className="size-4 shrink-0 mt-px" />
          <span>
            No contract book is attached to this proposal, so the events can't be cited against real
            clauses. Pick one in the proposal's details (Edit proposal → Contract book).
          </span>
        </div>
      )}

      {extract.isRunning && (
        <div className="flex items-start gap-2 rounded-lg bg-navy-50/60 px-3 py-2.5 text-xs text-navy-700">
          <Sparkles className="size-4 shrink-0 mt-px text-amber-500" />
          <span>Claude is reading the documents and identifying the delay events. This can take a minute or two.</span>
        </div>
      )}
      {extract.error && (
        <div className="flex items-start gap-2 rounded-lg bg-error-bg/60 px-3 py-2.5 text-xs text-error">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{extract.error}</span>
        </div>
      )}

      {isLoading ? (
        <Card className="p-10 text-center text-sm text-muted inline-flex items-center justify-center gap-2 w-full">
          <Loader2 className="size-4 animate-spin" /> Loading delay events…
        </Card>
      ) : events.length === 0 && extract.isRunning ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <Loader2 className="size-6 animate-spin" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">Identifying delay events with AI…</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Claude is reading the uploaded documents and drafting the list of delay events.
          </p>
        </Card>
      ) : events.length === 0 ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <ListChecks className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">No delay events yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Upload the documents in the first tab and let AI analyse them, then identify the delay events here.
          </p>
          <button className="btn btn-primary btn-sm mt-4 inline-flex" onClick={handleExtract} disabled={extract.isRunning}>
            <Sparkles className="size-4" /> Identify with AI
          </button>
        </Card>
      ) : (
        <>
          <p className="text-xs text-muted">
            {events.length} delay event{events.length === 1 ? "" : "s"} identified
            {unevidencedCount > 0 && (
              <span className="text-warning">
                {" · "}
                {unevidencedCount} without a supporting document
              </span>
            )}
          </p>
          <div className="space-y-2.5">
            {events.map((e, i) => {
              const open = openId === e.id;
              const missingDoc = unevidenced(e);
              const clause = clauseFor(e);
              return (
                <Card key={e.id} className="p-0 overflow-hidden">
                  <button
                    type="button"
                    onClick={() => toggle(e.id)}
                    aria-expanded={open}
                    className="w-full flex items-center gap-3 p-4 text-left hover:bg-navy-50/50 transition-colors"
                  >
                    <span
                      className={cn(
                        "size-8 shrink-0 rounded-lg grid place-items-center text-sm font-semibold tabular-nums",
                        missingDoc ? "bg-warning-bg text-warning" : "bg-navy-100 text-navy-700",
                      )}
                    >
                      {i + 1}
                    </span>
                    <p className="min-w-0 flex-1 text-sm font-semibold text-ink leading-snug">{e.title}</p>
                    {e.clause && (
                      <span
                        className="shrink-0 inline-flex items-center gap-1 rounded-md bg-navy-100 px-2 py-0.5 text-[11px] font-semibold text-navy-700"
                        title={clause ? `${clause.clause} ${clause.title}` : e.clause}
                      >
                        <Scale className="size-3" /> {e.clause}
                      </span>
                    )}
                    {missingDoc && (
                      <span className="shrink-0 inline-flex items-center gap-1 rounded-md bg-warning-bg px-2 py-0.5 text-[11px] font-semibold text-warning">
                        <AlertTriangle className="size-3" /> No document
                      </span>
                    )}
                    <ChevronDown
                      className={cn("size-4 shrink-0 text-faint transition-transform", open && "rotate-180")}
                    />
                  </button>
                  {missingDoc && (
                    <div className="mx-4 mb-4 flex items-start gap-2 rounded-lg bg-warning-bg/60 px-3 py-2.5 text-xs text-warning">
                      <AlertTriangle className="size-4 shrink-0 mt-px" />
                      <span>
                        New event identified — no supporting document was provided for it. Upload the
                        correspondence or records evidencing this event in the Documents tab, then
                        re-run “Identify with AI”.
                      </span>
                    </div>
                  )}
                  {open && (
                    <div className="px-4 pb-4 pl-15 space-y-3">
                      <p className="text-sm text-muted leading-relaxed">{description(e.narrative)}</p>
                      {/* The book clause this event is assessed under — the wording
                          comes from the proposal's clause library, so it is the
                          selected book's own text, not a canned reference. */}
                      {clause ? (
                        <div className="rounded-lg border border-border bg-navy-50/40 px-3 py-2.5">
                          <p className="text-xs font-semibold text-ink flex items-center gap-1.5">
                            <Scale className="size-3.5 text-navy-600" />
                            {clause.clause} — {clause.title}
                            {clause.book && <span className="font-normal text-faint">· {clause.book}</span>}
                          </p>
                          {clause.summary && (
                            <p className="mt-1 text-xs text-muted leading-relaxed">{clause.summary}</p>
                          )}
                        </div>
                      ) : e.clause ? (
                        <p className="text-xs text-faint">
                          Cited clause <span className="font-semibold text-muted">{e.clause}</span> — not
                          found in this proposal's clause library.
                        </p>
                      ) : null}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
