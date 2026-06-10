// Formatting helpers shared across the UI.

export function money(x: number | null | undefined, dollars = true): string {
  if (x == null || isNaN(x)) return "—";
  const sign = x < 0 ? "-" : "";
  const v = Math.abs(x);
  const p = dollars ? "$" : "";
  if (v >= 1e9) return `${sign}${p}${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${sign}${p}${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${sign}${p}${(v / 1e3).toFixed(1)}K`;
  return `${sign}${p}${v.toFixed(0)}`;
}

export function pct(x: number | null | undefined, dp = 1): string {
  if (x == null || isNaN(x)) return "—";
  return `${(x * 100).toFixed(dp)}%`;
}

export function num(x: number | null | undefined, dp = 0): string {
  if (x == null || isNaN(x)) return "—";
  return x.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function compact(x: number | null | undefined): string {
  if (x == null || isNaN(x)) return "—";
  return money(x, false);
}

// label -> human title (snake_case -> Title Case)
export function title(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bEcl\b/g, "ECL")
    .replace(/\bNpl\b/g, "NPL")
    .replace(/\bNim\b/g, "NIM")
    .replace(/\bRoa\b/g, "ROA")
    .replace(/\bRoe\b/g, "ROE")
    .replace(/\bGmv\b/g, "GMV")
    .replace(/\bAov\b/g, "AOV")
    .replace(/\bDpd\b/g, "DPD")
    .replace(/\bCfo\b/g, "CFO")
    .replace(/\bCff\b/g, "CFF")
    .replace(/\bPd\b/g, "PD")
    .replace(/\bLgd\b/g, "LGD")
    .replace(/\bEbt\b/g, "EBT");
}
