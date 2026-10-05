/// Data classes mirroring the AgriGuard FastAPI market-intelligence schemas
/// (backend/app/routers/markets.py). Field names below are the camelCase
/// Dart-side names; `fromJson` maps them from the snake_case JSON keys the
/// backend actually returns — keep these in sync with markets.py if its
/// Pydantic models change.

class MarketPrice {
  final String market;
  final String? region;
  final double latestPrice;
  final String currency;
  final String unit;
  final String dateRecorded;
  final int daysSinceUpdate;
  final double? price30dAgo;
  final double? priceChangePct;
  final String trend; // "rising" | "falling" | "stable"
  final int dataPoints;

  MarketPrice({
    required this.market,
    this.region,
    required this.latestPrice,
    required this.currency,
    required this.unit,
    required this.dateRecorded,
    required this.daysSinceUpdate,
    this.price30dAgo,
    this.priceChangePct,
    required this.trend,
    required this.dataPoints,
  });

  factory MarketPrice.fromJson(Map<String, dynamic> json) {
    return MarketPrice(
      market: json['market'] as String,
      region: json['region'] as String?,
      latestPrice: (json['latest_price'] as num).toDouble(),
      currency: json['currency'] as String? ?? 'UGX',
      unit: json['unit'] as String? ?? 'KG',
      dateRecorded: json['date_recorded'] as String? ?? '',
      daysSinceUpdate: json['days_since_update'] as int? ?? 0,
      price30dAgo: (json['price_30d_ago'] as num?)?.toDouble(),
      priceChangePct: (json['price_change_pct'] as num?)?.toDouble(),
      trend: json['trend'] as String? ?? 'stable',
      dataPoints: json['data_points'] as int? ?? 0,
    );
  }
}

/// Cross-market summary for one commodity.
/// Mirrors routers/markets.py::CommodityMarketSummary exactly — note the
/// backend has no separate "best_price"/"worst_price" fields; the best and
/// worst prices live on the matching entry in [markets] instead.
class CommodityMarketSummary {
  final String commodity;
  final List<MarketPrice> markets; // sorted highest -> lowest price
  final String bestMarketToSell;
  final String worstMarketToSell;
  final double priceSpread;
  final double priceSpreadPct;
  final double nationalAvgPrice;
  final String currency;
  final String unit;
  final String recommendation;
  final String generatedAt;

  CommodityMarketSummary({
    required this.commodity,
    required this.markets,
    required this.bestMarketToSell,
    required this.worstMarketToSell,
    required this.priceSpread,
    required this.priceSpreadPct,
    required this.nationalAvgPrice,
    required this.currency,
    required this.unit,
    required this.recommendation,
    required this.generatedAt,
  });

  factory CommodityMarketSummary.fromJson(Map<String, dynamic> json) {
    final list = (json['markets'] as List<dynamic>? ?? [])
        .map((e) => MarketPrice.fromJson(e as Map<String, dynamic>))
        .toList();
    return CommodityMarketSummary(
      commodity: json['commodity'] as String,
      markets: list,
      bestMarketToSell: json['best_market_to_sell'] as String? ?? '—',
      worstMarketToSell: json['worst_market_to_sell'] as String? ?? '—',
      priceSpread: (json['price_spread'] as num?)?.toDouble() ?? 0.0,
      priceSpreadPct: (json['price_spread_pct'] as num?)?.toDouble() ?? 0.0,
      nationalAvgPrice: (json['national_avg_price'] as num?)?.toDouble() ?? 0.0,
      currency: json['currency'] as String? ?? 'UGX',
      unit: json['unit'] as String? ?? 'KG',
      recommendation: json['recommendation'] as String? ?? '',
      generatedAt: json['generated_at'] as String? ?? '',
    );
  }

  /// Best-market price, read off the matching [MarketPrice] entry rather
  /// than assumed to be a top-level field (the backend doesn't send one).
  /// `markets` is sorted highest -> lowest by the backend, so the first /
  /// last entries are safe fallbacks if the name match ever misses.
  MarketPrice? get bestMarket => _findMarket(bestMarketToSell) ?? _firstOrNull(markets);

  MarketPrice? get worstMarket => _findMarket(worstMarketToSell) ?? _lastOrNull(markets);

  MarketPrice? _findMarket(String name) {
    for (final m in markets) {
      if (m.market == name) return m;
    }
    return null;
  }

  static MarketPrice? _firstOrNull(List<MarketPrice> list) => list.isEmpty ? null : list.first;
  static MarketPrice? _lastOrNull(List<MarketPrice> list) => list.isEmpty ? null : list.last;
}

/// One commodity x market pair with a notable recent price movement.
/// Mirrors routers/markets.py::TopMoverItem.
class TopMoverItem {
  final String commodity;
  final String market;
  final double latestPrice;
  final double previousPrice;
  final double changePct;
  final String direction; // "up" | "down"
  final String alertLevel; // "high" | "medium" | "low"
  final String currency;

  TopMoverItem({
    required this.commodity,
    required this.market,
    required this.latestPrice,
    required this.previousPrice,
    required this.changePct,
    required this.direction,
    required this.alertLevel,
    required this.currency,
  });

  factory TopMoverItem.fromJson(Map<String, dynamic> json) {
    return TopMoverItem(
      commodity: json['commodity'] as String,
      market: json['market'] as String,
      latestPrice: (json['latest_price'] as num).toDouble(),
      previousPrice: (json['previous_price'] as num).toDouble(),
      changePct: (json['change_pct'] as num).toDouble(),
      direction: json['direction'] as String? ?? 'up',
      alertLevel: json['alert_level'] as String? ?? 'low',
      currency: json['currency'] as String? ?? 'UGX',
    );
  }
}

/// Profit opportunity from buying in one market and selling in another.
/// Mirrors routers/markets.py::ArbitrageOpportunity. Does NOT account for
/// transport cost itself — [note] carries that caveat as backend-generated
/// text (see market_screen.dart's disclaimer above the arbitrage list).
class ArbitrageOpportunity {
  final String commodity;
  final String buyMarket;
  final String sellMarket;
  final double buyPrice;
  final double sellPrice;
  final double grossMargin; // sellPrice - buyPrice
  final double grossMarginPct; // grossMargin / buyPrice * 100
  final String currency;
  final String unit;
  final bool viable; // true if margin likely exceeds typical transport cost
  final String note;

  ArbitrageOpportunity({
    required this.commodity,
    required this.buyMarket,
    required this.sellMarket,
    required this.buyPrice,
    required this.sellPrice,
    required this.grossMargin,
    required this.grossMarginPct,
    required this.currency,
    required this.unit,
    required this.viable,
    required this.note,
  });

  factory ArbitrageOpportunity.fromJson(Map<String, dynamic> json) {
    return ArbitrageOpportunity(
      commodity: json['commodity'] as String? ?? '',
      buyMarket: json['buy_market'] as String? ?? '',
      sellMarket: json['sell_market'] as String? ?? '',
      buyPrice: (json['buy_price'] as num?)?.toDouble() ?? 0.0,
      sellPrice: (json['sell_price'] as num?)?.toDouble() ?? 0.0,
      grossMargin: (json['gross_margin'] as num?)?.toDouble() ?? 0.0,
      grossMarginPct: (json['gross_margin_pct'] as num?)?.toDouble() ?? 0.0,
      currency: json['currency'] as String? ?? 'UGX',
      unit: json['unit'] as String? ?? 'KG',
      viable: json['viable'] as bool? ?? false,
      note: json['note'] as String? ?? '',
    );
  }
}

/// Mirrors routers/markets.py::TopMoversResponse.
class TopMoversResponse {
  final List<TopMoverItem> gainers;
  final List<TopMoverItem> losers;
  final int periodDays;
  final String generatedAt;

  TopMoversResponse({
    required this.gainers,
    required this.losers,
    required this.periodDays,
    required this.generatedAt,
  });

  factory TopMoversResponse.fromJson(Map<String, dynamic> json) {
    return TopMoversResponse(
      gainers: (json['gainers'] as List<dynamic>? ?? [])
          .map((e) => TopMoverItem.fromJson(e as Map<String, dynamic>))
          .toList(),
      losers: (json['losers'] as List<dynamic>? ?? [])
          .map((e) => TopMoverItem.fromJson(e as Map<String, dynamic>))
          .toList(),
      periodDays: json['period_days'] as int? ?? 30,
      generatedAt: json['generated_at'] as String? ?? '',
    );
  }
}
