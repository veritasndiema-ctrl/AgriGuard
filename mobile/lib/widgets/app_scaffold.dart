import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/connectivity_service.dart';
import 'app_drawer.dart';
import 'data_source_chip.dart';

/// Common page shell used by every top-level screen: hamburger icon (opens
/// [AppDrawer]) on the leading edge, a title, optional actions, and the
/// live/offline status chip when a [isLive] value is provided. Keeping this
/// in one place is what makes the four screens look like one cohesive app
/// instead of four separately-styled prototypes.
class AppScaffold extends StatelessWidget {
  final String title;
  final Widget body;
  final bool? isLive; // null = don't show the chip yet (nothing loaded)
  final List<Widget>? extraActions;
  final Future<void> Function()? onRefresh;
  final Widget? floatingActionButton;

  const AppScaffold({
    super.key,
    required this.title,
    required this.body,
    this.isLive,
    this.extraActions,
    this.onRefresh,
    this.floatingActionButton,
  });

  @override
  Widget build(BuildContext context) {
    final online = context.watch<ConnectivityService>().isOnline;

    return Scaffold(
      drawer: const AppDrawer(),
      appBar: AppBar(
        title: Text(title),
        actions: [
          if (!online)
            const Padding(
              padding: EdgeInsets.only(right: 6),
              child: Tooltip(
                message: 'No network connection',
                child: Icon(Icons.signal_wifi_off_rounded, size: 18),
              ),
            ),
          if (isLive != null) DataSourceChip(isLive: isLive!),
          if (extraActions != null) ...extraActions!,
          if (onRefresh != null)
            IconButton(
              icon: const Icon(Icons.refresh),
              onPressed: onRefresh,
            ),
          const SizedBox(width: 4),
        ],
      ),
      body: body,
      floatingActionButton: floatingActionButton,
    );
  }
}
