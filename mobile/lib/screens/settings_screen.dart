import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../offline/local_cache.dart';
import '../services/backend_config.dart';
import '../services/connectivity_service.dart';
import '../services/preferences_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_scaffold.dart';

/// User-facing settings only.
///
/// This screen deliberately does NOT expose backend URLs, IPs, hostnames,
/// model/library names, or any other implementation detail — see the
/// header comment in `backend_config.dart` for why. Everything here is
/// something an ordinary farmer/trader using the app would actually want
/// to control.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _notifications = true;
  String? _preferredMarket;
  List<String> _watchlist = [];
  final _marketCtrl = TextEditingController();
  final _addCropCtrl = TextEditingController();
  int _versionTaps = 0;

  @override
  void initState() {
    super.initState();
    _loadPrefs();
  }

  @override
  void dispose() {
    _marketCtrl.dispose();
    _addCropCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadPrefs() async {
    final notifications = await PreferencesService.getNotificationsEnabled();
    final market = await PreferencesService.getPreferredMarket();
    final watchlist = await PreferencesService.getWatchlist();
    if (!mounted) return;
    setState(() {
      _notifications = notifications;
      _preferredMarket = market;
      _marketCtrl.text = market ?? '';
      _watchlist = watchlist;
    });
  }

  Future<void> _saveMarket() async {
    await PreferencesService.setPreferredMarket(_marketCtrl.text);
    if (!mounted) return;
    setState(() => _preferredMarket = _marketCtrl.text.trim().isEmpty ? null : _marketCtrl.text.trim());
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Preferred market saved.')),
    );
  }

  Future<void> _toggleNotifications(bool v) async {
    setState(() => _notifications = v);
    await PreferencesService.setNotificationsEnabled(v);
  }

  Future<void> _addCrop() async {
    final crop = _addCropCtrl.text.trim();
    if (crop.isEmpty || _watchlist.contains(crop)) return;
    final updated = [..._watchlist, crop];
    await PreferencesService.setWatchlist(updated);
    setState(() {
      _watchlist = updated;
      _addCropCtrl.clear();
    });
  }

  Future<void> _removeCrop(String crop) async {
    final updated = _watchlist.where((c) => c != crop).toList();
    await PreferencesService.setWatchlist(updated);
    setState(() => _watchlist = updated);
  }

  Future<void> _clearCache() async {
    await LocalCache().clear();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Offline cache cleared.')),
    );
  }

  void _handleVersionTap() async {
    _versionTaps++;
    if (_versionTaps >= 7) {
      _versionTaps = 0;
      await _showDevOverrideDialog();
    }
  }

  /// Hidden, undocumented developer entry point — not part of the normal
  /// settings surface. Reached only by tapping the version number in About
  /// seven times, mirroring Android's own "Developer options" pattern.
  Future<void> _showDevOverrideDialog() async {
    final hasOverride = await BackendConfig.hasDevOverride();
    final ctrl = TextEditingController(text: hasOverride ? await BackendConfig.getBaseUrl() : '');
    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Developer override'),
        content: TextField(
          controller: ctrl,
          decoration: const InputDecoration(
            labelText: 'Backend URL',
            hintText: 'http://10.0.2.2:8000',
          ),
          keyboardType: TextInputType.url,
          autocorrect: false,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () async {
              await BackendConfig.setDevOverride(ctrl.text);
              if (ctx.mounted) Navigator.of(ctx).pop();
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final online = context.watch<ConnectivityService>().isOnline;

    return AppScaffold(
      title: 'Settings',
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            color: online ? AppColors.rising.withOpacity(0.08) : AppColors.warning.withOpacity(0.08),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  Icon(
                    online ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                    color: online ? AppColors.rising : AppColors.warning,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      online
                          ? 'AgriGuard will refresh prices automatically while you\'re online.'
                          : 'You\'re offline — AgriGuard is showing the last saved data. '
                              'It will refresh automatically once you\'re back online.',
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          const SectionHeading(title: 'Alerts'),
          const SizedBox(height: 8),
          Card(
            child: SwitchListTile(
              value: _notifications,
              onChanged: _toggleNotifications,
              title: const Text('Price movement alerts'),
              subtitle: const Text('Get notified when a watched crop moves sharply.'),
            ),
          ),
          const SizedBox(height: 10),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Watchlist', style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _watchlist
                        .map(
                          (c) => Chip(
                            label: Text(c),
                            onDeleted: () => _removeCrop(c),
                            deleteIcon: const Icon(Icons.close, size: 16),
                          ),
                        )
                        .toList(),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _addCropCtrl,
                          decoration: const InputDecoration(hintText: 'Add a crop e.g. Sorghum'),
                          onSubmitted: (_) => _addCrop(),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filled(
                        onPressed: _addCrop,
                        icon: const Icon(Icons.add),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          const SectionHeading(title: 'Preferences'),
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Preferred market', style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 4),
                  Text(
                    'Used as the default market across Forecast, Markets and Alerts.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _marketCtrl,
                          decoration: const InputDecoration(hintText: 'e.g. Mbarara, Gulu, Lira'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      FilledButton(onPressed: _saveMarket, child: const Text('Save')),
                    ],
                  ),
                  if (_preferredMarket != null) ...[
                    const SizedBox(height: 8),
                    Text('Currently: $_preferredMarket', style: Theme.of(context).textTheme.bodySmall),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          const SectionHeading(title: 'Storage'),
          const SizedBox(height: 8),
          Card(
            child: ListTile(
              leading: const Icon(Icons.delete_sweep_outlined),
              title: const Text('Clear offline cache'),
              subtitle: const Text('Frees storage; live data will re-download next time you\'re online.'),
              onTap: _clearCache,
            ),
          ),
          const SizedBox(height: 24),
          const SectionHeading(title: 'About'),
          const SizedBox(height: 8),
          Card(
            child: GestureDetector(
              onTap: _handleVersionTap,
              child: const ListTile(
                leading: Icon(Icons.eco_outlined),
                title: Text('AgriGuard'),
                subtitle: Text('Version 0.1.0'),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
