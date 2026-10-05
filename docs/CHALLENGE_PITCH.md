# AgriGuard — Interfaculty Creativity & Innovation Challenge pitch

Theme: *Unlocking Potential through Research and Innovation*. Screening 6 Oct 2026, final 16 Oct 2026.

## Demo (rehearse only this; verified in `tests/test_demo_consistency.py`)

| `text=` | What the judge sees |
|---|---|
| *(empty)* | Main menu |
| `1*Beans*1` | Beans, Mbarara: UGX 3,604/kg, up 8% (Apr 2026) |
| `2*Maize` | Arua UGX 2,379 vs Gulu UGX 1,949: sell in Arua |
| `3*Maize` | Next-month outlook (Gulu), from the same model as `/forecasts/Maize` |

Record this path on video as a fallback. Do not demo Top Movers live.

## 2:30 script

**Problem (20s).** Same week, beans were UGX 4,956 in Gulu and 3,604 in Mbarara (WFP, Apr 2026). A farmer without a smartphone never sees that gap.

**Solution (20s).** Prices, best market and a next-month outlook on any phone over USSD. No data bundle, no app.

**Demo (50s).** `1*Beans*1`, `2*Maize`, `3*Maize`, one sentence each.

**Research and Innovation (30s).** 20 years of WFP data, about 40 markets. FastAPI backend, XGBoost and Prophet ensemble, quant layer with backtesting and prediction intervals, 73 passing tests. Measured honestly: one-step MAPE 12.8%, equal to a last-price baseline (12.8%). The contribution is delivery, intervals and cross-market comparison, not a claimed accuracy win.

**Feasibility, market, sustainability (30s).** About UGX 135 per 60-second session (verify below), paid by a funder or cooperative so it is free to the farmer. 10.7 million Ugandan households (UBOS NPHC 2024). SDG 1, 2, 8; NDP agro-industrialisation; Vision 2040.

**Close.** The prices exist. Farmers cannot see them. AgriGuard puts them on the phone they already own.

## Judging criteria map

| Criterion | Evidence | Gap to close before 16 Oct |
|---|---|---|
| Originality | Free, USSD-first price comparison plus forecast with intervals, on open data | Name Esoko and Farmgain; say "free, USSD-first", never "first ML forecasting" |
| Technical skill | 73 tests, CI, chronological evaluation, baseline reported | Live Africa's Talking shortcode |
| Presentation | One flow, one crop | Screen recording |
| Content/theme | Measured results, baseline, intervals | None |
| Economic feasibility | Worked example below | Confirm current Uganda rate card |
| Market potential | 10.7M households | Agricultural-household count from UBOS Vol. 1 |
| Sustainability | SDG 1, 2, 8; NDP; Vision 2040 | Cite NDP IV objective wording |

## Unit economics (assumptions, not facts; verify with Africa's Talking before citing)

- Postpaid session: about UGX 45 per 20 seconds (code owner pays), so about UGX 135 for a 60-second session.
- Fixed fees seen on the Africa's Talking pricing page: UGX 1.1M setup, UGX 500k per month. Confirm whether these apply to a shared or dedicated code.
- Example: 1,000 farmers x 4 sessions/month = 4,000 sessions x UGX 135 = UGX 540k, plus UGX 500k fixed, about UGX 1.04M/month, about UGX 1,000 per farmer per month.
- Payers: NGOs, cooperatives, district extension, MAAIF, telco sponsorship.

## Honest answers to likely questions

- **Accuracy?** On par with a last-price baseline for one-step point error (12.8% MAPE both). Value is intervals, cross-market comparison and access. Next: weather features and weekly price sources.
- **Live data?** No. WFP monthly; regular markets last observed April 2026. Every USSD answer shows its date.
- **Real USSD?** A working simulator with Africa's Talking-compatible callbacks. A live shortcode needs an Africa's Talking account (roadmap).
- **Why trust it?** Public WFP source, market and date shown on every answer.
