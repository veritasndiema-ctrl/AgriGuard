import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/forecast_model.dart';
import '../theme/app_theme.dart';

/// Full daily price timeline: every historical observation through to every
/// predicted day on the chosen horizon, in one continuous, pannable chart.
///
/// Unlike a fixed-size chart squeezed to fit the screen, this one lays out
/// a fixed pixel width per day and puts the whole thing inside a horizontal
/// scroll view — so a 365-day history plus a 90-day forecast doesn't get
/// crushed into unreadable static line, it can be slid through like the
/// reference app screenshots show.
class PriceHistoryChart extends StatefulWidget {
  final List<HistoryPoint> history;
  final List<ForecastPoint> forecast;
  final String currency;

  const PriceHistoryChart({
    super.key,
    required this.history,
    required this.forecast,
    required this.currency,
  });

  @override
  State<PriceHistoryChart> createState() => _PriceHistoryChartState();
}

class _PriceHistoryChartState extends State<PriceHistoryChart> {
  final _scrollCtrl = ScrollController();
  static const double _pxPerPoint = 14;

  @override
  void didUpdateWidget(covariant PriceHistoryChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Whenever the data changes (new horizon, new commodity), jump the
    // scroll position back to "today" — the seam between history and
    // forecast — rather than leaving the view wherever it happened to be.
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToToday());
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToToday());
  }

  void _scrollToToday() {
    if (!_scrollCtrl.hasClients) return;
    final histLen = widget.history.length;
    // Center the seam-point in the visible viewport rather than pinning it
    // to the left edge, so a little history is visible alongside the
    // forecast without an extra tap.
    final viewport = _scrollCtrl.position.viewportDimension;
    final target = (histLen * _pxPerPoint) - viewport / 2;
    _scrollCtrl.jumpTo(target.clamp(0, _scrollCtrl.position.maxScrollExtent).toDouble());
  }

  @override
  void dispose() {
    _scrollCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final spots = <FlSpot>[];
    final labels = <String>[];
    var i = 0.0;

    for (final h in widget.history) {
      spots.add(FlSpot(i, h.price));
      labels.add(h.date);
      i += 1;
    }
    final histEnd = i;

    for (final p in widget.forecast) {
      spots.add(FlSpot(i, p.predictedPrice));
      labels.add(p.date);
      i += 1;
    }

    if (spots.isEmpty) return const SizedBox.shrink();

    final minY = spots.map((s) => s.y).reduce((a, b) => a < b ? a : b) * 0.95;
    final maxY = spots.map((s) => s.y).reduce((a, b) => a > b ? a : b) * 1.05;
    final chartWidth = (spots.length * _pxPerPoint).clamp(
      MediaQuery.of(context).size.width - 32,
      double.infinity,
    );

    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionHeading(
              title: 'Price trend',
              subtitle: '${widget.currency} · slide to explore the full timeline',
            ),
            const SizedBox(height: 14),
            SizedBox(
              height: 230,
              child: Scrollbar(
                controller: _scrollCtrl,
                thumbVisibility: true,
                child: SingleChildScrollView(
                  controller: _scrollCtrl,
                  scrollDirection: Axis.horizontal,
                  physics: const BouncingScrollPhysics(),
                  child: SizedBox(
                    width: chartWidth.toDouble(),
                    child: LineChart(
                      LineChartData(
                        minY: minY,
                        maxY: maxY,
                        gridData: FlGridData(
                          show: true,
                          drawVerticalLine: false,
                          horizontalInterval: (maxY - minY) / 4,
                          getDrawingHorizontalLine: (v) => FlLine(
                            color: AppColors.outline,
                            strokeWidth: 1,
                          ),
                        ),
                        borderData: FlBorderData(show: false),
                        extraLinesData: ExtraLinesData(
                          verticalLines: [
                            if (histEnd > 0 && histEnd < spots.length)
                              VerticalLine(
                                x: histEnd - 1,
                                color: AppColors.outline,
                                strokeWidth: 1,
                                dashArray: const [4, 4],
                              ),
                          ],
                        ),
                        titlesData: FlTitlesData(
                          leftTitles: AxisTitles(
                            sideTitles: SideTitles(
                              showTitles: true,
                              reservedSize: 46,
                              getTitlesWidget: (v, _) => Text(
                                NumberFormat.compact().format(v),
                                style: Theme.of(context).textTheme.labelSmall,
                              ),
                            ),
                          ),
                          bottomTitles: AxisTitles(
                            sideTitles: SideTitles(
                              showTitles: true,
                              interval: (spots.length / 12).ceilToDouble().clamp(1, 30).toDouble(),
                              getTitlesWidget: (v, _) {
                                final idx = v.toInt();
                                if (idx < 0 || idx >= labels.length) {
                                  return const SizedBox.shrink();
                                }
                                final d = labels[idx];
                                final short = d.length >= 10 ? d.substring(5, 10) : d;
                                return Padding(
                                  padding: const EdgeInsets.only(top: 6),
                                  child: Text(short, style: Theme.of(context).textTheme.labelSmall),
                                );
                              },
                            ),
                          ),
                          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                        ),
                        lineBarsData: [
                          LineChartBarData(
                            spots: spots.where((s) => s.x < histEnd).toList(),
                            isCurved: true,
                            curveSmoothness: 0.2,
                            color: AppColors.primary,
                            barWidth: 2.5,
                            dotData: const FlDotData(show: false),
                            belowBarData: BarAreaData(
                              show: true,
                              gradient: LinearGradient(
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                                colors: [
                                  AppColors.primary.withOpacity(0.18),
                                  AppColors.primary.withOpacity(0.0),
                                ],
                              ),
                            ),
                          ),
                          if (histEnd < spots.length)
                            LineChartBarData(
                              spots: spots.where((s) => s.x >= (histEnd - 1).clamp(0, spots.length)).toList(),
                              isCurved: true,
                              curveSmoothness: 0.2,
                              color: AppColors.accent,
                              barWidth: 2.5,
                              dashArray: const [6, 4],
                              dotData: FlDotData(
                                show: true,
                                getDotPainter: (spot, percent, bar, index) => FlDotCirclePainter(
                                  radius: 2.6,
                                  color: AppColors.accent,
                                  strokeWidth: 0,
                                ),
                              ),
                            ),
                        ],
                        lineTouchData: LineTouchData(
                          touchTooltipData: LineTouchTooltipData(
                            getTooltipColor: (_) => AppColors.textPrimary,
                            getTooltipItems: (touched) {
                              return touched.map((t) {
                                final idx = t.x.toInt();
                                final label = idx >= 0 && idx < labels.length ? labels[idx] : '';
                                return LineTooltipItem(
                                  '$label\n${t.y.toStringAsFixed(0)} ${widget.currency}',
                                  const TextStyle(color: Colors.white, fontSize: 12),
                                );
                              }).toList();
                            },
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                _legendDot(AppColors.primary, 'History'),
                const SizedBox(width: 16),
                _legendDot(AppColors.accent, 'Forecast'),
                const Spacer(),
                Icon(Icons.swipe, size: 14, color: AppColors.textSecondary),
                const SizedBox(width: 4),
                Text('Swipe', style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _legendDot(Color color, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 9,
          height: 9,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(label, style: Theme.of(context).textTheme.labelSmall),
      ],
    );
  }
}
