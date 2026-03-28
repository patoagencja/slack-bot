"use client";
import dynamic from "next/dynamic";
import { useState, useEffect, useRef } from "react";

const SebolGalaxy = dynamic(() => import("../../components/SebolGalaxy"), { ssr: false });

/* ─── helpers ────────────────────────────────────────────────────────────── */

function useFadeIn(threshold = 0.18) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { setVisible(true); obs.disconnect(); }
    }, { threshold });
    obs.observe(el);
    return () => obs.disconnect();
  }, [threshold]);
  return { ref, visible };
}

function AnimatedNum({ target, suffix = "", duration = 1600 }: { target: number; suffix?: string; duration?: number }) {
  const [val, setVal] = useState(0);
  const { ref, visible } = useFadeIn(0.5);
  useEffect(() => {
    if (!visible) return;
    let start: number | null = null;
    const step = (ts: number) => {
      if (start === null) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      const ease = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(ease * target));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [visible, target, duration]);
  return <span ref={ref}>{val}{suffix}</span>;
}

function Fade({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  const { ref, visible } = useFadeIn();
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(28px)",
        transition: `opacity .65s ease ${delay}ms, transform .65s ease ${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}

/* ─── component ──────────────────────────────────────────────────────────── */

export default function LandingPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <>
      {/* ── NAV ─────────────────────────────────────────────────────────── */}
      <nav style={{
        position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
        background: "rgba(3,6,14,0.82)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid rgba(0,212,255,0.09)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 clamp(20px,5vw,60px)", height: 60,
      }}>
        <div style={{ fontFamily: "'Orbitron',monospace", fontWeight: 900, fontSize: 17, color: "#00d4ff", letterSpacing: ".3em", textShadow: "0 0 14px rgba(0,212,255,0.5)" }}>
          SEBOL
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {(["problem", "jak-dziala", "features", "rezultaty", "kontakt"] as const).map(id => (
            <button key={id} onClick={() => scrollTo(id)} style={{
              background: "none", border: "none", cursor: "pointer",
              fontFamily: "'IBM Plex Mono',monospace", fontSize: 11,
              color: "rgba(0,212,255,0.55)", letterSpacing: ".08em",
              padding: "6px 12px", borderRadius: 6,
              transition: "color .2s",
            }}
            onMouseEnter={e => (e.currentTarget.style.color = "#00d4ff")}
            onMouseLeave={e => (e.currentTarget.style.color = "rgba(0,212,255,0.55)")}
            >
              {id.replace(/-/g, " ")}
            </button>
          ))}
          <button onClick={() => scrollTo("kontakt")} style={{
            marginLeft: 8,
            background: "linear-gradient(135deg,#0066cc,#7c3aed)",
            border: "none", cursor: "pointer", borderRadius: 8,
            fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, fontWeight: 500,
            color: "#fff", padding: "8px 18px", letterSpacing: ".05em",
            boxShadow: "0 4px 20px rgba(0,102,204,0.4)",
            transition: "opacity .2s",
          }}
          onMouseEnter={e => (e.currentTarget.style.opacity = ".8")}
          onMouseLeave={e => (e.currentTarget.style.opacity = "1")}
          >
            demo →
          </button>
        </div>
      </nav>

      {/* ── HERO ────────────────────────────────────────────────────────── */}
      <section style={{ position: "relative", height: "100vh", overflow: "hidden" }}>
        <div style={{ position: "absolute", inset: 0 }}>
          <SebolGalaxy />
        </div>
        {/* dark overlay so text is readable */}
        <div style={{
          position: "absolute", inset: 0,
          background: "linear-gradient(to bottom, rgba(3,6,14,0.55) 0%, rgba(3,6,14,0.25) 50%, rgba(3,6,14,0.80) 100%)",
          pointerEvents: "none",
        }} />
        <div style={{
          position: "absolute", inset: 0, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          padding: "0 24px", textAlign: "center", pointerEvents: "none",
        }}>
          <div style={{
            fontFamily: "'Share Tech Mono',monospace", fontSize: "clamp(10px,1.2vw,13px)",
            color: "#00d4ff", letterSpacing: ".5em", marginBottom: 24,
            textShadow: "0 0 20px rgba(0,212,255,0.6)",
          }}>
            AI AGENT · PATO AGENCJA
          </div>
          <h1 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(36px,7vw,88px)", lineHeight: 1.05,
            background: "linear-gradient(135deg,#ffffff 0%,#a8d8ff 45%,#c084fc 100%)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
            marginBottom: 24, letterSpacing: "-.01em",
            filter: "drop-shadow(0 0 40px rgba(0,212,255,0.25))",
          }}>
            Twoje kampanie.<br />Zarządzane przez AI.
          </h1>
          <p style={{
            fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
            fontSize: "clamp(16px,2.2vw,22px)", color: "rgba(200,230,255,0.75)",
            maxWidth: 560, lineHeight: 1.65, marginBottom: 44,
          }}>
            Sebol automatyzuje performance marketing w Google Ads i Meta — raportuje, optymalizuje, alarmuje. Wszystko przez Slack.
          </p>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", justifyContent: "center", pointerEvents: "all" }}>
            <button onClick={() => scrollTo("kontakt")} style={{
              background: "linear-gradient(135deg,#0066cc,#7c3aed)",
              border: "none", cursor: "pointer", borderRadius: 10,
              fontFamily: "'IBM Plex Mono',monospace", fontWeight: 500,
              fontSize: "clamp(13px,1.4vw,15px)", color: "#fff",
              padding: "16px 36px", letterSpacing: ".06em",
              boxShadow: "0 8px 40px rgba(0,102,204,0.5), 0 0 0 1px rgba(255,255,255,0.08)",
              transition: "transform .18s, box-shadow .18s",
            }}
            onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-2px)"; e.currentTarget.style.boxShadow = "0 14px 50px rgba(0,102,204,0.65)"; }}
            onMouseLeave={e => { e.currentTarget.style.transform = ""; e.currentTarget.style.boxShadow = "0 8px 40px rgba(0,102,204,0.5)"; }}
            >
              Umów demo →
            </button>
            <button onClick={() => scrollTo("jak-dziala")} style={{
              background: "rgba(0,212,255,0.06)", border: "1px solid rgba(0,212,255,0.25)",
              cursor: "pointer", borderRadius: 10,
              fontFamily: "'IBM Plex Mono',monospace", fontSize: "clamp(13px,1.4vw,15px)",
              color: "rgba(0,212,255,0.8)", padding: "16px 32px", letterSpacing: ".06em",
              transition: "background .2s, border-color .2s",
            }}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(0,212,255,0.12)"; e.currentTarget.style.borderColor = "rgba(0,212,255,0.5)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "rgba(0,212,255,0.06)"; e.currentTarget.style.borderColor = "rgba(0,212,255,0.25)"; }}
            >
              Jak to działa
            </button>
          </div>
        </div>
        {/* scroll arrow */}
        <div style={{
          position: "absolute", bottom: 28, left: "50%", transform: "translateX(-50%)",
          fontFamily: "'Share Tech Mono',monospace", fontSize: 10,
          color: "rgba(0,212,255,0.3)", letterSpacing: ".3em", textAlign: "center",
          animation: "bounce 2.2s ease-in-out infinite",
        }}>
          ▼<br />scroll
        </div>
      </section>

      {/* ── LOGOS ───────────────────────────────────────────────────────── */}
      <section style={{
        borderTop: "1px solid rgba(255,255,255,0.05)", borderBottom: "1px solid rgba(255,255,255,0.05)",
        background: "rgba(0,0,0,0.4)", padding: "22px clamp(20px,6vw,80px)",
        display: "flex", alignItems: "center", justifyContent: "center", gap: "clamp(28px,5vw,64px)",
        flexWrap: "wrap",
      }}>
        {[
          { name: "Google Ads", color: "#facc15" },
          { name: "Meta Ads", color: "#fb923c" },
          { name: "Slack", color: "#4ade80" },
          { name: "Claude AI", color: "#a78bfa" },
          { name: "Render", color: "#94a3b8" },
        ].map(({ name, color }) => (
          <div key={name} style={{
            fontFamily: "'IBM Plex Mono',monospace", fontSize: 12,
            color: "rgba(255,255,255,0.22)", letterSpacing: ".1em",
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, display: "inline-block", opacity: 0.6 }} />
            {name}
          </div>
        ))}
      </section>

      {/* ── PROBLEM ─────────────────────────────────────────────────────── */}
      <section id="problem" style={{ padding: "120px clamp(20px,8vw,140px)", background: "#03060e" }}>
        <Fade>
          <div style={{
            fontFamily: "'Share Tech Mono',monospace", fontSize: 11, color: "#00d4ff",
            letterSpacing: ".5em", marginBottom: 20,
          }}>
            01 / PROBLEM
          </div>
        </Fade>
        <Fade delay={80}>
          <h2 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(26px,5vw,54px)", lineHeight: 1.1,
            color: "#fff", marginBottom: 24, maxWidth: 620,
          }}>
            Twój budżet reklamowy<br />
            <span style={{ color: "#ef4444" }}>wycieka.</span>
          </h2>
        </Fade>
        <Fade delay={160}>
          <p style={{
            fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
            fontSize: "clamp(15px,1.8vw,18px)", color: "rgba(200,210,230,0.65)",
            maxWidth: 500, lineHeight: 1.75, marginBottom: 64,
          }}>
            Kampanie optymalizowane raz w tygodniu tracą średnio 23% budżetu na nieskuteczne kreacje. Ty dowiadujesz się o tym dopiero z raportu miesięcznego.
          </p>
        </Fade>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 20, maxWidth: 900 }}>
          {[
            { icon: "⏱", title: "Ręczne raporty", body: "Analityk spędza 3–5h tygodniowo na kopiowaniu liczb z paneli do Excela. Czas, który mogłoby zająć AI w 4 sekundy." },
            { icon: "🔥", title: "Spalone budżety", body: "Kampanie bez daily monitoringu przekraczają pace nawet o 40%. Alert przychodzi za późno — pieniądze już wydane." },
            { icon: "📭", title: "Brak kontekstu", body: "Klient pyta »co słychać z kampanią«. Odpowiedź to 20 minut wchodzenia do panelu, filtrowania i sklejania danych." },
          ].map(({ icon, title, body }, i) => (
            <Fade key={title} delay={i * 100}>
              <div style={{
                background: "rgba(239,68,68,0.04)", border: "1px solid rgba(239,68,68,0.14)",
                borderRadius: 14, padding: "28px 28px",
              }}>
                <div style={{ fontSize: 28, marginBottom: 14 }}>{icon}</div>
                <div style={{
                  fontFamily: "'Orbitron',monospace", fontWeight: 700, fontSize: 14,
                  color: "#fff", marginBottom: 10, letterSpacing: ".05em",
                }}>
                  {title}
                </div>
                <div style={{
                  fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
                  fontSize: 13, color: "rgba(200,210,230,0.6)", lineHeight: 1.7,
                }}>
                  {body}
                </div>
              </div>
            </Fade>
          ))}
        </div>
      </section>

      {/* ── HOW IT WORKS ────────────────────────────────────────────────── */}
      <section id="jak-dziala" style={{
        padding: "120px clamp(20px,8vw,140px)",
        background: "linear-gradient(180deg,#03060e 0%,#050c1a 100%)",
        borderTop: "1px solid rgba(0,212,255,0.06)",
      }}>
        <Fade>
          <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 11, color: "#00d4ff", letterSpacing: ".5em", marginBottom: 20 }}>
            02 / JAK DZIAŁA
          </div>
        </Fade>
        <Fade delay={80}>
          <h2 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(26px,5vw,54px)", lineHeight: 1.1,
            color: "#fff", marginBottom: 64, maxWidth: 560,
          }}>
            3 kroki do<br />autonomicznego marketingu.
          </h2>
        </Fade>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 28, maxWidth: 860 }}>
          {[
            {
              step: "01",
              title: "Połącz konta",
              body: "Sebol łączy się z Google Ads, Meta Ads i Slack w ciągu kilku minut. Zero instalacji, zero serwerów — działa w chmurze.",
              color: "#00d4ff",
            },
            {
              step: "02",
              title: "Monitoruje 24/7",
              body: "Sprawdza pace budżetów, CTR, CPC i konwersje co godzinę. Wykrywa anomalie zanim zdążą kosztować.",
              color: "#a78bfa",
            },
            {
              step: "03",
              title: "Raportuje w Slack",
              body: "Pisze daily digest, alerty i rekomendacje bezpośrednio do kanałów Slack. Klient widzi wyniki — Ty nie ruszasz palcem.",
              color: "#4ade80",
            },
          ].map(({ step, title, body, color }, i) => (
            <Fade key={step} delay={i * 120}>
              <div style={{
                background: "rgba(255,255,255,0.025)",
                border: `1px solid rgba(255,255,255,0.07)`,
                borderRadius: 16, padding: "32px 28px",
                position: "relative", overflow: "hidden",
              }}>
                <div style={{
                  position: "absolute", top: 16, right: 20,
                  fontFamily: "'Orbitron',monospace", fontWeight: 900, fontSize: 52,
                  color: "rgba(255,255,255,0.03)", lineHeight: 1,
                }}>
                  {step}
                </div>
                <div style={{
                  display: "inline-block", fontFamily: "'Share Tech Mono',monospace",
                  fontSize: 10, letterSpacing: ".3em",
                  color, background: `rgba(0,0,0,0.3)`,
                  border: `1px solid ${color}33`,
                  padding: "3px 10px", borderRadius: 20, marginBottom: 20,
                }}>
                  KROK {step}
                </div>
                <div style={{
                  fontFamily: "'Orbitron',monospace", fontWeight: 700, fontSize: 16,
                  color: "#fff", marginBottom: 12, letterSpacing: ".04em",
                }}>
                  {title}
                </div>
                <div style={{
                  fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
                  fontSize: 13, color: "rgba(200,210,230,0.6)", lineHeight: 1.75,
                }}>
                  {body}
                </div>
              </div>
            </Fade>
          ))}
        </div>
      </section>

      {/* ── FEATURES ────────────────────────────────────────────────────── */}
      <section id="features" style={{
        padding: "120px clamp(20px,8vw,140px)",
        background: "#03060e",
        borderTop: "1px solid rgba(0,212,255,0.06)",
      }}>
        <Fade>
          <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 11, color: "#00d4ff", letterSpacing: ".5em", marginBottom: 20 }}>
            03 / MOŻLIWOŚCI
          </div>
        </Fade>
        <Fade delay={80}>
          <h2 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(26px,5vw,54px)", lineHeight: 1.1,
            color: "#fff", marginBottom: 64, maxWidth: 520,
          }}>
            Wszystko co potrzebujesz.<br />
            <span style={{ color: "#00d4ff" }}>Nic ponad to.</span>
          </h2>
        </Fade>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: 16, maxWidth: 920 }}>
          {[
            { icon: "📊", color: "#facc15", title: "Daily Digest", body: "Poranny raport wyników kampanii do Slack — automatycznie, każdy dzień." },
            { icon: "🚨", color: "#ef4444", title: "Budget Alerts", body: "Alarm gdy kampania przekroczy zakładany pace lub zejdzie poniżej targetowanego spend." },
            { icon: "🤖", color: "#a78bfa", title: "Claude AI Core", body: "Analiza przez LLM — rozumie kontekst biznesowy, nie tylko liczby." },
            { icon: "⚡", color: "#4ade80", title: "Slack Wizard", body: "Tworzenie i zarządzanie kampaniami Meta wprost z rozmowy na Slack." },
            { icon: "🔍", color: "#38bdf8", title: "Google & Meta", body: "Pełna integracja z Google Ads API i Meta Marketing API. Jeden agent, dwa ekosystemy." },
            { icon: "🔐", color: "#c084fc", title: "Token Optimizer", body: "Inteligentne cache i kompresja kontekstu — minimalne koszty API, maksymalna szybkość." },
          ].map(({ icon, color, title, body }, i) => (
            <Fade key={title} delay={i * 70}>
              <div
                style={{
                  background: "rgba(255,255,255,0.025)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 14, padding: "24px 24px",
                  transition: "border-color .25s, background .25s, transform .25s",
                  cursor: "default",
                }}
                onMouseEnter={e => {
                  const d = e.currentTarget;
                  d.style.borderColor = `${color}44`;
                  d.style.background = `rgba(255,255,255,0.04)`;
                  d.style.transform = "translateY(-3px)";
                }}
                onMouseLeave={e => {
                  const d = e.currentTarget;
                  d.style.borderColor = "rgba(255,255,255,0.06)";
                  d.style.background = "rgba(255,255,255,0.025)";
                  d.style.transform = "";
                }}
              >
                <div style={{ fontSize: 26, marginBottom: 14 }}>{icon}</div>
                <div style={{
                  fontFamily: "'Orbitron',monospace", fontWeight: 700, fontSize: 13,
                  color, marginBottom: 9, letterSpacing: ".05em",
                }}>
                  {title}
                </div>
                <div style={{
                  fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
                  fontSize: 13, color: "rgba(200,210,230,0.55)", lineHeight: 1.7,
                }}>
                  {body}
                </div>
              </div>
            </Fade>
          ))}
        </div>
      </section>

      {/* ── STATS / RESULTS ─────────────────────────────────────────────── */}
      <section id="rezultaty" style={{
        padding: "120px clamp(20px,8vw,140px)",
        background: "linear-gradient(135deg,#020810 0%,#060b1e 50%,#020810 100%)",
        borderTop: "1px solid rgba(0,212,255,0.07)",
        borderBottom: "1px solid rgba(0,212,255,0.07)",
      }}>
        <Fade>
          <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 11, color: "#00d4ff", letterSpacing: ".5em", marginBottom: 20 }}>
            04 / LICZBY
          </div>
        </Fade>
        <Fade delay={80}>
          <h2 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(26px,5vw,54px)", lineHeight: 1.1,
            color: "#fff", marginBottom: 72,
          }}>
            Wyniki mówią<br />same za siebie.
          </h2>
        </Fade>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 32, maxWidth: 860 }}>
          {[
            { value: 14, suffix: "", label: "aktywnych modułów", color: "#00d4ff" },
            { value: 5, suffix: "x", label: "szybsze raportowanie", color: "#a78bfa" },
            { value: 23, suffix: "%", label: "mniej straconego budżetu", color: "#4ade80" },
            { value: 369, suffix: "", label: "commitów deweloperskich", color: "#facc15" },
          ].map(({ value, suffix, label, color }, i) => (
            <Fade key={label} delay={i * 100}>
              <div style={{ textAlign: "center" }}>
                <div style={{
                  fontFamily: "'Orbitron',monospace", fontWeight: 900,
                  fontSize: "clamp(44px,6vw,72px)", lineHeight: 1,
                  color, textShadow: `0 0 40px ${color}66`,
                  marginBottom: 12,
                }}>
                  <AnimatedNum target={value} suffix={suffix} />
                </div>
                <div style={{
                  fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
                  fontSize: 13, color: "rgba(200,210,230,0.5)", letterSpacing: ".04em",
                }}>
                  {label}
                </div>
              </div>
            </Fade>
          ))}
        </div>
      </section>

      {/* ── TESTIMONIAL ─────────────────────────────────────────────────── */}
      <section style={{
        padding: "120px clamp(20px,8vw,140px)",
        background: "#03060e",
        display: "flex", justifyContent: "center",
      }}>
        <Fade>
          <div style={{
            maxWidth: 680, textAlign: "center",
            background: "rgba(0,212,255,0.03)",
            border: "1px solid rgba(0,212,255,0.1)",
            borderRadius: 20, padding: "52px 48px",
            position: "relative",
          }}>
            <div style={{
              fontFamily: "'Orbitron',monospace", fontSize: 64, lineHeight: 0.7,
              color: "rgba(0,212,255,0.12)", position: "absolute", top: 28, left: 36,
            }}>
              "
            </div>
            <blockquote style={{
              fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
              fontSize: "clamp(17px,2.5vw,22px)", color: "rgba(220,235,255,0.82)",
              lineHeight: 1.65, marginBottom: 36, fontStyle: "italic",
            }}>
              Zamiast spędzać poranki na sprawdzaniu paneli, wchodzę na Slack i Sebol już czeka z gotowym raportem. Klient widzi wyniki, ja widzę co poprawić.
            </blockquote>
            <div style={{
              fontFamily: "'Share Tech Mono',monospace", fontSize: 11,
              color: "rgba(0,212,255,0.5)", letterSpacing: ".3em",
            }}>
              — PATO AGENCJA · PERFORMANCE TEAM
            </div>
          </div>
        </Fade>
      </section>

      {/* ── CTA / CONTACT ───────────────────────────────────────────────── */}
      <section id="kontakt" style={{
        padding: "120px clamp(20px,8vw,140px) 140px",
        background: "linear-gradient(180deg,#03060e 0%,#040918 100%)",
        borderTop: "1px solid rgba(0,212,255,0.07)",
        textAlign: "center",
      }}>
        <Fade>
          <div style={{
            fontFamily: "'Share Tech Mono',monospace", fontSize: 11, color: "#00d4ff",
            letterSpacing: ".5em", marginBottom: 24,
          }}>
            05 / ZACZNIJ TERAZ
          </div>
        </Fade>
        <Fade delay={80}>
          <h2 style={{
            fontFamily: "'Orbitron',monospace", fontWeight: 900,
            fontSize: "clamp(28px,6vw,64px)", lineHeight: 1.08,
            background: "linear-gradient(135deg,#ffffff 0%,#a8d8ff 50%,#c084fc 100%)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
            marginBottom: 24,
          }}>
            Gotowy żeby przestać<br />tracić budżet?
          </h2>
        </Fade>
        <Fade delay={160}>
          <p style={{
            fontFamily: "'IBM Plex Sans',sans-serif", fontWeight: 300,
            fontSize: "clamp(15px,2vw,18px)", color: "rgba(200,210,230,0.6)",
            maxWidth: 440, margin: "0 auto 48px", lineHeight: 1.75,
          }}>
            Napisz do nas — skonfigurujemy Sebol pod Twoje konto w ciągu jednego spotkania.
          </p>
        </Fade>
        <Fade delay={240}>
          <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap" }}>
            <a
              href="mailto:kontakt@patoagencja.pl"
              style={{
                display: "inline-block",
                background: "linear-gradient(135deg,#0066cc,#7c3aed)",
                borderRadius: 12, textDecoration: "none",
                fontFamily: "'IBM Plex Mono',monospace", fontWeight: 500,
                fontSize: "clamp(14px,1.5vw,16px)", color: "#fff",
                padding: "18px 44px", letterSpacing: ".07em",
                boxShadow: "0 10px 50px rgba(0,102,204,0.5)",
                transition: "transform .18s, box-shadow .18s",
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-3px)"; e.currentTarget.style.boxShadow = "0 16px 60px rgba(0,102,204,0.7)"; }}
              onMouseLeave={e => { e.currentTarget.style.transform = ""; e.currentTarget.style.boxShadow = "0 10px 50px rgba(0,102,204,0.5)"; }}
            >
              Napisz do nas →
            </a>
            <a
              href="https://patoagencja.pl"
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "inline-block",
                background: "rgba(0,212,255,0.05)",
                border: "1px solid rgba(0,212,255,0.22)",
                borderRadius: 12, textDecoration: "none",
                fontFamily: "'IBM Plex Mono',monospace",
                fontSize: "clamp(14px,1.5vw,16px)", color: "rgba(0,212,255,0.75)",
                padding: "18px 36px", letterSpacing: ".07em",
                transition: "background .2s, border-color .2s",
              }}
              onMouseEnter={e => { e.currentTarget.style.background = "rgba(0,212,255,0.1)"; e.currentTarget.style.borderColor = "rgba(0,212,255,0.45)"; }}
              onMouseLeave={e => { e.currentTarget.style.background = "rgba(0,212,255,0.05)"; e.currentTarget.style.borderColor = "rgba(0,212,255,0.22)"; }}
            >
              patoagencja.pl
            </a>
          </div>
        </Fade>
      </section>

      {/* ── FOOTER ──────────────────────────────────────────────────────── */}
      <footer style={{
        borderTop: "1px solid rgba(255,255,255,0.05)",
        background: "#020508",
        padding: "28px clamp(20px,6vw,80px)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12,
      }}>
        <div style={{ fontFamily: "'Orbitron',monospace", fontWeight: 900, fontSize: 14, color: "rgba(0,212,255,0.35)", letterSpacing: ".3em" }}>
          SEBOL
        </div>
        <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 10, color: "rgba(255,255,255,0.18)", letterSpacing: ".15em" }}>
          © 2025 PATO AGENCJA · AI AGENT v2.0
        </div>
      </footer>

      <style>{`
        @keyframes bounce {
          0%,100% { transform: translateX(-50%) translateY(0); opacity:.4; }
          50% { transform: translateX(-50%) translateY(8px); opacity:.8; }
        }
        html { scroll-behavior: smooth; }
      `}</style>
    </>
  );
}
