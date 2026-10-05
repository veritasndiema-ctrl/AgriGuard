import 'package:flutter/material.dart';

import '../screens/profile_screen.dart';
import '../services/preferences_service.dart';
import '../theme/app_theme.dart';

/// The drawer opened from the three-line (hamburger) icon in every screen's
/// app bar — profile / sign-in at the top, a couple of light-weight links
/// below. Intentionally does not duplicate the bottom navigation
/// (Forecast/Markets/Alerts/Settings already live there); this is for
/// account-level actions instead.
class AppDrawer extends StatefulWidget {
  const AppDrawer({super.key});

  @override
  State<AppDrawer> createState() => _AppDrawerState();
}

class _AppDrawerState extends State<AppDrawer> {
  bool _signedIn = false;
  String? _name;
  String? _email;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final signedIn = await PreferencesService.isSignedIn();
    final name = await PreferencesService.getName();
    final email = await PreferencesService.getEmail();
    if (!mounted) return;
    setState(() {
      _signedIn = signedIn;
      _name = name;
      _email = email;
    });
  }

  Future<void> _openSignIn() async {
    Navigator.of(context).pop(); // close drawer first
    await Future.delayed(const Duration(milliseconds: 150));
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const ProfileScreen()),
    );
    _refresh();
  }

  Future<void> _signOut() async {
    await PreferencesService.signOut();
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final initials = (_name != null && _name!.trim().isNotEmpty)
        ? _name!.trim().substring(0, 1).toUpperCase()
        : null;

    return Drawer(
      backgroundColor: AppColors.surface,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 24, 20, 20),
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 26,
                    backgroundColor: AppColors.primary,
                    child: _signedIn && initials != null
                        ? Text(
                            initials,
                            style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.w700,
                              fontSize: 18,
                            ),
                          )
                        : const Icon(Icons.person_outline, color: Colors.white),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _signedIn ? (_name ?? 'Farmer') : 'Guest',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 2),
                        if (_signedIn && _email != null)
                          Text(_email!, style: Theme.of(context).textTheme.bodySmall)
                        else
                          Text(
                            'Not signed in',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            if (!_signedIn)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _openSignIn,
                    icon: const Icon(Icons.login, size: 18),
                    label: const Text('Sign in'),
                  ),
                ),
              ),
            const SizedBox(height: 8),
            const Divider(height: 1),
            ListTile(
              leading: const Icon(Icons.eco_outlined),
              title: const Text('About AgriGuard'),
              onTap: () {
                Navigator.of(context).pop();
                showAboutDialog(
                  context: context,
                  applicationName: 'AgriGuard',
                  applicationVersion: '0.1.0',
                  applicationIcon: const Icon(Icons.eco, color: AppColors.primary),
                  children: const [
                    Text(
                      'Crop price forecasts and market intelligence for '
                      'Ugandan farmers and traders.',
                    ),
                  ],
                );
              },
            ),
            ListTile(
              leading: const Icon(Icons.share_outlined),
              title: const Text('Share AgriGuard'),
              onTap: () => Navigator.of(context).pop(),
            ),
            ListTile(
              leading: const Icon(Icons.help_outline),
              title: const Text('Help & feedback'),
              onTap: () => Navigator.of(context).pop(),
            ),
            const Spacer(),
            if (_signedIn)
              Padding(
                padding: const EdgeInsets.all(12),
                child: ListTile(
                  leading: const Icon(Icons.logout, color: AppColors.falling),
                  title: const Text('Sign out', style: TextStyle(color: AppColors.falling)),
                  onTap: () {
                    Navigator.of(context).pop();
                    _signOut();
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }
}
