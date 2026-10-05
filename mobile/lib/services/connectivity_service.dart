import 'dart:async';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

/// App-wide network reachability signal, backed by `connectivity_plus`.
///
/// This is deliberately separate from [LiveFlag] in `api_service.dart`:
/// [LiveFlag] answers "did *this specific request* come from the live
/// backend or the bundled snapshot?", while [ConnectivityService] answers
/// "does the phone currently have a network path at all?". A phone can be
/// on Wi-Fi with no internet, or have signal but no backend configured —
/// both are meaningfully different from a hard "airplane mode" offline.
///
/// Screens watch [isOnline] to show a live/offline indicator, and listen
/// for the false → true transition to silently refresh once connectivity
/// comes back (previously nothing ever re-fetched until the user manually
/// hit refresh again).
class ConnectivityService extends ChangeNotifier {
  ConnectivityService() {
    _sub = Connectivity().onConnectivityChanged.listen(_handle);
    _bootstrap();
  }

  bool _isOnline = true;
  bool get isOnline => _isOnline;

  StreamSubscription<List<ConnectivityResult>>? _sub;

  Future<void> _bootstrap() async {
    try {
      final result = await Connectivity().checkConnectivity();
      _handle(result);
    } catch (_) {
      // Default to "online" and let individual requests prove otherwise —
      // never block the UI behind a connectivity check that itself failed.
    }
  }

  void _handle(List<ConnectivityResult> result) {
    final online = !result.contains(ConnectivityResult.none) && result.isNotEmpty;
    if (online != _isOnline) {
      _isOnline = online;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}
