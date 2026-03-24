"use client";
import { useEffect, useRef, useState } from "react";

const NODES = [
  { id: "core",       label: "Sebol Core",       importance: 5, desc: "AI Agent Hub",         color: "#00d4ff", angle: 0,   radius: 0,   orbitSpeed: 0 },
  // Imp4 — 84px gap (> sum radii 52 + big buffer → zero collision)
  { id: "claude",     label: "Claude AI",         importance: 4, desc: "LLM Engine",           color: "#a78bfa", angle: 25,  radius: 96,  orbitSpeed: 0.000200 },
  { id: "slack",      label: "Slack",             importance: 4, desc: "Bolt + Socket Mode",   color: "#4ade80", angle: 148, radius: 180, orbitSpeed: 0.000164 },
  { id: "meta",       label: "Meta Ads",          importance: 4, desc: "Facebook / Instagram", color: "#fb923c", angle: 245, radius: 264, orbitSpeed: 0.000142 },
  { id: "google",     label: "Google Ads",        importance: 4, desc: "Search & Display",     color: "#facc15", angle: 320, radius: 348, orbitSpeed: 0.000127 },
  // Imp3 — expanded gaps (Saturn rings extend to r*2.1 → need >54px clear from orbit 348)
  { id: "strategy",   label: "Strategy Engine",   importance: 3, desc: "Self-Learning AI",     color: "#c084fc", angle: 348, radius: 440, orbitSpeed: 0.000118 },
  { id: "standup",    label: "Standup Bot",       importance: 3, desc: "Team Automation",      color: "#34d399", angle: 128, radius: 490, orbitSpeed: 0.000111 },
  { id: "campaign",   label: "Kampanie",          importance: 3, desc: "Approval Workflow",    color: "#f472b6", angle: 190, radius: 540, orbitSpeed: 0.000106 },
  { id: "digest",     label: "Daily Digest",      importance: 3, desc: "Performance Alerts",   color: "#38bdf8", angle: 68,  radius: 590, orbitSpeed: 0.000101 },
  // Imp2 — expanded outward proportionally
  { id: "token",      label: "Token Optimizer",   importance: 2, desc: "API Cost Reducer",     color: "#fbbf24", angle: 290, radius: 635, orbitSpeed: 0.000105 },
  { id: "blockkit",   label: "Block Kit UI",      importance: 2, desc: "Slack Modals",         color: "#22d3ee", angle: 160, radius: 672, orbitSpeed: 0.000078 },
  { id: "scheduler",  label: "APScheduler",       importance: 2, desc: "Job Scheduling",       color: "#64748b", angle: 20,  radius: 708, orbitSpeed: 0.000115 },
  { id: "onboarding", label: "Onboarding",        importance: 2, desc: "Client Checklists",    color: "#86efac", angle: 210, radius: 742, orbitSpeed: 0.000072 },
  { id: "render",     label: "Render.com",        importance: 2, desc: "Cloud Deployment",     color: "#94a3b8", angle: 55,  radius: 775, orbitSpeed: 0.000098 },
];

// NASA / Wikimedia Commons public domain planet textures (thumbnail versions for fast loading)
const PLANET_IMAGES = {
  // Sun — NASA SDO (AIA 304 Å extreme ultraviolet, vivid orange/gold)
  core:       "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b4/The_Sun_by_the_Atmospheric_Imaging_Assembly_of_NASA%27s_Solar_Dynamics_Observatory_-_20100819.jpg/600px-The_Sun_by_the_Atmospheric_Imaging_Assembly_of_NASA%27s_Solar_Dynamics_Observatory_-_20100819.jpg",
  // Neptune — Voyager 2 true color (deep blue)
  claude:     "https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Neptune_Full.jpg/600px-Neptune_Full.jpg",
  // Earth — Apollo 17 Blue Marble
  slack:      "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/The_Earth_seen_from_Apollo_17.jpg/600px-The_Earth_seen_from_Apollo_17.jpg",
  // Jupiter — Juno mission PJ21 closeup (vivid bands & storms)
  meta:       "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Jupiter_New_Horizons.jpg/600px-Jupiter_New_Horizons.jpg",
  // Saturn — NO image here: rendered as canvas gradient sphere so ring texture doesn't double up
  // Mercury — MESSENGER true color
  digest:     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4a/Mercury_in_true_color.jpg/600px-Mercury_in_true_color.jpg",
  // Mars — OSIRIS true color (rusty red)
  campaign:   "https://upload.wikimedia.org/wikipedia/commons/thumb/0/02/OSIRIS_Mars_true_color.jpg/600px-OSIRIS_Mars_true_color.jpg",
  // Venus — Mariner 10 (golden clouds)
  standup:    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Venus-real_color.jpg/600px-Venus-real_color.jpg",
  // Uranus — Voyager 2 (pale blue-green)
  strategy:   "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Uranus2.jpg/600px-Uranus2.jpg",
  // Moon — full moon true color
  scheduler:  "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/FullMoon2010.jpg/600px-FullMoon2010.jpg",
  // Io — Galileo spacecraft (volcanic yellow-orange)
  blockkit:   "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/Io_highest_resolution_true_color.jpg/600px-Io_highest_resolution_true_color.jpg",
  // Europa — Galileo (icy blue-white cracks)
  onboarding: "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Europa-moon-with-margins.jpg/600px-Europa-moon-with-margins.jpg",
  // Pluto — New Horizons true color (heart terrain)
  token:      "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ef/Pluto_in_True_Color_-_High-Res.jpg/600px-Pluto_in_True_Color_-_High-Res.jpg",
  // Titan — Cassini (orange haze)
  render:     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Titan_in_true_color.jpg/600px-Titan_in_true_color.jpg",
};

// Axial rotation speed (texture scroll) — faster = spins quicker
const ROTATION_SPEEDS = {
  core:       0.000055, // Sun   — slow, majestic
  claude:     0.000048, // Neptune — slow gas giant
  slack:      0.000110, // Earth — medium
  meta:       0.000038, // Jupiter — slow (huge mass)
  google:     0.000032, // Saturn — slowest large planet
  digest:     0.000190, // Mercury — fast tiny planet
  campaign:   0.000075, // Mars
  standup:    0.000028, // Venus — very slow retrograde
  strategy:   0.000060, // Uranus
  scheduler:  0.000130, // Moon — moderate
  blockkit:   0.000160, // Io — fast (tidal lock makes it orbit fast)
  onboarding: 0.000120, // Europa
  token:      0.000058, // Pluto — slow dwarf planet
  render:     0.000045, // Titan
};

const EDGES = [
  ["core","claude"],["core","slack"],["core","meta"],["core","google"],
  ["core","scheduler"],["core","render"],
  ["claude","strategy"],["claude","digest"],["claude","campaign"],
  ["slack","blockkit"],["slack","digest"],["slack","standup"],["slack","campaign"],
  ["meta","digest"],["meta","campaign"],["meta","strategy"],
  ["google","digest"],["google","campaign"],["google","strategy"],
  ["scheduler","digest"],["scheduler","standup"],
  ["blockkit","campaign"],["blockkit","onboarding"],
  ["token","meta"],["token","claude"],
  ["render","scheduler"],
];

const SIZES = { 5: 38, 4: 26, 3: 18, 2: 12 };

const TICKER_MSGS = [
  "14:23 · Meta Ads · 3 kampanie zaktualizowane · spend +12%",
  "14:18 · Google Ads · Budget alert · Campaign at 87% pace",
  "14:15 · Claude API · 2,340 tokenów · cache hit 78%",
  "14:12 · Standup Bot · @team daily check-in wysłany do 5 osób",
  "14:08 · Daily Digest · 5 klientów · raport wygenerowany ✓",
  "14:05 · Block Kit UI · Wizard kampanii Meta · krok 3/5",
  "14:01 · Token Optimizer · Oszczędność 340 tokenów w ostatniej godz.",
  "13:58 · Render.com · Deploy #371 · Build successful ✓",
  "13:55 · APScheduler · Budget alert job · 12 kampanii sprawdzonych",
  "13:50 · Strategy Engine · Weekly learnings · 7 sugestii wygenerowanych",
];

const hexToRgb = (hex) => {
  const r = parseInt(hex.slice(1,3), 16);
  const g = parseInt(hex.slice(3,5), 16);
  const b = parseInt(hex.slice(5,7), 16);
  return `${r},${g},${b}`;
};

class EdgeParticle {
  constructor(fromNode, toNode) {
    this.from = fromNode;
    this.to = toNode;
    this.t = 0;
    this.speed = 0.004 + Math.random() * 0.004;
    this.size = 1.5 + Math.random() * 1.5;
    this.opacity = 0.6 + Math.random() * 0.4;
  }
  update() { this.t += this.speed; return this.t <= 1; }
  getPos() {
    return {
      x: this.from.x + (this.to.x - this.from.x) * this.t,
      y: this.from.y + (this.to.y - this.from.y) * this.t,
    };
  }
}

export default function SebolGalaxy() {
  const canvasRef = useRef(null);
  const canvasWrapRef = useRef(null);
  const animRef = useRef(null);
  const starsRef = useRef([]);
  const particlesRef = useRef([]);
  const timeRef = useRef(0);
  const hoveredRef = useRef(null);
  const lastParticleSpawn = useRef(0);
  const imagesRef = useRef({});
  const imagesLoadedRef = useRef({});
  const tailsRef = useRef({});
  const orbitPacketsRef = useRef([]);
  const lastOrbitPacketRef = useRef(0);
  const shootingStarsRef = useRef([]);
  const lastShootingStarRef = useRef(0);
  const tiltRef = useRef({ x: 0, y: 0, tx: 0, ty: 0 });
  const [clickedNode, setClickedNode] = useState(null);
  const [tooltip, setTooltip] = useState(null);
  const [imagesReady, setImagesReady] = useState(false);

  // Preload planet images
  useEffect(() => {
    let loaded = 0;
    const total = Object.keys(PLANET_IMAGES).length;
    Object.entries(PLANET_IMAGES).forEach(([id, url]) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        imagesLoadedRef.current[id] = true;
        loaded++;
        if (loaded >= total) setImagesReady(true);
      };
      img.onerror = () => {
        // fallback: mark as failed so gradient is used for this node
        imagesLoadedRef.current[id] = false;
        loaded++;
        if (loaded >= total) setImagesReady(true);
      };
      img.src = url;
      imagesRef.current[id] = img;
    });
  }, []);

  useEffect(() => {
    starsRef.current = Array.from({ length: 350 }, () => ({
      x: Math.random(), y: Math.random(),
      size: Math.random() * 1.4 + 0.2,
      bright: Math.random(),
      phase: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.015 + 0.004,
    }));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const resize = () => {
      canvas.width = canvas.offsetWidth * devicePixelRatio;
      canvas.height = canvas.offsetHeight * devicePixelRatio;
      ctx.scale(devicePixelRatio, devicePixelRatio);
    };
    resize();
    window.addEventListener("resize", resize);

    const getScale = () => Math.min(
      canvas.width / devicePixelRatio,
      canvas.height / devicePixelRatio
    ) / 1500;

    const getPositions = (t) => {
      const cw = canvas.width / devicePixelRatio / 2;
      const ch = canvas.height / devicePixelRatio / 2;
      const s = getScale();
      return NODES.map(n => {
        const a = (n.angle * Math.PI / 180) + t * n.orbitSpeed;
        return {
          ...n,
          x: cw + Math.cos(a) * n.radius * s,
          y: ch + Math.sin(a) * n.radius * s,
          r: SIZES[n.importance] * s,
        };
      });
    };

    const draw = (ts) => {
      timeRef.current = ts;
      const W = canvas.width / devicePixelRatio;
      const H = canvas.height / devicePixelRatio;
      const cx = W / 2, cy = H / 2;

      ctx.fillStyle = "#03060e";
      ctx.fillRect(0, 0, W, H);

      [
        [cx,      cy,      W*0.55, "rgba(0,80,180,0.07)"],
        [cx-W*0.12, cy+H*0.08, W*0.3, "rgba(80,0,180,0.05)"],
        [cx+W*0.1, cy-H*0.1,  W*0.25, "rgba(0,180,120,0.04)"],
        [cx-W*0.08, cy-H*0.12,W*0.2,  "rgba(200,60,0,0.03)"],
      ].forEach(([nx,ny,nr,col]) => {
        const g = ctx.createRadialGradient(nx,ny,0,nx,ny,nr);
        g.addColorStop(0, col);
        g.addColorStop(1, "transparent");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
      });

      starsRef.current.forEach(star => {
        star.phase += star.speed;
        const a = (0.15 + 0.85 * (Math.sin(star.phase) * 0.5 + 0.5)) * star.bright;
        ctx.beginPath();
        ctx.arc(star.x * W, star.y * H, star.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(200,220,255,${a})`;
        ctx.fill();
      });

      const coreGlow = ctx.createRadialGradient(cx,cy,0,cx,cy,getScale()*120);
      coreGlow.addColorStop(0, "rgba(0,212,255,0.18)");
      coreGlow.addColorStop(0.5, "rgba(0,100,200,0.06)");
      coreGlow.addColorStop(1, "transparent");
      ctx.fillStyle = coreGlow;
      ctx.fillRect(0, 0, W, H);

      const positions = getPositions(ts);
      const posMap = {};
      positions.forEach(p => { posMap[p.id] = p; });

      const hovered = hoveredRef.current;
      const connected = new Set();
      if (hovered) {
        EDGES.forEach(([a,b]) => {
          if (a === hovered || b === hovered) { connected.add(a); connected.add(b); }
        });
      }

      if (hovered && ts - lastParticleSpawn.current > 80) {
        lastParticleSpawn.current = ts;
        EDGES.forEach(([a,b]) => {
          if (a === hovered || b === hovered) {
            particlesRef.current.push(new EdgeParticle(posMap[a], posMap[b]));
            particlesRef.current.push(new EdgeParticle(posMap[b], posMap[a]));
          }
        });
      }

      particlesRef.current = particlesRef.current.filter(p => p.update());

      // ── Shooting stars ──────────────────────────────────────────────────────
      if (ts - lastShootingStarRef.current > 2800 + Math.random() * 3200) {
        lastShootingStarRef.current = ts;
        const angle = Math.PI * (0.15 + Math.random() * 0.25);
        const speed = 5 + Math.random() * 7;
        shootingStarsRef.current.push({
          x: Math.random() * W, y: -10,
          vx: Math.cos(angle + (Math.random()-0.5)*0.4) * speed,
          vy: Math.sin(angle) * speed,
          life: 1.0, tailLen: 60 + Math.random() * 80,
        });
      }
      shootingStarsRef.current = shootingStarsRef.current.filter(ss => {
        ss.x += ss.vx; ss.y += ss.vy; ss.life -= 0.022;
        if (ss.life <= 0 || ss.y > H + 50) return false;
        const spd = Math.hypot(ss.vx, ss.vy);
        const nx = ss.vx / spd, ny = ss.vy / spd;
        const grad = ctx.createLinearGradient(
          ss.x - nx * ss.tailLen, ss.y - ny * ss.tailLen, ss.x, ss.y
        );
        grad.addColorStop(0, "rgba(255,255,255,0)");
        grad.addColorStop(1, `rgba(210,230,255,${0.95 * ss.life})`);
        ctx.beginPath();
        ctx.moveTo(ss.x - nx * ss.tailLen, ss.y - ny * ss.tailLen);
        ctx.lineTo(ss.x, ss.y);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.8 * ss.life;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(ss.x, ss.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,255,255,${ss.life})`;
        ctx.shadowColor = "#aaccff"; ctx.shadowBlur = 10;
        ctx.fill(); ctx.shadowBlur = 0;
        return true;
      });

      const s = getScale();

      // ── Subtle orbit rings ──────────────────────────────────────────────────
      positions.forEach(node => {
        if (node.radius === 0) return;
        const orbitR = node.radius * s;
        const isActive = hovered && (node.id === hovered || connected.has(node.id));
        const opacity = isActive ? 0.18 : (hovered ? 0.03 : 0.07);
        ctx.beginPath();
        ctx.arc(cx, cy, orbitR, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${hexToRgb(node.color)},${opacity})`;
        ctx.lineWidth = isActive ? 1 : 0.5;
        ctx.stroke();
      });

      // ── Orbit data packets ──────────────────────────────────────────────────
      if (ts - lastOrbitPacketRef.current > 350 + Math.random() * 400) {
        lastOrbitPacketRef.current = ts;
        const orbitNodes = NODES.filter(n => n.radius > 0);
        const n = orbitNodes[Math.floor(Math.random() * orbitNodes.length)];
        orbitPacketsRef.current.push({
          orbitRadius: n.radius,
          angle: Math.random() * Math.PI * 2,
          angularSpeed: (0.0015 + Math.random() * 0.002) * (Math.random() > 0.5 ? 1 : -1),
          color: n.color,
          life: 1.0,
        });
      }
      orbitPacketsRef.current = orbitPacketsRef.current.filter(p => {
        p.angle += p.angularSpeed;
        p.life -= 0.0025;
        if (p.life <= 0) return false;
        const screenR = p.orbitRadius * s;
        const fade = Math.min(1, p.life * 4) * Math.min(1, (1 - p.life) * 4 + 0.3);
        for (let t = 1; t <= 4; t++) {
          const ga = p.angle - p.angularSpeed * t * 6;
          const gx = cx + Math.cos(ga) * screenR;
          const gy = cy + Math.sin(ga) * screenR;
          ctx.beginPath();
          ctx.arc(gx, gy, Math.max(0.5, 2 - t * 0.4), 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${hexToRgb(p.color)},${(0.7 - t * 0.15) * fade})`;
          ctx.fill();
        }
        const px = cx + Math.cos(p.angle) * screenR;
        const py = cy + Math.sin(p.angle) * screenR;
        ctx.beginPath();
        ctx.arc(px, py, 3, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${hexToRgb(p.color)},${fade})`;
        ctx.shadowColor = p.color; ctx.shadowBlur = 12;
        ctx.fill(); ctx.shadowBlur = 0;
        return true;
      });

      EDGES.forEach(([a, b]) => {
        const na = posMap[a], nb = posMap[b];
        if (!na || !nb) return;
        const isLit = hovered && connected.has(a) && connected.has(b);
        const dimmed = hovered && !isLit;
        const opacity = dimmed ? 0.04 : (isLit ? 0.75 : 0.18);
        const lw = isLit ? 1.4 : 0.7;
        const grad = ctx.createLinearGradient(na.x, na.y, nb.x, nb.y);
        grad.addColorStop(0, `rgba(${hexToRgb(na.color)},${opacity})`);
        grad.addColorStop(1, `rgba(${hexToRgb(nb.color)},${opacity})`);
        ctx.beginPath();
        ctx.moveTo(na.x, na.y);
        ctx.lineTo(nb.x, nb.y);
        ctx.strokeStyle = grad;
        ctx.lineWidth = lw;
        if (isLit) {
          ctx.shadowColor = `rgba(${hexToRgb(na.color)},0.6)`;
          ctx.shadowBlur = 10;
        } else {
          ctx.shadowBlur = 0;
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
      });

      particlesRef.current.forEach(p => {
        const {x, y} = p.getPos();
        const fade = Math.sin(p.t * Math.PI);
        ctx.beginPath();
        ctx.arc(x, y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${hexToRgb(p.from.color)},${p.opacity * fade})`;
        ctx.shadowColor = p.from.color;
        ctx.shadowBlur = 6;
        ctx.fill();
        ctx.shadowBlur = 0;
      });

      positions.forEach(node => {
        const isHov = node.id === hovered;
        const isConn = connected.has(node.id);
        const dimmed = hovered && !isHov && !isConn;
        const pulse = 1 + 0.07 * Math.sin(ts * 0.0018 + node.angle * 0.05);
        const extraScale = isHov ? 1.35 : 1;
        const r = node.r * pulse * extraScale;
        const alpha = dimmed ? 0.25 : 1;

        // ── Comet tail ─────────────────────────────────────────────────────────
        if (node.radius > 0) {
          if (!tailsRef.current[node.id]) tailsRef.current[node.id] = [];
          const tail = tailsRef.current[node.id];
          tail.push({ x: node.x, y: node.y });
          if (tail.length > 28) tail.shift();
          for (let i = 1; i < tail.length; i++) {
            const t = i / tail.length;
            ctx.beginPath();
            ctx.arc(tail[i].x, tail[i].y, Math.max(0.4, t * node.r * 0.45), 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${hexToRgb(node.color)},${t * t * 0.40 * (dimmed ? 0.2 : 1)})`;
            ctx.fill();
          }
        }

        // Halo glow
        const haloR = r * (isHov ? 4.5 : 3.2);
        const halo = ctx.createRadialGradient(node.x, node.y, r*0.5, node.x, node.y, haloR);
        halo.addColorStop(0, `rgba(${hexToRgb(node.color)},${0.28 * alpha})`);
        halo.addColorStop(0.6, `rgba(${hexToRgb(node.color)},${0.05 * alpha})`);
        halo.addColorStop(1, "transparent");
        ctx.fillStyle = halo;
        ctx.beginPath();
        ctx.arc(node.x, node.y, haloR, 0, Math.PI * 2);
        ctx.fill();

        // Planet body — use real texture if loaded, else gradient fallback
        const img = imagesRef.current[node.id];
        const imgLoaded = imagesLoadedRef.current[node.id];

        // Saturn rings — back half (upper visual arc, behind planet)
        if (node.id === "google") {
          // ry/rx ratio = perspective squish (0.38 ≈ 22° tilt looks natural)
          const PY = 0.38;
          // Each band: [innerRx, outerRx, fillColor, opacity] — outer capped at r*2.1 to not clip adjacent orbit
          const bands = [
            [r*1.20, r*1.40, "#c8b478", 0.42],   // C ring
            [r*1.42, r*1.82, "#ead490", 0.80],   // B ring
            [r*1.90, r*2.10, "#d4b87c", 0.62],   // A ring
          ];
          ctx.save();
          bands.forEach(([ir, or_, color, oa]) => {
            ctx.globalAlpha = alpha * oa;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.ellipse(node.x, node.y, or_, or_ * PY, 0, Math.PI, Math.PI * 2, false);
            ctx.ellipse(node.x, node.y, ir,  ir  * PY, 0, Math.PI * 2, Math.PI, true);
            ctx.closePath();
            ctx.fill();
          });
          ctx.restore();
        }

        if (img && imgLoaded) {
          const imgSize = r * 2;

          // Sun: animated corona rays BEFORE planet body
          if (node.id === "core") {
            const rayCount = 14;
            const rayAngle = ts * 0.00055;
            ctx.save();
            for (let i = 0; i < rayCount; i++) {
              const a = (i / rayCount) * Math.PI * 2 + rayAngle;
              const innerR = r * 1.08;
              const outerR = r * (1.75 + 0.28 * Math.sin(ts * 0.0013 + i * 0.9));
              const half = 0.055 + 0.025 * Math.sin(ts * 0.0019 + i);
              const grad = ctx.createLinearGradient(
                node.x + Math.cos(a) * innerR, node.y + Math.sin(a) * innerR,
                node.x + Math.cos(a) * outerR, node.y + Math.sin(a) * outerR,
              );
              grad.addColorStop(0, `rgba(255,210,60,${0.55 * alpha})`);
              grad.addColorStop(1, "rgba(255,120,0,0)");
              ctx.beginPath();
              ctx.moveTo(node.x + Math.cos(a - half) * innerR, node.y + Math.sin(a - half) * innerR);
              ctx.lineTo(node.x + Math.cos(a) * outerR, node.y + Math.sin(a) * outerR);
              ctx.lineTo(node.x + Math.cos(a + half) * innerR, node.y + Math.sin(a + half) * innerR);
              ctx.fillStyle = grad;
              ctx.fill();
            }
            ctx.restore();
          }

          // Outer atmosphere glow — skip for Saturn (rings replace it visually)
          if (node.id !== "core" && node.id !== "google") {
            ctx.save();
            ctx.globalAlpha = alpha;
            const outerAtmo = ctx.createRadialGradient(node.x, node.y, r * 0.9, node.x, node.y, r * 1.28);
            outerAtmo.addColorStop(0, `rgba(${hexToRgb(node.color)},${0.55 * alpha})`);
            outerAtmo.addColorStop(0.5, `rgba(${hexToRgb(node.color)},${0.18 * alpha})`);
            outerAtmo.addColorStop(1,   "rgba(0,0,0,0)");
            ctx.fillStyle = outerAtmo;
            ctx.beginPath();
            ctx.arc(node.x, node.y, r * 1.28, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
          }

          // Rotating texture via context rotation — no seam, no sliding edges
          const rotSpeed = ROTATION_SPEEDS[node.id] || 0.0001;
          const rotAngle = node.id === "core" ? 0 : ts * rotSpeed * 1.5;

          ctx.save();
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.clip();
          ctx.globalAlpha = alpha;
          ctx.translate(node.x, node.y);
          ctx.rotate(rotAngle);
          ctx.translate(-node.x, -node.y);
          ctx.drawImage(img, node.x - r, node.y - r, imgSize, imgSize);
          ctx.restore(); // end rotation

          // Fixed lighting overlays (not rotated) — limb darkening + atmosphere
          ctx.save();
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.clip();
          ctx.globalAlpha = alpha;

          // Limb darkening — edges darker, light from upper-left
          const limbFx = node.x - r * 0.18;
          const limbFy = node.y - r * 0.18;
          const limb = ctx.createRadialGradient(limbFx, limbFy, r * 0.25, node.x, node.y, r);
          limb.addColorStop(0,    "rgba(0,0,0,0)");
          limb.addColorStop(0.58, "rgba(0,0,0,0)");
          limb.addColorStop(0.80, `rgba(0,0,0,${0.18 * alpha})`);
          limb.addColorStop(1,    `rgba(0,0,0,${0.72 * alpha})`);
          ctx.fillStyle = limb;
          ctx.fillRect(node.x - r, node.y - r, imgSize, imgSize);

          // Colored atmosphere inner rim
          const atmo = ctx.createRadialGradient(node.x, node.y, r * 0.7, node.x, node.y, r);
          atmo.addColorStop(0, `rgba(${hexToRgb(node.color)},0)`);
          atmo.addColorStop(1, `rgba(${hexToRgb(node.color)},${0.4 * alpha})`);
          ctx.fillStyle = atmo;
          ctx.fillRect(node.x - r, node.y - r, imgSize, imgSize);
          ctx.restore();

          // Specular highlight — strong, sells the 3D sphere illusion
          ctx.save();
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.clip();
          ctx.globalAlpha = alpha * (node.id === "core" ? 0.12 : 0.5);
          const specG = ctx.createRadialGradient(
            node.x - r * 0.38, node.y - r * 0.38, 0,
            node.x - r * 0.38, node.y - r * 0.38, r * 0.72,
          );
          specG.addColorStop(0,   "rgba(255,255,255,1.0)");
          specG.addColorStop(0.25,"rgba(255,255,255,0.5)");
          specG.addColorStop(1,   "rgba(255,255,255,0)");
          ctx.fillStyle = specG;
          ctx.fillRect(node.x - r, node.y - r, imgSize, imgSize);
          ctx.restore();

          // Saturn rings — front half (lower visual arc, in front of planet)
          if (node.id === "google") {
            const PY = 0.38;
            const bands = [
              [r*1.20, r*1.40, "#c8b478", 0.50],
              [r*1.42, r*1.82, "#ead490", 0.92],
              [r*1.90, r*2.10, "#d4b87c", 0.74],
            ];
            ctx.save();
            bands.forEach(([ir, or_, color, oa]) => {
              ctx.globalAlpha = alpha * oa;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.ellipse(node.x, node.y, or_, or_ * PY, 0, 0, Math.PI, false);
              ctx.ellipse(node.x, node.y, ir,  ir  * PY, 0, Math.PI, 0,        true);
              ctx.closePath();
              ctx.fill();
            });
            ctx.restore();
          }
        } else if (node.id === "google") {
          // Saturn: canvas-drawn banded sphere (no texture image → no ring double-up)
          ctx.save();
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.clip();

          // Base — warm tan gradient
          const satBase = ctx.createRadialGradient(node.x - r*0.25, node.y - r*0.25, 0, node.x, node.y, r);
          satBase.addColorStop(0,   `rgba(255,240,190,${alpha})`);
          satBase.addColorStop(0.5, `rgba(220,190,120,${alpha})`);
          satBase.addColorStop(1,   `rgba(160,130, 70,${alpha * 0.7})`);
          ctx.globalAlpha = 1;
          ctx.fillStyle = satBase;
          ctx.fillRect(node.x - r, node.y - r, r*2, r*2);

          // Atmosphere bands — thin horizontal stripes (linear gradient)
          const bands = ctx.createLinearGradient(node.x, node.y - r, node.x, node.y + r);
          bands.addColorStop(0.00, "rgba(200,170,100,0)");
          bands.addColorStop(0.12, "rgba(180,150, 90,0.55)");
          bands.addColorStop(0.22, "rgba(240,210,140,0.30)");
          bands.addColorStop(0.34, "rgba(170,140, 80,0.60)");
          bands.addColorStop(0.44, "rgba(230,200,130,0.25)");
          bands.addColorStop(0.50, "rgba(200,175,110,0.40)"); // equator
          bands.addColorStop(0.56, "rgba(240,215,145,0.25)");
          bands.addColorStop(0.66, "rgba(165,138, 78,0.60)");
          bands.addColorStop(0.78, "rgba(235,205,135,0.30)");
          bands.addColorStop(0.88, "rgba(175,148, 88,0.55)");
          bands.addColorStop(1.00, "rgba(200,170,100,0)");
          ctx.fillStyle = bands;
          ctx.fillRect(node.x - r, node.y - r, r*2, r*2);

          // Limb darkening
          const limb = ctx.createRadialGradient(node.x - r*0.18, node.y - r*0.18, r*0.3, node.x, node.y, r);
          limb.addColorStop(0,    "rgba(0,0,0,0)");
          limb.addColorStop(0.65, "rgba(0,0,0,0)");
          limb.addColorStop(1,    `rgba(0,0,0,${0.65*alpha})`);
          ctx.fillStyle = limb;
          ctx.fillRect(node.x - r, node.y - r, r*2, r*2);

          // Specular highlight
          ctx.globalAlpha = alpha * 0.45;
          const spec = ctx.createRadialGradient(node.x - r*0.38, node.y - r*0.38, 0, node.x - r*0.38, node.y - r*0.38, r*0.65);
          spec.addColorStop(0,   "rgba(255,255,255,1)");
          spec.addColorStop(0.3, "rgba(255,255,255,0.4)");
          spec.addColorStop(1,   "rgba(255,255,255,0)");
          ctx.fillStyle = spec;
          ctx.fillRect(node.x - r, node.y - r, r*2, r*2);

          ctx.restore();

          // Saturn rings front half (drawn OVER the sphere)
          {
            const PY = 0.38;
            const satBands = [
              [r*1.20, r*1.40, "#c8b478", 0.50],
              [r*1.42, r*1.82, "#ead490", 0.92],
              [r*1.90, r*2.10, "#d4b87c", 0.74],
            ];
            ctx.save();
            satBands.forEach(([ir, or_, color, oa]) => {
              ctx.globalAlpha = alpha * oa;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.ellipse(node.x, node.y, or_, or_ * PY, 0, 0, Math.PI, false);
              ctx.ellipse(node.x, node.y, ir,  ir  * PY, 0, Math.PI, 0, true);
              ctx.closePath();
              ctx.fill();
            });
            ctx.restore();
          }
        } else {
          const body = ctx.createRadialGradient(
            node.x - r*0.3, node.y - r*0.3, 0,
            node.x, node.y, r
          );
          body.addColorStop(0, `rgba(255,255,255,${0.95 * alpha})`);
          body.addColorStop(0.35, `rgba(${hexToRgb(node.color)},${0.85 * alpha})`);
          body.addColorStop(1, `rgba(${hexToRgb(node.color)},${0.4 * alpha})`);
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fillStyle = body;
          ctx.shadowColor = node.color;
          ctx.shadowBlur = isHov ? 30 : 14;
          ctx.fill();
          ctx.shadowBlur = 0;
        }

        // Hover ring only (no permanent border)
        if (isHov) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 4, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${hexToRgb(node.color)},0.7)`;
          ctx.lineWidth = 1.5;
          ctx.shadowColor = node.color;
          ctx.shadowBlur = 20;
          ctx.stroke();
          ctx.shadowBlur = 0;
        }

        if (node.importance === 5) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 8 + 4 * Math.sin(ts * 0.003), 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${hexToRgb(node.color)},${0.35 * alpha})`;
          ctx.lineWidth = 1.2;
          ctx.shadowColor = node.color;
          ctx.shadowBlur = 10;
          ctx.stroke();
          ctx.shadowBlur = 0;
        }

        const fs = Math.max(9, Math.round(r * 0.85));
        ctx.font = `700 ${fs}px 'Orbitron', 'Share Tech Mono', monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const lx = node.x;
        const ly = node.y + r + 7;
        ctx.fillStyle = "rgba(0,0,0,0.8)";
        ctx.fillText(node.label, lx+1, ly+1);
        ctx.fillStyle = `rgba(${hexToRgb(node.color)},${0.95 * alpha})`;
        ctx.shadowColor = node.color;
        ctx.shadowBlur = isHov || isConn ? 12 : 4;
        ctx.fillText(node.label, lx, ly);
        ctx.shadowBlur = 0;

        if (isHov || isConn) {
          const sfs = Math.max(8, Math.round(r * 0.62));
          ctx.font = `400 ${sfs}px 'Share Tech Mono', monospace`;
          ctx.fillStyle = "rgba(160,220,255,0.75)";
          ctx.fillText(node.desc, lx, ly + fs + 4);
        }
      });

      // ── 3D tilt lerp ───────────────────────────────────────────────────────
      const tilt = tiltRef.current;
      tilt.x += (tilt.tx - tilt.x) * 0.06;
      tilt.y += (tilt.ty - tilt.y) * 0.06;
      if (canvasWrapRef.current) {
        canvasWrapRef.current.style.transform =
          `perspective(900px) rotateX(${tilt.x.toFixed(3)}deg) rotateY(${tilt.y.toFixed(3)}deg)`;
      }

      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);

    const handleMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = (e.clientX - rect.left);
      const my = (e.clientY - rect.top);
      const W2 = canvas.width / devicePixelRatio;
      const H2 = canvas.height / devicePixelRatio;
      // 3D tilt target
      tiltRef.current.tx = ((my / H2) - 0.5) * -6;
      tiltRef.current.ty = ((mx / W2) - 0.5) * 8;
      const positions = getPositions(timeRef.current);
      const scale = getScale();
      let found = null;
      for (const n of positions) {
        const hit = SIZES[n.importance] * Math.max(0.55, scale) + 12;
        if (Math.hypot(mx - n.x, my - n.y) < hit) { found = n.id; break; }
      }
      hoveredRef.current = found;
      canvas.style.cursor = found ? "pointer" : "default";
      if (found) {
        const node = positions.find(n => n.id === found);
        setTooltip({ id: found, x: node.x, y: node.y - node.r - 10, label: node.label, desc: node.desc });
      } else {
        setTooltip(null);
      }
    };

    const handleClick = (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const positions = getPositions(timeRef.current);
      const scale = getScale();
      for (const n of positions) {
        const hit = SIZES[n.importance] * Math.max(0.55, scale) + 12;
        if (Math.hypot(mx - n.x, my - n.y) < hit) {
          setClickedNode(prev => prev?.id === n.id ? null : { id: n.id, label: n.label, desc: n.desc, color: n.color, cx: mx, cy: my });
          return;
        }
      }
      setClickedNode(null);
    };

    const handleMouseLeave = () => {
      tiltRef.current.tx = 0;
      tiltRef.current.ty = 0;
    };

    canvas.addEventListener("mousemove", handleMove);
    canvas.addEventListener("click", handleClick);
    canvas.addEventListener("mouseleave", handleMouseLeave);
    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousemove", handleMove);
      canvas.removeEventListener("click", handleClick);
      canvas.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, []);

  return (
    <>
    <div style={{ width:"100vw", height:"100vh", background:"#03060e", display:"flex", flexDirection:"column", overflow:"hidden" }}>

      {/* ── Title bar — separate section, never overlaps galaxy ── */}
      <div style={{
        flexShrink: 0, height: 72,
        display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
        borderBottom:"1px solid rgba(0,212,255,0.08)",
        background:"linear-gradient(180deg,rgba(0,20,40,0.9) 0%,rgba(3,6,14,0) 100%)",
      }}>
        <div style={{ fontFamily:"'Orbitron',monospace", fontWeight:900, fontSize:"clamp(18px,2.8vw,32px)",
          color:"#00d4ff", letterSpacing:"0.35em",
          textShadow:"0 0 18px #00d4ff, 0 0 50px rgba(0,212,255,0.35)",
        }}>SEBOL</div>
        <div style={{ fontFamily:"'Share Tech Mono',monospace", fontSize:"clamp(8px,1vw,11px)",
          color:"rgba(0,212,255,0.45)", letterSpacing:"0.5em", marginTop:3,
        }}>GALAKTYKA SYSTEMU · v2.0</div>
      </div>

      {/* ── Galaxy canvas — fills remaining space ── */}
      <div
        ref={canvasWrapRef}
        style={{
          flex:1, position:"relative", overflow:"hidden",
          transformOrigin:"center center", transformStyle:"preserve-3d",
        }}
      >
        <div style={{
          position:"absolute", bottom:34, left:20, zIndex:10,
          fontFamily:"'Share Tech Mono',monospace", fontSize:10,
          color:"rgba(100,180,255,0.45)", letterSpacing:"0.1em", lineHeight:1.8,
          pointerEvents:"none",
        }}>
          <div>● RDZEŃ &nbsp; ◉ API &nbsp; ○ MODUŁ &nbsp; · NARZĘDZIE</div>
          <div style={{ marginTop:3, color:"rgba(100,180,255,0.28)" }}>hover → podświetl · kliknij → szczegóły</div>
        </div>

        <div style={{
          position:"absolute", bottom:34, right:20, zIndex:10,
          fontFamily:"'Share Tech Mono',monospace", fontSize:10,
          color:"rgba(100,180,255,0.35)", letterSpacing:"0.1em", textAlign:"right",
          pointerEvents:"none",
        }}>
          <div>{NODES.length} WĘZŁÓW</div>
          <div>{EDGES.length} POŁĄCZEŃ</div>
        </div>

        <canvas ref={canvasRef} style={{ width:"100%", height:"100%", display:"block" }} />

        {/* Info card on planet click */}
        {clickedNode && (
          <div key={clickedNode.id} style={{
            position:"absolute",
            left: Math.min(clickedNode.cx + 18, "calc(100% - 220px)"),
            top: Math.max(10, clickedNode.cy - 50),
            background:"rgba(2,8,22,0.94)",
            backdropFilter:"blur(18px)",
            border:`1px solid rgba(${hexToRgb(clickedNode.color)},0.45)`,
            borderRadius:10, padding:"14px 18px",
            zIndex:20, pointerEvents:"none", width:200,
            boxShadow:`0 4px 32px rgba(${hexToRgb(clickedNode.color)},0.22)`,
            animation:"infoFadeIn .18s ease",
          }}>
            <div style={{ width:32, height:3, borderRadius:2, background:clickedNode.color, marginBottom:10, opacity:0.8 }} />
            <div style={{ fontFamily:"'Orbitron',monospace", fontSize:11, fontWeight:700, color:clickedNode.color, marginBottom:5 }}>
              {clickedNode.label}
            </div>
            <div style={{ fontFamily:"'Share Tech Mono',monospace", fontSize:10, color:"rgba(160,210,255,0.7)", lineHeight:1.6, marginBottom:10 }}>
              {clickedNode.desc}
            </div>
            <div style={{ fontFamily:"'Share Tech Mono',monospace", fontSize:9, color:"rgba(80,120,180,0.5)", letterSpacing:".05em" }}>
              KLIKNIJ ABY ZAMKNĄĆ
            </div>
          </div>
        )}

        {/* Live activity ticker */}
        <div style={{
          position:"absolute", bottom:0, left:0, right:0, height:26,
          background:"rgba(1,4,12,0.88)",
          borderTop:"1px solid rgba(0,212,255,0.1)",
          overflow:"hidden", display:"flex", alignItems:"center",
          zIndex:15, pointerEvents:"none",
        }}>
          <div style={{ display:"flex", animation:"tickerScroll 55s linear infinite", whiteSpace:"nowrap" }}>
            {[...TICKER_MSGS, ...TICKER_MSGS].map((msg, i) => (
              <span key={i} style={{
                fontFamily:"'Share Tech Mono',monospace",
                fontSize:10, padding:"0 44px",
                color:"rgba(0,195,255,0.45)",
              }}>
                <span style={{ color:"rgba(0,195,255,0.22)", marginRight:8 }}>►</span>
                {msg}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
    <style>{`
      @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
      @keyframes tickerScroll { from{transform:translateX(0)} to{transform:translateX(-50%)} }
      @keyframes infoFadeIn { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:translateY(0)} }
    `}</style>
    </>
  );
}
