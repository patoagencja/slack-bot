# Claude Code — Instrukcje dla tego repozytorium

## Auto-monitoring błędów produkcyjnych

**Na początku każdej sesji** i **po każdym pushu** uruchom:

```bash
python3 scripts/check_render_logs.py --minutes 15
```

- Exit 0 → brak błędów, kontynuuj normalnie
- Exit 1 → znaleziono błędy w kanale Slack `#errors` → **przeanalizuj, napraw, commituj, pushuj** na branch `claude/push-changes-H9Q0z`
- Exit 2 → problem z siecią / tokenem (zignoruj jeśli brak internetu w sandbox)

## Branch roboczy

Zawsze pushuj na: `claude/push-changes-H9Q0z`

```bash
git push -u origin claude/push-changes-H9Q0z
```

## Stack

- **Bot**: Slack Bolt (Socket Mode) + Anthropic Claude API
- **Deploy**: Render (worker, `Procfile`)
- **Ads**: Google Ads API v19 (google-ads 29.x), Meta Ads API v19
- **State**: `_ctx.py` — shared in-memory state, persisted to `data/*.json`

## Struktura kluczowych plików

| Plik | Rola |
|------|------|
| `bot.py` | Główny handler — wszystkie eventy Slack, komendy, wizardy kampanii |
| `_ctx.py` | Shared state (wizard states, conversation history, bot_threads) |
| `tools/google_ads.py` | Google Ads API — tworzenie kampanii |
| `tools/meta_ads.py` | Meta Ads API — pobieranie danych |
| `jobs/performance_analysis.py` | Analiza kampanii, backfill historii |
| `config/constants.py` | Channel IDs, team config, AD_CLIENTS |

## Znane pułapki

- `_google_wizard_post()` — używa Block Kit; sekcje max 3000 znaków, przyciski max 75 znaków
- `contains_eu_political_advertising` — musi być enum, nie `False`
- `budget.explicitly_shared = False` — wymagane przy tworzeniu kampanii
- `meta_ads_tool` — daty zawsze w formacie `YYYY-MM-DD`; nie modyfikuj roku

## Jak naprawiać błędy z logów

1. Skopiuj pełny stack trace z wyjścia skryptu
2. Znajdź plik i linię (`grep -n "FunctionName" *.py`)
3. Przeczytaj kontekst przed edycją
4. Napraw minimal — nie refaktoryzuj przy okazji
5. `git add <plik> && git commit -m "fix: <opis>" && git push -u origin claude/push-changes-H9Q0z`

## Auto-naprawa błędów

Gdy napotkasz jakikolwiek błąd podczas pracy (runtime error, import error, syntax error, test failure, deployment error):
1. Przeanalizuj błąd
2. Napraw go samodzielnie bez pytania
3. Uruchom kod ponownie żeby zweryfikować że działa
4. Jeśli po 2 próbach nadal nie działa – dopiero wtedy zapytaj mnie

Nie pytaj mnie o pozwolenie na naprawę błędów. Po prostu je naprawiaj i raportuj co naprawiłeś.

## Skill inwestycyjny — nowa architektura (pakiet `investing/`)

Główne zadanie: budować **konkretny plan wejścia** (PositionPlan), a nie opisywać spółkę.
Horyzont nowej strategii: 20–90 sesji. Stary skaner ~7-dniowych swingów (`jobs/weekly_setups.py`) zostaje jako osobna strategia.

Komenda Slack: `/wejscie TICKER [KWOTA] [risk=0.5]` (np. `/wejscie NVDA 50000 risk=0.5`).
`risk` jest w procentach portfela; brak kwoty/risk → wartości domyślne z `investing/config.py`.

Statusy decyzji (nigdy ogólne „KUP”): `READY_TO_ENTER`, `WAIT_FOR_TRIGGER`, `NO_TRADE`, `DATA_INCOMPLETE`.

Zasady architektury:
- **Decyzja jest deterministyczna i audytowalna** (`investing/decision.py`). LLM (`investing/llm.py`)
  zwraca WYŁĄCZNIE ocenę jakościową (bull/bear/katalizatory) przez strict tool use + walidację Pydantic
  + jeden retry naprawczy, inaczej `LLM_SCHEMA_ERROR`. LLM nigdy nie ustala ceny/stopa/score/ilości/statusu.
- **Model Claude** tylko z `investing/config.py` (`CLAUDE_MODEL_PRIMARY/FALLBACK/FAST`) — żadnych
  zahardkodowanych stringów modelu.
- **Data Quality Gate** (`investing/data_quality.py`): każda wartość ma source/as_of/fetched_at/
  age_seconds/status. Brak danych = sentinel (NIE neutralna liczba). Brak ceny/earnings/danych
  wymaganych przez strategię ⇒ brak `READY_TO_ENTER`.
- **Setup classifier** (`investing/setups.py`): BREAKOUT / PULLBACK_CONTINUATION / BASE_BUILDING /
  MEAN_REVERSION / WYCKOFF_REVERSAL / EVENT_DRIVEN / NO_VALID_SETUP — każdy z własnym triggerem,
  stopem, targetami i warunkami anulowania. RSI to tylko jedna cecha rozszerzenia (nigdy sam nie daje
  NO_TRADE/WAIT).
- **Sizing** (`investing/sizing.py`): stop z unieważnienia setupu; ATR jako bufor. Limity: risk budget,
  position cap, liquidity.
- **Event/Portfolio/Market** (`event_risk.py`, `portfolio.py`, `market_health.py`): wydarzenia binarne
  blokują pełne wejście; limity koncentracji/heat; reżim z percentyla/z-score (nie z zakresu −58…+73);
  makro zmienia wymagane R/R i size, nie blokuje wszystkiego.
- **Dane** przez `investing/gateway.py` (retry/backoff/jitter/timeout/circuit breaker/TTL cache/fallback).
  Credit spready z FRED OAS (nie HYG/TLT). Brak zahardkodowanego roku — zapytania używają bieżącej daty.
- **Asset-proxy NAV** (`investing/providers/asset_proxy.py`): liczba BTC/aktywów MSTR itp. pochodzi z
  **datowanego rejestru** `data/asset_proxies.json` (uzupełniaj z filingów), nie z kodu.
- **Stan w SQLite** (`investing/persistence.py`, `data/investing.db`): signals, position_plans, positions,
  recommendation_outcomes, market_health_history, api_cache, job_runs, data_quality_events. Każda
  rekomendacja zapisuje pełny snapshot cech (reprodukowalność/backtest — `investing/backtest.py`).
- **Scheduler**: brief przed sesją USA = XNYS open − 45 min (`investing/market_calendar.py`, strefa
  America/New_York, święta pomijane), joby z `max_instances=1, coalesce=True, misfire_grace_time`.

Testy nowej architektury: `python3 -m pytest tests/test_investing_*.py -q`
(wymaga `pydantic` i `pytest`; deterministyczny rdzeń nie potrzebuje pandas/yfinance).
