import { Row } from "../api";
import { title } from "../format";

type Fmt = "money" | "pct" | "num" | "num2" | "int";

function fmtCell(v: number | string | null, fmt: Fmt): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  switch (fmt) {
    case "money": return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    case "pct": return `${(v * 100).toFixed(2)}%`;
    case "num2": return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case "num": return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
    case "int": return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    default: return String(v);
  }
}

export function DataTable({
  rows, columns, fmt = "money", maxHeight = 460,
}: {
  rows: Row[];
  columns?: string[];
  fmt?: Fmt;
  maxHeight?: number;
}) {
  if (!rows?.length) return <div className="muted">No data.</div>;
  const cols = columns ?? Object.keys(rows[0]);
  return (
    <div className="table-wrap" style={{ maxHeight }}>
      <table className="data-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className={c === "month" ? "sticky-col" : ""}>{title(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => {
                const isMonth = c === "month";
                return (
                  <td key={c} className={isMonth ? "sticky-col mono" : "num"}>
                    {isMonth ? String(r[c]) : fmtCell(r[c], fmt)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
