import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";
import { Row } from "../api";
import { money, pct } from "../format";

export const COLORS = {
  accent: "#6366f1",
  green: "#10b981",
  red: "#ef4444",
  amber: "#f59e0b",
  cyan: "#06b6d4",
  violet: "#8b5cf6",
  slate: "#94a3b8",
};
const PALETTE = [COLORS.accent, COLORS.green, COLORS.amber, COLORS.red, COLORS.cyan, COLORS.violet];

const axisProps = { stroke: "#64748b", fontSize: 11, tickLine: false };
const gridProps = { stroke: "#1e293b", strokeDasharray: "3 3", vertical: false };

interface SeriesDef {
  key: string;
  name: string;
  color?: string;
}

function fmtVal(v: number, asPct?: boolean) {
  return asPct ? pct(v) : money(v);
}

function ChartTooltip({ active, payload, label, asPct }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="tt-label">Month {label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="tt-row">
          <span className="tt-dot" style={{ background: p.color }} />
          <span className="tt-name">{p.name}</span>
          <span className="tt-val">{fmtVal(p.value, asPct)}</span>
        </div>
      ))}
    </div>
  );
}

export function LineCard({
  title, data, series, asPct, height = 300,
}: { title: string; data: Row[]; series: SeriesDef[]; asPct?: boolean; height?: number }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="month" {...axisProps} />
          <YAxis {...axisProps} tickFormatter={(v) => (asPct ? pct(v, 0) : money(v))} width={56} />
          <Tooltip content={<ChartTooltip asPct={asPct} />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s, i) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.name}
              stroke={s.color || PALETTE[i % PALETTE.length]} strokeWidth={2.2} dot={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function AreaStackCard({
  title, data, series, asPct, height = 300,
}: { title: string; data: Row[]; series: SeriesDef[]; asPct?: boolean; height?: number }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="month" {...axisProps} />
          <YAxis {...axisProps} tickFormatter={(v) => (asPct ? pct(v, 0) : money(v))} width={56} />
          <Tooltip content={<ChartTooltip asPct={asPct} />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s, i) => (
            <Area key={s.key} type="monotone" dataKey={s.key} name={s.name} stackId="1"
              stroke={s.color || PALETTE[i % PALETTE.length]}
              fill={s.color || PALETTE[i % PALETTE.length]} fillOpacity={0.25} strokeWidth={1.5} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function BarCard({
  title, data, xKey, yKey, color = COLORS.accent, asPct, height = 300,
}: { title: string; data: Row[]; xKey: string; yKey: string; color?: string; asPct?: boolean; height?: number }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey={xKey} {...axisProps} />
          <YAxis {...axisProps} tickFormatter={(v) => (asPct ? pct(v, 0) : money(v))} width={56} />
          <Tooltip
            formatter={(v: any) => fmtVal(v, asPct)}
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8, fontSize: 12 }} />
          <Bar dataKey={yKey} fill={color} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
