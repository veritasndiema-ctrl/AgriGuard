import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/weather_model.dart';
import '../services/api_service.dart';
import '../services/connectivity_service.dart';
import '../services/preferences_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_scaffold.dart';

/// Weather tab: current conditions + a short forecast + a plain-language
/// farmer advisory for one market (always available, from the bundled
/// snapshot — see scripts/gen_offline_data.py), followed by the live
/// drought-risk / heavy-rain analytics from backend/app/routers/weather.py
/// whenever a live connection is available.
class WeatherScreen extends StatefulWidget {
  const WeatherScreen({super.key});

  @override
  State<WeatherScreen> createState() => _WeatherScreenState();
}

class _WeatherScreenState extends State<WeatherScreen> {
  String? _market;
  List<String> _markets = [];
  WeatherSnapshot? _snapshot;
  bool _snapshotLoading = true;

  bool _liveLoading = false;
  bool _everLoadedLive = false;
  DroughtRiskResponse? _drought;
  HeavyRainAlertResponse? _rain;
  String? _liveError;
  bool? _wasOnline;

  Future<void> _loadSnapshot() async {
    setState(() => _snapshotLoading = true);
    final api = context.read<ApiService>();
    try {
      final markets = await api.weatherMarkets();
      final preferred = await PreferencesService.getPreferredMarket();
      final chosen = _market ??
          (preferred != null && markets.contains(preferred) ? preferred : null) ??
          (markets.isNotEmpty ? markets.first : null);
      final snap = chosen != null ? await api.weatherSnapshot(chosen) : null;
      if (!mounted) return;
      setState(() {
        _markets = markets;
        _market = chosen;
        _snapshot = snap;
        _snapshotLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _snapshotLoading = false);
    }
  }

  Future<void> _refreshLive() async {
    setState(() {
      _liveLoading = true;
      _liveError = null;
    });
    final api = context.read<ApiService>();
    try {
      final results = await Future.wait([
        api.droughtRisk(),
        api.heavyRainAlerts(),
      ]);
      if (!mounted) return;
      final drought = results[0] as DroughtRiskResponse?;
      final rain = results[1] as HeavyRainAlertResponse?;
      setState(() {
        _drought = drought;
        _rain = rain;
        _liveError = (drought == null && rain == null)
            ? 'Drought and flood-risk analytics need a live connection — '
                'showing your bundled current conditions and forecast above '
                'in the meantime.'
            : null;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _liveError = 'Could not load live weather analytics right now.');
    } finally {
      if (mounted) {
        setState(() {
          _liveLoading = false;
          _everLoadedLive = true;
        });
      }
    }
  }

  Future<void> _refreshAll() async {
    await Future.wait([_loadSnapshot(), _refreshLive()]);
  }

  Color _riskColor(String level) {
    switch (level.toUpperCase()) {
      case 'SEVERE':
      case 'DRY SPELL':
      case 'HEAVY RAIN':
        return AppColors.falling;
      case 'HIGH':
      case 'HOT CONDITIONS':
        return AppColors.warning;
      case 'MODERATE':
        return AppColors.accent;
      default:
        return AppColors.rising;
    }
  }

  IconData _riskIcon(String level) {
    switch (level.toUpperCase()) {
      case 'DRY SPELL':
      case 'SEVERE':
      case 'HIGH':
        return Icons.water_drop_outlined;
      case 'HEAVY RAIN':
        return Icons.thunderstorm_outlined;
      case 'HOT CONDITIONS':
        return Icons.wb_sunny_outlined;
      default:
        return Icons.check_circle_outline;
    }
  }

  @override
  void initState() {
    super.initState();
    _loadSnapshot();
    _refreshLive();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final online = context.watch<ConnectivityService>().isOnline;
    if (_wasOnline == false && online == true) _refreshLive();
    _wasOnline = online;
  }

  @override
  Widget build(BuildContext context) {
    final hasLiveData = (_drought?.markets.isNotEmpty ?? false) ||
        (_rain?.alerts.isNotEmpty ?? false);

    return AppScaffold(
      title: 'Weather',
      isLive: _everLoadedLive ? (_drought != null || _rain != null) : null,
      onRefresh: (_snapshotLoading || _liveLoading) ? null : _refreshAll,
      body: RefreshIndicator(
        onRefresh: _refreshAll,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _buildMarketPicker(context),
            const SizedBox(height: 16),
            if (_snapshotLoading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 30),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_snapshot != null) ...[
              _buildCurrentConditionsCard(context, _snapshot!),
              const SizedBox(height: 16),
              _buildAdviceCard(context, _snapshot!),
              const SizedBox(height: 16),
              if (_snapshot!.forecast.isNotEmpty) ...[
                const SectionHeading(title: 'Next few days'),
                const SizedBox(height: 10),
                _buildForecastStrip(context, _snapshot!),
                const SizedBox(height: 24),
              ],
            ] else
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 30),
                child: Center(child: Text('Weather data isn\'t available for this market yet.')),
              ),
            const Divider(height: 32),
            const SectionHeading(
              title: 'Regional risk analytics',
              subtitle: 'Live drought and flood-risk warnings across all markets.',
            ),
            const SizedBox(height: 10),
            if (_liveLoading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 20),
                child: Center(child: CircularProgressIndicator()),
              )
            else ...[
              if (_liveError != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(_liveError!, style: Theme.of(context).textTheme.bodySmall),
                ),
              if (_drought != null && _drought!.markets.isNotEmpty) ...[
                Text('Drought risk', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                Text(
                  'Last ${_drought!.lookbackDays} days, as of ${_drought!.generatedForDate}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 10),
                ..._drought!.markets.map((m) => Card(
                      margin: const EdgeInsets.only(bottom: 10),
                      child: ListTile(
                        leading: CircleAvatar(
                          backgroundColor: _riskColor(m.riskLevel).withOpacity(0.15),
                          child: Icon(Icons.water_drop_outlined, color: _riskColor(m.riskLevel)),
                        ),
                        title: Text('${m.marketName} · ${m.region}'),
                        subtitle: Text(
                          '${m.deficitDays}/${m.lookbackDays} days below deficit threshold'
                          '${m.avgWaterBalanceMm != null ? ' · avg water balance ${m.avgWaterBalanceMm!.toStringAsFixed(1)} mm' : ''}',
                        ),
                        trailing: Text(
                          m.riskLevel,
                          style: TextStyle(color: _riskColor(m.riskLevel), fontWeight: FontWeight.w700),
                        ),
                      ),
                    )),
                const SizedBox(height: 10),
              ],
              if (_rain != null && _rain!.alerts.isNotEmpty) ...[
                Text('Heavy rain / flood warnings', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                Text(
                  'Above ${_rain!.thresholdMm.toStringAsFixed(0)} mm, last ${_rain!.lookbackDays} days',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 10),
                ..._rain!.alerts.map((a) => Card(
                      margin: const EdgeInsets.only(bottom: 10),
                      child: ListTile(
                        leading: const CircleAvatar(
                          backgroundColor: Color(0x1F1976D2),
                          child: Icon(Icons.thunderstorm_outlined, color: Color(0xFF1976D2)),
                        ),
                        title: Text('${a.marketName} · ${a.region}'),
                        subtitle: Text(
                          '${a.rainfallMm.toStringAsFixed(0)} mm on ${a.readingDate}'
                          '${a.isForecast ? ' (forecast)' : ''}',
                        ),
                      ),
                    )),
              ],
              if (!hasLiveData && _liveError == null)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 12),
                  child: Text('No drought or heavy-rain risk right now.'),
                ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildMarketPicker(BuildContext context) {
    if (_markets.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        child: DropdownButtonFormField<String>(
          value: _markets.contains(_market) ? _market : null,
          decoration: const InputDecoration(labelText: 'Market', border: InputBorder.none),
          isExpanded: true,
          items: _markets
              .map((m) => DropdownMenuItem(value: m, child: Text(m)))
              .toList(),
          onChanged: (v) async {
            if (v == null) return;
            setState(() => _market = v);
            final api = context.read<ApiService>();
            final snap = await api.weatherSnapshot(v);
            if (mounted) setState(() => _snapshot = snap);
          },
        ),
      ),
    );
  }

  Widget _buildCurrentConditionsCard(BuildContext context, WeatherSnapshot s) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.wb_cloudy_outlined, size: 28, color: AppColors.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${s.market} · ${s.region}', style: Theme.of(context).textTheme.titleMedium),
                      Text('As of ${s.asOf}', style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                ),
                Text(
                  '${s.currentTempMaxC.toStringAsFixed(0)}°',
                  style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const Divider(height: 24),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                _stat(context, Icons.thermostat_outlined, 'Low / High',
                    '${s.currentTempMinC.toStringAsFixed(0)}° / ${s.currentTempMaxC.toStringAsFixed(0)}°'),
                _stat(context, Icons.water_drop_outlined, 'Humidity', '${s.currentHumidityMaxPct.toStringAsFixed(0)}%'),
                _stat(context, Icons.grain_outlined, 'Rainfall', '${s.currentRainfallMm.toStringAsFixed(1)} mm'),
                _stat(context, Icons.air_outlined, 'Wind', '${s.currentWindSpeedMaxKmh.toStringAsFixed(0)} km/h'),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _stat(BuildContext context, IconData icon, String label, String value) {
    return Column(
      children: [
        Icon(icon, size: 18, color: AppColors.textSecondary),
        const SizedBox(height: 4),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w700)),
        Text(label, style: Theme.of(context).textTheme.labelSmall),
      ],
    );
  }

  Widget _buildAdviceCard(BuildContext context, WeatherSnapshot s) {
    final color = _riskColor(s.riskLevel);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: color.withOpacity(0.08),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withOpacity(0.25)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(_riskIcon(s.riskLevel), color: color, size: 22),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${s.riskLevel} · advice for farmers',
                  style: TextStyle(color: color, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                Text(s.advice, style: Theme.of(context).textTheme.bodyMedium),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildForecastStrip(BuildContext context, WeatherSnapshot s) {
    return SizedBox(
      height: 108,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: s.forecast.length,
        separatorBuilder: (_, __) => const SizedBox(width: 10),
        itemBuilder: (context, i) {
          final d = s.forecast[i];
          return Container(
            width: 84,
            padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
            decoration: BoxDecoration(
              color: AppColors.surfaceMuted,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(_shortDate(d.date), style: Theme.of(context).textTheme.labelSmall),
                Icon(
                  d.rainfallMm >= 5 ? Icons.umbrella_outlined : Icons.wb_sunny_outlined,
                  color: d.rainfallMm >= 5 ? AppColors.primary : AppColors.warning,
                  size: 22,
                ),
                Text(
                  '${d.tempMinC.toStringAsFixed(0)}° / ${d.tempMaxC.toStringAsFixed(0)}°',
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
                ),
                if (d.rainfallMm > 0)
                  Text('${d.rainfallMm.toStringAsFixed(0)}mm', style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
          );
        },
      ),
    );
  }

  String _shortDate(String isoDate) {
    try {
      final d = DateTime.parse(isoDate);
      const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      return days[d.weekday - 1];
    } catch (_) {
      return isoDate;
    }
  }
}
