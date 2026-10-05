/// Data classes mirroring the AgriGuard FastAPI weather schemas
/// (backend/app/routers/weather.py, backend/app/schemas/weather.py).
/// Only the two analytics endpoints that need no market_id lookup are
/// modelled here — drought risk and heavy-rain alerts — since every other
/// screen in this app already addresses markets by name, not numeric id,
/// and these two endpoints are the ones that match that pattern.
///
/// [WeatherSnapshot] below is a different thing: it mirrors the bundled
/// offline snapshot (assets/data/agriguard_weather_data.json, built by
/// scripts/gen_offline_data.py) rather than a live endpoint — current
/// conditions, a short forecast, and a plain-language farmer advisory,
/// per market, all available with no network connection required.

/// One day of forecast weather for a market.
class WeatherForecastDay {
  final String date;
  final double tempMaxC;
  final double tempMinC;
  final double rainfallMm;
  final double humidityMaxPct;

  WeatherForecastDay({
    required this.date,
    required this.tempMaxC,
    required this.tempMinC,
    required this.rainfallMm,
    required this.humidityMaxPct,
  });

  factory WeatherForecastDay.fromJson(Map<String, dynamic> json) {
    return WeatherForecastDay(
      date: json['date'] as String? ?? '',
      tempMaxC: (json['temp_max_c'] as num?)?.toDouble() ?? 0.0,
      tempMinC: (json['temp_min_c'] as num?)?.toDouble() ?? 0.0,
      rainfallMm: (json['rainfall_mm'] as num?)?.toDouble() ?? 0.0,
      humidityMaxPct: (json['humidity_max_pct'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

/// Current conditions, short forecast, and farmer advisory for one market.
/// Mirrors one entry under `markets` in the bundled weather snapshot.
class WeatherSnapshot {
  final String market;
  final String region;
  final String asOf;
  final double currentTempMaxC;
  final double currentTempMinC;
  final double currentRainfallMm;
  final double currentHumidityMaxPct;
  final double currentWindSpeedMaxKmh;
  final List<WeatherForecastDay> forecast;
  final String riskLevel; // "Dry spell" | "Heavy rain" | "Hot conditions" | "Normal"
  final String advice;
  final double rainfallLast7dMm;

  WeatherSnapshot({
    required this.market,
    required this.region,
    required this.asOf,
    required this.currentTempMaxC,
    required this.currentTempMinC,
    required this.currentRainfallMm,
    required this.currentHumidityMaxPct,
    required this.currentWindSpeedMaxKmh,
    required this.forecast,
    required this.riskLevel,
    required this.advice,
    required this.rainfallLast7dMm,
  });

  factory WeatherSnapshot.fromJson(Map<String, dynamic> json) {
    final current = json['current'] as Map<String, dynamic>? ?? {};
    return WeatherSnapshot(
      market: json['market'] as String? ?? '',
      region: json['region'] as String? ?? '',
      asOf: current['as_of'] as String? ?? '',
      currentTempMaxC: (current['temp_max_c'] as num?)?.toDouble() ?? 0.0,
      currentTempMinC: (current['temp_min_c'] as num?)?.toDouble() ?? 0.0,
      currentRainfallMm: (current['rainfall_mm'] as num?)?.toDouble() ?? 0.0,
      currentHumidityMaxPct: (current['humidity_max_pct'] as num?)?.toDouble() ?? 0.0,
      currentWindSpeedMaxKmh: (current['wind_speed_max_kmh'] as num?)?.toDouble() ?? 0.0,
      forecast: (json['forecast'] as List<dynamic>? ?? [])
          .map((e) => WeatherForecastDay.fromJson(e as Map<String, dynamic>))
          .toList(),
      riskLevel: json['risk_level'] as String? ?? 'Normal',
      advice: json['advice'] as String? ?? '',
      rainfallLast7dMm: (json['rainfall_last_7d_mm'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

class DroughtRiskItem {
  final int marketId;
  final String marketName;
  final String region;
  final int deficitDays;
  final int lookbackDays;
  final double? avgWaterBalanceMm;
  final String? latestReadingDate;
  final String riskLevel; // "LOW" | "MODERATE" | "HIGH" | "SEVERE"

  DroughtRiskItem({
    required this.marketId,
    required this.marketName,
    required this.region,
    required this.deficitDays,
    required this.lookbackDays,
    this.avgWaterBalanceMm,
    this.latestReadingDate,
    required this.riskLevel,
  });

  factory DroughtRiskItem.fromJson(Map<String, dynamic> json) {
    return DroughtRiskItem(
      marketId: json['market_id'] as int,
      marketName: json['market_name'] as String? ?? '',
      region: json['region'] as String? ?? '',
      deficitDays: json['deficit_days'] as int? ?? 0,
      lookbackDays: json['lookback_days'] as int? ?? 30,
      avgWaterBalanceMm: (json['avg_water_balance_mm'] as num?)?.toDouble(),
      latestReadingDate: json['latest_reading_date'] as String?,
      riskLevel: json['risk_level'] as String? ?? 'LOW',
    );
  }
}

class DroughtRiskResponse {
  final String generatedForDate;
  final double thresholdMm;
  final int lookbackDays;
  final List<DroughtRiskItem> markets;

  DroughtRiskResponse({
    required this.generatedForDate,
    required this.thresholdMm,
    required this.lookbackDays,
    required this.markets,
  });

  factory DroughtRiskResponse.fromJson(Map<String, dynamic> json) {
    return DroughtRiskResponse(
      generatedForDate: json['generated_for_date'] as String? ?? '',
      thresholdMm: (json['threshold_mm'] as num?)?.toDouble() ?? 0.0,
      lookbackDays: json['lookback_days'] as int? ?? 30,
      markets: (json['markets'] as List<dynamic>? ?? [])
          .map((e) => DroughtRiskItem.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

class HeavyRainAlertItem {
  final int marketId;
  final String marketName;
  final String region;
  final String readingDate;
  final double rainfallMm;
  final bool isForecast;

  HeavyRainAlertItem({
    required this.marketId,
    required this.marketName,
    required this.region,
    required this.readingDate,
    required this.rainfallMm,
    required this.isForecast,
  });

  factory HeavyRainAlertItem.fromJson(Map<String, dynamic> json) {
    return HeavyRainAlertItem(
      marketId: json['market_id'] as int,
      marketName: json['market_name'] as String? ?? '',
      region: json['region'] as String? ?? '',
      readingDate: json['reading_date'] as String? ?? '',
      rainfallMm: (json['rainfall_mm'] as num?)?.toDouble() ?? 0.0,
      isForecast: json['is_forecast'] as bool? ?? false,
    );
  }
}

class HeavyRainAlertResponse {
  final double thresholdMm;
  final int lookbackDays;
  final List<HeavyRainAlertItem> alerts;

  HeavyRainAlertResponse({
    required this.thresholdMm,
    required this.lookbackDays,
    required this.alerts,
  });

  factory HeavyRainAlertResponse.fromJson(Map<String, dynamic> json) {
    return HeavyRainAlertResponse(
      thresholdMm: (json['threshold_mm'] as num?)?.toDouble() ?? 0.0,
      lookbackDays: json['lookback_days'] as int? ?? 7,
      alerts: (json['alerts'] as List<dynamic>? ?? [])
          .map((e) => HeavyRainAlertItem.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
