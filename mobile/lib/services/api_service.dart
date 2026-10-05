import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart' show rootBundle;
import 'package:http/http.dart' as http;
import '../models/forecast_model.dart';
import '../models/market_model.dart';
import '../models/weather_model.dart';
import '../offline/local_cache.dart';
import 'backend_config.dart';

/// Mutable out-parameter a caller can pass to any ApiService method to find
/// out whether the result it got back was live or from the offline
/// fallback. Deliberately NOT a field on ApiService itself: several screens
/// (market_screen.dart) fire multiple ApiService calls concurrently against
/// one shared instance, and a shared "last call was live" field would be a
/// race between them — whichever request happened to finish last would
/// silently overwrite the flag for the others. Each call site creates its
/// own LiveFlag, so there's nothing to race.
class LiveFlag {
  bool value = false;
}

/// Talks to the live AgriGuard FastAPI backend when a URL is configured
/// (Settings tab) and it responds, and falls back to a bundled offline
/// snapshot (assets/data/agriguard_offline_data.json) when it isn't
/// configured, unreachable, or too slow.
///
/// Previous version of this file went fully offline — no HTTP, ever —
/// because Keith's dev machine wasn't reachable while out on mobile data.
/// That meant every screen (change market, hit refresh) only ever re-read
/// the same static snapshot, so nothing ever visibly changed. This restores
/// the live path (matching the desktop dashboard's behaviour) while keeping
/// the offline snapshot as a safety net instead of the only source.
class ApiService {
  static const String _assetPath = 'assets/data/agriguard_offline_data.json';
  static const String _weatherAssetPath = 'assets/data/agriguard_weather_data.json';
  static const Duration _timeout = Duration(seconds: 15);

  /// Render's free tier spins the backend down after inactivity; a cold
  /// start can take 50+ seconds to answer. This longer timeout is used only
  /// by [health] / [warmUp] so app launch can wait out a cold start without
  /// forcing every ordinary data call (which uses [_timeout]) to hang that
  /// long on ordinary network flakiness.
  static const Duration _warmUpTimeout = Duration(seconds: 60);
  static Map<String, dynamic>? _offlineCache;
  static Map<String, dynamic>? _weatherOfflineCache;
  final LocalCache _localCache = LocalCache();

  Future<Map<String, dynamic>> _offlineData() async {
    if (_offlineCache != null) return _offlineCache!;
    final raw = await rootBundle.loadString(_assetPath);
    _offlineCache = jsonDecode(raw) as Map<String, dynamic>;
    return _offlineCache!;
  }

  Future<Map<String, dynamic>> _weatherOfflineData() async {
    if (_weatherOfflineCache != null) return _weatherOfflineCache!;
    final raw = await rootBundle.loadString(_weatherAssetPath);
    _weatherOfflineCache = jsonDecode(raw) as Map<String, dynamic>;
    return _weatherOfflineCache!;
  }

  /// Every market the bundled weather snapshot covers (see
  /// scripts/gen_offline_data.py) — a small subset (8 markets) of the full
  /// price-data market list, matching what Open-Meteo is actually wired up
  /// for. Used to populate the Weather screen's market picker.
  Future<List<String>> weatherMarkets() async {
    final d = await _weatherOfflineData();
    final markets = (d['markets'] as Map<String, dynamic>?) ?? {};
    return markets.keys.toList()..sort();
  }

  /// Current conditions, short forecast, and a plain-language farmer
  /// advisory for one market. Always reads from the bundled snapshot —
  /// there's no live equivalent of this exact shape on the backend (the
  /// backend instead exposes /weather/{market_id}/forecast plus separate
  /// drought-risk / heavy-rain analytics, both of which the Weather screen
  /// also shows underneath this for anyone with a live connection).
  Future<WeatherSnapshot?> weatherSnapshot(String market) async {
    final d = await _weatherOfflineData();
    final markets = (d['markets'] as Map<String, dynamic>?) ?? {};
    final resolved = markets.keys.firstWhere(
      (k) => k.toLowerCase() == market.trim().toLowerCase(),
      orElse: () => markets.isNotEmpty ? markets.keys.first : '',
    );
    if (resolved.isEmpty) return null;
    return WeatherSnapshot.fromJson(markets[resolved] as Map<String, dynamic>);
  }

  /// Case/whitespace-insensitive match against a known name list — mirrors
  /// the backend's `.strip().title()` normalisation so a user typing
  /// "maize" or " Maize " still resolves.
  String? _resolve(String input, List<String> options) {
    final target = input.trim().toLowerCase();
    for (final o in options) {
      if (o.toLowerCase() == target) return o;
    }
    return null;
  }

  /// GET against the configured backend. Returns null (never throws) if no
  /// backend is configured, it's unreachable, or it times out — callers
  /// treat null as "fall back to offline data". A non-2xx response with a
  /// body IS treated as a real answer from the server (e.g. 404 "no data
  /// for that market") and throws ApiException rather than falling back,
  /// so a genuinely wrong commodity name reports clearly instead of
  /// silently substituting bundled data.
  Future<dynamic> _get(String path, [Map<String, String>? query, LiveFlag? source]) async {
    final base = await BackendConfig.getBaseUrl();
    if (base.isEmpty) {
      source?.value = false;
      return null;
    }
    final uri = Uri.parse('$base$path').replace(queryParameters: query);
    try {
      final res = await http.get(uri).timeout(_timeout);
      if (res.statusCode >= 200 && res.statusCode < 300) {
        source?.value = true;
        return jsonDecode(res.body);
      }
      source?.value = true; // reached the server; it just said no
      String detail = res.body;
      try {
        final parsed = jsonDecode(res.body);
        if (parsed is Map && parsed['detail'] != null) detail = '${parsed['detail']}';
      } catch (_) {}
      throw ApiException(res.statusCode, detail);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      source?.value = false;
      return null;
    } on SocketException {
      source?.value = false;
      return null;
    } catch (_) {
      source?.value = false;
      return null;
    }
  }

  Future<bool> health() async {
    final base = await BackendConfig.getBaseUrl();
    if (base.isEmpty) return false;
    try {
      final res = await http.get(Uri.parse('$base/health')).timeout(_timeout);
      return res.statusCode >= 200 && res.statusCode < 300;
    } catch (_) {
      return false;
    }
  }

  /// Fire this once at app launch (see main.dart) to start waking a
  /// cold Render instance immediately, using [_warmUpTimeout] rather than
  /// the shorter [_timeout] regular calls use. Fire-and-forget by design —
  /// callers don't need to await it or handle its result; it exists purely
  /// so the backend is more likely to already be awake by the time the
  /// user's first real screen makes a data request.
  Future<void> warmUp() async {
    final base = await BackendConfig.getBaseUrl();
    if (base.isEmpty) return;
    try {
      await http.get(Uri.parse('$base/health')).timeout(_warmUpTimeout);
    } catch (_) {
      // Doesn't matter — ordinary calls fall back to offline data anyway.
    }
  }

  Future<CommodityList> listCommodities({LiveFlag? source}) async {
    final live = await _get('/forecasts/commodities', null, source);
    if (live != null) return CommodityList.fromJson(live as Map<String, dynamic>);

    final d = await _offlineData();
    final commodities = (d['commodities'] as List<dynamic>).cast<String>();
    final markets = (d['markets'] as List<dynamic>).cast<String>();
    return CommodityList(
      commodities: commodities,
      markets: markets,
      totalObservations: (d['forecasts'] as Map<String, dynamic>).length,
    );
  }

  Future<ForecastResponse> getForecast({
    required String commodity,
    String market = 'Kampala',
    int horizon = 14,
    LiveFlag? source,
  }) async {
    final live = await _get(
      '/forecasts/${Uri.encodeComponent(commodity.trim())}',
      {'market': market.trim(), 'horizon': '$horizon'},
      source,
    );
    if (live != null) return ForecastResponse.fromJson(live as Map<String, dynamic>);
    source?.value = false;

    // Second tier: a forecast for this exact commodity+market that
    // SyncService.prefetch fetched live earlier and cached on-device (see
    // offline/sync_service.dart). This is real data for the person's own
    // watchlist, just not from this exact moment, so it beats the generic
    // bundled snapshot below whenever it's available and still fresh.
    final cached = await _localCache.get(LocalCache.forecastKey(commodity, market));
    if (cached != null) {
      try {
        // Re-derive to the exact horizon asked for, same as the bundled
        // snapshot below — a prefetch always runs at a fixed horizon (see
        // main.dart), so without this a cache hit would silently ignore
        // whatever the person picked on the Forecast screen's slider.
        return _reshapeHorizon(ForecastResponse.fromJson(cached), horizon);
      } catch (_) {
        // Malformed/stale-shape cache entry — fall through to the bundled
        // snapshot rather than surfacing a parse error for what's supposed
        // to be a silent fallback tier.
      }
    }

    return _offlineForecast(commodity: commodity, market: market, horizon: horizon);
  }

  Future<ForecastResponse> _offlineForecast({
    required String commodity,
    required String market,
    required int horizon,
  }) async {
    final d = await _offlineData();
    final forecasts = d['forecasts'] as Map<String, dynamic>;
    final commodities = (d['commodities'] as List<dynamic>).cast<String>();

    final resolvedCommodity = _resolve(commodity, commodities) ?? commodity.trim();
    var key = '$resolvedCommodity|${market.trim()}';
    String? fallbackUsedMarket;
    if (!forecasts.containsKey(key)) {
      final fallbackKey = forecasts.keys.firstWhere(
        (k) => k.startsWith('$resolvedCommodity|'),
        orElse: () => '',
      );
      if (fallbackKey.isEmpty) {
        throw ApiException(404, 'No offline price data bundled for "$commodity".');
      }
      key = fallbackKey;
      fallbackUsedMarket = fallbackKey.split('|').last;
    }
    final entry = forecasts[key] as Map<String, dynamic>;
    final horizons = entry['horizons'] as Map<String, dynamic>;

    // Pick the closest bundled horizon, then re-derive it to the exact
    // number of days requested. The offline snapshot only ships a handful
    // of pre-computed horizons (7 / 14 / 28 / ...); previously the whole
    // point set was returned as-is, silently ignoring whatever the person
    // picked on the slider, so a 45-day request quietly came back as 28
    // days of points with no explanation.
    final available = horizons.keys.map(int.parse).toList()..sort();
    final chosen = available.reduce(
      (a, b) => (a - horizon).abs() <= (b - horizon).abs() ? a : b,
    );
    // ForecastResponse.fromJson expects commodity/market/currency/unit at
    // the top level of the map it's given (mirroring the live backend's
    // response shape), but scripts/gen_offline_data.py stores those once
    // per commodity/market pair on `entry` and keeps each per-horizon
    // object (`entry['horizons'][h]`) limited to just that horizon's own
    // fields. Passing the bare horizon object straight to fromJson silently
    // produced a ForecastResponse with an empty commodity/market ("" is a
    // valid non-null String, so nothing threw) — which is exactly the blank
    // "· ." header and "closest match: ." notice Keith saw. Merge the
    // entry-level fields in before parsing so this can't happen regardless
    // of which of the two shapes the bundle uses.
    final horizonJson = {
      ...horizons['$chosen'] as Map<String, dynamic>,
      'commodity': entry['commodity'],
      'market': entry['market'],
      'currency': entry['currency'],
      'unit': entry['unit'],
    };
    var resp = ForecastResponse.fromJson(horizonJson);
    resp = _reshapeHorizon(resp, horizon);

    if (fallbackUsedMarket != null) {
      // Attach a non-fatal note instead of throwing the data away — this is
      // exactly the "changed market, nothing happened" bug: the bundled
      // snapshot has no Maize data for Mbale, so it used to quietly return
      // Lira instead with no indication anything had changed, AND any
      // caller that only wanted the numbers (e.g. the Alerts screen) had no
      // way to get them because the whole response was replaced by a thrown
      // exception.
      return resp.withSubstitutionNote(market.trim());
    }
    return resp;
  }

  /// Re-derives a bundled forecast entry (computed for a fixed horizon like
  /// 7/14/28 days) to the exact horizon the person actually asked for, by
  /// linearly extrapolating the last two bundled points forward (or
  /// truncating) day by day. This keeps the offline experience consistent
  /// with the live backend, where `horizon` is honoured exactly (1–90 days),
  /// instead of silently rounding to the nearest pre-baked bucket.
  ForecastResponse _reshapeHorizon(ForecastResponse base, int horizon) {
    if (base.forecast.length == horizon) return base;
    final points = List.of(base.forecast);
    if (points.isEmpty) return base;

    if (points.length > horizon) {
      final trimmed = points.sublist(0, horizon);
      return _withPoints(base, trimmed);
    }

    // Extend using the average daily delta of the last few bundled points.
    final tailStart = points.length >= 4 ? points.length - 4 : 0;
    final tail = points.sublist(tailStart);
    double dailyDelta = 0;
    for (var i = 1; i < tail.length; i++) {
      dailyDelta += tail[i].predictedPrice - tail[i - 1].predictedPrice;
    }
    dailyDelta = tail.length > 1 ? dailyDelta / (tail.length - 1) : 0;

    final extended = List.of(points);
    var lastDate = DateTime.tryParse(points.last.date) ?? DateTime.now();
    var lastPrice = points.last.predictedPrice;
    final spreadRatio = points.last.upperBound > 0 && points.last.predictedPrice > 0
        ? (points.last.upperBound - points.last.lowerBound) / points.last.predictedPrice
        : 0.06;

    while (extended.length < horizon) {
      lastDate = lastDate.add(const Duration(days: 1));
      lastPrice += dailyDelta;
      // Confidence intervals widen the further out the extrapolation goes —
      // a straight-line projection this far past the model's own horizon
      // genuinely is less certain, and the UI should say so.
      final widen = 1 + (extended.length - points.length) * 0.03;
      final half = lastPrice * spreadRatio / 2 * widen;
      extended.add(
        ForecastPoint(
          date: '${lastDate.year.toString().padLeft(4, '0')}-'
              '${lastDate.month.toString().padLeft(2, '0')}-'
              '${lastDate.day.toString().padLeft(2, '0')}',
          predictedPrice: double.parse(lastPrice.toStringAsFixed(2)),
          lowerBound: double.parse((lastPrice - half).toStringAsFixed(2)),
          upperBound: double.parse((lastPrice + half).toStringAsFixed(2)),
          confidence: (points.last.confidence / widen).clamp(0.0, 1.0).toDouble(),
        ),
      );
    }
    return _withPoints(base, extended);
  }

  ForecastResponse _withPoints(ForecastResponse base, List<ForecastPoint> points) {
    final first = points.first.predictedPrice;
    final last = points.last.predictedPrice;
    final pctChange = first == 0 ? 0.0 : double.parse((((last - first) / first) * 100).toStringAsFixed(2));
    final trend = pctChange > 1.5 ? 'rising' : (pctChange < -1.5 ? 'falling' : 'stable');
    // Regenerate the alert text for the reshaped horizon rather than
    // carrying over `base.alert` — that string was baked in at bundle-build
    // time for whichever fixed horizon (7/14/28d) got picked as the nearest
    // match, so after reshaping to the horizon the person actually chose
    // (e.g. 43 days), the old text quoted a stale percentage that visibly
    // disagreed with the trend badge above it.
    String? alert;
    if (pctChange.abs() >= 5) {
      final direction = trend == 'rising' ? 'rise' : 'fall';
      final emoji = trend == 'rising' ? '\u{1F4C8}' : '\u{1F4C9}';
      final tip = trend == 'rising' ? 'Consider selling soon.' : 'Good time to buy inputs.';
      alert = '$emoji Prices are expected to $direction by ~${pctChange.abs().toStringAsFixed(1)}% '
          'over the forecast period. $tip';
    }
    return ForecastResponse(
      commodity: base.commodity,
      market: base.market,
      currency: base.currency,
      unit: base.unit,
      horizonDays: points.length,
      observationsUsed: base.observationsUsed,
      forecast: points,
      trend: trend,
      pctChange: pctChange,
      alert: alert,
      modelUsed: base.modelUsed,
      generatedAt: base.generatedAt,
    );
  }

  /// Not a real HTTP status — used to distinguish "found data, but for a
  /// different market than asked" so callers can decide whether to show it
  /// anyway (with a banner) or treat it as a hard failure. Kept for
  /// backwards compatibility; new code should check
  /// [ForecastResponse.substitutedFromMarket] instead, since the response
  /// data itself is no longer discarded on substitution.
  static const int offlineMarketSubstituted = 782;

  Future<List<HistoryPoint>> getHistory({
    required String commodity,
    String market = 'Kampala',
    int days = 180,
    LiveFlag? source,
  }) async {
    final live = await _get(
      '/forecasts/history/${Uri.encodeComponent(commodity.trim())}',
      {'market': market.trim(), 'days': '$days'},
      source,
    );
    if (live != null) {
      final hist = (live['history'] as List<dynamic>? ?? [])
          .map((e) => HistoryPoint.fromJson(e as Map<String, dynamic>))
          .toList();
      return hist;
    }
    source?.value = false;

    final d = await _offlineData();
    final forecasts = d['forecasts'] as Map<String, dynamic>;
    final commodities = (d['commodities'] as List<dynamic>).cast<String>();
    final resolvedCommodity = _resolve(commodity, commodities) ?? commodity.trim();

    var key = '$resolvedCommodity|${market.trim()}';
    if (!forecasts.containsKey(key)) {
      final fallbackKey = forecasts.keys.firstWhere(
        (k) => k.startsWith('$resolvedCommodity|'),
        orElse: () => '',
      );
      if (fallbackKey.isEmpty) return [];
      key = fallbackKey;
    }
    final entry = forecasts[key] as Map<String, dynamic>;
    return (entry['history'] as List<dynamic>)
        .map((e) => HistoryPoint.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<CommodityMarketSummary> marketSummary(String commodity, {LiveFlag? source}) async {
    final live = await _get('/markets/summary/${Uri.encodeComponent(commodity.trim())}', null, source);
    if (live != null) return CommodityMarketSummary.fromJson(live as Map<String, dynamic>);
    source?.value = false;

    final d = await _offlineData();
    final summaries = d['market_summaries'] as Map<String, dynamic>;
    final commodities = (d['commodities'] as List<dynamic>).cast<String>();
    final resolved = _resolve(commodity, commodities) ?? commodity.trim();
    if (!summaries.containsKey(resolved)) {
      throw ApiException(404, 'No market data found for "$commodity".');
    }
    return CommodityMarketSummary.fromJson(summaries[resolved] as Map<String, dynamic>);
  }

  Future<TopMoversResponse> topMovers({int periodDays = 30, int topN = 5, LiveFlag? source}) async {
    final live = await _get(
      '/markets/movers',
      {'period_days': '$periodDays', 'top_n': '$topN'},
      source,
    );
    if (live != null) return TopMoversResponse.fromJson(live as Map<String, dynamic>);
    source?.value = false;

    final d = await _offlineData();
    final movers = d['top_movers'] as Map<String, dynamic>;
    final gainers = (movers['gainers'] as List<dynamic>).take(topN).toList();
    final losers = (movers['losers'] as List<dynamic>).take(topN).toList();
    return TopMoversResponse.fromJson({
      'gainers': gainers,
      'losers': losers,
      'period_days': movers['period_days'],
      'generated_at': movers['generated_at'],
    });
  }

  Future<Map<String, dynamic>> nationalSummary({LiveFlag? source}) async {
    final live = await _get('/markets/national-summary', null, source);
    if (live != null) {
      return {'data_as_of': live['data_as_of'], 'generated_at': live['generated_at']};
    }
    source?.value = false;
    final d = await _offlineData();
    return {'data_as_of': d['data_as_of'], 'generated_at': d['generated_at']};
  }

  Future<List<ArbitrageOpportunity>> arbitrageOpportunities({
    required String commodity,
    String markets = 'Kampala,Mbarara,Gulu,Kabale,Jinja,Mbale',
    double minMarginPct = 10.0,
    LiveFlag? source,
  }) async {
    final live = await _get(
      '/markets/arbitrage/${Uri.encodeComponent(commodity.trim())}',
      {'markets': markets, 'min_margin_pct': '$minMarginPct'},
      source,
    );
    if (live != null) {
      return (live as List<dynamic>)
          .map((e) => ArbitrageOpportunity.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    source?.value = false;

    final d = await _offlineData();
    final arbitrage = d['arbitrage'] as Map<String, dynamic>;
    final commodities = (d['commodities'] as List<dynamic>).cast<String>();
    final resolved = _resolve(commodity, commodities) ?? commodity.trim();

    if (!arbitrage.containsKey(resolved)) {
      throw ApiException(
        404,
        'No arbitrage opportunities found for "$commodity" above ${minMarginPct.toStringAsFixed(0)}%.',
      );
    }
    final list = (arbitrage[resolved] as List<dynamic>)
        .map((e) => ArbitrageOpportunity.fromJson(e as Map<String, dynamic>))
        .where((o) => o.grossMarginPct >= minMarginPct)
        .toList();
    if (list.isEmpty) {
      throw ApiException(
        404,
        'No arbitrage opportunities found for "$commodity" above ${minMarginPct.toStringAsFixed(0)}%.',
      );
    }
    return list;
  }

  /// Drought-stress signal per market (backend/app/routers/weather.py ::
  /// GET /weather/analytics/drought-risk). No offline fallback exists for
  /// this — the bundled snapshot (assets/data/agriguard_offline_data.json)
  /// predates the weather feature and carries no weather data at all — so
  /// unlike every other ApiService method above, a null/failed live call
  /// here means "genuinely unavailable right now", not "serve the stale
  /// snapshot". Callers should show that honestly rather than inventing
  /// numbers, same spirit as the rest of this file's live-vs-offline
  /// discipline.
  Future<DroughtRiskResponse?> droughtRisk({
    int lookbackDays = 30,
    double deficitThresholdMm = -3.0,
    LiveFlag? source,
  }) async {
    final live = await _get(
      '/weather/analytics/drought-risk',
      {
        'lookback_days': '$lookbackDays',
        'deficit_threshold_mm': '$deficitThresholdMm',
      },
      source,
    );
    if (live == null) return null;
    return DroughtRiskResponse.fromJson(live as Map<String, dynamic>);
  }

  /// Heavy-rain / flood early warning (backend/app/routers/weather.py ::
  /// GET /weather/alerts/heavy-rain). Same no-offline-fallback caveat as
  /// [droughtRisk] above.
  Future<HeavyRainAlertResponse?> heavyRainAlerts({
    double thresholdMm = 50.0,
    int lookbackDays = 7,
    bool includeForecast = true,
    LiveFlag? source,
  }) async {
    final live = await _get(
      '/weather/alerts/heavy-rain',
      {
        'threshold_mm': '$thresholdMm',
        'lookback_days': '$lookbackDays',
        'include_forecast': '$includeForecast',
      },
      source,
    );
    if (live == null) return null;
    return HeavyRainAlertResponse.fromJson(live as Map<String, dynamic>);
  }
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);

  bool get isOfflineMarketSubstitution => statusCode == ApiService.offlineMarketSubstituted;

  @override
  String toString() => isOfflineMarketSubstitution ? body : 'ApiException($statusCode): $body';
}
