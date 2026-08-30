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
- `backend/`: FastAPI app, Odds API integration, ranking/filtering logic, cache layer.
- `frontend/`: static HTML/CSS/JS client that calls backend JSON endpoints.

Data flow:
1. Frontend requests `/api/picks` or `/api/picks/game`.
2. Backend fetches upstream odds data (or demo fallback when no key).
3. Service computes implied probability + edge, applies thresholds/fallbacks, ranks lines.
4. Response returns grouped games + metadata, and frontend renders league/game cards.

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

uvicorn main:app --reload
```

Then open `http://localhost:8000`.

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

## Testing / Error Handling 
Automated backend test suite (pytest). Install dev dependencies once, then run tests from
`backend/` — that directory contains `pytest.ini` (`asyncio_mode`, `pythonpath`, etc.). Running
bare `pytest` from the repo root skips that config and async tests will fail.

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

Run a single file or test:

```bash
cd backend
python -m pytest tests/test_cors.py
python -m pytest tests/test_cors.py::TestGetCorsOrigins::test_returns_production_origin_when_env_is_set -v
```

Optional coverage report (also from `backend/`):

```bash
python -m pytest --cov=app --cov=services --cov-report=term-missing
```

From the repo root, pass the backend config explicitly:

```bash
python -m pytest -c backend/pytest.ini backend/tests
```

The suite is deterministic and CI-safe: it needs no Odds API key and makes no external network
calls. The Odds API is mocked at the HTTP boundary (`httpx.MockTransport`), the current time is
pinned for any test that depends on slate windows, and an autouse fixture fails any test that
tries to reach the internet. It runs on every push and pull request via
`.github/workflows/backend-tests.yml`.

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
- `test_suite_guardrails.py` - proves the suite itself cannot use a real key or real network.

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
