import 'package:shared_preferences/shared_preferences.dart';

/// User-facing app preferences — everything the Settings screen is allowed
/// to show. Deliberately does NOT include anything about the backend
/// deployment (URLs, hosts, IPs, model names) — see backend_config.dart for
/// why that is kept out of the normal UI entirely.
class PreferencesService {
  static const _kMarket = 'agriguard_pref_market';
  static const _kNotifications = 'agriguard_pref_notifications';
  static const _kWatchlist = 'agriguard_pref_watchlist';
  static const _kName = 'agriguard_profile_name';
  static const _kEmail = 'agriguard_profile_email';
  static const _kSignedIn = 'agriguard_signed_in';

  static const defaultWatchlist = <String>[
    'Maize',
    'Beans',
    'Cassava',
    'Sorghum',
    'Millet',
  ];

  /// The market used as a default across Forecast / Alerts / Markets when
  /// the person hasn't typed a specific one. Null means "let the data
  /// decide" — each screen/service picks the best available match rather
  /// than assuming a market (e.g. "Kampala") that may not exist in the
  /// loaded dataset.
  static Future<String?> getPreferredMarket() async {
    final prefs = await SharedPreferences.getInstance();
    final v = prefs.getString(_kMarket);
    return (v == null || v.isEmpty) ? null : v;
  }

  static Future<void> setPreferredMarket(String? market) async {
    final prefs = await SharedPreferences.getInstance();
    if (market == null || market.trim().isEmpty) {
      await prefs.remove(_kMarket);
    } else {
      await prefs.setString(_kMarket, market.trim());
    }
  }

  static Future<bool> getNotificationsEnabled() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kNotifications) ?? true;
  }

  static Future<void> setNotificationsEnabled(bool v) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kNotifications, v);
  }

  static Future<List<String>> getWatchlist() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getStringList(_kWatchlist) ?? defaultWatchlist;
  }

  static Future<void> setWatchlist(List<String> crops) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(_kWatchlist, crops);
  }

  // --- Local profile / "sign in" stub -------------------------------------
  // There is no auth backend in this codebase yet, so this intentionally
  // just persists a display name + email on-device so the drawer has
  // something real to show. Swap this out for real auth (Firebase, a
  // /auth endpoint, etc.) without touching any UI beyond this file.

  static Future<bool> isSignedIn() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kSignedIn) ?? false;
  }

  static Future<String?> getName() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kName);
  }

  static Future<String?> getEmail() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kEmail);
  }

  static Future<void> signIn({required String name, required String email}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kName, name.trim());
    await prefs.setString(_kEmail, email.trim());
    await prefs.setBool(_kSignedIn, true);
  }

  static Future<void> signOut() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kSignedIn, false);
  }
}
