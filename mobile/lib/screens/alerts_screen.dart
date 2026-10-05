import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/forecast_model.dart';
import '../models/weather_model.dart';
import '../services/api_service.dart';
import '../services/connectivity_service.dart';
import '../services/preferences_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_scaffold.dart';

/// Why alerts used to show nothing (both online and offline):
///
/// 1. The watchlist used a hardcoded default market ("Kampala") that does
///    not exist anywhere in the WFP Uganda dataset — every request for it
///    either 404'd or (offline) triggered a market-substitution exception
///    that the old code swallowed with a bare `catch (_) { }`.
/// 2. "Cassava" isn't a commodity name in the dataset either — it's listed
///    as "Cassava (Fresh)" / "Cassava Flour" — so that lookup 404'd too,
///    every single time, and was also silently dropped.
///
/// The fix here is two-fold: resolve watchlist entries against the real,
/// live list of commodities/markets instead of assuming names, and stop
/// discarding informative failures — a substituted-market result is still
/// good data (see [ForecastResponse.substitutedFromMarket]), so it's no
/// longer thrown away upstream in ApiService either.
class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key});

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertItem {
  final String commodity;
  final String market;
  final String message;
  final bool rising;
  _AlertItem({
    required this.commodity,
    required this.market,
    required this.message,
    required this.rising,
  });
}

class _WeatherAlertItem {
  final String market;
  final String riskLevel;
  final String advice;
  _WeatherAlertItem({
    required this.market,
    required this.riskLevel,
    required this.advice,
  });
}

class _AlertsScreenState extends State<AlertsScreen> {
  final _alerts = <_AlertItem>[];
  final _weatherAlerts = <_WeatherAlertItem>[];
  bool _loading = false;
  bool _isLive = false;
  bool _everLoaded = false;
  String? _notice;
  bool? _wasOnline;

  Future<void> _loadWeatherAlerts() async {
    _weatherAlerts.clear();
    final api = context.read<ApiService>();
    try {
      final markets = await api.weatherMarkets();
      final preferred = await PreferencesService.getPreferredMarket();
      // Only check the person's own market plus any others if none is set —
      // this is meant as a quick heads-up, not a full regional scan (that's
      // what the Weather tab's live drought/rain section is for).
      final toCheck = (preferred != null && markets.contains(preferred))
          ? [preferred]
          : markets;
      for (final m in toCheck) {
        final snap = await api.weatherSnapshot(m);
        if (snap != null && snap.riskLevel.toLowerCase() != 'normal') {
          _weatherAlerts.add(_WeatherAlertItem(
            market: snap.market,
            riskLevel: snap.riskLevel,
            advice: snap.advice,
          ));
        }
      }
    } catch (_) {
      // Weather alerts are a bonus on this screen — a failure here
      // shouldn't block the price alerts below.
    }
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _alerts.clear();
      _notice = null;
    });
    final api = context.read<ApiService>();
    var sawLive = false;

    await _loadWeatherAlerts();

    try {
      final watchlist = await PreferencesService.getWatchlist();
      final catalogSource = LiveFlag();
      final catalog = await api.listCommodities(source: catalogSource);
      if (catalogSource.value) sawLive = true;

      final preferredMarket = await PreferencesService.getPreferredMarket();

      final resolvedCrops = <String>{};
      for (final wanted in watchlist) {
        final match = _resolveCommodity(wanted, catalog.commodities);
        if (match != null) resolvedCrops.add(match);
      }

      if (resolvedCrops.isEmpty) {
        setState(() {
          _notice = 'None of your watchlist crops were found in the current dataset.';
          _loading = false;
          _everLoaded = true;
        });
        return;
      }

      for (final crop in resolvedCrops) {
        try {
          final source = LiveFlag();
          final fc = await api.getForecast(
            commodity: crop,
            market: preferredMarket ?? 'Kampala',
            horizon: 14,
            source: source,
          );
          if (source.value) sawLive = true;

          if (fc.alert != null && fc.alert!.isNotEmpty) {
            _alerts.add(_AlertItem(
              commodity: fc.commodity,
              market: fc.market,
              message: fc.alert!,
              rising: fc.trend == 'rising',
            ));
          } else if (fc.pctChange.abs() >= 5) {
            final dir = fc.pctChange > 0 ? 'rise' : 'fall';
            _alerts.add(_AlertItem(
              commodity: fc.commodity,
              market: fc.market,
              message: 'Expected to $dir ${fc.pctChange.abs().toStringAsFixed(1)}% '
                  'over the next ${fc.horizonDays} days.',
              rising: fc.pctChange > 0,
            ));
          }
        } on ApiException {
          // A genuine miss for this specific crop (e.g. too little price
          // history) — skip just that one, don't blank the whole screen.
          continue;
        } catch (_) {
          continue;
        }
      }
    } catch (_) {
      _notice = 'Could not check for alerts right now.';
    }

    if (!mounted) return;
    setState(() {
      _loading = false;
      _isLive = sawLive;
      _everLoaded = true;
    });
  }

  /// Matches a friendly watchlist name (e.g. "Cassava") against the actual
  /// dataset commodity names (e.g. "Cassava (Fresh)", "Cassava Flour"),
  /// which often carry qualifiers the short name doesn't. Prefers an exact
  /// match, then falls back to "starts with" / "contains".
  String? _resolveCommodity(String wanted, List<String> available) {
    final w = wanted.trim().toLowerCase();
    for (final a in available) {
      if (a.toLowerCase() == w) return a;
    }
    for (final a in available) {
      if (a.toLowerCase().startsWith(w)) return a;
    }
    for (final a in available) {
      if (a.toLowerCase().contains(w)) return a;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final online = context.watch<ConnectivityService>().isOnline;
    if (_wasOnline == false && online == true) _refresh();
    _wasOnline = online;
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: 'Alerts',
      isLive: _everLoaded ? _isLive : null,
      onRefresh: _loading ? null : _refresh,
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _refresh,
              child: (_alerts.isEmpty && _weatherAlerts.isEmpty)
                  ? ListView(
                      padding: const EdgeInsets.all(24),
                      children: [
                        const SizedBox(height: 80),
                        Icon(Icons.notifications_off_outlined,
                            size: 40, color: AppColors.textSecondary),
                        const SizedBox(height: 12),
                        Center(
                          child: Text(
                            _notice ??
                                'No significant price or weather alerts right now.\nPull down to refresh.',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                      ],
                    )
                  : ListView(
                      padding: const EdgeInsets.all(16),
                      children: [
                        if (_weatherAlerts.isNotEmpty) ...[
                          Text('Weather', style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: 10),
                          ..._weatherAlerts.map((w) => Card(
                                margin: const EdgeInsets.only(bottom: 10),
                                child: ListTile(
                                  leading: Container(
                                    padding: const EdgeInsets.all(8),
                                    decoration: BoxDecoration(
                                      color: AppColors.warning.withOpacity(0.12),
                                      borderRadius: BorderRadius.circular(10),
                                    ),
                                    child: const Icon(Icons.cloud_outlined, color: AppColors.warning, size: 20),
                                  ),
                                  title: Text('${w.riskLevel} · ${w.market}'),
                                  subtitle: Text(w.advice),
                                  isThreeLine: true,
                                ),
                              )),
                          const SizedBox(height: 16),
                        ],
                        if (_alerts.isNotEmpty) ...[
                          Text('Prices', style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: 10),
                          ..._alerts.map((a) {
                            final color = a.rising ? AppColors.rising : AppColors.falling;
                            return Card(
                              margin: const EdgeInsets.only(bottom: 10),
                              child: Padding(
                                padding: const EdgeInsets.all(14),
                                child: Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Container(
                                      padding: const EdgeInsets.all(8),
                                      decoration: BoxDecoration(
                                        color: color.withOpacity(0.12),
                                        borderRadius: BorderRadius.circular(10),
                                      ),
                                      child: Icon(
                                        a.rising ? Icons.trending_up_rounded : Icons.trending_down_rounded,
                                        color: color,
                                        size: 20,
                                      ),
                                    ),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            '${a.commodity} · ${a.market}',
                                            style: Theme.of(context).textTheme.titleMedium,
                                          ),
                                          const SizedBox(height: 4),
                                          Text(a.message, style: Theme.of(context).textTheme.bodyMedium),
                                        ],
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            );
                          }),
                        ],
                      ],
                    ),
            ),
    );
  }
}
