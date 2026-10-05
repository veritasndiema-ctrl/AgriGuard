/**
 * Shared TypeScript client for the AgriGuard FastAPI backend.
 *
 * Used by any TypeScript frontend (desktop, web) that wants a typed wrapper
 * around the API instead of hand-rolling fetch/axios calls. Mirrors
 * backend/app/main.py and backend/app/routers/*.py — see the per-method
 * JSDoc for which router each call maps to.
 *
 * Design notes:
 *  - Every method returns parsed response data directly (not the raw axios
 *    response) and throws AgriGuardApiError on failure, so callers never
 *    need to know axios is involved.
 *  - Response types come from ../types/schemas.ts wherever that file already
 *    defines a matching shape. A handful of backend/app/routers/markets.py
 *    endpoints return richer objects than schemas.ts currently models
 *    (e.g. full market-by-market breakdowns, arbitrage detail, national
 *    summaries). Rather than editing schemas.ts's existing contract as a
 *    side effect of this file, those shapes are declared locally below and
 *    re-exported — promote them into schemas.ts in a follow-up if other
 *    consumers need them too.
 *  - Where schemas.ts's existing type is a *simplified* view of a richer
 *    backend response (MarketSummary, ArbitrageOpportunity), this client
 *    exposes both: a `*Detailed` method returning the full backend shape,
 *    and the plain method adapting it down to the schemas.ts contract so
 *    existing consumers of that type keep working unchanged.
 *  - backend/app/routers/prices.py is intentionally not wired into main.py
 *    yet (see backend README "Known issues"), so it has no client method
 *    here either — add one once that router is actually mounted.
 *  - POST /ussd is an inbound webhook for the Africa's Talking gateway, not
 *    something a frontend calls directly, so it has no client method. The
 *    GET /ussd/simulate dev endpoint (browser-testable, same session logic)
 *    is wrapped instead.
 */

import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from "axios";
import type {
  ArbitrageOpportunity,
  CommodityListResponse,
  CompareResponse,
  ForecastResponse,
  HealthResponse,
  HistoryResponse,
  MarketSummary,
  PricePredictionRequest,
  PricePredictionResponse,
  UssdSimulateRequest,
  UssdSimulateResponse,
} from "../types/schemas";

export * from "../types/schemas";

// =============================================================================
// Client configuration & errors
// =============================================================================

export interface AgriGuardApiClientOptions {
  /** Base URL of the AgriGuard FastAPI backend, e.g. "http://localhost:8000" */
  baseURL: string;
  /** Request timeout in milliseconds. Defaults to 15000. */
  timeoutMs?: number;
}

/**
 * Normalised error thrown by every AgriGuardApiClient method.
 *
 * FastAPI's default error body is `{ "detail": ... }`; main.py's global
 * exception handler instead returns `{ "error", "detail", "timestamp" }`.
 * `detail` here is whichever of those the server actually sent, so callers
 * get a consistent shape regardless of which error path produced it.
 */
export class AgriGuardApiError extends Error {
  /** HTTP status code, if the server responded at all (undefined for network errors). */
  readonly status?: number;
  /** Raw error payload from the response body, if any. */
  readonly detail?: unknown;

  constructor(message: string, status?: number, detail?: unknown) {
    super(message);
    this.name = "AgriGuardApiError";
    this.status = status;
    this.detail = detail;
  }
}

// =============================================================================
// Locally-scoped types for endpoints not (yet) modelled in types/schemas.ts
// =============================================================================

/** One market's latest-price snapshot. Mirrors routers/markets.py::MarketPrice. */
export interface MarketPrice {
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

/**
 * Full cross-market summary for one commodity.
 * Mirrors routers/markets.py::CommodityMarketSummary — the complete version
 * of the trimmed `MarketSummary` type from types/schemas.ts.
 */
export interface CommodityMarketSummary {
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

/** All commodities tracked in a single market. Mirrors routers/markets.py::MarketOverview. */
export interface MarketOverview {
  market: string;
  region: string | null;
  total_commodities_tracked: number;
  commodities: Array<{
    commodity: string;
    latest_price: number;
    currency: string;
    unit: string;
    trend: string;
    date_recorded: string;
    days_since_update: number;
  }>;
  generated_at: string;
}

/** A dated price point tagged with the market it came from. */
export interface MarketPriceHistoryPoint {
  date: string;
  price: number;
  market: string;
}

/**
 * Historical price series with summary statistics for one commodity × market.
 * Mirrors routers/markets.py::PriceHistoryResponse. Named
 * `MarketPriceHistoryResponse` here to avoid clashing with the simpler
 * `HistoryResponse` type in types/schemas.ts (which mirrors
 * routers/forecasts.py's /forecasts/history endpoint instead).
 */
export interface MarketPriceHistoryResponse {
  commodity: string;
  market: string;
  currency: string;
  unit: string;
  history: MarketPriceHistoryPoint[];
  min_price: number;
  max_price: number;
  avg_price: number;
  volatility_pct: number;
}

/** One commodity × market pair with a notable recent price movement. */
export interface TopMoverItem {
  commodity: string;
  market: string;
  latest_price: number;
  previous_price: number;
  change_pct: number;
  direction: "up" | "down";
  alert_level: "high" | "medium" | "low";
  currency: string;
}

/** Biggest price gainers and losers over a given period. */
export interface TopMoversResponse {
  gainers: TopMoverItem[];
  losers: TopMoverItem[];
  period_days: number;
  generated_at: string;
}

/**
 * Full arbitrage opportunity detail. Mirrors
 * routers/markets.py::ArbitrageOpportunity — the complete version of the
 * trimmed `ArbitrageOpportunity` type from types/schemas.ts.
 */
export interface ArbitrageOpportunityDetailed {
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

/** One row in the national price snapshot. */
export interface NationalCommoditySummary {
  commodity: string;
  national_avg_price: number;
  min_price: number;
  max_price: number;
  price_spread_pct: number;
  trend: string;
  markets_tracked: number;
  currency: string;
  unit: string;
}

/** Snapshot of all commodity prices at the national level. */
export interface NationalSummaryResponse {
  commodities: NationalCommoditySummary[];
  data_as_of: string;
  generated_at: string;
}

/** Root landing response from GET /. */
export interface ApiInfoResponse {
  message: string;
  docs: string;
  health: string;
}

// =============================================================================
// Client
// =============================================================================

export class AgriGuardApiClient {
  private readonly http: AxiosInstance;

  constructor(options: AgriGuardApiClientOptions | string) {
    const baseURL = typeof options === "string" ? options : options.baseURL;
    const timeout = typeof options === "string" ? 15000 : options.timeoutMs ?? 15000;
    this.http = axios.create({ baseURL, timeout });
  }

  // ── Internal helpers ─────────────────────────────────────────────────────

  private async request<T>(config: AxiosRequestConfig): Promise<T> {
    try {
      const res = await this.http.request<T>(config);
      return res.data;
    } catch (err) {
      throw this.toApiError(err);
    }
  }

  private toApiError(err: unknown): AgriGuardApiError {
    if (axios.isAxiosError(err)) {
      const axiosErr = err as AxiosError<Record<string, unknown>>;
      const body = axiosErr.response?.data;
      const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
      const message =
        typeof detail === "string"
          ? detail
          : axiosErr.response
          ? `AgriGuard API request failed with status ${axiosErr.response.status}`
          : axiosErr.message;
      return new AgriGuardApiError(message, axiosErr.response?.status, detail ?? body);
    }
    return new AgriGuardApiError(err instanceof Error ? err.message : "Unknown AgriGuard API error");
  }

  // ── System ───────────────────────────────────────────────────────────────

  /** GET / — basic API landing info. */
  getApiInfo(): Promise<ApiInfoResponse> {
    return this.request({ method: "GET", url: "/" });
  }

  /** GET /health — liveness/readiness check. */
  health(): Promise<HealthResponse> {
    return this.request({ method: "GET", url: "/health" });
  }

  // ── Price prediction & validation (backend/app/main.py) ─────────────────

  /**
   * POST /api/v1/predict — single-point price prediction for one crop,
   * region, and target date. This is the simpler sibling of the forecast
   * endpoints below; it returns one predicted price plus a SELL/HOLD/STORE
   * recommendation rather than a full horizon.
   */
  predictPrice(payload: PricePredictionRequest): Promise<PricePredictionResponse> {
    return this.request({ method: "POST", url: "/api/v1/predict", data: payload });
  }

  // ── Forecasts (routers/forecasts.py) ─────────────────────────────────────

  /** GET /forecasts/commodities — all commodities and markets with data. */
  listCommodities(): Promise<CommodityListResponse> {
    return this.request({ method: "GET", url: "/forecasts/commodities" });
  }

  /**
   * GET /forecasts/{commodity} — Prophet (or linear-fallback) price forecast
   * for one commodity × market over the given horizon.
   */
  getForecast(
    commodity: string,
    options: { market?: string; horizon?: number } = {}
  ): Promise<ForecastResponse> {
    return this.request({
      method: "GET",
      url: `/forecasts/${encodeURIComponent(commodity)}`,
      params: { market: options.market, horizon: options.horizon },
    });
  }

  /** GET /forecasts/history/{commodity} — raw historical prices (for sparklines). */
  getForecastHistory(
    commodity: string,
    options: { market?: string; days?: number } = {}
  ): Promise<HistoryResponse> {
    return this.request({
      method: "GET",
      url: `/forecasts/history/${encodeURIComponent(commodity)}`,
      params: { market: options.market, days: options.days },
    });
  }

  /** GET /forecasts/compare/{commodity} — forecast for the same commodity across several markets. */
  compareForecasts(
    commodity: string,
    options: { markets?: string[]; horizon?: number } = {}
  ): Promise<CompareResponse> {
    return this.request({
      method: "GET",
      url: `/forecasts/compare/${encodeURIComponent(commodity)}`,
      params: {
        markets: options.markets?.join(","),
        horizon: options.horizon,
      },
    });
  }

  // ── Markets (routers/markets.py) ─────────────────────────────────────────

  /**
   * GET /markets/summary/{commodity} — best/worst market to sell in, adapted
   * down to the trimmed `MarketSummary` shape from types/schemas.ts.
   * Use `getMarketSummaryDetailed` for the full per-market breakdown.
   */
  async getMarketSummary(commodity: string, markets?: string[]): Promise<MarketSummary> {
    const detailed = await this.getMarketSummaryDetailed(commodity, markets);
    const best = detailed.markets.find((m) => m.market === detailed.best_market_to_sell);
    const worst = detailed.markets.find((m) => m.market === detailed.worst_market_to_sell);
    return {
      commodity: detailed.commodity,
      best_market: detailed.best_market_to_sell,
      best_price: best?.latest_price ?? detailed.markets[0]?.latest_price ?? 0,
      worst_market: detailed.worst_market_to_sell,
      worst_price: worst?.latest_price ?? detailed.markets[detailed.markets.length - 1]?.latest_price ?? 0,
      national_avg: detailed.national_avg_price,
      currency: detailed.currency,
    };
  }

  /** GET /markets/summary/{commodity} — full response, including the per-market breakdown and advisory text. */
  getMarketSummaryDetailed(commodity: string, markets?: string[]): Promise<CommodityMarketSummary> {
    return this.request({
      method: "GET",
      url: `/markets/summary/${encodeURIComponent(commodity)}`,
      params: { markets: markets?.join(",") },
    });
  }

  /** GET /markets/overview/{market} — every commodity currently priced in one market. */
  getMarketOverview(market: string): Promise<MarketOverview> {
    return this.request({ method: "GET", url: `/markets/overview/${encodeURIComponent(market)}` });
  }

  /**
   * GET /markets/history/{commodity} — historical series plus summary stats
   * (min/max/avg/volatility) for one commodity × market.
   * Distinct from `getForecastHistory`, which hits /forecasts/history and
   * returns the plainer `HistoryResponse` shape.
   */
  getMarketPriceHistory(
    commodity: string,
    options: { market?: string; days?: number } = {}
  ): Promise<MarketPriceHistoryResponse> {
    return this.request({
      method: "GET",
      url: `/markets/history/${encodeURIComponent(commodity)}`,
      params: { market: options.market, days: options.days },
    });
  }

  /** GET /markets/movers — biggest price gainers and losers across all commodities/markets. */
  getTopMovers(options: { periodDays?: number; topN?: number } = {}): Promise<TopMoversResponse> {
    return this.request({
      method: "GET",
      url: "/markets/movers",
      params: { period_days: options.periodDays, top_n: options.topN },
    });
  }

  /** GET /markets/compare/{commodity} — latest prices for one commodity across several markets, sorted highest first. */
  compareMarketPrices(commodity: string, markets?: string[]): Promise<MarketPrice[]> {
    return this.request({
      method: "GET",
      url: `/markets/compare/${encodeURIComponent(commodity)}`,
      params: { markets: markets?.join(",") },
    });
  }

  /**
   * GET /markets/arbitrage/{commodity} — buy-low/sell-high opportunities
   * between markets, adapted down to the trimmed `ArbitrageOpportunity`
   * shape from types/schemas.ts. Use `getArbitrageOpportunitiesDetailed`
   * for the full response including viability and transport-cost notes.
   */
  async getArbitrageOpportunities(
    commodity: string,
    options: { markets?: string[]; minMarginPct?: number } = {}
  ): Promise<ArbitrageOpportunity[]> {
    const detailed = await this.getArbitrageOpportunitiesDetailed(commodity, options);
    return detailed.map((o) => ({
      commodity: o.commodity,
      buy_market: o.buy_market,
      sell_market: o.sell_market,
      buy_price: o.buy_price,
      sell_price: o.sell_price,
      spread_pct: o.gross_margin_pct,
      currency: o.currency,
    }));
  }

  /** GET /markets/arbitrage/{commodity} — full response, including gross margin, viability, and advisory notes. */
  getArbitrageOpportunitiesDetailed(
    commodity: string,
    options: { markets?: string[]; minMarginPct?: number } = {}
  ): Promise<ArbitrageOpportunityDetailed[]> {
    return this.request({
      method: "GET",
      url: `/markets/arbitrage/${encodeURIComponent(commodity)}`,
      params: { markets: options.markets?.join(","), min_margin_pct: options.minMarginPct },
    });
  }

  /** GET /markets/national-summary — one row per commodity, averaged across all markets. */
  getNationalSummary(): Promise<NationalSummaryResponse> {
    return this.request({ method: "GET", url: "/markets/national-summary" });
  }

  // ── USSD (routers/ussd.py) ───────────────────────────────────────────────

  /**
   * GET /ussd/simulate — dev-only browser simulator for the USSD menu tree,
   * useful for testing the Africa's Talking flow without a real carrier.
   *
   * The live endpoint is plain-text (`CON ...` / `END ...`), not JSON, and
   * the simulator always uses a fixed server-side session id
   * ("SIM-DEV-001") — `request.session_id` and `request.service_code` are
   * accepted here for forward-compatibility with `UssdSimulateRequest` but
   * are not actually sent to the server today; only `text` and
   * `phone_number` affect the simulated session.
   */
  async simulateUssd(request: UssdSimulateRequest = {}): Promise<UssdSimulateResponse> {
    try {
      const res = await this.http.request<string>({
        method: "GET",
        url: "/ussd/simulate",
        params: {
          text: request.text ?? "",
          phone: request.phone_number ?? "+256700000000",
        },
        // The response is plain text starting with "CON " or "END ", not
        // JSON — disable axios's default JSON.parse attempt on the body.
        transformResponse: [(data: string) => data],
      });
      const raw = res.data ?? "";
      const endOfSession = raw.startsWith("END");
      const response = raw.replace(/^(CON|END)\s?/, "");
      return {
        session_id: request.session_id ?? "SIM-DEV-001",
        response,
        end_of_session: endOfSession,
      };
    } catch (err) {
      throw this.toApiError(err);
    }
  }
}

/** Convenience factory, equivalent to `new AgriGuardApiClient(baseURL)`. */
export function createAgriGuardClient(baseURL: string, timeoutMs?: number): AgriGuardApiClient {
  return new AgriGuardApiClient({ baseURL, timeoutMs });
}

export default AgriGuardApiClient;
