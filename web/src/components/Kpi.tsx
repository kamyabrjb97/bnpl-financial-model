import { Kpis } from "../api";
import { money, pct, compact } from "../format";

export function KpiStrip({ k }: { k: Kpis }) {
  const items: { label: string; value: string; tone?: string }[] = [
    { label: "Cumulative net income", value: money(k.cumulative_net_income),
      tone: k.cumulative_net_income >= 0 ? "pos" : "neg" },
    { label: "Final monthly NI", value: money(k.final_net_income),
      tone: k.final_net_income >= 0 ? "pos" : "neg" },
    { label: "Breakeven month", value: k.breakeven_month == null ? "—" : `m${k.breakeven_month}` },
    { label: "Final NPL ratio", value: pct(k.final_npl_ratio, 2) },
    { label: "Final ECL coverage", value: pct(k.final_ecl_coverage, 2) },
    { label: "Loans / mo (final)", value: compact(k.final_loans_originated) },
  ];
  return (
    <div className="kpi-strip">
      {items.map((it) => (
        <div className="kpi" key={it.label}>
          <div className="kpi-label">{it.label}</div>
          <div className={`kpi-value ${it.tone || ""}`}>{it.value}</div>
        </div>
      ))}
    </div>
  );
}

export function Banner({ k }: { k: Kpis }) {
  return (
    <>
      {k.articulation_ok ? (
        <div className="banner ok">
          ✓ All articulation invariants pass — balance sheet balances, cash ties, ECL allowance ties to the statements.
        </div>
      ) : (
        <div className="banner err">
          ⚠️ One or more articulation invariants failed — see the Invariants tab.
        </div>
      )}
      {k.feasibility_failures.length > 0 && (
        <div className="banner warn">
          ⚠️ Feasibility flag ({k.feasibility_failures.join(", ")}): this scenario is internally consistent but not
          self-funding (e.g. cash goes negative). Add equity, raise the funding line, or slow growth.
        </div>
      )}
    </>
  );
}
