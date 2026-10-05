import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/connectivity_service.dart';
import '../services/preferences_service.dart';
import '../models/forecast_model.dart';
import '../theme/app_theme.dart';
import '../widgets/app_scaffold.dart';
import '../widgets/forecast_card.dart';
import '../widgets/price_history_chart.dart';

class ForecastScreen extends StatefulWidget {
  const ForecastScreen({super.key});

  @override
  State<ForecastScreen> createState() => _ForecastScreenState();
}

class _ForecastScreenState extends State<ForecastScreen> {
  String _commodity = 'Maize';
  String? _market; // null = use preferred/default market
  List<String> _commodities = [];
  List<String> _markets = [];
  double _horizon = 14;
  ForecastResponse? _forecast;
  List<HistoryPoint> _history = [];
  bool _loading = false;
  bool _catalogLoading = true;
  String? _error;
  bool _isLive = false;
  bool? _wasOnline;

  double? get _currentPrice => _history.isNotEmpty ? _history.last.price : null;

  Future<void> _loadCatalog() async {
    try {
      final api = context.read<ApiService>();
      final catalog = await api.listCommodities();
      if (!mounted) return;
      setState(() {
        _commodities = catalog.commodities;
        _markets = catalog.markets;
        if (_commodities.isNotEmpty && !_commodities.contains(_commodity)) {
          _commodity = _commodities.first;
        }
        _catalogLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _catalogLoading = false);
    }
  }

  Future<void> _load() async {
    final commodity = _commodity;
    final preferredMarket = await PreferencesService.getPreferredMarket();
    final market = _market ?? preferredMarket ?? 'Kampala';

    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      final source = LiveFlag();
      final fc = await api.getForecast(
        commodity: commodity,
        market: market,
        horizon: _horizon.round(),
        source: source,
      );
      List<HistoryPoint> hist = [];
      try {
        hist = await api.getHistory(
          commodity: commodity,
          market: fc.market, // use the resolved market so history lines up
          days: 180,
        );
      } catch (_) {
        // history is optional
      }
      if (!mounted) return;
      setState(() {
        _forecast = fc;
        _history = hist;
        _isLive = source.value;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = 'Could not load a forecast right now.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _loadCatalog();
    _load();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // When connectivity flips from offline -> online, silently refresh so
    // the screen picks up live data without the person having to tap
    // refresh themselves.
    final online = context.watch<ConnectivityService>().isOnline;
    if (_wasOnline == false && online == true) _load();
    _wasOnline = online;
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: 'Price Forecast',
      isLive: (_forecast != null || _error != null) ? _isLive : null,
      onRefresh: _loading ? null : _load,
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            _buildQueryCard(context),
            const SizedBox(height: 16),
            if (_error != null)
              _ErrorBanner(message: _error!),
            if (_loading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 40),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (_forecast != null && !_loading) ...[
              ForecastCard(forecast: _forecast!, currentPrice: _currentPrice),
              const SizedBox(height: 16),
              if (_history.isNotEmpty || _forecast!.forecast.isNotEmpty)
                PriceHistoryChart(
                  history: _history,
                  forecast: _forecast!.forecast,
                  currency: _forecast!.currency,
                ),
              const SizedBox(height: 16),
              const SectionHeading(title: 'Forecast points'),
              const SizedBox(height: 10),
              Card(
                clipBehavior: Clip.antiAlias,
                child: Column(
                  children: _forecast!.forecast
                      .map(
                        (p) => ForecastPointTile(
                          point: p,
                          currency: _forecast!.currency,
                          unit: _forecast!.unit,
                        ),
                      )
                      .toList(),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildQueryCard(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: _catalogLoading
                      ? const SizedBox(
                          height: 56,
                          child: Center(
                            child: SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            ),
                          ),
                        )
                      : DropdownButtonFormField<String>(
                          value: _commodities.contains(_commodity) ? _commodity : null,
                          decoration: const InputDecoration(labelText: 'Crop'),
                          isExpanded: true,
                          items: _commodities
                              .map((c) => DropdownMenuItem(value: c, child: Text(c, overflow: TextOverflow.ellipsis)))
                              .toList(),
                          onChanged: (v) {
                            if (v == null) return;
                            setState(() => _commodity = v);
                            _load();
                          },
                        ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _catalogLoading
                      ? const SizedBox(height: 56)
                      : DropdownButtonFormField<String?>(
                          value: _markets.contains(_market) ? _market : null,
                          decoration: const InputDecoration(labelText: 'Market', hintText: 'Default'),
                          isExpanded: true,
                          items: [
                            const DropdownMenuItem<String?>(value: null, child: Text('Default')),
                            ..._markets.map((m) => DropdownMenuItem<String?>(value: m, child: Text(m, overflow: TextOverflow.ellipsis))),
                          ],
                          onChanged: (v) {
                            setState(() => _market = v);
                            _load();
                          },
                        ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Forecast horizon', style: Theme.of(context).textTheme.titleSmall),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                  decoration: BoxDecoration(
                    color: AppColors.primary.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '${_horizon.round()} day${_horizon.round() == 1 ? '' : 's'}',
                    style: const TextStyle(
                      color: AppColors.primary,
                      fontWeight: FontWeight.w700,
                      fontSize: 12,
                    ),
                  ),
                ),
              ],
            ),
            SliderTheme(
              data: SliderTheme.of(context).copyWith(
                trackHeight: 3,
                thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8),
              ),
              child: Slider(
                value: _horizon,
                min: 1,
                max: 90,
                divisions: 89,
                label: '${_horizon.round()}d',
                onChanged: (v) => setState(() => _horizon = v),
                onChangeEnd: (_) => _load(),
              ),
            ),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('1 day', style: Theme.of(context).textTheme.labelSmall),
                Text(
                  'Confidence narrows past 30 days',
                  style: Theme.of(context).textTheme.labelSmall,
                ),
                Text('90 days', style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _loading ? null : _load,
                icon: const Icon(Icons.trending_up_rounded, size: 18),
                label: Text(_loading ? 'Forecasting…' : 'Update forecast'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  final String message;
  const _ErrorBanner({required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.warning.withOpacity(0.1),
          borderRadius: BorderRadius.circular(AppRadius.sm),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.info_outline, color: AppColors.warning, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(message, style: const TextStyle(color: AppColors.warning)),
            ),
          ],
        ),
      ),
    );
  }
}
