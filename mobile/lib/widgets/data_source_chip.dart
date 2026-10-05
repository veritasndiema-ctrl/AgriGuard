import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Small app-bar chip showing whether the data on screen came from the
/// live backend or the bundled offline snapshot. Every screen that calls
/// ApiService should show this — silently falling back to stale bundled
/// data with no indication was the original bug report.
class DataSourceChip extends StatelessWidget {
  final bool isLive;

  const DataSourceChip({super.key, required this.isLive});

  @override
  Widget build(BuildContext context) {
    final color = isLive ? AppColors.rising : AppColors.warning;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: color.withOpacity(0.12),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
            const SizedBox(width: 6),
            Text(
              isLive ? 'Live' : 'Offline',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
