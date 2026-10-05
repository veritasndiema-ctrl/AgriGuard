import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

/// Simple key-value offline cache for forecast / market payloads.
class LocalCache {
  static const _prefix = 'agriguard_';

  /// Shared between [SyncService.prefetch] (writer) and
  /// [ApiService.getForecast] (reader) — both sides must agree on this key
  /// shape or a prefetch is invisible to the app. Lives here rather than on
  /// either service so neither has to import the other.
  static String forecastKey(String commodity, String market) =>
      'forecast_${commodity.trim()}_${market.trim()}';

  Future<void> put(String key, Map<String, dynamic> value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('$_prefix$key', jsonEncode(value));
    await prefs.setInt('$_prefix${key}_ts', DateTime.now().millisecondsSinceEpoch);
  }

  Future<Map<String, dynamic>?> get(String key, {Duration maxAge = const Duration(hours: 6)}) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('$_prefix$key');
    if (raw == null) return null;

    final ts = prefs.getInt('$_prefix${key}_ts') ?? 0;
    final age = DateTime.now().millisecondsSinceEpoch - ts;
    if (age > maxAge.inMilliseconds) {
      await prefs.remove('$_prefix$key');
      await prefs.remove('$_prefix${key}_ts');
      return null;
    }
    return jsonDecode(raw) as Map<String, dynamic>;
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    final keys = prefs.getKeys().where((k) => k.startsWith(_prefix));
    for (final k in keys) {
      await prefs.remove(k);
    }
  }
}
