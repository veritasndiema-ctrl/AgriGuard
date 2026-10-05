/**
 * Shared TypeScript types mirroring AgriGuard FastAPI Pydantic schemas.
 * Keep in sync with backend/app/schemas.py and the routers.
 */

export type Trend = "rising" | "falling" | "stable" | "up" | "down";

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  ml_ready: boolean;
  validator_ready: boolean;
  timestamp: string;
}

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
  trend: Trend;
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

export interface HistoryPoint {
  date: string;
  price: number;
}

export interface HistoryResponse {
  commodity: string;
  market: string;
  currency: string;
  unit: string;
  history: HistoryPoint[];
}

export interface CompareResponse {
  commodity: string;
  horizon_days: number;
  results: ForecastResponse[];
  skipped_markets: string[];
}

export interface PricePredictionRequest {
  crop: string;
  region: string;
  date: string; // YYYY-MM-DD
}

export interface PricePredictionResponse {
  crop: string;
  region: string;
  date: string;
  predicted_price: number;
  currency: string;
  trend: Trend;
  recommendation: "SELL" | "STORE" | "HOLD";
  confidence: number;
  timestamp: string;
}

export interface MarketSummary {
  commodity: string;
  best_market: string;
  best_price: number;
  worst_market: string;
  worst_price: number;
  national_avg: number;
  currency: string;
}

export interface ArbitrageOpportunity {
  commodity: string;
  buy_market: string;
  sell_market: string;
  buy_price: number;
  sell_price: number;
  spread_pct: number;
  currency: string;
}

export interface UssdSimulateRequest {
  session_id?: string;
  phone_number?: string;
  text?: string;
  service_code?: string;
}

export interface UssdSimulateResponse {
  session_id: string;
  response: string;
  end_of_session: boolean;
}
