# desktop/

Tauri 2 desktop client for AgriGuard. A thin React/TypeScript UI
(`src/`) wrapped in a minimal Rust shell (`src-tauri/`) that talks
to the same FastAPI backend as the mobile app and Streamlit frontend
— no separate backend logic lives here.

## Layout

```
desktop/
├── index.html              # Vite entry point
├── src/
│   ├── main.tsx             # React root
│   ├── App.tsx               # Shell: header + API base URL input
│   ├── screens/
│   │   └── Dashboard.tsx     # Commodity/market forecast + cross-market summary
│   └── services/
│       └── apiClient.ts      # Typed wrapper around the FastAPI backend
├── src-tauri/
│   ├── src/main.rs           # Tauri entrypoint
│   ├── build.rs              # Required by the tauri-build dependency
│   ├── capabilities/
│   │   └── default.json      # Window permission grants (Tauri v2 ACL)
│   ├── icons/                 # App icons — see note below
│   ├── Cargo.toml
│   └── tauri.conf.json
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## Prerequisites

- Node.js 18+ and npm
- Rust (stable) + the platform build tools Tauri needs — see the
  [Tauri prerequisites guide](https://v2.tauri.app/start/prerequisites/)
- The AgriGuard FastAPI backend running locally (default: `http://localhost:8000`)

## Usage

```bash
# from desktop/
npm install
npm run dev        # Vite dev server only, at http://localhost:1420
npm run tauri dev  # full desktop app (opens a native window)
```

The API base URL is editable from the header at runtime — point it at
a remote deployment without rebuilding.

## Building a release bundle

```bash
npm run tauri build
```

## Icons

`src-tauri/icons/` ships with only a `.gitkeep` so the directory is
tracked by git. Generate the actual icon set referenced in
`tauri.conf.json` (`32x32.png`, `128x128.png`, `128x128@2x.png`,
`icon.icns`, `icon.ico`) from a single source image with:

```bash
npm run tauri icon path/to/source-icon.png
```

`npm run tauri build` will fail until these are present.
