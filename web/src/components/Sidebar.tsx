import { useState } from "react";
import { Cfg, FieldDef, Group } from "../api";
import { pct } from "../format";

const ICONS: Record<string, string> = {
  package: "📦", dollar: "💵", trend: "📈", alert: "⚠️", bank: "🏛️",
  globe: "🌐", building: "🏦",
};

function visible(f: FieldDef, cfg: Cfg): boolean {
  if (!f.showIf) return true;
  return cfg[f.showIf.key] === f.showIf.equals;
}

function Field({ f, cfg, onChange }: { f: FieldDef; cfg: Cfg; onChange: (k: string, v: any) => void }) {
  const value = cfg[f.key];
  if (f.type === "slider") {
    return (
      <label className="field">
        <span className="field-row">
          <span className="field-label">{f.label}</span>
          <span className="field-val">{f.fmt === "pct" ? pct(value, 1) : value}</span>
        </span>
        <input
          type="range" min={f.min} max={f.max} step={f.step} value={value}
          onChange={(e) => onChange(f.key, parseFloat(e.target.value))}
        />
      </label>
    );
  }
  if (f.type === "number") {
    return (
      <label className="field">
        <span className="field-label">{f.label}</span>
        <input
          type="number" min={f.min} max={f.max} step={f.step} value={value}
          onChange={(e) => onChange(f.key, e.target.value === "" ? 0 : parseFloat(e.target.value))}
        />
      </label>
    );
  }
  if (f.type === "select") {
    return (
      <label className="field">
        <span className="field-label">{f.label}</span>
        <select
          value={String(value)}
          onChange={(e) => {
            const opt = f.options!.find((o) => String(o) === e.target.value);
            onChange(f.key, opt);
          }}
        >
          {f.options!.map((o) => (
            <option key={String(o)} value={String(o)}>{String(o)}</option>
          ))}
        </select>
      </label>
    );
  }
  return null;
}

function GroupBlock({ g, cfg, onChange, open, toggle }:
  { g: Group; cfg: Cfg; onChange: (k: string, v: any) => void; open: boolean; toggle: () => void }) {
  return (
    <div className={`group ${open ? "open" : ""}`}>
      <button className="group-head" onClick={toggle}>
        <span>{ICONS[g.icon] || "•"} {g.title}</span>
        <span className="chev">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="group-body">
          {g.fields.filter((f) => visible(f, cfg)).map((f) => (
            <Field key={f.key} f={f} cfg={cfg} onChange={onChange} />
          ))}
        </div>
      )}
    </div>
  );
}

export function Sidebar({
  groups, cfg, onChange, onReset,
}: {
  groups: Group[];
  cfg: Cfg;
  onChange: (k: string, v: any) => void;
  onReset: () => void;
}) {
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set([0]));
  const toggle = (i: number) => {
    const next = new Set(openIdx);
    next.has(i) ? next.delete(i) : next.add(i);
    setOpenIdx(next);
  };
  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <h2>⚙️ Assumptions</h2>
        <button className="btn-ghost" onClick={onReset}>Reset</button>
      </div>
      <p className="muted small">Every control re-runs the model live.</p>
      {groups.map((g, i) => (
        <GroupBlock key={g.title} g={g} cfg={cfg} onChange={onChange}
          open={openIdx.has(i)} toggle={() => toggle(i)} />
      ))}
    </aside>
  );
}
