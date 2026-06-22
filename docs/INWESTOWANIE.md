# 📈 Sebol — Instrukcja funkcji inwestycyjnych

Bot wspiera decyzje inwestycyjne na kanale **#inwestowanie**. Główna zasada nowej
architektury: **bot pomaga zbudować transakcję, a nie tylko opisać spółkę.**

> ⚠️ To wsparcie decyzji, **nie** rekomendacja inwestycyjna. Ostateczną decyzję podejmujesz Ty.

---

## ⭐ `/wejscie` — Plan wejścia w pozycję (NOWE, główne narzędzie)

Buduje **konkretny, mierzalny i weryfikowalny plan transakcji**: wejście, stop, targety,
R/R i wielkość pozycji. Horyzont **20–90 sesji**. Decyzja jest **deterministyczna** —
liczy ją kod, nie „opinia” modelu.

**Składnia:**
```
/wejscie TICKER [KWOTA] [risk=0.5]
```
- `TICKER` — np. `NVDA` (można `$NVDA`); do **3 tickerów** naraz
- `KWOTA` — wartość portfela w USD (np. `50000`)
- `risk=` — % portfela ryzykowany na transakcję (np. `0.5` = 0,5%)
- brak `KWOTA`/`risk` → wartości domyślne (patrz niżej)

**Przykłady:**
```
/wejscie NVDA
/wejscie NVDA 50000 risk=0.5
/wejscie $NVDA $AMD $MU
```

### Pierwsza linia = werdykt
| Status | Znaczenie |
|---|---|
| 🟢 `READY TO ENTER` | Cena w strefie wejścia, R/R i ryzyko portfela OK — można działać |
| 🟡 `WAIT FOR TRIGGER` | Dobry setup, ale czekaj na trigger / lepsze wejście / po wydarzeniu |
| 🔴 `NO TRADE` | Brak ważnego setupu albo przekroczone limity ryzyka portfela |
| ⚪ `DATA INCOMPLETE` | Brak/nieaktualne kluczowe dane — bot **nie zgaduje** |

### Co zawiera odpowiedź
- **Strategia + setup + horyzont** — typ setupu (BREAKOUT / PULLBACK / BASE / MEAN_REVERSION / WYCKOFF / EVENT_DRIVEN)
- **Strefa wejścia / trigger / max chase** — gdzie wchodzić i do jakiej ceny *nie gonić*
- **Stop techniczny + unieważnienie tezy + ryzyko/akcję** (ze slippage)
- **Targety T1/T2/T3 z R/R** dla każdego
- **Sizing** — ile sztuk, wartość pozycji, % portfela, budżet ryzyka
- **Event risk** — earnings/wydarzenia binarne i plan (HOLD / REDUCE / EXIT / EVENT_STRATEGY)
- **Reżim rynku + wpływ makro** (per sektor) + **rotacja sektora**
- **Wpływ na portfel** — ekspozycja sektora, heat, ostrzeżenie o korelacji
- **Jakość danych** + pewność sygnału + czego brakuje
- **Bull / bear case** i **warunki anulowania** (jakościowo, z LLM)

Odpowiedź jest widoczna **dla Ciebie** w miejscu wywołania (ephemeral) — możesz odpalać
gdziekolwiek bot jest obecny.

---

## 🔎 Analiza pojedynczej spółki

| Komenda | Co robi |
|---|---|
| `/analiza TICKER` | Pełna karta analizy spółki (fundamenty + technikalia + werdykt) |
| `/cyklicznosc TICKER` | Pozycja w cyklu (dla spółek cyklicznych: półprzewodniki, uran, lotnictwo) |
| `/insider TICKER` | Jakość transakcji insiderów (zakupy vs przyznania, funkcja, cluster) |
| `/swing TICKER` | Setup pod ~7-dniowy swing (osobna, krótkoterminowa strategia) |

Przykłady: `/analiza NVDA` · `/cyklicznosc MU` · `/insider MSTR` · `/swing PLTR`

---

## 🎯 Skanery i wyszukiwanie zagrań

| Komenda | Co robi |
|---|---|
| `/swing` | Skan S&P500 + NDX + krypto → TOP 5 swingów na #inwestowanie (~5 min) |
| `/swing N` | Jak wyżej, ale TOP N (np. `/swing 10`, max 15) |
| `/swing scan` | Wszyscy kandydaci bez filtra (~5 min) |
| `/swing watchlist` | Tylko watchlista → TOP 5 (~2 min) |
| `/swing {sektor}` | Skan sektora: `space`, `nuclear`, `defense`, `ai`, `biotech`, `fintech`, `cyber`, `semis`, `energy`, `consumer` |
| `/supercykle` | Aktywne supercykle i ich beneficjenci (~2 min) |
| `/narracje` | Narrative Radar — gdzie buduje się momentum narracyjne (~2 min) |
| `/narracje {sektor}` | Głębsze zanurzenie w narrację sektora (~1 min) |

---

## 🩺 Stan rynku i makro

| Komenda | Co robi |
|---|---|
| `/zdrowie` | Pełny Market Health Score — 20 wskaźników + reżim rynku (~3 min) |
| `/recesja` | 3 filary recesji + trend |
| `/vix` | Aktualny VIX z interpretacją (szybkie, inline) |
| `/makro` | Briefing makro: sentyment, VIX, BTC, główne ryzyka → #inwestowanie |
| `/kapital` | Rotacja sektorowa — gdzie płynie kapitał (~2 min); `/kapital refresh` wymusza świeże dane |

---

## 🗞️ Digesty zbiorcze

| Komenda | Co robi |
|---|---|
| `/digest` | Skrótowy digest całej watchlisty → #inwestowanie (~2 min) |
| `/watchlist` | Szczegółowy digest z kartami per ticker → #inwestowanie (kilka min) |

---

## 💬 Auto-detekcja w wiadomościach

Na kanale **#inwestowanie** wpisanie tickera z watchlisty w zwykłej wiadomości
uruchamia automatyczną analizę. (Wkrótce: `$TICKER`, do 3 naraz, pod silnik `/wejscie` —
jeśli chcesz, mogę to podpiąć.)

---

## ⚙️ Wartości domyślne i konfiguracja

- **Domyślny portfel:** 100 000 USD · **domyślne ryzyko:** 0,5% na transakcję
  (zmienne env: `INVEST_DEFAULT_PORTFOLIO`, `INVEST_DEFAULT_RISK_PCT`)
- **Limity ryzyka portfela:** pozycja ≤10%, sektor ≤30%, narracja ≤35%, portfolio heat ≤6%
- **Max chase:** pivot + 0,75 ATR (nie gonimy wybicia wyżej)
- **Model Claude:** z konfiguracji (`CLAUDE_MODEL_PRIMARY`, domyślnie aktualny Sonnet)
- **`FRED_API_KEY`** (env) — włącza credit spready (OAS) i pełny reżim rynku; bez niego reżim = `UNKNOWN`
- **Spółki-proxy na aktywa (MSTR itp.):** dane o aktywach z `data/asset_proxies.json`
  (uzupełniaj z oficjalnych filingów — każda wartość ma datę i źródło). Brak danych ⇒ `DATA INCOMPLETE`, nie zmyślona liczba.

---

## 🧠 Najważniejsze zasady (czym różni się `/wejscie`)

1. **Decyzja deterministyczna i audytowalna** — cenę, stop, score, ilość i status liczy kod.
   LLM dodaje tylko ocenę jakościową (bull/bear/katalizatory), nigdy liczb ani werdyktu.
2. **Brak danych ≠ wartość neutralna** — każda dana ma źródło, datę i status; brak ceny/earnings/
   danych strategii ⇒ brak `READY TO ENTER`.
3. **Setup ma własne reguły** — trigger, stop, targety i warunki anulowania. RSI to jedna cecha,
   nie wyrocznia.
4. **Stop z unieważnienia setupu**, nie z samego mnożnika ATR (ATR = bufor).
5. **Wydarzenia binarne** (earnings, FDA) blokują pełne wejście bez jawnego planu.
6. **Makro** zmienia wymagane R/R i wielkość pozycji — nie blokuje wszystkiego naraz.
7. Każda rekomendacja jest **zapisywana** (SQLite) z pełnym snapshotem cech → później liczymy
   skuteczność (MFE/MAE/R, trafienia stop/target po 5/10/20/40/60 sesjach).

---

## ❓ Szybkie FAQ

- **„DATA INCOMPLETE" mimo że spółka znana?** → brak świeżej ceny/słupków/daty earnings z dostawcy.
  Spróbuj ponownie lub sprawdź dostępność danych.
- **„NO TRADE" choć spółka dobra?** → albo brak ważnego setupu *teraz*, albo wejście łamałoby
  limity portfela. Świetna spółka ≠ dobre wejście dzisiaj.
- **„WAIT FOR TRIGGER"?** → setup jest, ale czekamy aż cena dotrze do strefy/triggera lub minie wydarzenie.
- **Ile tickerów naraz?** → max 3 (`/wejscie $NVDA $AMD $MU`); nadmiar bot zgłosi, nie pominie po cichu.
