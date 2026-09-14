# Phase 0 — External Feasibility Checklist (spec Appendix A)

**Do this before enabling REAL execution (M9). Cheap to verify, expensive to get wrong.**
This gate exists because "buildable" ≠ "permitted" ≠ "profitable".

## Webull OpenAPI
Docs: https://developer.webull.com/apis/docs/about-open-api/

- [ ] OpenAPI application approved; App Key + App Secret issued
- [ ] **ToS permits automated / algorithmic trading** (confirm in writing — many retail brokers restrict this)
- [ ] Production trading account linked and funded appropriately for small first orders
- [ ] Sandbox environment available and reachable
- [ ] Account API (balance, buying power) — https://developer.webull.com/apis/docs/trade-api/account/
- [ ] Positions API
- [ ] Order API: preview, place, cancel, replace — https://developer.webull.com/apis/docs/trade-api/stock/
- [ ] Order status / order events (MQTT subscription) — https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes/
- [ ] Market data (real-time) subscription tier enabled
- [ ] Historical data — https://developer.webull.com/apis/docs/market-data-api/data-api/
- [ ] Options access enabled (only if options strategies will be used)
- [ ] **Rate limits understood and documented** — https://developer.webull.com/apis/docs/rate-limits/
- [ ] Official Python SDK installs & authenticates: `pip install webull-openapi-python-sdk`
      (repo: https://github.com/webull-inc/openapi-python-sdk)

## Claude (AI provider)
- [x] Method chosen: **Anthropic API key (pay-per-token, Commercial Terms)** — sanctioned for
      always-on automation, no ban risk. Do NOT use a Claude.ai subscription / OAuth for this
      unattended service (risks account action).
- [ ] `ANTHROPIC_API_KEY` provisioned with a spend limit set
- [ ] Non-interactive/headless calls verified from the container
- [ ] Structured JSON output (tool-use) verified against the decision contract
- [ ] Token/cost budget + usage monitoring in place (spec §60)

## Sign-off
- [ ] All Webull boxes checked against **current** official docs (they change)
- [ ] Rate limits + costs acceptable for the intended decision cadence
- [ ] Only then: set `APM_EXECUTION_MODE=REAL` + `APM_TRADING_ENABLED=true` and place one
      minimal real order end-to-end (M9).
