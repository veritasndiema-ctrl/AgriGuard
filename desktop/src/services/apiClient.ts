/**
 * Desktop API client — thin wrapper around the AgriGuard FastAPI backend.
 * Mirrors the shared TypeScript client for consistency.
 */

export interface ForecastPoint {
  date: string;
  predicted_price: number;
  lower_bound: number;
  upper_bound: number;
  confidence: number;
}

export interface ForecastResponse {
  commodity: string;
  market: string;
  currency: string;
  unit: string;
  horizon_days: number;
  observations_used: number;
  forecast: ForecastPoint[];
  trend: string;
  pct_change: number;
  alert: string | null;
  model_used: string;
  generated_at: string;
}

export interface CommodityListResponse {
  commodities: string[];
  markets: string[];
  total_observations: number;
}

/** Mirrors backend/app/routers/markets.py::ArbitrageOpportunity exactly. */
export interface ArbitrageOpportunity {
  commodity: string;
  buy_market: string;
  sell_market: string;
  buy_price: number;
  sell_price: number;
  gross_margin: number;
  gross_margin_pct: number;
  currency: string;
  unit: string;
  viable: boolean;
  note: string;
}

export class AgriGuardApi {
  constructor(private baseURL: string) {}

  private async get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
    const url = new URL(path, this.baseURL);
    if (params) {
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
    }
    const res = await fetch(url.toString());
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return res.json() as Promise<T>;
  }

  health(): Promise<{ status: string; version: string }> {
    return this.get("/health");
  }

  listCommodities(): Promise<CommodityListResponse> {
    return this.get("/forecasts/commodities");
  }

  getForecast(commodity: string, market = "Kampala", horizon = 14): Promise<ForecastResponse> {
    return this.get(`/forecasts/${encodeURIComponent(commodity)}`, {
      market,
      horizon,
    });
  }

  marketSummary(commodity: string): Promise<Record<string, unknown>> {
    return this.get(`/markets/summary/${encodeURIComponent(commodity)}`);
  }

  /**
   * GET /markets/arbitrage/{commodity} — buy/sell market pairs with a gross
   * margin above minMarginPct, ranked biggest first. Throws (via get()'s
   * HTTP ${status} error) on the backend's two expected non-200 outcomes —
   * 404 when nothing clears the threshold, 422 when fewer than 2 requested
   * markets have data — so callers should inspect the thrown Error's
   * message for "404"/"422" rather than treating every rejection the same
   * way (see Dashboard.tsx for the pattern).
   */
  arbitrageOpportunities(
    commodity: string,
    minMarginPct = 10,
    markets = "Kampala,Mbarara,Gulu,Kabale,Jinja,Mbale"
  ): Promise<ArbitrageOpportunity[]> {
    return this.get(`/markets/arbitrage/${encodeURIComponent(commodity)}`, {
      min_margin_pct: minMarginPct,
      markets,
    });
  }
}
