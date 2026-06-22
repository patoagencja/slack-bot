# Skill inwestycyjny bota „Sebol” — pełny opis techniczny (brief dla ChatGPT)

> **Po co ten dokument:** to kompletny, samowystarczalny opis tzw. „skilla inwestycyjnego” w naszym
> Slack-bocie. ChatGPT **nie ma dostępu do repozytorium**, więc wszystko, co potrzebne do zrozumienia
> logiki i zaproponowania poprawek, jest tutaj.
>
> **Prośba do ChatGPT:** przeczytaj całość, a potem powiedz:
> 1. gdzie są realne bugi / błędy logiczne,
> 2. gdzie scoring/progi inwestycyjne są wątpliwe metodologicznie (z punktu widzenia analizy rynków),
> 3. co uprościć / ujednolicić / zcentralizować,
> 4. jakie są ryzyka operacyjne (API rate-limit, dane stale, brak cache, strefy czasowe).
>
> Sekcja 7 to nasza własna lista podejrzanych miejsc — potwierdź, obal lub rozszerz.

---

## 0. TL;DR

- **„Skill inwestycyjny” to NIE jest jeden plik ani osobny moduł `skills/`** (katalog `skills/` zawiera wyłącznie
  skille marketingowe). To zestaw 6 modułów w `jobs/` + podpięcie (komendy, auto-detekcja, scheduler) w `bot.py`.
- Działa głównie na jednym kanale Slack: **`#inwestowanie` = `C0B5LA4Q064`** (ID zahardkodowane).
- **Stack danych:** `yfinance` (ceny + fundamenty), `Tavily` (newsy/katalizatory), `FRED` (makro USA),
  `CoinGecko` (krypto), `Anthropic Claude` (model `claude-sonnet-4-20250514`) jako warstwa „analityka”.
- **3 sposoby uruchomienia:** (a) slash-komendy, (b) auto-detekcja tickera w treści wiadomości na `#inwestowanie`,
  (c) harmonogram APScheduler (strefa `Europe/Warsaw`).
- **Filozofia analizy** (zaszyta w promptach do Claude, „DNA Rynków”): rewizje EPS > snapshot wyceny;
  siła narracji (AI, nuclear, space, GLP-1, fintech EM); jakość biznesu (przejściowe vs strukturalne);
  insider buying jako mocny sygnał; timing (nie kupować po pompie); zarządzanie ryzykiem (bull + bear case).

---

## 1. Architektura w pigułce

```
                         ┌──────────────────────────────┐
                         │      bot.py (Slack Bolt)       │
                         └──────────────────────────────┘
        ┌────────────────────────┼────────────────────────────┐
        ▼                        ▼                              ▼
 SLASH-KOMENDY          AUTO-DETEKCJA TICKERA            APSCHEDULER (Europe/Warsaw)
 /analiza /swing        na kanale #inwestowanie          • morning_brief  pon–pt 15:00
 /digest /makro         (ticker z WATCHLIST w treści)    • weekly_setups  pt 16:00
 /kapital /narracje                                      • narrative_radar pt 16:30
 /zdrowie /recesja /vix
 /supercykle /cyklicznosc /insider /watchlist
        └────────────────────────┼────────────────────────────┘
                                  ▼
        ┌──────────────────────────── jobs/ (6 modułów) ───────────────────────────┐
        │  stock_digest.py   weekly_setups.py   market_health_monitor.py            │
        │  capital_flow.py   narrative_scanner.py   morning_brief.py                │
        └───────────────────────────────────────────────────────────────────────────┘
                                  ▼
        ┌───────────┬──────────────┬────────────┬───────────────┬──────────────────┐
        ▼           ▼              ▼            ▼               ▼
    yfinance     Tavily          FRED       CoinGecko     Anthropic Claude
   (ceny/fund.) (newsy)        (makro USA)  (krypto)      claude-sonnet-4-20250514
```

**Ważna granica:** Claude w trybie czatu/DM **nie ma** narzędzi inwestycyjnych jako „tools”. Inwestycje
odpalają się wyłącznie przez (a) slash-komendy, (b) auto-detekcję tickera na `#inwestowanie`, (c) scheduler.
Czyli pytanie w DM „co sądzisz o NVDA?” **nie** wywoła analizy — trzeba `/analiza NVDA`.

---

## 2. Źródła danych (i ich ograniczenia)

| Źródło | Co dostarcza | Jak | Pułapki |
|---|---|---|---|
| **yfinance** | ceny OHLCV, 52-tyg high/low, `info` (PE, marże, short %, earnings date), wolumen | `Ticker().history()`, `.info`, `.fast_info`, batch `download()` | brak oficjalnego API → błędy 401/„crumb”, `.info` bywa wolny/wisi, rate-limit Yahoo |
| **Tavily** | newsy, katalizatory, kontekst sektora, makro snippety | `TavilyClient.search(query, max_results)` | **opcjonalne** — bez `TAVILY_API_KEY` `_tavily=None`, ciche pomijanie (brak ostrzeżenia) |
| **FRED** (St. Louis Fed) | makro USA: `INDPRO`, `RSXFS`, `ICSA`, `CPIAUCSL`, `T10Y2Y`, `GDP`, `DGS10` | REST `series/observations`, timeout 10 s | wymaga `FRED_API_KEY`; bez niego zwraca `[]` → filary „brak danych” |
| **CoinGecko** | top 20/100 krypto (cena, zmiana 24h/7d, market cap rank, ATH), BTC dominance, global | publiczne `/coins/markets`, `/global` | brak auth → rate-limit ~10–50/min; przy błędzie sekcja krypto pusta |
| **Anthropic Claude** | synteza/werdykty/scoring jakościowy | `_ctx.claude.messages.create(model="claude-sonnet-4-20250514", ...)` | model **zahardkodowany** wszędzie; brak fallbacku/konfiguracji; parsowanie JSON regexem |

**Cache:** brak trwałego cache API. W obrębie jednego runu cache’owane są: QQQ 30d, dane BTC, kontekst sektora
(per sektor). `capital_flow` i `market_health_monitor` trzymają stan/historię w plikach JSON (`data/`).

---

## 3. Moduły — szczegóły

### 3.1 `jobs/stock_digest.py` — rdzeń (analiza pojedynczego tickera + digesty + makro + krypto + supercykle)

**Najważniejsza funkcja: `analyze_ticker(ticker, qqq_30d=None, btc_data=None) -> dict`**
Krok po kroku:
1. **Kategoria** tickera (`get_category`): `STANDARD_TECH`, `CRYPTO_PROXY`, `URANIUM`, `DEFENSE`,
   `SPACE_DEFENSE`, `BIOTECH_HEALTH`, `EMERGING_MARKETS`, `CONSUMER_DISCRETIONARY`.
2. **Cena + zmiana %** z yfinance (`currentPrice`/`regularMarketPrice`/`ask`); brak ceny → `ValueError`.
3. **ATH / near-ATH**: `near_ath = price >= 0.95 * fiftyTwoWeekHigh` (próg **5%**).
4. **Wskaźniki techniczne** (historia 1Y):
   - **RSI-14** (`_calc_rsi`, period=14) liczone klasycznie (`diff` → gains/losses → RS → 100−100/(1+RS)).
   - **MA50 / MA200** (`_calc_technicals`): `above_ma50/200`, **golden cross** (MA50>MA200 w ostatnich 5 słupkach,
     wcześniej nie), **death cross** (analogicznie), **support 30d** (min z 30 dni) i `% od supportu`.
   - **30-dniowy zwrot** i **RS vs QQQ** = `zwrot_tickera_30d − qqq_30d`.
5. **Short interest** (`shortPercentOfFloat`, normalizacja do %), **dni do earnings** (`_check_earnings_soon`,
   zwraca tylko gdy 0–14 dni).
6. **Fundamenty:** PE, Fwd PE, EV/EBITDA, marża netto, wzrost przychodów; sektor z `TICKER_SECTORS`.
7. **Trendy kwartalne** (`_fetch_quarterly_trends`): rev. deceleration, margin decline, flaga `deteriorating`
   (oba naraz).
8. **Kontekst sektora** (Tavily, cache per sektor) + **capital flow** sektora (`get_ticker_flow` →
   `INFLOW/OUTFLOW/NEUTRAL`, fallback `NEUTRAL` gdy moduł niedostępny).
9. **CRYPTO_PROXY** (np. MSTR/MARA): dorzuca dane BTC (RSI/MA200/bullish), szacunkowe NAV
   (**hardkod: ~214 400 BTC trzymane przez MSTR**, korygowane kontekstem Tavily w prompcie).
10. **Extra sygnały** (`_fetch_extra_signals`, Tavily): guidance, rewizje EPS, insider, katalizatory, cła,
    sprzedaż w USA, convertible debt, kontekst cyklu.
11. **Analiza Claude** (`_claude_analyze`) — patrz niżej.
12. **Deterministyczne korekty score’ów po analizie** (nadpisują/uzupełniają wynik Claude):
    - deteriorating qtrs → `fundamentals_score −= 2`
    - guidance obniżony → werdykt `KUP → CZEKAJ`
    - sektor INFLOW/OUTFLOW → `timing_score ±1`
    - short >15% bez kontekstu convertible → flaga HIGH_SHORT; z kontekstem → traktowane jako delta-hedge
    - insider buy (CEO/CFO + min. 2 „mocne” słowa) → `fundamentals_score += 1`
    - przynależność do supercyklu → `timing_score += 1`
    - margin compression: przy rosnących przychodach = „inwestycja” (neutralne/pozytywne),
      przy braku wzrostu = „strukturalna” (`−1` do fundamentów)
    - catalyst: earnings <14 dni = IMMINENT, 14–60 dni = SWEET_SPOT
    - low liquidity: market cap <2 mld i śr. dzienny obrót <$5 mln → flaga
13. **Reguły werdyktu** (`_apply_verdict_rules`) — deterministyczny silnik nadpisujący werdykt:
    - **OMIJAJ**: `fundamentals ≤ 2`, lub `guidance_cut ∧ deteriorating`, lub `death_cross ∧ sector_outflow ∧ RSI>70`.
    - **CZEKAJ**: gdy `macro_risk == "high"`.
    - **KUP**: `fundamentals ≥ 3 ∧ timing ≥ 3 ∧ ¬(near_ath ∧ RSI>75)`, lub
      `revision_momentum=POSITIVE ∧ narrative_strength=STRONG ∧ timing ≥ 2`.
    - **OBSERWUJ**: dobre fundamenty, ale słaby timing.
    - domyślnie: **CZEKAJ**.

**Integracja z Claude w `analyze_ticker`:**
- Model `claude-sonnet-4-20250514`, `max_tokens=450`.
- **System prompt per kategoria** (8 wariantów) + wspólne „DNA Rynków”.
- Wymuszony **JSON**: `fundamentals_score (1–5)`, `timing_score (1–5)`, `macro_risk (low/medium/high)`,
  `verdict (KUP/CZEKAJ/OMIJAJ/OBSERWUJ)`, `confidence (LOW/MEDIUM/HIGH)`, `reasoning`, `bull_case`, `bear_case`,
  `revision_momentum`, `narrative_strength`, `basket`, `business_quality_intact`.
- Parsowanie: `re.search(r"\{.*\}", raw, re.DOTALL)` → `json.loads`, z `setdefault` na brakujące pola.

**Pozostałe funkcje eksportowane:**
- `send_stock_digest(tickers=None)` — szczegółowe kolorowe karty Block Kit per ticker (batch po 8) → `#inwestowanie`.
- `send_summary_digest()` / `run_summary_digest()` — jeden zbiorczy call do Claude (`max_tokens≈2500`),
  klasyfikacja całej listy w 4 koszyki: 🟢 WARTE UWAGI / 🔵 OBSERWUJ / 🟡 CZEKAJ / 🔴 OMIJAJ; chunkowanie do 3900 znaków.
- `run_stock_digest()` — wariant plain-text.
- `format_ticker_attachment` / `format_ticker_slack` — formatowanie Slack (kolory: KUP `#2eb886`, CZEKAJ `#e6b833`,
  OMIJAJ `#e01e5a`, OBSERWUJ `#1d9bd1`).
- `send_macro_briefing()` / `fetch_macro_briefing()` — 7 zapytań Tavily (Fed, VIX, recesja, geopolityka, flow krypto, DXY)
  → Claude → `sentiment` (RISK-ON/RISK-OFF/NEUTRALNY) + summary + main_risk.
- `send_crypto_digest(limit=20)` — top coiny z CoinGecko + dominacja BTC + makro → analiza per coin.
- `run_supercycle_scan()` / `send_supercycle_scan()` — skan 6 supercykli (HBM/DRAM, Nuclear Renaissance, Defense,
  GLP-1/Obesity, Agentic AI, Power Grid/Energy) → status (WCZESNY/ŚRODKOWY/PÓŹNY/ZAKOŃCZONY) i momentum.
- `run_cyclicality_analysis(ticker)` — gdzie w cyklu jest spółka (werdykt: KUPUJ W DOŁKU / TRZYMAJ / REDUKUJ PRZY SZCZYCIE).
- `run_insider_analysis(ticker)` — jakość transakcji insiderów (MOCNY/SŁABY/BRAK).

**WATCHLIST (71 tickerów, zdefiniowana tu, nie w configu):**
```
SPOT NVDA MSFT META AMZN AMD AVGO CRWD SNOW ADBE
CRM NOW ORCL ANET AXON ISRG MCO TDG MELI APP
MU ASML NKE LULU UBER TTD BABA NVO HOOD RACE
CMG FTNT SNPS PATH RBRK NU SNAP TEM MARA MSTR
ALAB LITE UNH IBM APH NOC CCJ UEC DNN UUUU
SE GRAB TDOC PGY DECK USAR EOSE S DLO RYCEY
SYNA GFS PRM PSIX BA
RKLB ASTS LUNR PL RDW IRDM   ← Space & Defense
```
+ `TICKER_SECTORS` mapuje każdy ticker na ~13 sektorów (AI/Semis, Tech/Cloud, Cybersecurity, Networking,
Social/Ads, E-commerce, AI Apps, Crypto, Consumer, Healthcare, Financial, Defense, Space/Defense, Nuclear/Energy,
Aerospace, Other).

---

### 3.2 `jobs/weekly_setups.py` — skaner swing-setupów (horyzont ~7 dni)

**Cel:** przeskanować S&P 500 + Nasdaq-100 + watchlistę + top 100 krypto i wybrać najlepsze setupy techniczne,
ocenione wielowymiarowo (Minervini, Weinstein, CAN SLIM, VCP, Wyckoff) + ocena Claude.

**Funkcje publiczne:**
- `send_weekly_setups(limit=5, mode="all")` — skan → prescreen → pełna analiza → Claude wybiera TOP N → karty Slack.
  Tryby: `all` (S&P500+NDX+watchlist+krypto), `watchlist`, `scan` (wszyscy przechodzący, bez Claude),
  nazwa sektora (`space`/`nuclear`/`defense`/`ai`/`biotech`/`fintech`/`cyber`/`semis`/`energy`/`consumer`).
- `send_scan_setups(mode)` — jak wyżej, ale bez filtra Claude; sortuje po score.
- `analyze_single_swing(ticker)` — pojedynczy ticker (osobna ścieżka dla krypto z anti-pump >20%/7d).

**Filtry prescreen (`_batch_prescreen`):**
- cena > **$5** (anti-penny),
- **RSI 35–73** (rozszerzony klasyczny 30–70),
- trend: cena > MA50 **lub** maks. **5% poniżej** MA50 (setupy „bounce”),
- **ATH filter**: SKIP jeśli w granicach **5%** od 52-tyg high (ryzyko reversal),
- płynność: śr. dzienny obrót ≥ **$5 mln** (20 dni),
- momentum: **RS vs QQQ ≥ −10%**,
- potencjał: **ATR×3 ≥ 8%**.

**Scoring 0–100 (`_calc_swing_score`):**
- Trend 25 pkt (slope MA50 +8, > MA200 +8, 0–5% nad MA50 +9 / 5–12% +4)
- Momentum 20 pkt (RS vs QQQ: >+10% +12 / >+2% +8 / >−3% +3; vol spike ≥1.5× +8 / ≥1.2× +4)
- RSI 15 pkt (50–65 → +15; 45–50 lub 65–70 → +9; 40–45 → +4)
- Minervini/Weinstein 20 pkt (Minervini 0–7 ×2; Stage 2 +6; VCP +6; Wyckoff Spring +5; AVWAP +3; RS rank ≥80 +5 / ≥70 +2)
- Katalizatory/Narracja 20 pkt (catalyst z Tavily +12; ticker w aktywnej narracji sektorowej +8)

**Modele jakościowe:**
- **Minervini Trend Template** (`_minervini_template`) — 7 kryteriów (cena>MA150/MA200, MA150>MA200, MA200 rosnąca,
  MA50>MA150>MA200, cena>MA50, w 25% od 52-tyg high, ≥30% nad 52-tyg low); ≥6 = premium.
- **Weinstein Stage** (weekly) — Stage 2 (cena>MA30tyg rosnąca, wyższe szczyty/dołki) = jedyny „kupuj”.
- **VCP** — 3 malejące korekty w 60 dni + kontrakcja wolumenu.
- **Wyckoff Spring** — dip pod support na niskim wolumenie i odbicie.
- **AVWAP support** — VWAP zakotwiczony w 52-tyg low; cena w [AVWAP, AVWAP+4%].
- **CAN SLIM** (0–7) i **Setup Grade** (0–28: A+ ≥24, B 18–23, C 12–17, D <12).

**R/R (`_calc_rr`):** z ATR → `stop = price·(1 − ATR%/100·1.5)`, `target = price·(1 + ATR%/100·3.0)` (R:R ~2:1);
fallback bez ATR → stop `price·0.955`, target = max(opór z 20 dni, `price·1.09`).

**Wydajność:** prescreen batchami po 50; pełna analiza w `ThreadPoolExecutor(max_workers=4)` (limit Yahoo),
timeout 20 s/ticker, 120 s pula; Tavily tylko dla top 10 po score; retry 3× z backoffem na błąd 401/„crumb”;
fallback list S&P500/NDX (hardkod) gdy Wikipedia padnie.

**Claude:** `_pick_top_setups` ocenia top 20 kandydatów 1–10 (`max_tokens≈3000`); `analyze_single_swing` robi
deep-dive (`max_tokens≈600`), format „jak do kolegi tradera” (Makro → Setup → Potencjał → Katalizatory → Ryzyka → Werdykt: WCHODZĘ/CZEKAM/OMIJAM).

---

### 3.3 `jobs/market_health_monitor.py` — „zdrowie rynku” + DNA recesji

**Cel:** zebrać ~20 wskaźników (makro / rynkowe / sentyment), zważyć, znormalizować do **0–100** i sklasyfikować
reżim rynku; osobno śledzić **3 filary recesji**; wysyłać alerty przy przekroczeniu progów.

**Wejścia:** `run_market_health()` (główny), `run_zdrowie_command()`, `run_recesja_command()`, `run_vix_command()`,
`evaluate_recession_pillars()`, `get_health_header_text()`. Stan w `data/market_health_state.json`
(ostatnie ~5 wyników, statusy alertów, ostatnie wartości).

**3 filary recesji (FRED):**
- **Produkcja przemysłowa** (INDPRO): ≥3 spadki m/m z rzędu = 🔴; ≥2 = 🟡.
- **Sprzedaż detaliczna** (RSXFS): ≥2 spadki m/m = 🟡 (brak 🔴).
- **Wnioski o zasiłek** (ICSA): >350k **lub** ≥8 tyg. wzrostów = 🔴; >300k **lub** ≥4 = 🟡.
- **Recesja ALERT**: ≥2 filary czerwone.

**Reżim (score → mode):** ≥70 BULL, ≥50 CAUTION, ≥30 DEFENSIVE, <30 BEAR.
**Normalizacja:** surowy ważony wynik z zakresu **−58…+73** liniowo → 0–100 *(do potwierdzenia — patrz sekcja 7)*.

**Wybrane wskaźniki i progi (waga w nawiasie):**
- Jobless claims (1.0): <220k i 0 wzrostów → +5; >350k lub ≥8 wzrostów → −5.
- Krzywa dochodowości T10Y2Y (—): >0.5 i rosnąca +5; >0 +2; >−0.5 → 0; ≤−0.5 −3 (głęboka inwersja).
- ISM PMI (Tavily): >55 +5; ≥50 +3; ≥45 0; <45 −3.
- CPI YoY: ≤3% i spada +4; ≤3% +2; ≤4% 0; >4% −3.
- Fed policy (Tavily, słowa): cut/pause +4; hike −2; emergency −5; inaczej +2.
- GDP QoQ annualizowane: >3% +4; ≥2% +3; ≥1% +1; ≥0% 0; <0% −5.
- **VIX term structure** (1.2, leading): VIX3M−VIX; contango >2 +4 … głęboka backwardation <−5 −6; +SKEW (>160 −4 … <120 +2).
- S&P500 vs MA200 (1.0): >+5% +4; ≥0 +2; >−10% −3; ≤−10% −5.
- Market breadth (1.0): % z 48 dużych spółek > MA50: >70% +4; ≥50% +2; ≥30% 0; <30% −4.
- **Credit spreads** (1.2, leading): proxy HYG/TLT; 2-tyg. rozszerzanie −5.
- Smart money (SPY cena vs wolumen), put/call, sezonowość, rotacja QQQ vs (XLU+GLD), NAAIM, rewizje EPS,
  BofA FMS cash%, Fear&Greed, retail flows, DXY, złoto vs akcje, rentowność 10Y.

**Alerty (`check_and_send_alerts`):** spadek score ≥15 pkt/tydz.; zmiana reżimu; zmiana statusu filaru;
przejście VIX w backwardation; SKEW >160; rozszerzanie spreadów kredytowych; rotacja risk-off; ≥2 czerwone filary.

---

### 3.4 `jobs/capital_flow.py` — rotacja kapitału między sektorami (15 ETF)

**Cel:** śledzić przepływy kapitału przez 15 sektorowych ETF, liczyć streaki napływów/odpływów i syntezować
(Claude) actionable wnioski (z czego wyjść, w co wejść, gdzie nowy kapitał). Cache dzienny + historia 30 dni
(`data/capital_flow_history.json`).

**ETF-y:** XLK (Tech), XLV (Healthcare), XLE (Energia), XLF (Finanse), XLI (Industrials), XLC (Komunikacja),
XLY (Consumer Discr.), XLP (Consumer Staples), XLB (Materiały), XLRE (Real Estate), XLU (Utilities),
ITA (Defense/Aero), ARKK (Innowacje/Growth), GLD (Złoto), USO (Ropa).

**Funkcje:** `build_capital_flow_snapshot(force=False)`, `fetch_sector_etf_performance()` (5-dniowa zmiana %),
`fetch_etf_daily_data()` (1d %, dolarowy wolumen vs 30d), `compute_streaks()` (dni z rzędu w tym samym kierunku +
momentum: „przyspiesza” gdy recent_avg > older_avg·1.2, „zwalnia” < ·0.8), `get_ticker_flow(ticker)`
(mapa `_TICKER_ETF_MAP` ~48 tickerów → ETF → sygnał INFLOW/OUTFLOW/NEUTRAL), `format_capital_flow_block()`,
`send_capital_flow_snapshot()`.

**Claude:** synteza ETF + newsy (7 zapytań Tavily) + top 20 krypto (CoinGecko) → JSON z `sector_signals`
(wszystkie 15 ETF), `rotation_summary`, krypto winners/losers + sentyment, `rotate_from/rotate_to/new_money`.
Model `claude-sonnet-4-20250514`, `max_tokens≈800`, retry 2×, parsowanie regexem JSON.

---

### 3.5 `jobs/narrative_scanner.py` — radar narracji inwestycyjnych

**Cel:** wcześnie wyłapywać narracje (IPO, regulacje, geopolityka, tech inflection, early signals), klasyfikować
stadium (HEATING 🔥 / HOT ♨️ / COOLING ❄️ / COLD 💤) i mapować na beneficjentów z watchlisty.

**Funkcje:** `send_narrative_radar()`, `run_narrative_scan()`, `run_sector_dive(sector)` (deep-dive z aliasami
PL/EN: „kosmiczny”→space, „uran”→nuclear, „obronny”→defense itd.).

**Działanie:** 5 kategorii × 3–4 zapytania Tavily (z rokiem **„2026”** zaszytym w queries) → Claude strukturyzuje do
JSON (model `claude-sonnet-4-20250514`, `max_tokens=1500` dla radaru, 600 dla sector-dive). Mapa
sektor→beneficjenci (np. space: RKLB/ASTS/LUNR/PL/RDW/IRDM; ai: NVDA/APP/TTD/CRWD/NOW/AMD/ALAB/MSFT).
Brak system-promptu (tylko user message).

---

### 3.6 `jobs/morning_brief.py` — agregator (jeden post dziennie)

`send_morning_brief()` skleja w jeden post na `#inwestowanie`: Market Health (score + reżim) + 3 filary +
leading signals (VIX, credit spreads, rotacja QQQ) + capital flow (top/bottom 3 ETF) + makro briefing
(sentyment, BTC, ryzyko) + zbiorczy digest watchlisty (KUP/CZEKAJ/OMIJAJ). Sam nie woła Claude bezpośrednio —
korzysta z gotowych wyników innych modułów. Chunkowanie do 3900 znaków.

---

## 4. Komendy, auto-detekcja, harmonogram, routing (`bot.py`)

**Slash-komendy (wszystkie async w wątku daemon, oprócz `/vix` — synchroniczny):**

| Komenda | Co robi |
|---|---|
| `/analiza TICKER` | pełna analiza tickera (yfinance + Claude) → attachment |
| `/watchlist` | szczegółowe karty per ticker z całej WATCHLIST |
| `/digest` | zbiorczy digest (klasyfikacja KUP/CZEKAJ/OMIJAJ) |
| `/makro` | makro briefing (sentyment, VIX, BTC, ryzyka) |
| `/kapital [refresh]` | snapshot rotacji kapitału (ETF) |
| `/narracje [sektor]` | radar narracji lub deep-dive sektora |
| `/zdrowie` | Market Health Score (20 wskaźników, 0–100) |
| `/recesja` | 3 filary recesji |
| `/vix` | term structure VIX + SKEW (synchronicznie) |
| `/supercykle` | status 6 supercykli + beneficjenci |
| `/cyklicznosc TICKER` | pozycja w cyklu |
| `/insider TICKER` | jakość transakcji insiderów |
| `/swing [TICKER\|N\|scan\|watchlist\|sektor]` | swing setupy |

**Auto-detekcja tickera (tylko na `#inwestowanie = C0B5LA4Q064`):**
- najpierw detekcja intencji „swing” (słowa: „swing”, „setup na”, „zagranie na”, „wejście tygodniowe”…) →
  `analyze_single_swing`;
- potem dopasowanie tickera z `WATCHLIST` do słów wiadomości; stop-słowa wykluczone: `NA, OR, IN, IS, BY, AT`;
- **bierze tylko pierwszy** dopasowany ticker → `analyze_ticker` → odpowiedź w wątku.

**Harmonogram (APScheduler, strefa `Europe/Warsaw`):**
- `send_morning_brief` — **pon–pt 15:00** (id `market_health_daily`; ~13:00 UTC w CEST)
- `send_weekly_setups` — **pt 16:00**
- `send_narrative_radar` — **pt 16:30**
- `send_stock_digest` — **WYŁĄCZONY** (komentarz: „uruchamiać ręcznie przez `/digest`”)

**Konfiguracja / ENV:** `SLACK_STOCK_CHANNEL` (domyślnie `C0B5LA4Q064`), `TAVILY_API_KEY`, `FRED_API_KEY`,
klucz Claude. Kanał inwestycyjny **nie** jest w `config/constants.py` — `_INWESTOWANIE_ID="C0B5LA4Q064"`
zahardkodowany w `bot.py`, a `STOCK_CHANNEL_ID` w jobach (ten sam ID).
**Biblioteki:** `yfinance`, `pandas`, `numpy`, `tavily-python`, `anthropic`, `apscheduler==3.10.4`, `pytz==2024.1`,
`requests`, `slack-bolt==1.18.0`.

---

## 5. Najważniejsze progi (ściąga)

- near-ATH akcje: **5%**; krypto: **10%** (niespójność).
- RSI: „przegrzane” >**75**; prescreen swing **35–73**; analiza ogólna 50–65 optymalne.
- swing: min cena **$5**, min obrót **$5 mln/d**, min potencjał **ATR×3 ≥ 8%**, RS vs QQQ ≥ **−10%**,
  stop **1.5×ATR**, target **3×ATR**, Grade A+ = **24/28**.
- earnings: catalyst <**14 dni** IMMINENT, **14–60 dni** SWEET_SPOT.
- koncentracja sektora w watchliście: ostrzeżenie >**40%**.
- market health reżim: **70/50/30**; recesja alert: **≥2** czerwone filary; alert spadku score: **≥15 pkt/tydz.**
- capital flow streak: istotny przy **≥3 dni**; momentum ±**20%** (×1.2 / ×0.8).
- Claude: model `claude-sonnet-4-20250514` wszędzie; tokeny 450 (ticker) / 800 (flow) / 1500 (narracje) /
  2500 (summary) / 3000 (swing rating) / 600 (deep swing).

---

## 6. Znane problemy / ryzyka / pytania do ChatGPT (priorytet poprawek)

**A. Bugi / kruchość kodu**
1. **Parsowanie JSON z Claude regexem** `\{.*\}` (DOTALL, zachłanne) w wielu miejscach — ryzyko złapania za dużo /
   wywrotki przy dodatkowym tekście. Czy wymusić structured output / tool-use / `response_format`?
2. **Auto-detekcja bierze tylko pierwszy ticker** z wiadomości (`_matched_tickers[0]`) — „NVDA AMD MU” analizuje tylko NVDA.
3. **Stop-słowa tickerów** to mała lista (`NA, OR, IN, IS, BY, AT`) — realne tickery jak `IT`, `ON`, `ALL`, `SO`, `GO`
   mogą fałszywie łapać/być łapane. Lepszy mechanizm (np. `$TICKER` albo słownik znanych słów)?
4. **`yfinance` bez retry w `stock_digest`** (jest w `weekly_setups`), brak trwałego cache → przy skanie wielu
   tickerów dużo wywołań i ryzyko rate-limit/401.
5. **`get_ticker_flow` fallback = `NEUTRAL`** gdy moduł capital_flow niedostępny — cicho zaniża sygnał.
6. **Tavily/FRED ciche degradowanie** (brak ostrzeżenia w outputach) — użytkownik nie wie, że analiza jest „bez newsów”
   lub „bez makro”.

**B. Metodologia inwestycyjna (proszę o krytykę merytoryczną)**
7. **RS vs QQQ** liczone jako prosta różnica 30-dniowych zwrotów — nieznormalizowane do beta/zmienności. Sensowne?
8. **Mieszanie scoringu deterministycznego z oceną LLM** (Claude daje score, potem kod go koryguje ±1/±2,
   a na końcu `_apply_verdict_rules` nadpisuje werdykt). Czy to spójne, czy ryzyko sprzecznych sygnałów?
9. **Market Health: normalizacja −58…+73 → 0–100** i wagi wskaźników — czy progi reżimu (70/50/30) i wagi
   (np. VIX/credit 1.2) są uzasadnione? Czy nie ma podwójnego liczenia (jobless claims są i w filarach, i w score)?
10. **Hardkod ~214 400 BTC dla MSTR** w wycenie NAV — szybko się starzeje (poleganie na korekcie przez Tavily ryzykowne).
11. **Supercykl = płaskie `+1` do timing** niezależnie od fazy/siły — zbyt grube?
12. **Wykrywanie cyklu i „recovery/peak” przez dopasowanie słów kluczowych** w tekście — podatne na błędy semantyczne.

**C. Operacyjne / architektura**
13. **Model Claude zahardkodowany** w każdym pliku (`claude-sonnet-4-20250514`), brak centralnej konfiguracji
    ani fallbacku — sugerowana centralizacja + ewentualny upgrade modelu.
14. **ID kanału zahardkodowany** w kilku miejscach zamiast w `config/constants.py`.
15. **Strefa czasowa:** scheduler w `Europe/Warsaw`, dane rynku w US ET — `morning_brief` 15:00 CEST leci jeszcze
    przed otwarciem rynku USA (15:30 CEST). Czy to celowe (przed-sesyjny brief), czy bug w timingu?
16. **`weekly_setups`: ThreadPool timeout 20 s/ticker** — wolne tickery są po cichu pomijane (można tracić setupy).
17. **Stary rok w zapytaniach Tavily** (`narrative_scanner` ma „2026” na sztywno w treści zapytań) — wymaga aktualizacji.
18. **Historia DM do Claude `limit=500`** bez truncation — duży payload (to poza skillem inwestycyjnym, ale wpływa na koszt).

> **Pytanie zbiorcze do ChatGPT:** które z powyższych to realne bugi do natychmiastowej naprawy, a które to
> świadome kompromisy? Zaproponuj konkretne poprawki (z priorytetami) — zwłaszcza dla punktów 1, 2, 8, 9 i 15.
