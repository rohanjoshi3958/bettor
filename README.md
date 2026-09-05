# Project Title
Bettor - Daily Line Picks

## Problem Statement
Sports bettors often compare lines across books manually, which is slow and error-prone, especially when props and odds change throughout the day. The people most affected are casual-to-serious bettors who want a quick daily shortlist without scanning every book and market by hand.

This matters because delayed comparisons lead to missed value and inconsistent decisions. If solved, users can open one view and immediately see the best available lines per game. Success looks like fast, repeatable daily workflows: load date -> open league -> open game -> review top picks with clear implied-probability and edge context.

## Solution Overview
Bettor is a FastAPI + static frontend app that fetches odds, computes implied probability and line-shopping edge, ranks candidates, and surfaces the top lines per game. The UI is organized by league and game, with refresh controls for slate-level or game-level updates.

Key features:
- League and game drill-down with top 3 ranked lines per game.
- Implied-probability floor with fallback logic to avoid empty slates.
- Lightweight caching to reduce repeated upstream calls.
- Health endpoint and warning propagation for operational visibility.

AI is supplementary in this version. The core runtime product logic (odds retrieval, ranking, filtering, caching, API responses) is deterministic application code. AI was primarily used during development to accelerate implementation and iteration.

## AI Integration
Development used AI coding assistance in Cursor to speed up architecture updates, deployment fixes, and iterative feature changes (for example, threshold tuning and hosting adjustments). The implementation did not add an end-user LLM feature to the app runtime.

Patterns used in the build process:
- Multi-step reasoning for debugging deployment/runtime path issues.
- Tool-assisted code search and targeted edits across backend/frontend files.
- Iterative refinement loops for product copy and threshold logic.

Tradeoffs considered:
- Cost/latency/reliability favored deterministic backend logic over an online LLM dependency in production.
- AI-assisted coding improved speed, but correctness still required manual verification (especially env/deploy behavior and path resolution).

AI exceeded expectations in rapid refactors and issue triage, and fell short when deployment platform settings required exact environment-specific configuration that still needed human confirmation.

## Architecture / Design Decisions
Backend/frontend structure:
- `backend/`: FastAPI app, Odds API integration, cache layer, and a dependency-free ranking engine (`services/ranking.py`).
- `frontend/`: static HTML/CSS/JS client that calls backend JSON endpoints.

Data flow:
1. Frontend requests `/api/picks` or `/api/picks/game`.
2. Backend fetches upstream odds data (or demo fallback when no key).
3. Service computes implied probability + edge, then the ranking engine applies its documented thresholds/fallbacks and ranks lines.
4. Response returns grouped games + metadata, and frontend renders league/game cards.

### Odds service module structure

The odds service (`backend/services/odds/`) is split into focused modules. Each layer only imports from layers below it; there are no upward or cross-layer dependencies.

```
API layer  (app/api/picks.py)
    │
    ▼
service.py          ← orchestration: demo vs live branching, client fan-out, response assembly
    │               also re-exports all public symbols for backward compatibility
    ├── client.py           ← HTTP requests to The Odds API, quota tracking, retry, warnings
    │       ├── parser.py           ← convert raw API payloads into BetPick domain objects
    │       │       ├── normalization.py    ← event-ID normalise, time utils, shell/merge helpers
    │       │       │       └── models.py   ← BetPick dataclass, shared constants
    │       │       └── sports.py           ← sport keys, markets, display titles, get_api_key
    │       └── normalization.py
    ├── ranking.py          ← adapts BetPick rows to services/ranking.py; group/JSON/fallback
    │       ├── normalization.py
    │       └── services/ranking.py   ← dependency-free score + floors (configurable)
    ├── demo.py             ← static fallback picks (no HTTP)
    │       ├── normalization.py
    │       └── sports.py
    └── normalization.py    ← also wraps services/ranking.py for implied_probability
```

Dependency rules enforced by `tests/test_module_boundaries.py`:
- **models** — no HTTP/I/O; may read floors from `services.ranking`
- **sports** — no internal odds imports; sport/provider config + env-key resolution only
- **normalization** — no HTTP; no `services.odds.ranking` / parser / client
- **parser** — imports `models`, `normalization`, `sports`; no HTTP
- **ranking** — imports `models`, `normalization`, and `services.ranking`; no HTTP, no parser
- **demo** — imports `models`, `normalization`, `sports`; no HTTP
- **client** — imports `models`, `normalization`, `parser`; no ranking, no demo
- **service** — imports all of the above; owns orchestration and public re-exports

Key design choices:
- In-memory short-TTL cache (`picks_cache.py`) to reduce redundant API fan-out.
- Per-request metadata (source, fallback used, warning state) for transparency.
- API base indirection in frontend config to support split hosting (Pages + hosted API).

Tradeoffs/assumptions:
- In-memory cache is simple and fast but not shared across multiple instances.
- Odds API quota and response volatility require graceful fallback and warning messaging.
- Public static hosting alone is insufficient; live data requires a hosted backend with server-side API key.

## What did AI help you do faster, and where did it get in your way?
AI coding tools helped accelerate:
- Endpoint and service refactors.
- Deployment configuration scaffolding (Docker + host-specific setup).
- UI copy updates and consistency changes.
- Root-cause analysis from logs and stack traces.

Limitations encountered:
- Deployment platforms have strict/path-sensitive settings that AI suggestions still needed manual adaptation.
- Environment variable and host wiring required careful human verification.
- Final correctness still depended on testing real runtime behavior, not just code diffs.

Using AI changed the build approach by making iteration cycles shorter: generate -> validate -> correct -> redeploy, with human review as the quality gate.

## Getting Started / Setup Instructions
```bash
git clone <repo-url>
cd bettor/backend
pip install -r requirements.txt

# Set up environment variables (create backend/.env manually if missing)
# Add your key:
# THE_ODDS_API_KEY=your_key_here
#
# Optional logging (see "Application logging" below):
# LOG_LEVEL=INFO
# LOG_FORMAT=text

uvicorn main:app --reload
```

Then open `http://localhost:8000`.

### Application logging

The backend emits structured application logs (stdlib `logging`, no extra dependencies).

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, …) |
| `LOG_FORMAT` | `text` locally; `json` on Render / `ENVIRONMENT=production` | Line format |

JSON mode writes one JSON object per line with `timestamp`, `level`, `logger`, `message`, plus structured fields (`request_id`, `path`, `status_code`, `sport_key`, cache outcomes, upstream errors, …). Secrets matching `api_key` / `token` / `password` / `authorization` are redacted.

Every HTTP response (except `/assets/*`) gets an `X-Request-ID` header. Pass `X-Request-ID` on the request to correlate client and server logs.

Operational events are logged from:
- `bettor.http` — request completion (method, path, status, duration)
- `bettor.cache` — picks cache hit / miss / bypass
- `bettor.odds.client` — Odds API HTTP / transport / timeout / invalid-JSON failures (never logs the API key)
- `bettor.odds.service` — slate / single-game fetch outcomes (source, counts, warning flags)

## Demo
How to use:
1. Open the app homepage.
2. Pick a date (today/future pickable day).
3. Expand a league card to view games.
4. Expand a game card to view the top ranked lines.
5. Use **Update odds** on a game for a targeted refresh.
6. Use **Reload slate** to refetch the full day.

Useful API checks:
- `GET /api/health` - verifies service up and whether live odds key is configured.
- `GET /api/picks?date=YYYY-MM-DD&timezone=America/New_York`
- `GET /api/picks/game?sport_key=basketball_nba&event_id=<id>&date=YYYY-MM-DD&timezone=America/New_York`

## CI Quality Gates

Every push and pull request runs the full backend quality gate via
`.github/workflows/backend-tests.yml`. The workflow runs on Python 3.11 and 3.12, requires no
Odds API key, and makes no external network calls.

### Local equivalents

Install dev dependencies once, then run all three checks from `backend/`:

```bash
cd backend
pip install -r requirements-dev.txt
```

**Lint (ruff)**

```bash
ruff check app/ services/
```

**Type check (mypy)**

```bash
mypy app/ services/
```

**Tests (pytest)**

```bash
python -m pytest
```

Run a single file or test:

```bash
python -m pytest tests/test_cors.py
python -m pytest tests/test_cors.py::TestGetCorsOrigins::test_returns_production_origin_when_env_is_set -v
```

Optional coverage report:

```bash
python -m pytest --cov=app --cov=services --cov-report=term-missing
```

From the repo root, pass the backend config explicitly:

```bash
python -m pytest -c backend/pytest.ini backend/tests
```

### What the checks cover

- **ruff** — import order, style, modernization (`E`, `F`, `I`, `UP`, `W` rules; configured in
  `backend/pyproject.toml`).
- **mypy** — static type checking scoped to `app/` and `services/`; configuration in
  `backend/pyproject.toml`.
- **pytest** — full deterministic test suite. The Odds API is mocked at the HTTP boundary
  (`httpx.MockTransport`), the current time is pinned for slate-window tests, and an autouse
  fixture fails any test that reaches the real network.

Coverage by area (`backend/tests/`):
- `test_normalization.py` - event-id normalization, implied probability, timestamp parsing, prop labels, API-key resolution.
- `test_ranking.py` - rank score blend, per-game top-N selection, implied-probability floor and its relaxed fallback, JSON contract.
- `test_price_collection.py` - bookmaker allowlist, line shopping/edge math, malformed bookmaker payloads.
- `test_grouping.py` - schedule shells, merging picks onto scheduled games, kickoff ordering, started-game exclusion.
- `test_time_windows.py` - local day bounds (including DST transitions), timezone-dependent slate filtering, pickable-day window.
- `test_picks_cache.py` - hit/miss, TTL expiry and bypass, copy-on-read isolation, warning results not cached, per-key locking under concurrent requests.
- `test_odds_upstream.py` - upstream request shape, 429/402/5xx handling, retry behavior, undecodable bodies, prop fan-out caps.
- `test_fetch_slate.py` / `test_fetch_event.py` - demo/live source selection, partial-failure degradation, warning propagation.
- `test_api_picks.py` - `/api/health`, `/api/picks`, `/api/picks/game`: response envelopes, validation errors, cache headers, concurrent-request de-duplication.
- `test_logging.py` - JSON/text formatters, secret redaction, `LOG_*` env defaults, request-id middleware and request completion logs.
- `test_suite_guardrails.py` - proves the suite itself cannot use a real key or real network.
- `test_module_boundaries.py` - verifies the odds service module split: each layer's isolation, dependency graph (no upward/cross-layer imports), and that every public symbol is re-exported from `service.py`.

Manual checks still used alongside the suite:
- Endpoint validation with different dates/timezones and league selections.
- Empty-state/fallback behavior when thresholds are strict.
- Cache hit/miss/bypass behavior via the `X-Picks-Cache` response header.

Error handling implemented:
- Input validation for malformed dates and unsupported sport keys.
- Graceful warnings when upstream Odds API partial failures/quota pressure occur.
- Demo/live source signaling for visibility when API key is absent.
- Deployment-safe static path handling for local and containerized environments.

## Link to website URL or application 
- Website: bettor.studio
