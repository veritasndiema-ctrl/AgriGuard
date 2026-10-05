import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/market_model.dart';
import '../services/api_service.dart';
import '../services/connectivity_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_scaffold.dart';

class MarketScreen extends StatefulWidget {
  const MarketScreen({super.key});

  @override
  State<MarketScreen> createState() => _MarketScreenState();
}

class _MarketScreenState extends State<MarketScreen> {
  String _commodity = 'Maize';
  List<String> _commodities = [];
  bool _catalogLoading = true;
  CommodityMarketSummary? _summary;
  TopMoversResponse? _movers;
  List<ArbitrageOpportunity>? _arbitrage;
  String? _arbitrageNotice;
  bool _loading = false;
  String? _error;
  bool _isLive = false;
  bool? _wasOnline;

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      final summarySource = LiveFlag();
      final summaryFuture = api.marketSummary(_commodity, source: summarySource);
      final moversFuture = api.topMovers(periodDays: 30, topN: 5);
      final arbitrageFuture = api.arbitrageOpportunities(commodity: _commodity);

      CommodityMarketSummary? summary;
      TopMoversResponse? movers;
      List<ArbitrageOpportunity>? arbitrage;
      String? error;
      String? arbitrageNotice;

      try {
        summary = await summaryFuture;
      } catch (e) {
        error = e.toString();
      }
      try {
        movers = await moversFuture;
      } catch (_) {}
      try {
        arbitrage = await arbitrageFuture;
      } on ApiException catch (e) {
        if (e.statusCode == 404) {
          arbitrageNotice = 'No strong arbitrage opportunities for $_commodity right now.';
        } else if (e.statusCode == 422) {
          arbitrageNotice = 'Not enough $_commodity price data across markets yet.';
        } else {
          arbitrageNotice = 'Could not load arbitrage data.';
        }
      } catch (_) {
        arbitrageNotice = 'Could not load arbitrage data.';
      }

      if (!mounted) return;
      setState(() {
        _summary = summary;
        _movers = movers;
        _arbitrage = arbitrage;
        _arbitrageNotice = arbitrageNotice;
        _error = error;
        _isLive = summarySource.value;
      });
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadCatalog() async {
    try {
      final api = context.read<ApiService>();
      final catalog = await api.listCommodities();
      if (!mounted) return;
      setState(() {
        _commodities = catalog.commodities;
        if (_commodities.isNotEmpty && !_commodities.contains(_commodity)) {
          _commodity = _commodities.first;
        }
        _catalogLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _catalogLoading = false);
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
    final online = context.watch<ConnectivityService>().isOnline;
    if (_wasOnline == false && online == true) _load();
    _wasOnline = online;
  }

  @override
  void dispose() {
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: 'Market Intelligence',
      isLive: (_summary != null || _error != null) ? _isLive : null,
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
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
                    FilledButton(
                      onPressed: _loading ? null : _load,
                      child: Text(_loading ? '…' : 'Load'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(_error!, style: const TextStyle(color: AppColors.falling)),
              ),
            if (_summary != null) ..._buildSummarySection(_summary!),
            ..._buildArbitrageSection(),
            if (_movers != null) ..._buildMoversSection(_movers!),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildSummarySection(CommodityMarketSummary s) {
    final best = s.bestMarket;
    final worst = s.worstMarket;
    return [
      SectionHeading(title: '${s.commodity} · cross-market'),
      const SizedBox(height: 10),
      _tile('Best market to sell', best == null ? '—' : '${best.market} (${_fmtPrice(best.latestPrice, s.currency, s.unit)})', icon: Icons.arrow_upward_rounded, color: AppColors.rising),
      _tile('Worst market to sell', worst == null ? '—' : '${worst.market} (${_fmtPrice(worst.latestPrice, s.currency, s.unit)})', icon: Icons.arrow_downward_rounded, color: AppColors.falling),
      _tile('National average', _fmtPrice(s.nationalAvgPrice, s.currency, s.unit)),
      _tile('Price spread', '${_fmtPrice(s.priceSpread, s.currency, s.unit)} (${s.priceSpreadPct.toStringAsFixed(1)}%)'),
      if (s.recommendation.isNotEmpty) ...[
        const SizedBox(height: 8),
        Card(
          color: AppColors.surfaceMuted,
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                const Icon(Icons.lightbulb_outline, size: 18, color: AppColors.primary),
                const SizedBox(width: 10),
                Expanded(child: Text(s.recommendation)),
              ],
            ),
          ),
        ),
      ],
      const SizedBox(height: 16),
      ...s.markets.map(
        (m) => _tile(
          '${m.market}${m.region != null ? ' · ${m.region}' : ''}',
          '${_fmtPrice(m.latestPrice, m.currency, m.unit)}',
          icon: _trendIcon(m.trend),
          color: _trendColor(m.trend),
          subtitle: m.priceChangePct != null
              ? '${m.priceChangePct! >= 0 ? '+' : ''}${m.priceChangePct!.toStringAsFixed(1)}% / 30d'
              : null,
        ),
      ),
    ];
  }

  List<Widget> _buildArbitrageSection() {
    if (_arbitrage == null || _arbitrage!.isEmpty) {
      if (_arbitrageNotice == null) return const [];
      return [
        const SizedBox(height: 24),
        const SectionHeading(title: 'Arbitrage Opportunities'),
        const SizedBox(height: 8),
        Text(_arbitrageNotice!, style: const TextStyle(color: AppColors.textSecondary)),
      ];
    }
    return [
      const SizedBox(height: 24),
      const SectionHeading(
        title: 'Arbitrage Opportunities',
        subtitle: 'Gross margin only — weigh against real transport cost.',
      ),
      const SizedBox(height: 10),
      ..._arbitrage!.take(5).map(_arbitrageTile),
    ];
  }

  Widget _arbitrageTile(ArbitrageOpportunity o) {
    final color = o.viable ? AppColors.rising : AppColors.warning;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: Icon(
          o.viable ? Icons.check_circle_outline : Icons.warning_amber_outlined,
          color: color,
        ),
        title: Text('${o.buyMarket} → ${o.sellMarket}'),
        subtitle: Text(
          '${_fmtPrice(o.buyPrice, o.currency, o.unit)} → ${_fmtPrice(o.sellPrice, o.currency, o.unit)}\n${o.note}',
        ),
        isThreeLine: true,
        trailing: Text(
          '+${o.grossMarginPct.toStringAsFixed(1)}%',
          style: TextStyle(fontWeight: FontWeight.w800, color: color),
        ),
      ),
    );
  }

  List<Widget> _buildMoversSection(TopMoversResponse movers) {
    return [
      const SizedBox(height: 24),
      SectionHeading(title: 'Biggest movers · last ${movers.periodDays} days'),
      const SizedBox(height: 10),
      if (movers.gainers.isEmpty && movers.losers.isEmpty)
        const Text('No significant price movements right now.'),
      ...movers.gainers.map((g) => _moverTile(g, up: true)),
      ...movers.losers.map((l) => _moverTile(l, up: false)),
    ];
  }

  Widget _moverTile(TopMoverItem m, {required bool up}) {
    final color = up ? AppColors.rising : AppColors.falling;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: Icon(up ? Icons.trending_up_rounded : Icons.trending_down_rounded, color: color),
        title: Text('${m.commodity} in ${m.market}'),
        subtitle: Text('Alert level: ${m.alertLevel}'),
        trailing: Text(
          '${m.changePct >= 0 ? '+' : ''}${m.changePct.toStringAsFixed(1)}%',
          style: TextStyle(fontWeight: FontWeight.w800, color: color),
        ),
      ),
    );
  }

  Widget _tile(String label, String value, {IconData? icon, Color? color, String? subtitle}) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: icon != null ? Icon(icon, color: color ?? AppColors.textSecondary, size: 20) : null,
        title: Text(label),
        subtitle: subtitle != null ? Text(subtitle) : null,
        trailing: Text(value, style: const TextStyle(fontWeight: FontWeight.w700)),
      ),
    );
  }

  String _fmtPrice(double price, String currency, String unit) =>
      '$currency ${price.toStringAsFixed(0)}/$unit';

  IconData _trendIcon(String trend) {
    switch (trend) {
      case 'rising':
        return Icons.trending_up_rounded;
      case 'falling':
        return Icons.trending_down_rounded;
      default:
        return Icons.trending_flat_rounded;
    }
  }

  Color _trendColor(String trend) {
    switch (trend) {
      case 'rising':
        return AppColors.rising;
      case 'falling':
        return AppColors.falling;
      default:
        return AppColors.neutral;
    }
  }
}
