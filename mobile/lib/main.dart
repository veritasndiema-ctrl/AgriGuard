import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'offline/local_cache.dart';
import 'offline/sync_service.dart';
import 'screens/forecast_screen.dart';
import 'screens/market_screen.dart';
import 'screens/alerts_screen.dart';
import 'screens/weather_screen.dart';
import 'screens/settings_screen.dart';
import 'services/api_service.dart';
import 'services/connectivity_service.dart';
import 'services/preferences_service.dart';
import 'theme/app_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // Fire-and-forget: starts waking a cold Render instance the moment the
  // app opens, so it's more likely to already be awake by the time the
  // first screen makes its real data request. See ApiService.warmUp().
  unawaited(ApiService().warmUp());
  // Fire-and-forget: silently refreshes the person's own watchlist crops
  // (Settings > Watchlist) into LocalCache while online, so that if they
  // later lose connectivity, Forecast/Alerts fall back to their own recent
  // real data (see ApiService.getForecast's cache tier) instead of jumping
  // straight to the generic bundled snapshot. No-ops quickly if offline —
  // see SyncService.isOnline.
  unawaited(_prefetchWatchlist());
  runApp(const AgriGuardApp());
}

Future<void> _prefetchWatchlist() async {
  final watchlist = await PreferencesService.getWatchlist();
  if (watchlist.isEmpty) return;
  final market = await PreferencesService.getPreferredMarket() ?? 'Kampala';
  final sync = SyncService(api: ApiService(), cache: LocalCache());
  await sync.prefetch(watchlist.map((c) => (c, market)).toList());
}

class AgriGuardApp extends StatelessWidget {
  const AgriGuardApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        Provider<ApiService>(create: (_) => ApiService()),
        ChangeNotifierProvider<ConnectivityService>(
          create: (_) => ConnectivityService(),
        ),
      ],
      child: MaterialApp(
        title: 'AgriGuard',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light,
        home: const HomeShell(),
      ),
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _pages = [
    ForecastScreen(),
    MarketScreen(),
    AlertsScreen(),
    WeatherScreen(),
    SettingsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _index, children: _pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.trending_up_outlined),
            selectedIcon: Icon(Icons.trending_up),
            label: 'Forecast',
          ),
          NavigationDestination(
            icon: Icon(Icons.storefront_outlined),
            selectedIcon: Icon(Icons.storefront),
            label: 'Markets',
          ),
          NavigationDestination(
            icon: Icon(Icons.notifications_outlined),
            selectedIcon: Icon(Icons.notifications),
            label: 'Alerts',
          ),
          NavigationDestination(
            icon: Icon(Icons.cloud_outlined),
            selectedIcon: Icon(Icons.cloud),
            label: 'Weather',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_outlined),
            selectedIcon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}
