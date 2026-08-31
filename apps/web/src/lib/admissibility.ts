/**
 * Contractor-admissibility scoring — the maths the Admissibility tab's
 * "Contractor admissibility" sheet and "Delay event summary" list both read
 * from, so an event's percentage and category are identical in both.
 *
 * A criterion scores its FULL weightage when the clause applies to the event AND
 * the Contractor complied; nothing otherwise. The percentage is measured against
 * the weight that actually applies to that event, not the full 100 — criteria
 * that don't bear on the event are excluded from both sides of the fraction.
 */
import type { ContractorCriterion, ContractorEventScore, ContractorRow } from "@/types";

/** The bands the scoring sheet reports against. Ordered high to low. */
export const ADMISSIBILITY_BANDS = [
  { min: 80, label: "Admissible", badge: "bg-success-bg text-success", text: "text-success" },
  { min: 70, label: "Moderate", badge: "bg-info-bg text-info", text: "text-info" },
  { min: 50, label: "Weak", badge: "bg-warning-bg text-warning", text: "text-warning" },
  { min: -1, label: "Not Admissible", badge: "bg-error-bg text-error", text: "text-error" },
] as const;

export type AdmissibilityBand = (typeof ADMISSIBILITY_BANDS)[number];

export const bandFor = (pct: number): AdmissibilityBand =>
  ADMISSIBILITY_BANDS.find((b) => pct >= b.min) ?? ADMISSIBILITY_BANDS[ADMISSIBILITY_BANDS.length - 1];

export const num = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** Round to at most 2 dp for display. */
export const fmt = (n: number) => String(Math.round(n * 100) / 100);

/** A row's score: the full weightage when the clause applies AND was complied with. */
export const scoreOf = (row: ContractorRow | undefined, c: ContractorCriterion) =>
  row?.applicable === "Y" && row?.complied === "Y" ? num(c.weightage) : 0;

export interface EventTotals {
  /** Weightage of the criteria that apply to this event. */
  applicableWeight: number;
  achieved: number;
  /** achieved / applicableWeight, as a percentage. */
  pct: number;
}

export function totalsFor(
  ev: ContractorEventScore,
  criteria: ContractorCriterion[],
): EventTotals {
  const byId = new Map(ev.rows.map((r) => [r.criterionId, r]));
  let applicableWeight = 0;
  let achieved = 0;
  for (const c of criteria) {
    const row = byId.get(c.id);
    if (row?.applicable !== "Y") continue;
    applicableWeight += num(c.weightage);
    achieved += scoreOf(row, c);
  }
  return {
    applicableWeight,
    achieved,
    pct: applicableWeight > 0 ? (achieved / applicableWeight) * 100 : 0,
  };
}
