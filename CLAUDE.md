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
