"use client";
import dynamic from "next/dynamic";
import { useState, useEffect, useRef } from "react";

const SebolGalaxy = dynamic(() => import("../components/SebolGalaxy"), { ssr: false });

type CardStatus = "done" | "wip" | "gap" | "nice";

interface CardData {
  status: CardStatus;
  name: string;
  sub: string;
  detail: string;
}

function StatusCard({ status, name, sub, detail }: CardData) {
  const [open, setOpen] = useState(false);
  const dotColor = { done: "#22c55e", wip: "#f59e0b", gap: "#ef4444", nice: "#3b82f6" }[status];
  const badgeStyle = {
    done: { color: "#22c55e", background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.2)" },
    wip:  { color: "#f59e0b", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)" },
    gap:  { color: "#ef4444", background: "rgba(239,68,68,0.08)",  border: "1px solid rgba(239,68,68,0.2)" },
    nice: { color: "#3b82f6", background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.2)" },
  }[status];
  const badgeLabel = { done: "działa", wip: "częściowe", gap: "brak", nice: "planowane" }[status];

  return (
    <div
      onClick={() => setOpen(o => !o)}
      style={{ background:"var(--bg2)", border:"1px solid var(--border)", borderRadius:10, overflow:"hidden", cursor:"pointer" }}
    >
      <div style={{ display:"flex", alignItems:"center", gap:10, padding:"13px 14px 11px" }}>
        <div style={{ width:8, height:8, borderRadius:"50%", background:dotColor, flexShrink:0 }} />
        <div style={{ fontSize:13, fontWeight:500, flex:1, lineHeight:1.3, color:"var(--text)" }}>{name}</div>
        <span style={{ fontFamily:"var(--mono)", fontSize:10, fontWeight:500, padding:"2px 8px", borderRadius:20, flexShrink:0, whiteSpace:"nowrap", ...badgeStyle }}>{badgeLabel}</span>
        <span style={{ color:"var(--faint)", fontSize:10, flexShrink:0, display:"inline-block", transition:"transform .15s", transform: open ? "rotate(180deg)" : "none" }}>▼</span>
      </div>
      <div style={{ fontSize:12, color:"var(--muted)", padding:"0 14px 12px", lineHeight:1.5 }}>{sub}</div>
      {open && (
        <div style={{ borderTop:"1px solid var(--border)", padding:"14px 14px 16px", background:"rgba(255,255,255,0.02)" }}>
          <div style={{ fontFamily:"var(--mono)", fontSize:10, fontWeight:500, textTransform:"uppercase", letterSpacing:".1em", color:"var(--faint)", marginBottom:8 }}>Szczegóły</div>
          <div style={{ fontSize:12, color:"#bbb", lineHeight:1.7 }} dangerouslySetInnerHTML={{ __html: detail }} />
        </div>
      )}
    </div>
  );
}

const code = (s: string) => `<code style="font-family:monospace;font-size:11px;background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:3px;color:#d4b896">${s}</code>`;

const INFRA: CardData[] = [
  { status:"done", name:"Slack Bolt + Socket Mode", sub:"Real-time eventy, slash commands, Block Kit",
    detail:`Bot nasłuchuje eventów Slack przez Socket Mode — brak publicznego URL, działa za firewallem. Obsługuje slash commands, shortcuty, interactive components (przyciski, modals, select menus).<br/>${code("bot.py")} ${code("tools/slack_tools.py")}` },
  { status:"done", name:"APScheduler (cron jobs)", sub:"Daily digest, budget alerts, standup, news, email sync",
    detail:`Harmonogram zadań cyklicznych oparty na APScheduler. Strefa czasowa Europe/Warsaw. Każdy job ma własny plik w ${code("/jobs/")}.<br/>Aktywne joby: digest dzienny, alerty budżetowe (co godz.), standup, wiadomości branżowe, weekly report, check-in zespołu, email→iCloud sync (co godz.).` },
  { status:"done", name:"Render.com deploy + CI/CD", sub:"GitHub Actions, auto-deploy na push",
    detail:`Deploy automatyczny przez GitHub Actions przy każdym pushu na ${code("main")}. Env vars w Render dashboard. Procfile uruchamia bota jako web process.` },
  { status:"done", name:"Conversation memory (SQLite)", sub:"FTS5 search + pełna historia per user",
    detail:`Każda wiadomość DM (user + bot) zapisywana do SQLite z FTS5. Dwa tryby: ${code("recall()")} — semantic search, ${code("get_history()")} — pełna historia do Claude messages[]. Memory backfill z historii Slack.` },
  { status:"done", name:"Token cost tracking", sub:"SQLite · koszty w PLN · /koszty w Slacku",
    detail:`Każde wywołanie Anthropic API logowane do ${code("data/token_usage.db")} — model, tokeny in/out/cache, koszt USD i PLN. Komenda ${code("/koszty [dni]")} wyświetla raport w Slacku.` },
  { status:"done", name:"Parallel tool use", sub:"Claude może wywoływać wiele narzędzi naraz",
    detail:`Pętla tool-use w DM handlerze obsługuje wszystkie ${code("tool_use")} bloki z jednej odpowiedzi Claude — naprawiony błąd 400 gdy model wywoływał 4 narzędzia jednocześnie.` },
  { status:"gap", name:"Observability / Error logs", sub:"Brak centralnego trackingu błędów API",
    detail:"Błędy lądują w logach Render ale nikt ich nie monitoruje. Brak alertu gdy bot się wywali lub API przestanie odpowiadać." },
];

const INTEGRATIONS: CardData[] = [
  { status:"done", name:"Meta Ads API", sub:"Kampanie, statystyki, tworzenie, retry",
    detail:"Pełna integracja przez Graph API. Pobieranie danych kampanii, adsetów, kreacji. Tworzenie kampanii przez wizard (Block Kit modal). Auto-retry przy rate limitach (kod 4/17/32) z exponential backoff." },
  { status:"done", name:"Google Ads API", sub:"Kampanie, raporty, tworzenie (wizard)",
    detail:"Integracja przez google-ads-python-client. Raporty przez GAQL. Campaign creation wizard (Block Kit). Czytelne komunikaty błędów z GoogleAdsException." },
  { status:"wip", name:"GA4 / Google Analytics", sub:"Plik istnieje, były problemy z estymacją",
    detail:`Plik ${code("tools/google_analytics.py")} istnieje i integruje GA4 Data API. Ostatni fix: zakaz estymacji z Meta gdy GA4 nie działa + lepsze logowanie błędów.` },
  { status:"done", name:"PPTX Generator (python-pptx)", sub:"Prezentacje jako plik .pptx uploadowane na Slack",
    detail:`Sebol generuje prezentacje .pptx bez zewnętrznych OAuth. Claude Opus pisze strukturę JSON → python-pptx renderuje slajdy z brandingiem. Upload do Slacka przez ${code("files_upload_v2")}, fallback na transfer.sh. Zastąpił Google Slides (zepsute OAuth).` },
  { status:"done", name:"iCloud Calendar (CalDAV)", sub:"Odczyt i tworzenie wydarzeń, RRULE, auto-sync z emaila",
    detail:`Integracja przez CalDAV + vobject. Tworzenie wydarzeń cyklicznych przez RRULE (daily, weekly, biweekly, monthly, weekdays). Auto-sync: Sebol co godzinę skanuje skrzynkę email, wyciąga załączniki .ics i dodaje nowe zaproszenia do iCloud — bez żadnej akcji użytkownika.` },
  { status:"nice", name:"TikTok Ads API", sub:"Agencja prowadzi też TikTok Ads",
    detail:"Pato prowadzi kampanie TikTok Ads ale Sebol jeszcze ich nie monitoruje. TikTok Business API wymaga osobnej aplikacji w TikTok for Business portal. Priorytet: niski." },
];

const MODULES: CardData[] = [
  { status:"done", name:"Daily digest", sub:"Alerty, TL;DR, top performer, eksperyment",
    detail:"Codzienny raport rano. Format: critical alerts → TL;DR → top performer → 1 sugestia eksperymentu. Zawiera weekly learnings (podział na Meta/Google)." },
  { status:"done", name:"Campaign creator (Meta + Google)", sub:"Block Kit wizard + approval workflow",
    detail:"Wieloetapowy wizard tworzenia kampanii przez Slack modals. Obsługuje upload plików (kreacje) do 50MB. Workflow zatwierdzania przed wysłaniem do API. Wizard state persisted to disk — przeżywa restarty serwera." },
  { status:"done", name:"Budget alerts (Meta)", sub:"Pace tracking co godz., CRITICAL + WARNING",
    detail:"Sprawdza pace (spend/daily_budget vs progress dnia) co godzinę między 7:00–22:00. Progi: &gt;120% → WARNING 🟡, &gt;150% → CRITICAL 🔴. Cooldown 4h zapobiega spam." },
  { status:"wip", name:"Budget alerts (Google)", sub:"Jest koszt, brak pace tracking",
    detail:`Funkcja ${code('check_budget_status("google")')} zwraca kampanie z kosztem &gt;10 PLN ale nie porównuje do budżetu dziennego — brak % pace.` },
  { status:"done", name:"Weekly reports", sub:"Tygodniowy raport dla każdego klienta",
    detail:"Automatyczny raport tygodniowy: spend, conversions, ROAS, top performers, alerty. Formatowanie z analizą trendów." },
  { status:"done", name:"Performance analysis", sub:"Analiza trendów, compact thread format",
    detail:"Analiza trendów kampanii: wykrywanie anomalii, top performers, kampanie wymagające uwagi. Format compact thread (wyniki w wątku Slack)." },
  { status:"done", name:"Standup + check-in zespołu", sub:"Codzienne check-iny, standup summary",
    detail:"Automatyczne pytania check-in przez DM. Standup summary do #zarzondpato. Dostępność zespołu persisted — przeżywa restarty." },
  { status:"done", name:"Industry news", sub:"Nagłówki w głównej wiad., szczegóły w wątku",
    detail:"Automatyczne wiadomości branżowe (marketing, performance). Nagłówki w głównej wiadomości, szczegóły w wątku. Deduplikacja po URL." },
  { status:"done", name:"Reminders (persistent)", sub:"Slack scheduled messages, przeżywa restarty",
    detail:`Przypomnienia przez ${code("chat.scheduleMessage")} — natywna funkcja Slack, nie APScheduler. Dzięki temu przeżywają restarty i deploje bota.` },
  { status:"done", name:"Voice transcription", sub:"Whisper, działa z każdym formatem audio",
    detail:"Transkrypcja głosówek przez OpenAI Whisper. Używa Slack filetype jako extension (nie MIME type). Kodeki przez imageio-ffmpeg (bez apt-get)." },
  { status:"done", name:"Email summary", sub:"Streszczenie emaili, DM do użytkownika",
    detail:`Automatyczne streszczenia emaili dostarczone przez DM. Integracja przez ${code("conversations_open")}.` },
  { status:"done", name:"Client onboarding", sub:"Checklisty dla nowych klientów",
    detail:"Checklisty onboardingowe dla nowych klientów agencji. Automatycznie wysyłane przy dodaniu nowego konta." },
  { status:"done", name:"LinkedIn Ghostwriter", sub:"/linkedin · 3 hooki · pełny post · grafika DALL-E",
    detail:`Slash command ${code("/linkedin <temat>")}. Claude Opus pisze 3 warianty hooka → user wybiera → pełny post → podgląd promptu do grafiki → zatwierdzenie → gpt-image-1 generuje obraz. Research przez web_search (trendy, wiralne posty). Dostęp tylko dla właściciela.` },
  { status:"done", name:"Email → iCloud auto-sync", sub:"Co godzinę · .ics z maila · powiadomienie Slack",
    detail:`APScheduler job co godzinę: skanuje INBOX (ostatnie 2 doby), wyciąga załączniki .ics, parsuje przez vobject, pomija już zsynchronizowane (${code("data/synced_invites.json")}), tworzy przez CalDAV i wysyła powiadomienie DM z listą dodanych wydarzeń.` },
  { status:"wip", name:"Strategy recommendations", sub:"Weekly learnings, sugestie strategii",
    detail:"Weekly learnings działają (podział na Meta/Google per kampania). Sebol sugeruje eksperymenty w daily digest. Brakuje zamknięcia pętli — wynik eksperymentu nie wraca automatycznie do bazy wiedzy." },
  { status:"gap", name:"A/B test tracker", sub:"Sebol nie mierzy wyników swoich sugestii",
    detail:"Sebol sugeruje eksperymenty ale nie śledzi ich wyników. Żeby self-learning działało naprawdę: przy każdej sugestii zapisz (co, kiedy, hypothesis), po 7 dniach auto-sprawdź wynik vs baseline." },
  { status:"gap", name:"Web dashboard", sub:"Brak UI poza Slackiem",
    detail:"Żeby pokazać Sebola klientowi lub nowej agencji — potrzebne jest UI poza Slackiem. Status page (ta strona!) to pierwszy krok. Następnie: historia akcji, statusy kont, koszty tokenów." },
  { status:"nice", name:"Creative performance analysis", sub:"Która kreacja działa i dlaczego",
    detail:"Analiza performance kreacji reklamowych. Claude Vision do analizy co jest na kreacji + korelacja z wynikami. Pozwoli Sebolowi rekomendować styl kreacji per klient." },
  { status:"nice", name:"Audience insights", sub:"Segmentacja, lookalike, rekomendacje",
    detail:"Analiza audience'ów Meta: które grupy konwertują, sugestie lookalike, wykrywanie audience overlap. Wymaga minimum 3 miesięcy historii." },
  { status:"nice", name:"Proactive client reports", sub:".pptx raport na żądanie lub auto",
    detail:`Prezentacje .pptx już działają (${code("tools/pptx_presentation.py")}). Brakuje szablonu raportu klientowego + cyklu automatycznego (tygodniowy raport jako .pptx wysyłany do klienta).` },
];

function AnimatedNum({ target, duration = 1200 }: { target: number; duration?: number }) {
  const [val, setVal] = useState(0);
  const elRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = elRef.current; if (!el) return;
    const obs = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      obs.disconnect();
      let start: number | null = null;
      const step = (ts: number) => {
        if (start === null) start = ts;
        const p = Math.min((ts - start) / duration, 1);
        const ease = 1 - Math.pow(1 - p, 3);
        setVal(Math.round(ease * target));
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }, { threshold: 0.5 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [target, duration]);
  return <span ref={elRef}>{val}</span>;
}

function AnimatedBar({ pct }: { pct: number }) {
  const [w, setW] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const obs = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      obs.disconnect();
      setTimeout(() => setW(pct), 80);
    }, { threshold: 0.5 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [pct]);
  return (
    <div ref={ref} style={{ height:4, background:"var(--bg3)", borderRadius:2, overflow:"hidden" }}>
      <div style={{ height:"100%", borderRadius:2, background:"linear-gradient(90deg,var(--green) 0%,#16a34a 100%)", width:`${w}%`, transition:"width 1.3s cubic-bezier(0.34,1.2,0.64,1)" }} />
    </div>
  );
}

function Section({ label, cards }: { label: string; cards: CardData[] }) {
  return (
    <div style={{ marginBottom:44 }}>
      <div style={{ fontFamily:"var(--mono)", fontSize:10, fontWeight:500, textTransform:"uppercase", letterSpacing:".12em", color:"var(--faint)", marginBottom:12 }}>{label}</div>
      <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:8 }}>
        {cards.map(c => <StatusCard key={c.name} {...c} />)}
      </div>
    </div>
  );
}

function FloatingNav() {
  const [active, setActive] = useState<"galaxy" | "status">("galaxy");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(e => {
          if (e.isIntersecting) setActive(e.target.id as "galaxy" | "status");
        });
      },
      { threshold: 0.4 }
    );
    const galaxy = document.getElementById("galaxy");
    const status = document.getElementById("status");
    if (galaxy) observer.observe(galaxy);
    if (status) observer.observe(status);
    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  };

  const btnStyle = (id: string): React.CSSProperties => ({
    fontFamily: "var(--mono)",
    fontSize: 11,
    fontWeight: 500,
    padding: "5px 14px",
    borderRadius: 20,
    border: "none",
    cursor: "pointer",
    transition: "all .2s",
    background: active === id ? "rgba(255,255,255,0.12)" : "transparent",
    color: active === id ? "#e8e8e8" : "#666",
  });

  return (
    <nav style={{
      position: "fixed", top: 18, right: 24,
      zIndex: 1000, display: "flex", gap: 2, alignItems: "center",
      background: "rgba(10,10,10,0.75)", backdropFilter: "blur(12px)",
      border: "1px solid rgba(255,255,255,0.1)", borderRadius: 24,
      padding: "4px 4px",
    }}>
      <button style={btnStyle("galaxy")} onClick={() => scrollTo("galaxy")}>galaxy</button>
      <button style={btnStyle("status")} onClick={() => scrollTo("status")}>status</button>
    </nav>
  );
}

export default function Page() {
  return (
    <>
      <FloatingNav />

      {/* Galaxy hero section */}
      <section id="galaxy" style={{ height:"100vh", width:"100vw" }}>
        <SebolGalaxy />
      </section>

      {/* Status dashboard */}
      <div id="status" style={{ background:"var(--bg)", minHeight:"100vh" }}>
        <header style={{ borderBottom:"1px solid var(--border)", padding:"28px 40px 24px", display:"flex", alignItems:"center", justifyContent:"space-between", gap:16, flexWrap:"wrap" }}>
          <div style={{ display:"flex", alignItems:"center", gap:14 }}>
            <div style={{ width:36, height:36, borderRadius:8, background:"linear-gradient(135deg,#1d4ed8,#7c3aed)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:18, flexShrink:0 }}>🤖</div>
            <div>
              <div style={{ fontFamily:"var(--mono)", fontSize:18, fontWeight:500, letterSpacing:"-0.01em" }}>sebol</div>
              <div style={{ fontSize:12, color:"var(--muted)", fontFamily:"var(--mono)", marginTop:1 }}>pato agencja · ai agent</div>
            </div>
          </div>
          <div style={{ display:"flex", alignItems:"center", gap:6, fontFamily:"var(--mono)", fontSize:11, color:"var(--green)", background:"var(--green-bg)", border:"1px solid var(--green-b)", padding:"4px 10px", borderRadius:20 }}>
            <div style={{ width:6, height:6, borderRadius:"50%", background:"var(--green)", animation:"pulse 2s ease-in-out infinite" }} />
            render.com
          </div>
        </header>

        <main style={{ maxWidth:860, margin:"0 auto", padding:"40px 24px 80px" }}>
          {/* Stats */}
          <div style={{ display:"flex", gap:24, flexWrap:"wrap", background:"var(--bg2)", border:"1px solid var(--border)", borderRadius:10, padding:"18px 24px", marginBottom:36 }}>
            {([
              [18, "Wdrożone",        "var(--green)"],
              [3,  "W trakcie / WIP", "var(--amber)"],
              [3,  "Brakuje",         "var(--red)"],
              [4,  "Planowane",       "var(--blue)"],
            ] as [number, string, string][]).map(([n, label, color]) => (
              <div key={label} style={{ display:"flex", flexDirection:"column", gap:3 }}>
                <div style={{ fontFamily:"var(--mono)", fontSize:22, fontWeight:500, lineHeight:1, color }}><AnimatedNum target={n} /></div>
                <div style={{ fontSize:11, color:"var(--muted)" }}>{label}</div>
              </div>
            ))}
            <div style={{ display:"flex", flexDirection:"column", gap:3, marginLeft:"auto" }}>
              <div style={{ fontFamily:"var(--mono)", fontSize:22, fontWeight:500, lineHeight:1, color:"var(--text)" }}><AnimatedNum target={165} duration={2000} /></div>
              <div style={{ fontSize:11, color:"var(--muted)" }}>commitów</div>
            </div>
          </div>

          {/* Progress bar */}
          <div style={{ marginBottom:36 }}>
            <div style={{ display:"flex", justifyContent:"space-between", fontSize:12, color:"var(--muted)", marginBottom:6 }}>
              <span>Ogólny postęp</span>
              <span style={{ color:"var(--green)", fontFamily:"var(--mono)" }}>64%</span>
            </div>
            <AnimatedBar pct={64} />
          </div>

          {/* Legend */}
          <div style={{ display:"flex", gap:20, flexWrap:"wrap", marginBottom:36 }}>
            {([
              ["var(--green)", "Wdrożone i działa"],
              ["var(--amber)", "Częściowe / WIP"],
              ["var(--red)",   "Brakuje (krytyczne)"],
              ["var(--blue)",  "Planowane (nice-to-have)"],
            ] as [string, string][]).map(([color, label]) => (
              <div key={label} style={{ display:"flex", alignItems:"center", gap:7, fontSize:12, color:"var(--muted)" }}>
                <div style={{ width:7, height:7, borderRadius:"50%", background:color }} />
                {label}
              </div>
            ))}
          </div>

          <Section label="Infrastruktura" cards={INFRA} />
          <Section label="Integracje z platformami" cards={INTEGRATIONS} />
          <Section label="Moduły agenta" cards={MODULES} />
        </main>

        <footer style={{ borderTop:"1px solid var(--border)", padding:"20px 40px", fontSize:11, color:"var(--faint)", fontFamily:"var(--mono)", display:"flex", justifyContent:"space-between", flexWrap:"wrap", gap:8 }}>
          <span>github.com/patoagencja/slack-bot</span>
          <span>sebol · pato agencja · {new Date().getFullYear()}</span>
        </footer>
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
