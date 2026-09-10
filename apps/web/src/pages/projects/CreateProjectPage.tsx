import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, BookOpen, FolderKanban, Loader2, Paperclip, UploadCloud, X } from "lucide-react";
import { apiErrorMessage } from "@/api/client";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { useAllProjects, useCreateProject, type ProjectDetails } from "@/store/projects";
import { useBooksQuery } from "@/hooks/useKnowledge";
import { useClauseBookQuery, useSelectClauseBook } from "@/hooks/useProjectClauses";
import { selectClauseBookApi } from "@/api/projectClauses";
import type { ContractBook } from "@/api/knowledge";
import type { ContractStandard } from "@/types";

const CURRENCIES = ["OMR", "AED", "USD", "SAR", "QAR", "KWD", "BHD"];

/** A book's display name — "FIDIC Red Book 2017" — used as the project's
 *  contract standard once the book is picked. */
function bookLabel(b: ContractBook): string {
  return b.edition ? `${b.name} ${b.edition}`.trim() : b.name;
}

const STEP_IDS = ["basics", "contract", "dates", "baseline"] as const;
type StepId = (typeof STEP_IDS)[number];
const STEPS: { id: StepId; n: number; title: string }[] = [
  { id: "basics", n: 1, title: "Basics" },
  { id: "contract", n: 2, title: "Contract" },
  { id: "dates", n: 3, title: "Key dates" },
  { id: "baseline", n: 4, title: "Baseline" },
];

/**
 * Create Project — 4-step wizard run by an Al Qarar admin. Produces the central
 * Project object everything else hangs off (documents, delay events, windows
 * analysis, the EOT claim). The new project becomes assignable to clients and
 * visible in their portal.
 */
export function CreateProjectPage() {
  const navigate = useNavigate();
  const { id: editId } = useParams<{ id: string }>();
  const allProjects = useAllProjects();
  const editing = editId ? allProjects.find((p) => p.id === editId) : undefined;
  const isEdit = Boolean(editId);
  const fileRef = useRef<HTMLInputElement>(null);

  // Basics
  const [name, setName] = useState(editing?.name ?? "");
  const [location, setLocation] = useState(editing?.location ?? "");
  const [employer, setEmployer] = useState(editing?.employer ?? "");
  const [engineer, setEngineer] = useState(editing?.engineer ?? "");
  const [contractor, setContractor] = useState(editing?.contractor ?? "");

  // Contract — the standard comes from a Knowledge-Center book, so the project's
  // clause library, delay-event analysis and EOT claim all cite its real clauses.
  const { data: books = [], isLoading: booksLoading } = useBooksQuery();
  // Only a fully extracted book can be used: its clauses are what get copied in.
  const readyBooks = useMemo(
    () => books.filter((b) => b.status === "done" && b.clauseCount > 0),
    [books],
  );
  const pendingBooks = useMemo(
    () => books.filter((b) => b.status === "pending" || b.status === "processing"),
    [books],
  );
  const [bookId, setBookId] = useState("");
  const selectedBook = readyBooks.find((b) => b.id === bookId);
  const standard = (selectedBook ? bookLabel(selectedBook) : "") as ContractStandard;

  // The book already attached to this project (edit mode) — pre-selects the
  // picker, and tells us whether the clauses need re-copying on save.
  const { data: existingBookId } = useClauseBookQuery(editId ?? "");
  const selectBook = useSelectClauseBook(editId ?? "");
  const seededBook = useRef(false);
  useEffect(() => {
    if (!isEdit || seededBook.current || !existingBookId) return;
    seededBook.current = true;
    setBookId(existingBookId);
  }, [isEdit, existingBookId]);

  const [value, setValue] = useState(editing?.value ? String(editing.value) : "");
  const [currency, setCurrency] = useState(editing?.currency ?? "OMR");
  const [loaRef, setLoaRef] = useState(editing?.loaRef ?? "");

  // Key dates
  const [commencementDate, setCommencementDate] = useState(editing?.commencementDate ?? "");
  const [completionDate, setCompletionDate] = useState(editing?.completionDate ?? "");
  const [timeForCompletion, setTimeForCompletion] = useState(editing?.timeForCompletionDays ? String(editing.timeForCompletionDays) : "");
  const [dataDate, setDataDate] = useState(editing?.dataDate ?? "");

  // Baseline
  const [baselineProgramme, setBaselineProgramme] = useState(editing?.baselineProgramme ?? "");

  const [errors, setErrors] = useState<{ name?: string; contractor?: string; book?: string }>({});
  const [submitError, setSubmitError] = useState("");
  const [tab, setTab] = useState<StepId>("basics");

  const createProject = useCreateProject();

  function submit(e: FormEvent) {
    e.preventDefault();
    const next: typeof errors = {};
    if (!name.trim()) next.name = "Project name is required.";
    if (!contractor.trim()) next.contractor = "Contractor is required.";
    if (!bookId) next.book = "Choose the contract book this project is governed by.";
    setErrors(next);
    if (Object.keys(next).length) {
      // Land on the step holding the first problem, so the message is visible.
      setTab(next.name || next.contractor ? "basics" : "contract");
      return;
    }

    const id = editing?.id ?? `p-${Date.now()}`;
    const code = loaRef.trim() || editing?.code || `PRJ-${new Date().getFullYear()}-${String(Date.now()).slice(-4)}`;
    const project: ProjectDetails = {
      id,
      name: name.trim(),
      code,
      employer: employer.trim(),
      contractor: contractor.trim(),
      standard,
      value: Number(value) || 0,
      currency,
      startDate: commencementDate || "",
      completionDate: completionDate || "",
      status: "Active",
      riskLevel: "Moderate",
      source: "created",
      location: location.trim() || undefined,
      engineer: engineer.trim() || undefined,
      loaRef: loaRef.trim() || undefined,
      commencementDate: commencementDate || undefined,
      timeForCompletionDays: Number(timeForCompletion) || undefined,
      dataDate: dataDate || undefined,
      baselineProgramme: baselineProgramme || undefined,
      createdAt: editing?.createdAt ?? new Date().toISOString(),
    };
    setSubmitError("");
    createProject.mutate(project, {
      onSuccess: async () => {
        // Copy the chosen book's clauses into the project's clause library — the
        // base set the delay-event analysis and any PCC comparison work from.
        // On edit, only when the book actually changed: re-selecting the same one
        // would discard the PCC amendments sitting on top of it.
        if (!isEdit || bookId !== existingBookId) {
          try {
            if (isEdit) await selectBook.mutateAsync(bookId);
            else await selectClauseBookApi(id, bookId);
          } catch (err) {
            setSubmitError(apiErrorMessage(err, "Could not attach the contract book's clauses."));
            return;
          }
        }
        navigate("/projects");
      },
      onError: (err) => setSubmitError(apiErrorMessage(err, "Could not create project — is the backend running?")),
    });
  }

  return (
    <div>
      <Link to="/projects" className="inline-flex items-center gap-1.5 text-sm font-medium text-muted hover:text-navy-700 mb-4">
        <ArrowLeft className="size-4" /> Projects
      </Link>

      <div className="mb-6">
        <h1 className="text-[26px] leading-tight font-bold text-ink tracking-tight">{isEdit ? "Edit project" : "New project"}</h1>
        <p className="mt-1.5 text-sm text-muted">
          {isEdit
            ? "Update this project's details."
            : "Set up the project Al Qarar is engaged on. Everything — documents, delay events, windows analysis and the EOT claim — is organised under it."}
        </p>
      </div>

      <form onSubmit={submit} className="space-y-5">
        <Card className="overflow-hidden">
          {/* Stepper */}
          <div className="px-5 sm:px-6 py-5 border-b border-border">
            <ol className="flex items-center gap-2 sm:gap-4">
              {STEPS.map((s, i) => {
                const isActive = tab === s.id;
                const isComplete = STEP_IDS.indexOf(tab) > i;
                const done = isActive || isComplete;
                return (
                  <li key={s.id} className="flex items-center gap-2 sm:gap-4 flex-1 last:flex-none min-w-0">
                    <button type="button" onClick={() => setTab(s.id)} className="flex items-center gap-3 min-w-0 text-left">
                      <span className={cn("grid place-items-center size-9 rounded-full text-sm font-semibold shrink-0 transition-colors", done ? "bg-navy-900 text-white" : "bg-navy-50 text-faint border border-border")}>
                        {s.n}
                      </span>
                      <span className="min-w-0 hidden sm:block">
                        <span className={cn("block text-[11px] font-bold uppercase tracking-wide", done ? "text-navy-700" : "text-faint")}>Step {s.n}</span>
                        <span className={cn("block text-sm font-medium truncate", isActive ? "text-ink" : "text-muted")}>{s.title}</span>
                      </span>
                    </button>
                    {i < STEPS.length - 1 && <span className={cn("hidden sm:block h-px flex-1 min-w-6", isComplete ? "bg-navy-300" : "bg-border")} />}
                  </li>
                );
              })}
            </ol>
          </div>

          {/* Step 1 — Basics */}
          {tab === "basics" && (
            <>
              <CardHeader title="Project basics" subtitle="The project and the parties" />
              <div className="p-5 space-y-5">
                <div>
                  <label className="label" htmlFor="name">Project name</label>
                  <input id="name" className="input" placeholder="e.g. Yiti Marina Hotel — Balancing Works" value={name} onChange={(e) => setName(e.target.value)} />
                  {errors.name && <p className="mt-1 text-xs text-error">{errors.name}</p>}
                </div>
                <div>
                  <label className="label" htmlFor="location">Location</label>
                  <input id="location" className="input" placeholder="e.g. Yiti, Sultanate of Oman" value={location} onChange={(e) => setLocation(e.target.value)} />
                </div>
                <div className="grid sm:grid-cols-3 gap-5">
                  <div>
                    <label className="label" htmlFor="employer">Employer</label>
                    <input id="employer" className="input" placeholder="e.g. SSH" value={employer} onChange={(e) => setEmployer(e.target.value)} />
                  </div>
                  <div>
                    <label className="label" htmlFor="engineer">Engineer</label>
                    <input id="engineer" className="input" placeholder="e.g. GIC" value={engineer} onChange={(e) => setEngineer(e.target.value)} />
                  </div>
                  <div>
                    <label className="label" htmlFor="contractor">Contractor</label>
                    <input id="contractor" className="input" placeholder="e.g. GIC" value={contractor} onChange={(e) => setContractor(e.target.value)} />
                    {errors.contractor && <p className="mt-1 text-xs text-error">{errors.contractor}</p>}
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Step 2 — Contract */}
          {tab === "contract" && (
            <>
              <CardHeader title="Contract" subtitle="The contractual basis for the engagement" />
              <div className="p-5 space-y-5">
                <div>
                  <label className="label" htmlFor="bookId">Contract book</label>
                  <select
                    id="bookId"
                    className="input"
                    value={bookId}
                    onChange={(e) => {
                      setBookId(e.target.value);
                      setErrors((prev) => ({ ...prev, book: undefined }));
                    }}
                    disabled={booksLoading}
                  >
                    <option value="">
                      {booksLoading
                        ? "Loading contract books…"
                        : readyBooks.length === 0
                          ? "No contract books available"
                          : "Select a contract book…"}
                    </option>
                    {readyBooks.map((b) => (
                      <option key={b.id} value={b.id}>
                        {bookLabel(b)} — {b.clauseCount} clause{b.clauseCount === 1 ? "" : "s"}
                      </option>
                    ))}
                  </select>
                  {errors.book && <p className="mt-1 text-xs text-error">{errors.book}</p>}

                  {/* What the chosen book does for the project, and how to get one
                      when the Knowledge Center is empty or still extracting. */}
                  {selectedBook ? (
                    <div className="mt-2 flex items-start gap-2 rounded-lg border border-border bg-navy-50/40 px-3 py-2.5 text-xs">
                      <BookOpen className="size-4 shrink-0 mt-px text-navy-600" />
                      <span className="text-muted">
                        <span className="font-semibold text-ink">{bookLabel(selectedBook)}</span>
                        {selectedBook.publisher ? ` · ${selectedBook.publisher}` : ""} — its{" "}
                        {selectedBook.clauseCount} extracted clauses become this project's Clause
                        Library, and the AI cites them across the delay events and the EOT claim.
                      </span>
                    </div>
                  ) : !booksLoading && readyBooks.length === 0 ? (
                    <div className="mt-2 flex items-start gap-2 rounded-lg border border-border bg-warning-bg/50 px-3 py-2.5 text-xs text-warning">
                      <BookOpen className="size-4 shrink-0 mt-px" />
                      <span>
                        {pendingBooks.length > 0
                          ? `${pendingBooks.length} book${pendingBooks.length === 1 ? " is" : "s are"} still being read by the AI — selectable once extraction finishes. `
                          : "No contract books have been uploaded yet. "}
                        <Link to="/knowledge" className="font-semibold underline">
                          Open the Knowledge Center
                        </Link>{" "}
                        to upload one.
                      </span>
                    </div>
                  ) : (
                    <p className="mt-1 text-xs text-faint">
                      The contract this project is governed by — picked from the{" "}
                      <Link to="/knowledge" className="underline">Knowledge Center</Link>. Its clauses
                      become the project's Clause Library.
                    </p>
                  )}
                </div>
                <div className="grid sm:grid-cols-3 gap-5">
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="value">Contract value</label>
                    <input id="value" type="number" min="0" className="input" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} />
                  </div>
                  <div>
                    <label className="label" htmlFor="currency">Currency</label>
                    <select id="currency" className="input" value={currency} onChange={(e) => setCurrency(e.target.value)}>
                      {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="label" htmlFor="loaRef">LOA / LPO reference</label>
                  <input id="loaRef" className="input" placeholder="e.g. LPO-46 Al Qarar Management Solutions" value={loaRef} onChange={(e) => setLoaRef(e.target.value)} />
                  <p className="mt-1 text-xs text-faint">Used as the project code if provided.</p>
                </div>
              </div>
            </>
          )}

          {/* Step 3 — Key dates */}
          {tab === "dates" && (
            <>
              <CardHeader title="Key dates" subtitle="These anchor the windows / delay analysis" />
              <div className="p-5 space-y-5">
                <div className="grid sm:grid-cols-2 gap-5">
                  <div>
                    <label className="label" htmlFor="commencementDate">Commencement date</label>
                    <input id="commencementDate" type="date" className="input" value={commencementDate} onChange={(e) => setCommencementDate(e.target.value)} />
                  </div>
                  <div>
                    <label className="label" htmlFor="completionDate">Baseline completion date</label>
                    <input id="completionDate" type="date" className="input" value={completionDate} onChange={(e) => setCompletionDate(e.target.value)} />
                  </div>
                </div>
                <div className="grid sm:grid-cols-2 gap-5">
                  <div>
                    <label className="label" htmlFor="timeForCompletion">Time for Completion (days)</label>
                    <input id="timeForCompletion" type="number" min="0" className="input" placeholder="e.g. 730" value={timeForCompletion} onChange={(e) => setTimeForCompletion(e.target.value)} />
                  </div>
                  <div>
                    <label className="label" htmlFor="dataDate">Current data date</label>
                    <input id="dataDate" type="date" className="input" value={dataDate} onChange={(e) => setDataDate(e.target.value)} />
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Step 4 — Baseline programme */}
          {tab === "baseline" && (
            <>
              <CardHeader title="Baseline programme" subtitle="The approved baseline used for delay analysis" />
              <div className="p-5 space-y-4">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xer,.xml,.mpp,.pdf"
                  className="hidden"
                  onChange={(e) => setBaselineProgramme(e.target.files?.[0]?.name ?? "")}
                />
                {baselineProgramme ? (
                  <div className="flex items-center gap-3 rounded-lg border border-border bg-navy-50/40 p-3.5">
                    <Paperclip className="size-4 text-navy-600 shrink-0" />
                    <span className="text-sm text-ink flex-1 truncate">{baselineProgramme}</span>
                    <button type="button" className="btn btn-ghost px-2" onClick={() => setBaselineProgramme("")} aria-label="Remove">
                      <X className="size-4" />
                    </button>
                  </div>
                ) : (
                  <button type="button" onClick={() => fileRef.current?.click()} className="w-full rounded-xl border border-dashed border-border-strong hover:border-navy-300 transition-colors p-8 text-center">
                    <UploadCloud className="size-7 text-faint mx-auto mb-2" />
                    <p className="text-sm font-medium text-ink">Upload approved baseline programme</p>
                    <p className="text-xs text-muted mt-0.5">Primavera P6 .xer / .xml, MS Project .mpp, or PDF</p>
                  </button>
                )}
                <p className="text-xs text-faint">Optional now — you can upload the baseline and approval letters later from the project's Data Room.</p>
              </div>
            </>
          )}
        </Card>

        {submitError && <p className="text-sm text-error bg-error-bg rounded-lg px-3 py-2">{submitError}</p>}

        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-2">
            {tab !== "basics" && (
              <button type="button" className="btn btn-outline" onClick={() => setTab(STEP_IDS[STEP_IDS.indexOf(tab) - 1])}>Back</button>
            )}
            {tab !== "baseline" && (
              <button type="button" className="btn btn-outline" onClick={() => setTab(STEP_IDS[STEP_IDS.indexOf(tab) + 1])}>Next</button>
            )}
          </div>
          <div className="flex gap-2">
            <Link to="/projects" className="btn btn-outline">Cancel</Link>
            <button type="submit" className="btn btn-primary" disabled={createProject.isPending}>
              {createProject.isPending
                ? <><Loader2 className="size-4 animate-spin" /> {isEdit ? "Saving…" : "Creating…"}</>
                : <><FolderKanban className="size-4" /> {isEdit ? "Save changes" : "Create project"}</>}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
