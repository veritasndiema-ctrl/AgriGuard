import 'package:flutter/material.dart';
import '../models/forecast_model.dart';
import '../theme/app_theme.dart';

/// Compact card showing a single forecast horizon summary or a key metric.
class ForecastCard extends StatelessWidget {
  final ForecastResponse forecast;
  final double? currentPrice;
  final VoidCallback? onTap;

  const ForecastCard({
    super.key,
    required this.forecast,
    this.currentPrice,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isUp = forecast.pctChange >= 0;
    final trendColor = isUp ? AppColors.rising : AppColors.falling;
    final trendIcon = isUp ? Icons.trending_up_rounded : Icons.trending_down_rounded;

    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.md),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${forecast.commodity} · ${forecast.market}',
                      style: theme.textTheme.titleMedium,
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: trendColor.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(trendIcon, color: trendColor, size: 16),
                        const SizedBox(width: 4),
                        Text(
                          '${isUp ? '+' : ''}${forecast.pctChange.toStringAsFixed(1)}%',
                          style: TextStyle(
                            color: trendColor,
                            fontWeight: FontWeight.w800,
                            fontSize: 13,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (forecast.substitutedFromMarket != null) ...[
                const SizedBox(height: 10),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppColors.warning.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.info_outline, size: 16, color: AppColors.warning),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Showing nearest available market data for '
                          '"${forecast.substitutedFromMarket}" — closest match: ${forecast.market}.',
                          style: theme.textTheme.bodySmall?.copyWith(color: AppColors.warning),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 6),
              Text(
                '${forecast.horizonDays}-day outlook · ${forecast.confidenceLabel}',
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: 14),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  if (currentPrice != null)
                    _metric(
                      context,
                      'Current price',
                      '${currentPrice!.toStringAsFixed(0)} ${forecast.currency}/${forecast.unit}',
                    ),
                  _metric(
                    context,
                    'Predicted (${forecast.horizonDays}d)',
                    '${forecast.lastPredicted.toStringAsFixed(0)} ${forecast.currency}/${forecast.unit}',
                  ),
                  _metric(context, 'Trend', _trendLabel(forecast.trend)),
                ],
              ),
              if (forecast.alert != null && forecast.alert!.isNotEmpty) ...[
                const SizedBox(height: 14),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  decoration: BoxDecoration(
                    color: trendColor.withOpacity(0.08),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: trendColor.withOpacity(0.25)),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.notifications_active_outlined, size: 18, color: trendColor),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          forecast.alert!,
                          style: theme.textTheme.bodySmall?.copyWith(color: AppColors.textPrimary),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  String _trendLabel(String trend) {
    switch (trend) {
      case 'rising':
        return 'Rising';
      case 'falling':
        return 'Falling';
      default:
        return 'Stable';
    }
  }

  Widget _metric(BuildContext context, String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelSmall),
        const SizedBox(height: 3),
        Text(
          value,
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5),
        ),
      ],
    );
  }
}

/// Small tile for an individual forecast point (used in the detail list).
class ForecastPointTile extends StatelessWidget {
  final ForecastPoint point;
  final String currency;
  final String unit;

  const ForecastPointTile({
    super.key,
    required this.point,
    this.currency = 'UGX',
    this.unit = 'KG',
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      dense: true,
      title: Text(point.date, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(
        'Range ${point.lowerBound.toStringAsFixed(0)} – ${point.upperBound.toStringAsFixed(0)}',
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text(
            '${point.predictedPrice.toStringAsFixed(0)} $currency',
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          Text(
            '${(point.confidence * 100).toStringAsFixed(0)}% confidence',
            style: Theme.of(context).textTheme.labelSmall,
          ),
        ],
      ),
    );
  }
}
