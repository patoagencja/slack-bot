"use client";
import { useEffect, useRef, useState } from "react";

const NODES = [
  { id: "core",       label: "Sebol Core",       importance: 5, desc: "AI Agent Hub",         color: "#00d4ff", angle: 0,   radius: 0,   orbitSpeed: 0 },
  // Imp4 — każda na osobnej orbicie, 52px odstęp (> 2×rozmiar=52px)
  { id: "claude",     label: "Claude AI",         importance: 4, desc: "LLM Engine",           color: "#a78bfa", angle: 25,  radius: 96,  orbitSpeed: 0.000200 },
  { id: "slack",      label: "Slack",             importance: 4, desc: "Bolt + Socket Mode",   color: "#4ade80", angle: 148, radius: 148, orbitSpeed: 0.000164 },
  { id: "meta",       label: "Meta Ads",          importance: 4, desc: "Facebook / Instagram", color: "#fb923c", angle: 245, radius: 200, orbitSpeed: 0.000142 },
  { id: "google",     label: "Google Ads",        importance: 4, desc: "Search & Display",     color: "#facc15", angle: 320, radius: 252, orbitSpeed: 0.000127 },
  // Imp3 — 36px odstęp (> 2×rozmiar=36px)
  { id: "strategy",   label: "Strategy Engine",   importance: 3, desc: "Self-Learning AI",     color: "#c084fc", angle: 348, radius: 292, orbitSpeed: 0.000118 },
  { id: "standup",    label: "Standup Bot",       importance: 3, desc: "Team Automation",      color: "#34d399", angle: 128, radius: 328, orbitSpeed: 0.000111 },
  { id: "campaign",   label: "Kampanie",          importance: 3, desc: "Approval Workflow",    color: "#f472b6", angle: 190, radius: 364, orbitSpeed: 0.000106 },
  { id: "digest",     label: "Daily Digest",      importance: 3, desc: "Performance Alerts",   color: "#38bdf8", angle: 68,  radius: 400, orbitSpeed: 0.000101 },
  // Imp2 — 24px odstęp (= 2×rozmiar=24px), naprzemiennie szybsze/wolniejsze
  { id: "token",      label: "Token Optimizer",   importance: 2, desc: "API Cost Reducer",     color: "#fbbf24", angle: 290, radius: 428, orbitSpeed: 0.000105 },
  { id: "blockkit",   label: "Block Kit UI",      importance: 2, desc: "Slack Modals",         color: "#22d3ee", angle: 160, radius: 452, orbitSpeed: 0.000078 },
  { id: "scheduler",  label: "APScheduler",       importance: 2, desc: "Job Scheduling",       color: "#64748b", angle: 20,  radius: 476, orbitSpeed: 0.000115 },
  { id: "onboarding", label: "Onboarding",        importance: 2, desc: "Client Checklists",    color: "#86efac", angle: 210, radius: 500, orbitSpeed: 0.000072 },
  { id: "render",     label: "Render.com",        importance: 2, desc: "Cloud Deployment",     color: "#94a3b8", angle: 55,  radius: 524, orbitSpeed: 0.000098 },
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
  // Saturn — Cassini equinox (iconic rings)
  google:     "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/Saturn_during_Equinox.jpg/600px-Saturn_during_Equinox.jpg",
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
  const animRef = useRef(null);
  const starsRef = useRef([]);
  const particlesRef = useRef([]);
  const timeRef = useRef(0);
  const hoveredRef = useRef(null);
  const lastParticleSpawn = useRef(0);
  const imagesRef = useRef({});
  const imagesLoadedRef = useRef({});
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
    ) / 1180;

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
          r: SIZES[n.importance] * Math.max(0.55, s),
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

        // Saturn rings — back half (upper arc, drawn BEHIND planet)
        if (node.id === "google") {
          const tilt = 0.28;
          const rings = [
            { rx: r*1.3,  ry: r*0.22, lw: r*0.07, a: `rgba(185,160,105,${0.55*alpha})` },
            { rx: r*1.55, ry: r*0.26, lw: r*0.16, a: `rgba(228,202,140,${0.80*alpha})` },
            { rx: r*1.82, ry: r*0.31, lw: r*0.12, a: `rgba(210,185,128,${0.65*alpha})` },
            { rx: r*2.05, ry: r*0.35, lw: r*0.05, a: `rgba(190,165,110,${0.35*alpha})` },
          ];
          ctx.save();
          rings.forEach(({ rx, ry, lw, a }) => {
            ctx.strokeStyle = a;
            ctx.lineWidth = lw;
            ctx.beginPath();
            ctx.ellipse(node.x, node.y, rx, ry, tilt, Math.PI, Math.PI * 2);
            ctx.stroke();
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

          // Saturn rings — front half (lower arc, drawn IN FRONT of planet)
          if (node.id === "google") {
            const tilt = 0.28;
            const rings = [
              { rx: r*1.3,  ry: r*0.22, lw: r*0.07, a: `rgba(185,160,105,${0.65*alpha})` },
              { rx: r*1.55, ry: r*0.26, lw: r*0.16, a: `rgba(228,202,140,${0.92*alpha})` },
              { rx: r*1.82, ry: r*0.31, lw: r*0.12, a: `rgba(210,185,128,${0.78*alpha})` },
              { rx: r*2.05, ry: r*0.35, lw: r*0.05, a: `rgba(190,165,110,${0.42*alpha})` },
            ];
            ctx.save();
            rings.forEach(({ rx, ry, lw, a }) => {
              ctx.strokeStyle = a;
              ctx.lineWidth = lw;
              ctx.beginPath();
              ctx.ellipse(node.x, node.y, rx, ry, tilt, 0, Math.PI);
              ctx.stroke();
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

      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);

    const handleMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = (e.clientX - rect.left);
      const my = (e.clientY - rect.top);
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

    canvas.addEventListener("mousemove", handleMove);
    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousemove", handleMove);
    };
  }, []);

  return (
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
      <div style={{ flex:1, position:"relative", overflow:"hidden" }}>
        <div style={{
          position:"absolute", bottom:20, left:20, zIndex:10,
          fontFamily:"'Share Tech Mono',monospace", fontSize:10,
          color:"rgba(100,180,255,0.45)", letterSpacing:"0.1em", lineHeight:1.8,
          pointerEvents:"none",
        }}>
          <div>● RDZEŃ &nbsp; ◉ API &nbsp; ○ MODUŁ &nbsp; · NARZĘDZIE</div>
          <div style={{ marginTop:3, color:"rgba(100,180,255,0.28)" }}>hover → podświetl zależności</div>
        </div>

        <div style={{
          position:"absolute", bottom:20, right:20, zIndex:10,
          fontFamily:"'Share Tech Mono',monospace", fontSize:10,
          color:"rgba(100,180,255,0.35)", letterSpacing:"0.1em", textAlign:"right",
          pointerEvents:"none",
        }}>
          <div>{NODES.length} WĘZŁÓW</div>
          <div>{EDGES.length} POŁĄCZEŃ</div>
        </div>

        <canvas ref={canvasRef} style={{ width:"100%", height:"100%", display:"block" }} />
      </div>
    </div>
  );
}
