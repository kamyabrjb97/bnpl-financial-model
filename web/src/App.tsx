import { useEffect, useRef, useState } from "react";
import { Cfg, RunResult, Schema, getSchema, runModel } from "./api";
import { Sidebar } from "./components/Sidebar";
import { KpiStrip, Banner } from "./components/Kpi";
import {
  Overview, IncomeStatement, CashFlow, BalanceSheet, Portfolio, Metrics,
  ScenarioCompare, Invariants, Export, SavedScenario,
} from "./components/tabs";

const TABS = [
  "📊 Overview", "🧾 Income Statement", "💧 Cash Flow", "⚖️ Balance Sheet",
  "🏛️ Portfolio & IFRS 9", "📐 Metrics", "🔬 Scenario Compare", "✅ Invariants", "⬇️ Export",
];

export default function App() {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState(0);
  const [scenarios, setScenarios] = useState<SavedScenario[]>([]);
  const debounceRef = useRef<number | null>(null);

  // load schema + defaults once
  useEffect(() => {
    getSchema()
      .then((s) => { setSchema(s); setCfg(s.defaults); })
      .catch((e) => setError(String(e)));
  }, []);

  // debounced re-run whenever the config changes
  useEffect(() => {
    if (!cfg) return;
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    setLoading(true);
    debounceRef.current = window.setTimeout(() => {
      runModel(cfg)
        .then((r) => { setResult(r); setError(null); })
        .catch((e) => setError(String(e.message || e)))
        .finally(() => setLoading(false));
    }, 220);
    return () => { if (debounceRef.current) window.clearTimeout(debounceRef.current); };
  }, [cfg]);

  const onChange = (k: string, v: any) => setCfg((c) => ({ ...(c as Cfg), [k]: v }));
  const onReset = () => schema && setCfg({ ...schema.defaults });

  if (!schema || !cfg) {
    return <div className="app-loading">{error ? `⚠️ ${error}` : "Loading model…"}</div>;
  }

  return (
    <div className="app">
      <Sidebar groups={schema.groups} cfg={cfg} onChange={onChange} onReset={onReset} />

      <main className="main">
        <header className="app-header">
          <div>
            <h1>💳 BNPL Financial Model</h1>
            <p className="muted small">
              Cohort-based three-statement model with IFRS 9 ECL provisioning wired in dynamically.
              {loading && <span className="live-dot"> • recomputing…</span>}
            </p>
          </div>
        </header>

        {error && <div className="banner err">⚠️ {error}</div>}

        {result && (
          <>
            <KpiStrip k={result.kpis} />
            <Banner k={result.kpis} />

            <nav className="tabs">
              {TABS.map((t, i) => (
                <button key={t} className={`tab ${i === tab ? "active" : ""}`} onClick={() => setTab(i)}>{t}</button>
              ))}
            </nav>

            <section className="tab-body">
              {tab === 0 && <Overview r={result} />}
              {tab === 1 && <IncomeStatement r={result} />}
              {tab === 2 && <CashFlow r={result} />}
              {tab === 3 && <BalanceSheet r={result} />}
              {tab === 4 && <Portfolio r={result} />}
              {tab === 5 && <Metrics r={result} />}
              {tab === 6 && (
                <ScenarioCompare
                  current={result}
                  saved={scenarios}
                  onSave={(name) => setScenarios((s) => [...s, { name, result }])}
                  onClear={() => setScenarios([])}
                />
              )}
              {tab === 7 && <Invariants r={result} />}
              {tab === 8 && <Export cfg={cfg} />}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
