// Typed client for the BNPL model API (thin wrapper over the Python engine).

export type Cfg = Record<string, any>;
export type Row = Record<string, number | string | null>;

export interface FieldDef {
  key: string;
  label: string;
  type: "slider" | "number" | "select" | "checkbox" | "text";
  min?: number;
  max?: number;
  step?: number;
  options?: (string | number)[];
  fmt?: "pct";
  help?: string;
  showIf?: { key: string; equals: any };
}
export interface Group {
  title: string;
  icon: string;
  fields: FieldDef[];
}
export interface Schema {
  defaults: Cfg;
  groups: Group[];
}

export interface Kpis {
  cumulative_net_income: number;
  final_net_income: number;
  breakeven_month: number | null;
  final_npl_ratio: number;
  final_ecl_coverage: number;
  final_loans_originated: number;
  cumulative_loans: number;
  final_gross_receivable: number;
  articulation_ok: boolean;
  feasibility_failures: string[];
}

export interface InvRow {
  id: string;
  category: string;
  description: string;
  passed: boolean;
  max_residual: number;
}

export interface RunResult {
  config: Cfg;
  pd_lifetime_stage2_used: number;
  kpis: Kpis;
  income_statement: Row[];
  cash_flow: Row[];
  balance_sheet: Row[];
  portfolio: Row[];
  metrics: Row[];
  stage_detail: Row[];
  vintage: Row[];
  invariants: InvRow[];
}

export async function getSchema(): Promise<Schema> {
  const r = await fetch("/api/schema");
  if (!r.ok) throw new Error("Failed to load schema");
  return r.json();
}

export async function runModel(cfg: Cfg): Promise<RunResult> {
  const r = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail || `Run failed (${r.status})`);
  }
  return r.json();
}

export async function exportExcel(cfg: Cfg): Promise<void> {
  const r = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!r.ok) throw new Error("Export failed");
  const blob = await r.blob();
  triggerDownload(blob, "bnpl_model.xlsx");
}

export function downloadJson(cfg: Cfg) {
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
  triggerDownload(blob, "bnpl_assumptions.json");
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
