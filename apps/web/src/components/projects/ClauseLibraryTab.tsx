import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  BookText,
  CheckCircle2,
  FileUp,
  Loader2,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { AddProjectClauseModal } from "@/components/projects/AddProjectClauseModal";
import { apiErrorMessage } from "@/api/client";
import {
  useClauseBookQuery,
  useClauseExtractStatus,
  useClearClauseBook,
  useDeleteProjectClause,
  useExtractProjectClauses,
  usePccStatus,
  useProjectClausesQuery,
  useSelectClauseBook,
  useUploadPcc,
} from "@/hooks/useProjectClauses";
import { useBooksQuery } from "@/hooks/useKnowledge";
import { useHasPermission } from "@/hooks/usePermission";
import type { ClauseRef } from "@/types";

/**
 * Per-project Clause Library tab.
 *
 * The project's clauses are built from a base standard-form contract book chosen
 * from the Knowledge Center (its clauses are copied in). The analyst may then
 * upload the project's Particular Conditions of Contract (PCC); Claude compares
 * them against the base clauses and flags the ones they amend as "Modified".
 * Clauses can also be added by hand. Delay events are later matched against these.
 */
export function ClauseLibraryTab({
  projectId,
  projectStandard,
}: {
  projectId: string;
  projectStandard?: string;
}) {
  const canManage = useHasPermission("contracts.manage");

  const { data: clauses, isLoading, isError, error } = useProjectClausesQuery(projectId);
  const deleteClause = useDeleteProjectClause(projectId);

  const { data: books = [] } = useBooksQuery();
  const { data: selectedBookId } = useClauseBookQuery(projectId);
  const selectBook = useSelectClauseBook(projectId);
  const clearBook = useClearClauseBook(projectId);

  const uploadPcc = useUploadPcc(projectId);
  const pccStatus = usePccStatus(projectId);

  const extractContract = useExtractProjectClauses(projectId);
  const extractStatus = useClauseExtractStatus(projectId);

  const pccRef = useRef<HTMLInputElement>(null);
  const contractRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "modified" | "new">("all");
  const [showAdd, setShowAdd] = useState(false);
  const [editClause, setEditClause] = useState<ClauseRef | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ClauseRef | null>(null);
  const [confirmClearBook, setConfirmClearBook] = useState(false);
  const [pccErr, setPccErr] = useState("");
  const [bookErr, setBookErr] = useState("");
  const [contractErr, setContractErr] = useState("");

  // Only books whose clauses finished extracting can be used as a base.
  const readyBooks = useMemo(() => books.filter((b) => b.status === "done"), [books]);
  const hasBook = !!selectedBookId;
  const bookBusy = selectBook.isPending || clearBook.isPending;

  // PCC comparison lifecycle.
  const pStatus = pccStatus.data?.status;
  const comparing = uploadPcc.isPending || pStatus === "running";
  const pccError = pStatus === "failed" ? pccStatus.data?.error || "PCC comparison failed." : "";
  const pccDone = pStatus === "done";
  const modifiedCount = pccStatus.data?.modified ?? pccStatus.data?.count ?? 0;
  const addedCount = pccStatus.data?.added ?? 0;
  const pccNotConfigured = pStatus === "idle" ? pccStatus.data?.error : "";

  // Full-contract extraction lifecycle (contract with PCC already included).
  const eStatus = extractStatus.data?.status;
  const extracting = extractContract.isPending || eStatus === "running";
  const extractError =
    eStatus === "failed" ? extractStatus.data?.error || "Clause extraction failed." : "";
  const extractDone = eStatus === "done";
  const extractCount = extractStatus.data?.count ?? 0;
  const extractModified = extractStatus.data?.modified ?? 0;
  const extractNotConfigured = eStatus === "idle" ? extractStatus.data?.error : "";

  const library = useMemo(() => clauses ?? [], [clauses]);

  // "New" = introduced by the PCC; "Modified" = a base clause the PCC amends.
  const newTotal = useMemo(() => library.filter((c) => c.source === "pcc").length, [library]);
  const modifiedTotal = useMemo(
    () => library.filter((c) => c.modified && c.source !== "pcc").length,
    [library],
  );

  // Unselecting the book drops its copied clauses and anything the PCC added or
  // amended on top of them — manual and AI-extracted clauses stay.
  const bookSourcedTotal = useMemo(
    () => library.filter((c) => c.source === "book" || c.source === "pcc").length,
    [library],
  );

  const filtered = useMemo(() => {
    let list = library;
    if (filter === "new") list = list.filter((c) => c.source === "pcc");
    else if (filter === "modified") list = list.filter((c) => c.modified && c.source !== "pcc");
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (c) =>
        c.clause.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q) ||
        c.book.toLowerCase().includes(q) ||
        c.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [query, filter, library]);

  function confirmDelete() {
    if (!deleteTarget) return;
    deleteClause.mutate(deleteTarget.id);
    setDeleteTarget(null);
  }

  async function onSelectBook(bookId: string) {
    setBookErr("");
    // The blank option unselects the current book; confirm first, since it
    // removes the clauses copied from it.
    if (!bookId) {
      if (hasBook) setConfirmClearBook(true);
      return;
    }
    try {
      await selectBook.mutateAsync(bookId);
    } catch (err) {
      setBookErr(apiErrorMessage(err, "Could not load the selected book's clauses."));
    }
  }

  async function onClearBook() {
    setConfirmClearBook(false);
    setBookErr("");
    try {
      await clearBook.mutateAsync();
    } catch (err) {
      setBookErr(apiErrorMessage(err, "Could not remove the base contract book."));
    }
  }

  async function onPickPcc(file: File | undefined) {
    if (!file) return;
    setPccErr("");
    try {
      await uploadPcc.mutateAsync(file);
    } catch (err) {
      setPccErr(apiErrorMessage(err, "Could not upload the Particular Conditions."));
    } finally {
      if (pccRef.current) pccRef.current.value = "";
    }
  }

  async function onPickContract(file: File | undefined) {
    if (!file) return;
    setContractErr("");
    try {
      await extractContract.mutateAsync(file);
    } catch (err) {
      setContractErr(apiErrorMessage(err, "Could not upload the contract."));
    } finally {
      if (contractRef.current) contractRef.current.value = "";
    }
  }

  return (
    <div className="space-y-4">
      {/* ── Base contract book + Particular Conditions ── */}
      <Card className="p-5 space-y-5">
        <div className="max-w-2xl">
          <h3 className="text-base font-semibold text-ink inline-flex items-center gap-2">
            <Sparkles className="size-4 text-amber-500" /> Build this project's clause library
          </h3>
          <p className="text-sm text-muted mt-1">
            Pick the base contract book (its General Conditions clauses are copied into this
            project). Optionally upload the project's Particular Conditions — Claude compares them
            against the base clauses and flags the ones they amend.
          </p>
        </div>

        {/* Base book selector */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-65 flex-1 max-w-md">
            <label className="block text-xs font-medium text-muted mb-1" htmlFor="base-book">
              Base contract book
            </label>
            <select
              id="base-book"
              className="input"
              value={selectedBookId ?? ""}
              disabled={!canManage || bookBusy}
              onChange={(e) => onSelectBook(e.target.value)}
            >
              <option value="">
                {hasBook
                  ? "None — remove the base book's clauses"
                  : readyBooks.length
                    ? "Select a contract book…"
                    : "No books ready in the Knowledge Center"}
              </option>
              {readyBooks.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                  {b.edition ? ` (${b.edition})` : ""} · {b.clauseCount} clauses
                </option>
              ))}
            </select>
            {readyBooks.length === 0 && (
              <p className="mt-1 text-xs text-muted">
                Upload a standard form in the{" "}
                <Link to="/knowledge" className="text-navy-700 hover:underline">
                  Knowledge Center
                </Link>{" "}
                first.
              </p>
            )}
          </div>

          {canManage && (
            <div className="flex items-center gap-2">
              {selectBook.isPending && (
                <span className="text-sm text-navy-700 inline-flex items-center gap-1.5">
                  <Loader2 className="size-4 animate-spin" /> Copying clauses…
                </span>
              )}
              {clearBook.isPending && (
                <span className="text-sm text-navy-700 inline-flex items-center gap-1.5">
                  <Loader2 className="size-4 animate-spin" /> Removing clauses…
                </span>
              )}
              {hasBook && (
                <button
                  className="btn btn-outline"
                  onClick={() => setConfirmClearBook(true)}
                  disabled={bookBusy}
                  title="Unselect the base contract book"
                >
                  <X className="size-4" /> Remove book
                </button>
              )}
              <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
                <Plus className="size-4" /> Add clause
              </button>
            </div>
          )}
        </div>

        {/* Particular Conditions upload */}
        <div className="flex flex-wrap items-end gap-3 pt-1 border-t border-border">
          <div className="flex-1 min-w-65 pt-3">
            <p className="text-sm font-medium text-ink">Particular Conditions of Contract (optional)</p>
            <p className="text-xs text-muted mt-0.5">
              Upload the project's PCC to mark which base clauses are modified.
            </p>
          </div>
          {canManage && (
            <div className="pt-3">
              <button
                className="btn btn-outline"
                onClick={() => pccRef.current?.click()}
                disabled={!hasBook || comparing}
                title={hasBook ? undefined : "Select a base contract book first"}
              >
                {comparing ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
                {uploadPcc.isPending ? "Uploading…" : comparing ? "Comparing…" : "Upload Particular Conditions"}
              </button>
            </div>
          )}
          <input
            ref={pccRef}
            type="file"
            accept=".pdf,.doc,.docx,.txt,.rtf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            className="hidden"
            onChange={(e) => onPickPcc(e.target.files?.[0])}
          />
        </div>

        {/* ── Full contract with the PCC already included ── */}
        <div className="flex flex-wrap items-end gap-3 pt-1 border-t border-border">
          <div className="flex-1 min-w-65 pt-3">
            <p className="text-sm font-medium text-ink">
              Or upload the full contract (Particular Conditions included)
            </p>
            <p className="text-xs text-muted mt-0.5">
              No base book needed — Claude reads the whole contract, extracts the claim-relevant
              clauses, and flags the ones its Particular Conditions amend.
            </p>
          </div>
          {canManage && (
            <div className="pt-3">
              <button
                className="btn btn-outline"
                onClick={() => contractRef.current?.click()}
                disabled={extracting}
              >
                {extracting ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
                {extractContract.isPending
                  ? "Uploading…"
                  : extracting
                    ? "Extracting…"
                    : "Upload Full Contract"}
              </button>
            </div>
          )}
          <input
            ref={contractRef}
            type="file"
            accept=".pdf,.doc,.docx,.txt,.rtf,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            className="hidden"
            onChange={(e) => onPickContract(e.target.files?.[0])}
          />
        </div>

        {/* ── Banners ── */}
        {comparing && (
          <p className="text-sm text-navy-700 bg-navy-50 rounded-md px-3 py-2 inline-flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Claude is comparing the Particular Conditions
            against the base clauses. This can take a minute.
          </p>
        )}
        {pccDone && (
          <p className="text-sm text-success bg-success-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <CheckCircle2 className="size-4" /> Comparison complete — {modifiedCount} clause
            {modifiedCount === 1 ? "" : "s"} modified
            {addedCount > 0 ? `, ${addedCount} new clause${addedCount === 1 ? "" : "s"} added` : ""} by the
            Particular Conditions.
          </p>
        )}
        {extracting && (
          <p className="text-sm text-navy-700 bg-navy-50 rounded-md px-3 py-2 inline-flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Claude is reading the contract and
            extracting its clauses. This can take a few minutes for a large or scanned contract.
          </p>
        )}
        {extractDone && (
          <p className="text-sm text-success bg-success-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <CheckCircle2 className="size-4" /> Extraction complete — {extractCount} clause
            {extractCount === 1 ? "" : "s"} added
            {extractModified > 0
              ? `, ${extractModified} flagged as amended by the Particular Conditions`
              : ""}
            .
          </p>
        )}
        {extractError && (
          <p className="text-sm text-error bg-error-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <AlertTriangle className="size-4" /> {extractError}
          </p>
        )}
        {(pccNotConfigured || extractNotConfigured) && (
          <p className="inline-flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 rounded-md px-2.5 py-1">
            <AlertTriangle className="size-3.5" /> {pccNotConfigured || extractNotConfigured}
          </p>
        )}
        {pccError && (
          <p className="text-sm text-error bg-error-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <AlertTriangle className="size-4" /> {pccError}
          </p>
        )}
        {bookErr && (
          <p className="text-sm text-error bg-error-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <AlertTriangle className="size-4" /> {bookErr}
          </p>
        )}
        {pccErr && (
          <p className="text-sm text-error bg-error-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <AlertTriangle className="size-4" /> {pccErr}
          </p>
        )}
        {contractErr && (
          <p className="text-sm text-error bg-error-bg rounded-md px-3 py-2 inline-flex items-center gap-2">
            <AlertTriangle className="size-4" /> {contractErr}
          </p>
        )}
      </Card>

      {/* ── Search + filter tabs ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative max-w-md flex-1 min-w-60">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-faint" />
          <input
            className="input pl-9"
            placeholder="Search this project's clauses…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1.5" role="tablist" aria-label="Filter clauses">
          <button
            role="tab"
            aria-selected={filter === "all"}
            className={`badge cursor-pointer transition-colors ${
              filter === "all"
                ? "bg-navy-700 text-white"
                : "bg-navy-50 text-navy-700 hover:bg-navy-100"
            }`}
            onClick={() => setFilter("all")}
          >
            All · {library.length}
          </button>
          <button
            role="tab"
            aria-selected={filter === "modified"}
            className={`badge cursor-pointer transition-colors ${
              filter === "modified"
                ? "bg-amber-500 text-white"
                : "bg-amber-100 text-amber-800 hover:bg-amber-200"
            }`}
            onClick={() => setFilter("modified")}
          >
            <AlertTriangle className="size-3" /> Modified · {modifiedTotal}
          </button>
          <button
            role="tab"
            aria-selected={filter === "new"}
            className={`badge cursor-pointer transition-colors ${
              filter === "new"
                ? "bg-emerald-600 text-white"
                : "bg-emerald-100 text-emerald-800 hover:bg-emerald-200"
            }`}
            onClick={() => setFilter("new")}
          >
            <Plus className="size-3" /> New · {newTotal}
          </button>
        </div>
      </div>

      {/* ── Clause list ── */}
      {isLoading ? (
        <div className="py-16 text-center text-muted inline-flex items-center gap-2 justify-center w-full">
          <Loader2 className="size-4 animate-spin" /> Loading clauses…
        </div>
      ) : isError ? (
        <p className="py-16 text-center text-error">
          {apiErrorMessage(error, "Could not load this project's clauses.")}
        </p>
      ) : library.length === 0 ? (
        <Card className="p-10 text-center">
          <span className="size-12 mx-auto rounded-xl bg-navy-50 text-navy-600 grid place-items-center">
            <BookText className="size-6" />
          </span>
          <h3 className="mt-3 font-semibold text-ink">No clauses yet</h3>
          <p className="mt-1 text-sm text-muted max-w-md mx-auto">
            Select a base contract book above (or add a clause by hand) to start building this
            project's library.
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((c) => {
            const isNew = c.source === "pcc";
            // A modified clause is shown in three parts: the base standard-form
            // wording, the PCC amendment, then the clause as amended. `summary`
            // already holds the amended wording; `baseDescription` the original.
            const baseText = (c.baseDescription ?? "").trim();
            const amendedText = (c.summary ?? "").trim();
            // A PCC entry without amended wording leaves the description at the
            // base text — no third part to show in that case.
            const showAmended = !!amendedText && amendedText !== baseText;
            return (
            <Card
              key={c.id}
              className={`card-hover p-5 relative group ${
                isNew ? "ring-1 ring-emerald-300" : c.modified ? "ring-1 ring-amber-300" : ""
              }`}
            >
              {canManage && (
                <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity bg-card/90 rounded-lg">
                  <button
                    className="btn btn-ghost px-2"
                    title="Edit clause"
                    aria-label={`Edit clause ${c.clause}`}
                    onClick={() => setEditClause(c)}
                  >
                    <Pencil className="size-4 text-navy-700" />
                  </button>
                  <button
                    className="btn btn-ghost px-2"
                    title="Delete clause"
                    aria-label={`Delete clause ${c.clause}`}
                    onClick={() => setDeleteTarget(c)}
                  >
                    <Trash2 className="size-4 text-error" />
                  </button>
                </div>
              )}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="badge bg-navy-50 text-navy-700 min-w-0 max-w-full" title={c.book}>
                    <BookText className="size-3 shrink-0" />
                    <span className="truncate">{c.book}</span>
                  </span>
                  {isNew ? (
                    <span className="badge bg-emerald-100 text-emerald-800 shrink-0" title="Added by the Particular Conditions">
                      <Plus className="size-3" /> New clause
                    </span>
                  ) : (
                    c.modified && (
                      <span className="badge bg-amber-100 text-amber-800 shrink-0" title={c.modificationNote}>
                        <AlertTriangle className="size-3" /> Modified
                      </span>
                    )
                  )}
                </div>
                <span className="text-xs font-bold text-faint tabular-nums shrink-0">Clause {c.clause}</span>
              </div>
              <h3 className="mt-2.5 font-semibold text-ink">{c.title}</h3>
              {!isNew && c.modified ? (
                /* Read in the order the amendment happened: the base clause as it
                   stands in the standard form, what the PCC changes, then the
                   clause as it now reads under this contract. */
                <div className="mt-2 space-y-2">
                  <div className="rounded-md border border-border px-2.5 py-1.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-faint">
                      {c.book} · Clause {c.clause}
                    </p>
                    {baseText ? (
                      <p className="mt-0.5 text-sm text-muted">{baseText}</p>
                    ) : (
                      <p className="mt-0.5 text-sm italic text-faint">
                        Base wording isn't recorded for this clause — re-upload the Particular
                        Conditions to capture it.
                      </p>
                    )}
                  </div>
                  <div className="rounded-md bg-amber-50 px-2.5 py-1.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">
                      PCC amendment
                    </p>
                    <p className="mt-0.5 text-sm text-amber-900">
                      {c.modificationNote || "Amended by the Particular Conditions."}
                    </p>
                  </div>
                  {showAmended && (
                    <div className="rounded-md bg-navy-50 px-2.5 py-1.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-navy-700">
                        Clause as amended
                      </p>
                      <p className="mt-0.5 text-sm text-ink">{amendedText}</p>
                    </div>
                  )}
                  {c.interpretation && (
                    <div className="rounded-md bg-emerald-50 px-2.5 py-1.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
                        Interpretation
                      </p>
                      <p className="mt-0.5 text-sm text-emerald-900">{c.interpretation}</p>
                    </div>
                  )}
                </div>
              ) : (
                <p className="mt-1 text-sm text-muted">{c.summary}</p>
              )}
              {isNew && (
                <p className="mt-2 text-xs text-emerald-800 bg-emerald-50 rounded-md px-2.5 py-1.5">
                  Introduced by the project's Particular Conditions — not in the base contract book.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {c.tags.map((t) => (
                  <span key={t} className="text-[11px] font-medium text-muted bg-navy-50 rounded px-2 py-0.5">#{t}</span>
                ))}
              </div>
            </Card>
            );
          })}
          {filtered.length === 0 && (
            <p className="col-span-full text-center text-muted py-12">
              {query.trim()
                ? "No clauses match your search."
                : filter === "new"
                  ? "No new clauses were added by the Particular Conditions."
                  : filter === "modified"
                    ? "No clauses were modified by the Particular Conditions."
                    : "No clauses match your search."}
            </p>
          )}
        </div>
      )}

      {showAdd && (
        <AddProjectClauseModal
          projectId={projectId}
          projectStandard={projectStandard}
          onClose={() => setShowAdd(false)}
        />
      )}
      {editClause && (
        <AddProjectClauseModal
          projectId={projectId}
          projectStandard={projectStandard}
          clause={editClause}
          onClose={() => setEditClause(null)}
        />
      )}

      <ConfirmDialog
        open={confirmClearBook}
        title="Remove base contract book"
        message={`Unselect the base contract book? This removes the ${bookSourcedTotal} clause${
          bookSourcedTotal === 1 ? "" : "s"
        } copied from it, along with any Particular Conditions changes on top of them. Clauses you added by hand or extracted from an uploaded contract are kept.`}
        confirmLabel="Remove book"
        onConfirm={onClearBook}
        onCancel={() => setConfirmClearBook(false)}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete clause"
        message={`Delete Clause ${deleteTarget?.clause ?? ""} — ${deleteTarget?.title ?? "this clause"}? This removes it from the project's library.`}
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
