import { useMemo, useState } from "react";
import { Cfg, RunResult, Row } from "../api";
import { exportExcel, downloadJson } from "../api";
import { LineCard, AreaStackCard, BarCard, COLORS } from "./charts";
import { DataTable } from "./DataTable";
import { money, pct, num, title } from "../format";

// ----------------------------------------------------------------- Overview
export function Overview({ r }: { r: RunResult }) {
  return (
    <>
      <div className="grid-2">
        <LineCard title="Loan originations / month" data={r.portfolio}
          series={[{ key: "loans_originated", name: "Loans originated", color: COLORS.accent }]} />
        <LineCard title="Receivables" data={r.portfolio} series={[
          { key: "gross_receivable", name: "Gross", color: COLORS.accent },
          { key: "net_receivable", name: "Net", color: COLORS.green },
        ]} />
      </div>
      <div className="grid-2">
        <LineCard title="Revenue & net income" data={r.income_statement} series={[
          { key: "total_revenue", name: "Revenue", color: COLORS.accent },
          { key: "net_income", name: "Net income", color: COLORS.green },
        ]} />
        <LineCard title="Credit quality" data={r.portfolio} asPct series={[
          { key: "npl_ratio", name: "NPL ratio", color: COLORS.red },
          { key: "ecl_coverage", name: "ECL coverage", color: COLORS.amber },
        ]} />
      </div>
      <AreaStackCard title="Portfolio by IFRS 9 stage ($)" data={r.portfolio} series={[
        { key: "stage1_balance", name: "Stage 1", color: COLORS.green },
        { key: "stage2_balance", name: "Stage 2", color: COLORS.amber },
        { key: "stage3_balance", name: "Stage 3", color: COLORS.red },
      ]} />
    </>
  );
}

// --------------------------------------------------------- Income Statement
export function IncomeStatement({ r }: { r: RunResult }) {
  return (
    <>
      <LineCard title="Income statement drivers" data={r.income_statement} series={[
        { key: "total_revenue", name: "Revenue", color: COLORS.accent },
        { key: "provision_expense", name: "Provision (ECL)", color: COLORS.red },
        { key: "operating_expense", name: "Opex", color: COLORS.amber },
        { key: "net_income", name: "Net income", color: COLORS.green },
      ]} />
      <DataTable rows={r.income_statement} />
    </>
  );
}

// ----------------------------------------------------------------- Cash Flow
export function CashFlow({ r }: { r: RunResult }) {
  const cols = ["month", "net_income", "provision_addback", "charge_offs_to_allowance",
    "d_gross_receivable", "d_deferred_fee", "d_opex_payable", "cfo", "cfo_indirect",
    "debt_draw_repay", "equity_issuance", "dividends", "cff", "net_change_cash", "ending_cash"];
  return (
    <>
      <LineCard title="Cash flow" data={r.cash_flow} series={[
        { key: "cfo", name: "Operating CF", color: COLORS.accent },
        { key: "cff", name: "Financing CF", color: COLORS.amber },
        { key: "ending_cash", name: "Ending cash", color: COLORS.green },
      ]} />
      <p className="muted small">Operating cash is computed <b>directly</b> (authoritative); the indirect column is
        asserted equal to it (invariant H-CFO).</p>
      <DataTable rows={r.cash_flow} columns={cols} />
    </>
  );
}

// -------------------------------------------------------------- Balance Sheet
export function BalanceSheet({ r }: { r: RunResult }) {
  const maxChk = Math.max(...r.balance_sheet.map((x) => Math.abs((x.balance_check as number) || 0)));
  const cols = ["month", "cash", "gross_receivables", "ecl_allowance", "net_receivables", "total_assets",
    "funding_debt", "deferred_fee_income", "opex_payable", "total_liabilities",
    "paid_in_capital", "retained_earnings", "total_equity", "balance_check"];
  return (
    <>
      <AreaStackCard title="Asset composition ($)" data={r.balance_sheet} series={[
        { key: "cash", name: "Cash", color: COLORS.green },
        { key: "net_receivables", name: "Net receivables", color: COLORS.accent },
      ]} />
      <p className="muted small">Balance check (Assets − Liabilities − Equity), max |residual| = {maxChk.toExponential(2)}</p>
      <DataTable rows={r.balance_sheet} columns={cols} />
    </>
  );
}

// ------------------------------------------------------- Portfolio & IFRS 9
export function Portfolio({ r }: { r: RunResult }) {
  const lastMonth = Math.max(...r.portfolio.map((x) => x.month as number));
  const last = r.portfolio.find((x) => x.month === lastMonth)!;
  const sd = r.stage_detail.filter((x) => x.month === lastMonth);

  const stageRows = useMemo(() => {
    const agg: Record<number, { balance: number; ecl: number; loans: number }> = {
      1: { balance: 0, ecl: 0, loans: 0 }, 2: { balance: 0, ecl: 0, loans: 0 }, 3: { balance: 0, ecl: 0, loans: 0 },
    };
    sd.forEach((x) => {
      const s = x.stage as number;
      agg[s].balance += (x.balance as number) || 0;
      agg[s].ecl += (x.ecl as number) || 0;
      agg[s].loans += (x.est_loans as number) || 0;
    });
    const tot = agg[1].balance + agg[2].balance + agg[3].balance;
    const names = { 1: "Stage 1 (Performing)", 2: "Stage 2 (SICR)", 3: "Stage 3 (NPL)" } as Record<number, string>;
    return [1, 2, 3].map((s) => ({
      Stage: names[s], Balance: agg[s].balance, "% balance": tot ? agg[s].balance / tot : 0,
      ECL: agg[s].ecl, "ECL rate": agg[s].balance ? agg[s].ecl / agg[s].balance : 0, "Est loans": agg[s].loans,
    }));
  }, [sd]);

  const bucketRows = sd.map((x) => ({ bucket: x.bucket, balance: x.balance }));

  return (
    <>
      <div className="section-title">IFRS 9 stage summary — month {lastMonth}</div>
      <div className="grid-portfolio">
        <div className="card">
          <table className="data-table compact">
            <thead><tr>{["Stage", "Balance", "% balance", "ECL", "ECL rate", "Est loans"].map((c) =>
              <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {stageRows.map((row, i) => (
                <tr key={i}>
                  <td>{row.Stage}</td>
                  <td className="num">{money(row.Balance)}</td>
                  <td className="num">{pct(row["% balance"])}</td>
                  <td className="num">{money(row.ECL)}</td>
                  <td className="num">{pct(row["ECL rate"], 2)}</td>
                  <td className="num">{num(row["Est loans"])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="kpi-col">
          <div className="mini-kpi"><div className="kpi-label">NPL ratio</div><div className="kpi-value neg">{pct(last.npl_ratio as number, 2)}</div></div>
          <div className="mini-kpi"><div className="kpi-label">ECL coverage</div><div className="kpi-value">{pct(last.ecl_coverage as number, 2)}</div></div>
          <div className="mini-kpi"><div className="kpi-label">Loss allowance</div><div className="kpi-value">{money(last.allowance as number)}</div></div>
        </div>
      </div>
      <div className="grid-2">
        <BarCard title="Balance by DPD bucket (latest)" data={bucketRows} xKey="bucket" yKey="balance" />
        <LineCard title="Credit flows" data={r.portfolio} series={[
          { key: "provision_charge", name: "Provision charge", color: COLORS.amber },
          { key: "write_offs", name: "Charge-offs", color: COLORS.red },
          { key: "recoveries", name: "Recoveries", color: COLORS.green },
        ]} />
      </div>
      {r.vintage.length > 0 && (
        <BarCard title="Cumulative charge-off rate by origination cohort" data={r.vintage}
          xKey="cohort" yKey="cum_loss_rate" color={COLORS.red} asPct />
      )}
    </>
  );
}

// ------------------------------------------------------------------- Metrics
export function Metrics({ r }: { r: RunResult }) {
  return (
    <>
      <div className="grid-2">
        <LineCard title="Returns" data={r.metrics} asPct series={[
          { key: "roe_ann", name: "ROE (ann.)", color: COLORS.accent },
          { key: "roa_ann", name: "ROA (ann.)", color: COLORS.green },
          { key: "net_margin", name: "Net margin", color: COLORS.amber },
        ]} />
        <LineCard title="Credit & yield" data={r.metrics} asPct series={[
          { key: "portfolio_yield_ann", name: "Yield (ann.)", color: COLORS.accent },
          { key: "cost_of_risk_ann", name: "Cost of risk", color: COLORS.red },
          { key: "charge_off_rate_ann", name: "Charge-off rate", color: COLORS.amber },
        ]} />
      </div>
      <DataTable rows={r.metrics} fmt="num" />
    </>
  );
}

// ----------------------------------------------------------- Scenario Compare
export interface SavedScenario { name: string; result: RunResult }
const COMPARE_METRICS: { key: string; src: "income_statement" | "portfolio"; pct?: boolean }[] = [
  { key: "net_income", src: "income_statement" },
  { key: "npl_ratio", src: "portfolio", pct: true },
  { key: "ecl_coverage", src: "portfolio", pct: true },
  { key: "gross_receivable", src: "portfolio" },
  { key: "loans_originated", src: "portfolio" },
];

export function ScenarioCompare({
  current, saved, onSave, onClear,
}: {
  current: RunResult;
  saved: SavedScenario[];
  onSave: (name: string) => void;
  onClear: () => void;
}) {
  const [name, setName] = useState(`Scenario ${saved.length + 1}`);
  const [metric, setMetric] = useState("net_income");
  const def = COMPARE_METRICS.find((m) => m.key === metric)!;

  // build merged chart data: month -> { [scenarioName]: value }
  const series = [{ name: "▶ Current (live)", result: current }, ...saved];
  const months = (current[def.src] as Row[]).map((x) => x.month as number);
  const data = months.map((m, i) => {
    const row: Row = { month: m };
    series.forEach((s) => {
      const src = s.result[def.src] as Row[];
      row[s.name] = (src[i]?.[metric] as number) ?? null;
    });
    return row;
  });

  return (
    <>
      <p className="muted">Save the current assumptions as a named scenario, then compare key trajectories.</p>
      <div className="row gap">
        <input className="grow" value={name} onChange={(e) => setName(e.target.value)} placeholder="Scenario name" />
        <button className="btn" onClick={() => onSave(name)}>💾 Save current</button>
        <button className="btn-ghost" onClick={onClear}>🗑️ Clear all</button>
      </div>
      <div className="row gap" style={{ marginTop: 12 }}>
        <label className="field inline">
          <span className="field-label">Compare metric</span>
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {COMPARE_METRICS.map((m) => <option key={m.key} value={m.key}>{title(m.key)}</option>)}
          </select>
        </label>
      </div>
      <LineCard title={`Scenario comparison — ${title(metric)}`} data={data} asPct={def.pct}
        series={series.map((s, i) => ({
          key: s.name, name: s.name,
          color: [COLORS.accent, COLORS.green, COLORS.amber, COLORS.red, COLORS.cyan, COLORS.violet][i % 6],
        }))} height={360} />
      {saved.length > 0 && (
        <table className="data-table" style={{ marginTop: 12 }}>
          <thead><tr>{["Scenario", "Cum NI", "Final NPL", "Final coverage", "Final cash"].map((c) =>
            <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {saved.map((s, i) => {
              const k = s.result.kpis;
              const cash = (s.result.balance_sheet.at(-1)?.cash as number) ?? 0;
              return (
                <tr key={i}>
                  <td>{s.name}</td>
                  <td className="num">{money(k.cumulative_net_income)}</td>
                  <td className="num">{pct(k.final_npl_ratio, 2)}</td>
                  <td className="num">{pct(k.final_ecl_coverage, 2)}</td>
                  <td className="num">{money(cash)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Invariants
export function Invariants({ r }: { r: RunResult }) {
  return (
    <>
      <p className="muted">Tie-out checks proving the statements articulate (spec Section H). Articulation checks must
        always pass; feasibility flags are business signals.</p>
      <table className="data-table">
        <thead><tr>{["ID", "Category", "Description", "Status", "Max residual"].map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {r.invariants.map((iv) => (
            <tr key={iv.id}>
              <td className="mono">{iv.id}</td>
              <td><span className={`pill ${iv.category}`}>{iv.category}</span></td>
              <td>{iv.description}</td>
              <td>{iv.passed ? <span className="ok-text">✅ pass</span> : <span className="err-text">❌ FAIL</span>}</td>
              <td className="num mono">{(iv.max_residual ?? 0).toExponential(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// -------------------------------------------------------------------- Export
export function Export({ cfg }: { cfg: Cfg }) {
  const [busy, setBusy] = useState(false);
  return (
    <>
      <p className="muted">Download the full model as an Excel workbook (a tab per statement/table), or the current
        assumptions as JSON.</p>
      <div className="row gap">
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true); try { await exportExcel(cfg); } finally { setBusy(false); }
        }}>{busy ? "Generating…" : "⬇️ Download Excel workbook"}</button>
        <button className="btn-ghost" onClick={() => downloadJson(cfg)}>⬇️ Download assumptions (JSON)</button>
      </div>
    </>
  );
}
