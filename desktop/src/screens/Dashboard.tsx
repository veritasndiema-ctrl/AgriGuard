import { useEffect, useMemo, useState } from "react";
import {
  AgriGuardApi,
  ForecastResponse,
  CommodityListResponse,
  ForecastPoint,
  ArbitrageOpportunity,
} from "../services/apiClient";

interface DashboardProps {
  apiBase: string;
}

/**
 * Cross-market price summary for one commodity.
 * Mirrors backend/app/routers/markets.py::CommodityMarketSummary.
 * Declared locally (rather than in apiClient.ts) since AgriGuardApi.marketSummary()
 * intentionally returns a loosely-typed Record for forward-compatibility; we narrow
 * it here where it's actually consumed.
 */
interface MarketPrice {
  market: string;
  region: string | null;
  latest_price: number;
  currency: string;
  unit: string;
  date_recorded: string;
  days_since_update: number;
  price_30d_ago: number | null;
  price_change_pct: number | null;
  trend: string;
  data_points: number;
}

interface CommodityMarketSummary {
  commodity: string;
  markets: MarketPrice[];
  best_market_to_sell: string;
  worst_market_to_sell: string;
  price_spread: number;
  price_spread_pct: number;
  national_avg_price: number;
  currency: string;
  unit: string;
  recommendation: string;
  generated_at: string;
}

type LoadStatus = "idle" | "loading" | "ready" | "error";
type HealthStatus = "checking" | "online" | "offline";

const DEFAULT_COMMODITY = "Maize";
const DEFAULT_MARKET = "Kampala";
const DEFAULT_HORIZON = 14;

const TREND_COLORS: Record<string, string> = {
  rising: "#e65100",
  falling: "#1565c0",
  stable: "#616161",
};

const TREND_ICONS: Record<string, string> = {
  rising: "📈",
  falling: "📉",
  stable: "➖",
};

function formatMoney(value: number, currency: string, unit: string): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency}/${unit}`;
}

/** Minimal dependency-free line chart for the forecast horizon. */
function Sparkline({ points }: { points: ForecastPoint[] }) {
  if (points.length === 0) return null;

  const width = 560;
  const height = 140;
  const padding = 24;

  const prices = points.map((p) => p.predicted_price);
  const lowers = points.map((p) => p.lower_bound);
  const uppers = points.map((p) => p.upper_bound);
  const min = Math.min(...lowers);
  const max = Math.max(...uppers);
  const range = max - min || 1;

  const xStep = (width - padding * 2) / Math.max(points.length - 1, 1);
  const xAt = (i: number) => padding + i * xStep;
  const yAt = (value: number) => height - padding - ((value - min) / range) * (height - padding * 2);

  const linePath = prices.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i)} ${yAt(p)}`).join(" ");
  const bandPath =
    points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i)} ${yAt(p.upper_bound)}`).join(" ") +
    " " +
    [...points]
      .reverse()
      .map((p, i) => `L ${xAt(points.length - 1 - i)} ${yAt(p.lower_bound)}`)
      .join(" ") +
    " Z";

  return (
    <svg width={width} height={height} role="img" aria-label="Forecast price chart">
      <path d={bandPath} fill="#1b5e2022" stroke="none" />
      <path d={linePath} fill="none" stroke="#1b5e20" strokeWidth={2} />
      {points.map((p, i) => (
        <circle key={p.date} cx={xAt(i)} cy={yAt(p.predicted_price)} r={2.5} fill="#1b5e20" />
      ))}
    </svg>
  );
}

function StatusDot({ status }: { status: HealthStatus }) {
  const color = status === "online" ? "#2e7d32" : status === "offline" ? "#c62828" : "#9e9e9e";
  const label = status === "online" ? "API online" : status === "offline" ? "API unreachable" : "Checking API…";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#444" }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" }} />
      {label}
    </span>
  );
}

const card: React.CSSProperties = {
  background: "white",
  borderRadius: 8,
  padding: 20,
  boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid #ccc",
  fontSize: 14,
};

export default function Dashboard({ apiBase }: DashboardProps) {
  const api = useMemo(() => new AgriGuardApi(apiBase), [apiBase]);

  const [health, setHealth] = useState<HealthStatus>("checking");
  const [catalog, setCatalog] = useState<CommodityListResponse | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [commodity, setCommodity] = useState(DEFAULT_COMMODITY);
  const [market, setMarket] = useState(DEFAULT_MARKET);
  const [horizon, setHorizon] = useState(DEFAULT_HORIZON);

  const [forecast, setForecast] = useState<ForecastResponse | null>(null);
  const [summary, setSummary] = useState<CommodityMarketSummary | null>(null);
  const [arbitrage, setArbitrage] = useState<ArbitrageOpportunity[] | null>(null);
  const [arbitrageNotice, setArbitrageNotice] = useState<string | null>(null);
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  // Check health and load the commodity/market catalog whenever the API base changes.
  useEffect(() => {
    let cancelled = false;
    setHealth("checking");

    api
      .health()
      .then(() => {
        if (!cancelled) setHealth("online");
      })
      .catch(() => {
        if (!cancelled) setHealth("offline");
      });

    api
      .listCommodities()
      .then((res) => {
        if (!cancelled) {
          setCatalog(res);
          setCatalogError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setCatalogError(err instanceof Error ? err.message : "Failed to load commodity list");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [api]);

  async function runForecast() {
    setStatus("loading");
    setError(null);
    setArbitrageNotice(null);
    try {
      const [fc, sm] = await Promise.all([
        api.getForecast(commodity, market, horizon),
        api
          .marketSummary(commodity)
          .then((res) => res as unknown as CommodityMarketSummary)
          .catch(() => null),
      ]);
      setForecast(fc);
      setSummary(sm);
      setStatus("ready");
    } catch (err) {
      setForecast(null);
      setSummary(null);
      setError(err instanceof Error ? err.message : "Failed to fetch forecast");
      setStatus("error");
    }

    // Arbitrage is supplementary — fetched independently so a 404/422 (both
    // expected outcomes, see arbitrageOpportunities() docs) never blanks the
    // forecast/summary above, and gets its own notice text instead of the
    // shared error banner.
    try {
      const arb = await api.arbitrageOpportunities(commodity);
      setArbitrage(arb);
    } catch (err) {
      setArbitrage(null);
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("404")) {
        setArbitrageNotice(`No strong arbitrage opportunities for ${commodity} right now.`);
      } else if (msg.includes("422")) {
        setArbitrageNotice(`Not enough ${commodity} price data across markets yet.`);
      } else {
        setArbitrageNotice("Could not load arbitrage data.");
      }
    }
  }

  // Run an initial forecast once the catalog has loaded (or failed) for the first time.
  useEffect(() => {
    if (status === "idle" && (catalog || catalogError)) {
      runForecast();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, catalogError]);

  const trendColor = forecast ? TREND_COLORS[forecast.trend] ?? "#616161" : "#616161";
  const trendIcon = forecast ? TREND_ICONS[forecast.trend] ?? "➖" : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 960, margin: "0 auto" }}>
      {/* Controls */}
      <div style={{ ...card, display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 12, color: "#555" }}>Commodity</label>
          <input
            list="commodities-list"
            value={commodity}
            onChange={(e) => setCommodity(e.target.value)}
            style={{ ...inputStyle, width: 160 }}
          />
          <datalist id="commodities-list">
            {catalog?.commodities.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 12, color: "#555" }}>Market</label>
          <input
            list="markets-list"
            value={market}
            onChange={(e) => setMarket(e.target.value)}
            style={{ ...inputStyle, width: 160 }}
          />
          <datalist id="markets-list">
            {catalog?.markets.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 12, color: "#555" }}>Horizon (days)</label>
          <input
            type="number"
            min={1}
            max={90}
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value) || DEFAULT_HORIZON)}
            style={{ ...inputStyle, width: 100 }}
          />
        </div>

        <button
          onClick={runForecast}
          disabled={status === "loading"}
          style={{
            padding: "9px 20px",
            borderRadius: 6,
            border: "none",
            background: status === "loading" ? "#9ccc9c" : "#1b5e20",
            color: "white",
            fontWeight: 600,
            cursor: status === "loading" ? "default" : "pointer",
          }}
        >
          {status === "loading" ? "Forecasting…" : "Get forecast"}
        </button>

        <div style={{ marginLeft: "auto" }}>
          <StatusDot status={health} />
        </div>
      </div>

      {catalogError && (
        <div style={{ ...card, borderLeft: "4px solid #c62828", color: "#c62828" }}>
          Could not load the commodity/market catalog: {catalogError}. You can still type a commodity and
          market manually above.
        </div>
      )}

      {error && (
        <div style={{ ...card, borderLeft: "4px solid #c62828", color: "#c62828" }}>{error}</div>
      )}

      {/* Forecast */}
      {forecast && (
        <div style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap" }}>
            <h2 style={{ margin: 0 }}>
              {forecast.commodity} — {forecast.market}
            </h2>
            <span style={{ fontSize: 13, color: "#777" }}>
              {forecast.observations_used} observations · model: {forecast.model_used}
            </span>
          </div>

          <div style={{ display: "flex", gap: 24, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Trend</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: trendColor }}>
                {trendIcon} {forecast.trend}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Predicted change</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: trendColor }}>
                {forecast.pct_change > 0 ? "+" : ""}
                {forecast.pct_change}%
              </div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Latest forecast price</div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>
                {formatMoney(
                  forecast.forecast[forecast.forecast.length - 1]?.predicted_price ?? 0,
                  forecast.currency,
                  forecast.unit
                )}
              </div>
            </div>
          </div>

          {forecast.alert && (
            <div
              style={{
                marginTop: 16,
                padding: "10px 14px",
                borderRadius: 6,
                background: "#fff3e0",
                border: "1px solid #ffcc80",
                fontSize: 14,
              }}
            >
              {forecast.alert}
            </div>
          )}

          <div style={{ marginTop: 20 }}>
            <Sparkline points={forecast.forecast} />
          </div>

          <div style={{ marginTop: 12, maxHeight: 220, overflowY: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #eee", position: "sticky", top: 0, background: "white" }}>
                  <th style={{ padding: "6px 8px" }}>Date</th>
                  <th style={{ padding: "6px 8px" }}>Predicted</th>
                  <th style={{ padding: "6px 8px" }}>Lower</th>
                  <th style={{ padding: "6px 8px" }}>Upper</th>
                  <th style={{ padding: "6px 8px" }}>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {forecast.forecast.map((p) => (
                  <tr key={p.date} style={{ borderBottom: "1px solid #f4f4f4" }}>
                    <td style={{ padding: "6px 8px" }}>{p.date}</td>
                    <td style={{ padding: "6px 8px" }}>{p.predicted_price.toFixed(2)}</td>
                    <td style={{ padding: "6px 8px", color: "#777" }}>{p.lower_bound.toFixed(2)}</td>
                    <td style={{ padding: "6px 8px", color: "#777" }}>{p.upper_bound.toFixed(2)}</td>
                    <td style={{ padding: "6px 8px" }}>{Math.round(p.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Cross-market summary */}
      {summary && (
        <div style={card}>
          <h3 style={{ marginTop: 0 }}>Where to sell {summary.commodity}</h3>
          <p style={{ color: "#444", fontSize: 14 }}>{summary.recommendation}</p>

          <div style={{ display: "flex", gap: 24, marginBottom: 16, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Best market</div>
              <div style={{ fontWeight: 700, color: "#2e7d32" }}>{summary.best_market_to_sell}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Worst market</div>
              <div style={{ fontWeight: 700, color: "#c62828" }}>{summary.worst_market_to_sell}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>National average</div>
              <div style={{ fontWeight: 700 }}>{formatMoney(summary.national_avg_price, summary.currency, summary.unit)}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "#777" }}>Spread</div>
              <div style={{ fontWeight: 700 }}>{summary.price_spread_pct.toFixed(1)}%</div>
            </div>
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                <th style={{ padding: "6px 8px" }}>Market</th>
                <th style={{ padding: "6px 8px" }}>Latest price</th>
                <th style={{ padding: "6px 8px" }}>30d change</th>
                <th style={{ padding: "6px 8px" }}>Trend</th>
                <th style={{ padding: "6px 8px" }}>Updated</th>
              </tr>
            </thead>
            <tbody>
              {summary.markets.map((m) => (
                <tr key={m.market} style={{ borderBottom: "1px solid #f4f4f4" }}>
                  <td style={{ padding: "6px 8px", fontWeight: m.market === summary.best_market_to_sell ? 700 : 400 }}>
                    {m.market}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{formatMoney(m.latest_price, m.currency, m.unit)}</td>
                  <td style={{ padding: "6px 8px", color: (m.price_change_pct ?? 0) >= 0 ? "#2e7d32" : "#c62828" }}>
                    {m.price_change_pct != null ? `${m.price_change_pct > 0 ? "+" : ""}${m.price_change_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", color: TREND_COLORS[m.trend] ?? "#616161" }}>
                    {TREND_ICONS[m.trend] ?? ""} {m.trend}
                  </td>
                  <td style={{ padding: "6px 8px", color: "#777" }}>{m.days_since_update}d ago</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Arbitrage — every buy/sell market pair above the margin threshold,
          ranked biggest first. More actionable than the best/worst-market
          fields on `summary` above: those only compare two markets, this
          shows every pair worth considering plus a viability call and a
          transport-cost caveat per pair, straight from the backend. */}
      {arbitrage && arbitrage.length > 0 && (
        <div style={card}>
          <h3 style={{ marginTop: 0 }}>Arbitrage opportunities — {commodity}</h3>
          <p style={{ color: "#777", fontSize: 13, marginTop: -8 }}>
            Gross margin only — always weigh it against actual transport cost.
          </p>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                <th style={{ padding: "6px 8px" }}>Buy in</th>
                <th style={{ padding: "6px 8px" }}>Sell in</th>
                <th style={{ padding: "6px 8px" }}>Margin</th>
                <th style={{ padding: "6px 8px" }}>Viable?</th>
                <th style={{ padding: "6px 8px" }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {arbitrage.slice(0, 5).map((o) => (
                <tr key={`${o.buy_market}-${o.sell_market}`} style={{ borderBottom: "1px solid #f4f4f4" }}>
                  <td style={{ padding: "6px 8px" }}>
                    {o.buy_market} ({formatMoney(o.buy_price, o.currency, o.unit)})
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {o.sell_market} ({formatMoney(o.sell_price, o.currency, o.unit)})
                  </td>
                  <td style={{ padding: "6px 8px", fontWeight: 700, color: o.viable ? "#2e7d32" : "#e65100" }}>
                    +{o.gross_margin_pct.toFixed(1)}%
                  </td>
                  <td style={{ padding: "6px 8px" }}>{o.viable ? "✅ Likely" : "⚠️ Check transport"}</td>
                  <td style={{ padding: "6px 8px", color: "#777" }}>{o.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {arbitrageNotice && (
        <div style={{ ...card, color: "#777", fontSize: 13 }}>{arbitrageNotice}</div>
      )}

      {status === "loading" && !forecast && <div style={card}>Loading forecast…</div>}
    </div>
  );
}
