/// Data classes mirroring the AgriGuard FastAPI forecasting schemas
/// (backend/app/routers/forecasts.py). Field names below are the camelCase
/// Dart-side names; `fromJson` maps them from the snake_case JSON keys the
/// backend actually returns — keep these in sync with forecasts.py if its
/// Pydantic models change.
///
/// Market-intelligence classes (MarketPrice, CommodityMarketSummary,
/// TopMoverItem, TopMoversResponse, ArbitrageOpportunity) live in
/// market_model.dart, not here — this file previously duplicated them,
/// which collided with those definitions at compile time, while the
/// classes this file actually needs to define (ForecastResponse,
/// ForecastPoint, HistoryPoint, CommodityList) were missing entirely.

/// A single predicted price point on the forecast horizon.
/// Mirrors forecasts.py::ForecastPoint.
class ForecastPoint {
  final String date; // ISO-8601 "YYYY-MM-DD"
  final double predictedPrice;
  final double lowerBound; // lower edge of 90% prediction interval
  final double upperBound; // upper edge of 90% prediction interval
  final double confidence; // clamped to [0.0, 1.0]

  ForecastPoint({
    required this.date,
    required this.predictedPrice,
    required this.lowerBound,
    required this.upperBound,
    required this.confidence,
  });

  factory ForecastPoint.fromJson(Map<String, dynamic> json) {
    return ForecastPoint(
      date: json['date'] as String? ?? '',
      predictedPrice: (json['predicted_price'] as num?)?.toDouble() ?? 0.0,
      lowerBound: (json['lower_bound'] as num?)?.toDouble() ?? 0.0,
      upperBound: (json['upper_bound'] as num?)?.toDouble() ?? 0.0,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.0,
    );
  }

  /// Inverse of [fromJson] — same snake_case shape the backend sends, so a
  /// [ForecastResponse] can be cached to disk (see [LocalCache] /
  /// [SyncService] in offline/) and later fed straight back through
  /// [ForecastResponse.fromJson] without a separate parser.
  Map<String, dynamic> toJson() => {
        'date': date,
        'predicted_price': predictedPrice,
        'lower_bound': lowerBound,
        'upper_bound': upperBound,
        'confidence': confidence,
      };
}

/// Full forecast for one commodity x market combination.
/// Mirrors forecasts.py::ForecastResponse exactly. Note the backend has
/// no top-level "last_predicted" field — that value is derived here from
/// the final point in [forecast] instead (see [lastPredicted]).
class ForecastResponse {
  final String commodity;
  final String market;
  final String currency;
  final String unit;
  final int horizonDays;
  final int observationsUsed;
  final List<ForecastPoint> forecast;
  final String trend; // "rising" | "falling" | "stable"
  final double pctChange; // predicted % change over the horizon
  final String? alert; // human-readable warning if pctChange is large
  final String modelUsed; // "prophet" | "prophet+xgb" | "linear" — internal only, never shown to users
  final String generatedAt;

  /// Set client-side (never from the JSON payload) when the offline
  /// snapshot didn't have data for the exact market that was requested and
  /// substituted the nearest one it did have. Non-null means "this is still
  /// good data, just for [substitutedFromMarket] instead of what was asked
  /// for" — screens can show a small informational note instead of
  /// discarding the forecast entirely.
  final String? substitutedFromMarket;

  ForecastResponse({
    required this.commodity,
    required this.market,
    required this.currency,
    required this.unit,
    required this.horizonDays,
    required this.observationsUsed,
    required this.forecast,
    required this.trend,
    required this.pctChange,
    this.alert,
    required this.modelUsed,
    required this.generatedAt,
    this.substitutedFromMarket,
  });

  /// Copy with a substitution note attached — used by ApiService when the
  /// offline snapshot serves a different market than requested.
  ForecastResponse withSubstitutionNote(String requestedMarket) {
    return ForecastResponse(
      commodity: commodity,
      market: market,
      currency: currency,
      unit: unit,
      horizonDays: horizonDays,
      observationsUsed: observationsUsed,
      forecast: forecast,
      trend: trend,
      pctChange: pctChange,
      alert: alert,
      modelUsed: modelUsed,
      generatedAt: generatedAt,
      substitutedFromMarket: requestedMarket,
    );
  }

  /// Farmer-friendly label for the forecasting confidence — deliberately
  /// abstracted away from [modelUsed] (which model / library ran under the
  /// hood is internal implementation detail, not something to expose in the
  /// UI).
  String get confidenceLabel {
    final avg = forecast.isEmpty
        ? 0.0
        : forecast.map((p) => p.confidence).reduce((a, b) => a + b) / forecast.length;
    if (avg >= 0.85) return 'High confidence';
    if (avg >= 0.6) return 'Moderate confidence';
    return 'Low confidence';
  }

  factory ForecastResponse.fromJson(Map<String, dynamic> json) {
    final points = (json['forecast'] as List<dynamic>? ?? [])
        .map((e) => ForecastPoint.fromJson(e as Map<String, dynamic>))
        .toList();
    return ForecastResponse(
      commodity: json['commodity'] as String? ?? '',
      market: json['market'] as String? ?? '',
      currency: json['currency'] as String? ?? 'UGX',
      unit: json['unit'] as String? ?? 'KG',
      horizonDays: json['horizon_days'] as int? ?? 0,
      observationsUsed: json['observations_used'] as int? ?? 0,
      forecast: points,
      trend: json['trend'] as String? ?? 'stable',
      pctChange: (json['pct_change'] as num?)?.toDouble() ?? 0.0,
      alert: json['alert'] as String?,
      modelUsed: json['model_used'] as String? ?? 'linear',
      generatedAt: json['generated_at'] as String? ?? '',
    );
  }

  /// Inverse of [fromJson]. Deliberately omits [substitutedFromMarket] —
  /// that flag is set client-side when *serving* a substitution, not a
  /// property of the forecast itself, so it shouldn't be baked into a
  /// cached copy and silently resurface on a later cache hit for a market
  /// that, by then, may have its own real data.
  Map<String, dynamic> toJson() => {
        'commodity': commodity,
        'market': market,
        'currency': currency,
        'unit': unit,
        'horizon_days': horizonDays,
        'observations_used': observationsUsed,
        'forecast': forecast.map((p) => p.toJson()).toList(),
        'trend': trend,
        'pct_change': pctChange,
        'alert': alert,
        'model_used': modelUsed,
        'generated_at': generatedAt,
      };

  /// The backend doesn't send a standalone "last predicted price" — the
  /// closest useful value is the final point on the forecast horizon.
  double get lastPredicted =>
      forecast.isNotEmpty ? forecast.last.predictedPrice : 0.0;
}

/// A single historical price observation (for sparklines / charts).
/// Mirrors forecasts.py::HistoryPoint.
class HistoryPoint {
  final String date;
  final double price;

  HistoryPoint({required this.date, required this.price});

  factory HistoryPoint.fromJson(Map<String, dynamic> json) {
    return HistoryPoint(
      date: json['date'] as String? ?? '',
      price: (json['price'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

/// Available commodities and markets in the loaded dataset.
/// Mirrors forecasts.py::CommodityListResponse.
class CommodityList {
  final List<String> commodities;
  final List<String> markets;
  final int totalObservations;

  CommodityList({
    required this.commodities,
    required this.markets,
    required this.totalObservations,
  });

  factory CommodityList.fromJson(Map<String, dynamic> json) {
    return CommodityList(
      commodities: (json['commodities'] as List<dynamic>? ?? [])
          .map((e) => e as String)
          .toList(),
      markets: (json['markets'] as List<dynamic>? ?? [])
          .map((e) => e as String)
          .toList(),
      totalObservations: json['total_observations'] as int? ?? 0,
    );
  }
}
