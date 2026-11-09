// Lightweight accessible SVG/HTML charts (no chart library dependency).

export function BarList({ rows, valueKey = "total", label = (r) => r.key, format = (v) => v, color = "#2563eb", secondary }) {
  const max = Math.max(1, ...rows.map((r) => r[valueKey] || 0));
  if (!rows.length) return <p className="ct-muted">No data for the selected filters.</p>;
  return (
    <ul className="ct-barlist">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="ct-barlist-top"><span>{label(r)}</span><strong>{format(r[valueKey])}</strong></div>
          <div className="ct-bar-track" aria-hidden="true"><div className="ct-bar-fill" style={{ width: `${((r[valueKey] || 0) / max) * 100}%`, background: color }} /></div>
          {secondary && <small className="ct-muted">{secondary(r)}</small>}
        </li>
      ))}
    </ul>
  );
}

/** Grouped column chart for created vs resolved per period. */
export function TrendChart({ data, height = 200 }) {
  if (!data.length) return <p className="ct-muted">No dated complaints for the selected filters.</p>;
  const max = Math.max(1, ...data.map((d) => Math.max(d.created, d.resolved)));
  const width = Math.max(320, data.length * 26);
  const step = width / data.length;
  const barW = Math.max(3, step / 2 - 3);
  const labelEvery = Math.ceil(data.length / 10);
  const plotH = height - 28;
  return (
    <div className="ct-chart-scroll">
      <svg width={width} height={height} role="img" aria-label={`Created versus resolved complaints across ${data.length} periods`}>
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1="0" x2={width} y1={plotH - plotH * f} y2={plotH - plotH * f} stroke="var(--ct-grid)" />
            <text x="2" y={plotH - plotH * f - 3} className="ct-axis">{Math.round(max * f)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = i * step + step / 2;
          const hc = (d.created / max) * plotH;
          const hr = (d.resolved / max) * plotH;
          return (
            <g key={d.key}>
              <title>{`${d.label}: ${d.created} created, ${d.resolved} resolved`}</title>
              <rect x={x - barW - 1} y={plotH - hc} width={barW} height={hc} fill="#2563eb" rx="2" />
              <rect x={x + 1} y={plotH - hr} width={barW} height={hr} fill="#16a34a" rx="2" />
              {i % labelEvery === 0 && <text x={x} y={height - 8} textAnchor="middle" className="ct-axis">{d.label}</text>}
            </g>
          );
        })}
      </svg>
      <div className="ct-legend"><span><i style={{ background: "#2563eb" }} />Created</span><span><i style={{ background: "#16a34a" }} />Resolved</span></div>
    </div>
  );
}

export function DonutStat({ value, label, color = "#16a34a" }) {
  const pct = value === null || value === undefined ? null : Math.round(value * 100);
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <div className="ct-donut">
      <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label={`${label}: ${pct === null ? "no data" : `${pct}%`}`}>
        <circle cx="44" cy="44" r={r} fill="none" stroke="var(--ct-grid)" strokeWidth="9" />
        {pct !== null && <circle cx="44" cy="44" r={r} fill="none" stroke={color} strokeWidth="9" strokeDasharray={`${(c * pct) / 100} ${c}`} transform="rotate(-90 44 44)" strokeLinecap="round" />}
        <text x="44" y="49" textAnchor="middle" className="ct-donut-text">{pct === null ? "—" : `${pct}%`}</text>
      </svg>
      <span>{label}</span>
    </div>
  );
}
