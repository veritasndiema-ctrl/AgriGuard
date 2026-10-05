import 'package:connectivity_plus/connectivity_plus.dart';
import '../services/api_service.dart';
import 'local_cache.dart';

/// Background-friendly sync that refreshes the most-used commodities
/// when connectivity is available and stores them offline.
class SyncService {
  final ApiService api;
  final LocalCache cache;

  SyncService({required this.api, required this.cache});

  Future<bool> get isOnline async {
    final result = await Connectivity().checkConnectivity();
    return !result.contains(ConnectivityResult.none);
  }

  /// Prefetch forecasts for a list of (commodity, market) pairs and store
  /// the *full* forecast (via [ForecastResponse.toJson]) — not just a
  /// summary — so [ApiService.getForecast] can later feed a cache hit
  /// straight back through [ForecastResponse.fromJson] and render it
  /// exactly like a live response (chart, forecast-point list, alert
  /// banner, all of it), rather than a stub with only a few fields.
  Future<int> prefetch(List<(String, String)> pairs, {int horizon = 14}) async {
    if (!await isOnline) return 0;
    var ok = 0;
    for (final (commodity, market) in pairs) {
      try {
        final source = LiveFlag();
        final forecast = await api.getForecast(
          commodity: commodity,
          market: market,
          horizon: horizon,
          source: source,
        );
        // Only cache genuinely live data — caching an offline-snapshot
        // result back into LocalCache would just be copying the bundled
        // asset into another format for no benefit, and would make a
        // future cache hit look fresher than it actually is.
        if (!source.value) continue;

        await cache.put(LocalCache.forecastKey(commodity, market), forecast.toJson());
        ok++;
      } catch (_) {
        // keep going
      }
    }
    return ok;
  }
}
