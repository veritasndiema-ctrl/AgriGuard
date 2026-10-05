import 'package:shared_preferences/shared_preferences.dart';

/// Where the app looks for the live AgriGuard backend.
///
/// IMPORTANT — intellectual-property note:
/// Earlier builds exposed a raw "Backend URL" text field (with emulator
/// aliases, LAN-IP instructions, etc.) directly in Settings. That leaks
/// infrastructure details — hostnames, ports, hosting provider — to anyone
/// who opens the app, which is unnecessary and gives away information about
/// how AgriGuard is deployed for free. None of that belongs in a
/// production build, so it has been removed from the UI entirely.
///
/// What ships now:
///   1. [productionBaseUrl] — baked in at BUILD time via
///      `--dart-define=AGRIGUARD_BACKEND_URL=...`, not hand-edited source.
///      This is what makes every APK built by .github/workflows/build-apk.yml
///      — whether triggered by a push or run manually via "Run workflow" —
///      wire up to the live backend automatically: the workflow passes the
///      AGRIGUARD_BACKEND_URL repository variable into the build step, so
///      there is nothing to edit or commit here when the backend's URL
///      changes (redeploy, new host, etc.) — just update that one repo
///      variable and the next build picks it up. A local
///      `flutter build apk --dart-define=AGRIGUARD_BACKEND_URL=...` works
///      the same way outside CI. Omit the define entirely (e.g. a plain
///      `flutter run` during development) and this falls back to ''.
///   2. A hidden developer override, reachable only by tapping the version
///      number in Settings → About seven times (the same pattern Android
///      itself uses for "Developer options"). This is for your own
///      on-device testing against a local server and is not documented or
///      discoverable anywhere in the normal UI, and always wins over #1
///      when set (see [getBaseUrl]).
///
/// If neither is set/reachable, [ApiService] silently serves the bundled
/// offline snapshot — the app always works, it just isn't live.
class BackendConfig {
  BackendConfig._();

  /// Baked in at build time — see class doc. Empty string means "no
  /// AGRIGUARD_BACKEND_URL was passed to this build".
  static const String productionBaseUrl =
      String.fromEnvironment('AGRIGUARD_BACKEND_URL', defaultValue: '');

  static const _devOverrideKey = 'agriguard_dev_backend_override';
  static String? _cachedOverride;

  static Future<String> getBaseUrl() async {
    final override = await _getDevOverride();
    if (override != null && override.isNotEmpty) return override;
    return productionBaseUrl;
  }

  static Future<String?> _getDevOverride() async {
    if (_cachedOverride != null) return _cachedOverride;
    final prefs = await SharedPreferences.getInstance();
    _cachedOverride = prefs.getString(_devOverrideKey) ?? '';
    return _cachedOverride;
  }

  /// Developer-only: set a local override (e.g. `http://10.0.2.2:8000` for
  /// the Android emulator, or your dev machine's LAN IP). Reached only via
  /// the hidden long-tap gesture in Settings — never surfaced as a normal
  /// setting.
  static Future<void> setDevOverride(String url) async {
    final trimmed = url.trim().replaceAll(RegExp(r'/+$'), '');
    final prefs = await SharedPreferences.getInstance();
    if (trimmed.isEmpty) {
      await prefs.remove(_devOverrideKey);
    } else {
      await prefs.setString(_devOverrideKey, trimmed);
    }
    _cachedOverride = trimmed;
  }

  static Future<bool> hasDevOverride() async {
    final v = await _getDevOverride();
    return v != null && v.isNotEmpty;
  }
}
