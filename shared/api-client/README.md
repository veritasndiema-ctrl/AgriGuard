# shared/api-client

`@agriguard/api-client` — a typed TypeScript client for the AgriGuard
FastAPI backend. One implementation, shared by any TS frontend (desktop
today; a future web dashboard tomorrow) instead of each app hand-rolling
its own fetch/axios calls and response types.

Not currently installed as a dependency by `desktop/` or `frontend/` —
`desktop/src/services/apiClient.ts` still has its own small local client.
Wire this package in (`npm install @agriguard/api-client` via a workspace
reference, or `npm link` during local development) when you want a single
source of truth instead of two clients drifting apart.

## Layout

```
shared/api-client/
├── types/
│   └── schemas.ts     # Types mirroring backend/app Pydantic schemas
├── src/
│   └── client.ts       # AgriGuardApiClient — one method per backend endpoint
├── package.json
└── tsconfig.json
```

## Coverage

`AgriGuardApiClient` wraps every endpoint the backend currently mounts:

- **System** — `getApiInfo()`, `health()`
- **Prediction** — `predictPrice()`, `validateInput()` (backend/app/main.py)
- **Forecasts** — `listCommodities()`, `getForecast()`, `getForecastHistory()`,
  `compareForecasts()` (routers/forecasts.py)
- **Markets** — `getMarketSummary()` / `getMarketSummaryDetailed()`,
  `getMarketOverview()`, `getMarketPriceHistory()`, `getTopMovers()`,
  `compareMarketPrices()`, `getArbitrageOpportunities()` /
  `getArbitrageOpportunitiesDetailed()`, `getNationalSummary()`
  (routers/markets.py)
- **USSD** — `simulateUssd()`, wrapping the dev-only `/ussd/simulate`
  browser simulator (routers/ussd.py)

`routers/prices.py` is intentionally not wired into `main.py` on the
backend yet (see the backend README's "Known issues"), so it has no
client method here either.

Some `routers/markets.py` endpoints return richer objects than
`types/schemas.ts` currently models. For those, this client exposes both
a plain method matching the existing (simpler) shared type, and a
`*Detailed` method returning the full backend response — see the JSDoc
on `getMarketSummary`/`getArbitrageOpportunities` in `src/client.ts`.

## Usage

```bash
npm install
npm run build      # emits dist/src/client.js + .d.ts
npm run typecheck  # tsc --noEmit
```

```ts
import { createAgriGuardClient } from "@agriguard/api-client";

const api = createAgriGuardClient("http://localhost:8000");

const forecast = await api.getForecast("Maize", { market: "Kampala", horizon: 14 });
const summary = await api.getMarketSummary("Maize");
```

Every method throws `AgriGuardApiError` (with `status` and `detail`
populated from the response body) on failure rather than a raw axios
error, so callers don't need to know axios is involved under the hood.
