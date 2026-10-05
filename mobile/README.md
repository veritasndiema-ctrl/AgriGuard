# AgriGuard Mobile (Flutter)

Cross-platform client for Ugandan farmers: crop price forecasts, market intelligence, weather/climate risk, and price alerts.

## Features

- **Forecast** — 7/14/28-day price forecasts for any commodity × market, with history + forecast chart (fl_chart)
- **Markets** — cross-market summary (best/worst price, national average)
- **Alerts** — watchlist of commodities with significant predicted moves or server-side alerts
- **Weather** — drought-risk and heavy-rain/flood warnings per market (live-only, no offline snapshot)
- **Offline** — Markets/Alerts fall back to the bundled snapshot (`assets/data/agriguard_offline_data.json`) with no backend reachable; Forecast additionally checks LocalCache first for a real forecast SyncService prefetched earlier for the person's own watchlist (fresher, since it's their actual crops, not the generic bundled set), falling back to the bundled snapshot if nothing's cached yet. Weather requires a live connection — no offline data for it at all.

## Prerequisites

- Flutter 3.22+ / Dart 3.3+
- Running AgriGuard FastAPI backend, reachable from the device

## Connecting to a live backend

The app never has a backend URL hardcoded in source. Where it comes from depends on how you're running it:

- **`flutter run` during local development** — nothing is configured by default, so the app runs entirely on the bundled offline snapshot (Weather tab will show "needs a live connection"). To point it at a local backend, launch the app, go to **Settings → About**, tap the version number **7 times** to reveal the hidden developer override, and enter your backend URL there (e.g. `http://10.0.2.2:8000` for the Android emulator, or your machine's LAN IP for a physical device). This is stored on-device only via `shared_preferences` and always wins over anything baked in at build time.
- **Release APKs built by `.github/workflows/build-apk.yml`** — the workflow bakes the backend URL in at compile time via `--dart-define=AGRIGUARD_BACKEND_URL=...`, reading it from the **`AGRIGUARD_BACKEND_URL`** repository variable (Settings → Secrets and variables → Actions → Variables — use a Variable, not a Secret, since it ends up inside a public APK either way). Set that once after you deploy the backend (Render / Railway / Fly.io / your own server), and every subsequent build — on push to `main` or a manual "Run workflow" — ships wired to live data automatically, with no code change or commit needed when the backend URL changes. If the variable is unset, the workflow logs a visible warning and the resulting APK falls back to offline-only.
- **Local release build**, matching CI: `flutter build apk --release --dart-define=AGRIGUARD_BACKEND_URL=https://your-backend-host`

See `lib/services/backend_config.dart` for the resolution order (dev override > build-time define > offline).

## Run

```bash
cd mobile
flutter pub get
flutter run
```

## Layout

```
lib/
  main.dart
  models/{forecast,market,weather}_model.dart
  services/{api_service,backend_config,connectivity_service,preferences_service}.dart
  screens/{forecast,market,alerts,weather,settings,profile}_screen.dart
  widgets/{app_scaffold,app_drawer,data_source_chip,forecast_card,price_history_chart}.dart
  offline/{local_cache,sync_service}.dart
```

## Platform folders

`android/` and `ios/` currently contain only `.gitkeep`. `build-apk.yml` regenerates the Android scaffolding on every CI run via `flutter create --platforms=android`. To do the same locally:

```bash
flutter create . --project-name agriguard_mobile --org com.agriguard
```