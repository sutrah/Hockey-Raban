// Hockey AI – overlay coach : dessine l'analyse du moteur par-dessus la vidéo de la page.
(() => {
  if (window.__hockeyAI) return;
  window.__hockeyAI = true;

  // ------------------------------------------------------------ réglages
  const TOP = 0.2;                       // bandeau du haut (tribunes) où se placent les panneaux
  const SHIFT_WARN = 40, SHIFT_LONG = 55; // secondes : vert -> orange -> rouge
  const C = { our: "#4a9eff", them: "#f5b928", ok: "#2ecc71", warn: "#f39c12", bad: "#e74c3c",
              bg: "rgba(10,14,22,0.82)", text: "#f2f4f8", dim: "#9aa4b2" };

  // ---------------------------------------------------------- connexion
  let port = null, wsDirect = null, engineOn = false;
  const send = (m) => {
    if (port) { try { port.postMessage(m); } catch (e) {} }
    else if (wsDirect && wsDirect.readyState === 1) wsDirect.send(JSON.stringify(m));
  };
  function connect() {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.connect) {
      port = chrome.runtime.connect({ name: "overlay" });
      port.onMessage.addListener(onMsg);
      port.onDisconnect.addListener(() => { port = null; engineOn = false; setTimeout(connect, 2000); });
    } else {                              // page de test sans extension : connexion directe
      wsDirect = new WebSocket("ws://127.0.0.1:8765");
      wsDirect.onopen = () => { engineOn = true; send({ type: "hello" }); };
      wsDirect.onmessage = (ev) => onMsg(JSON.parse(ev.data));
      wsDirect.onclose = () => { engineOn = false; setTimeout(connect, 2000); };
    }
  }

  // --------------------------------------------------------------- état
  let lastSig = "", sigT = 0, relocated = true, hover = null;
  // os du squelette (points COCO : 5/6 épaules, 7/8 coudes, 9/10 poignets, 11/12 hanches, 13/14 genoux, 15/16 chevilles)
  const BONES = [[5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
  let video = null, state = null, roster = null, prevBoxes = {}, lastStateT = 0;
  let mode = "team", focus = "our", selected = [], hidden = false, picking = false, markersOn = false;

  function onMsg(m) {
    if (m.type === "engine") { engineOn = m.connected; if (m.connected) { send({ type: "hello" }); sendUI(); } return; }
    if (m.type === "roster") {
      roster = m; buildPicker();
      focusBtns.forEach((b) => b.textContent = roster.abbr ? roster.abbr[b.dataset.side] : b.textContent);
      return;
    }
    if (m.type === "locate") { showMarkers(); return; }
    if (m.type === "located") { markersOn = false; return; }
    if (m.type === "state") {
      const now = performance.now();
      if (state) for (const p of state.players) prevBoxes[p.tid] = { box: p.box, t: lastStateT };
      state = m; lastStateT = now; mode = m.mode;
      if (m.focus && m.focus !== focus) { focus = m.focus; buildPicker(); }
      modeBtns.forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
      focusBtns.forEach((b) => b.classList.toggle("on", b.dataset.side === focus));
      renderPanel();
    }
  }

  // ------------------------------------------------------------ interface
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
  <style>
    :host{all:initial}
    *{box-sizing:border-box;font-family:Inter,Segoe UI,system-ui,sans-serif}
    canvas{position:absolute;left:0;top:0;pointer-events:none}
    canvas.pick{pointer-events:auto;cursor:crosshair}
    .panel{position:absolute;pointer-events:auto;background:${C.bg};color:${C.text};border-radius:8px;
      padding:6px 8px;font-size:12px;line-height:1.35;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.4)}
    .bar{display:flex;gap:4px;align-items:center;margin-bottom:4px;flex-wrap:wrap}
    button{all:unset;cursor:pointer;padding:2px 7px;border-radius:5px;background:#26303f;color:${C.text};font-size:11px}
    button.on{background:${C.our};color:#071425;font-weight:600}
    button:hover{filter:brightness(1.2)}
    .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-left:auto}
    .st{color:${C.dim};font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .cols{display:flex;gap:10px}
    .col{flex:1;min-width:0}
    h4{margin:2px 0;font-size:11px;font-weight:600;color:${C.dim};letter-spacing:.03em}
    .row{display:flex;gap:6px;align-items:center;white-space:nowrap}
    .num{font-weight:700;min-width:20px}
    .t{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:700;padding:0 4px;border-radius:3px;color:#081018}
    .gauge{display:flex;height:9px;border-radius:4px;overflow:hidden;margin:3px 0}
    .pp{background:${C.bad};color:#fff;border-radius:4px;padding:1px 5px;font-weight:700;margin:2px 0;display:inline-block}
    .card{background:#1a2230;border-radius:6px;padding:4px 6px;margin:3px 0}
    .big{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
    .menu{position:absolute;pointer-events:auto;background:#111824;color:${C.text};border-radius:8px;padding:6px;
      max-height:60vh;overflow:auto;font-size:12px;box-shadow:0 4px 18px rgba(0,0,0,.6);min-width:190px}
    .menu label{display:flex;gap:6px;padding:2px 4px;border-radius:4px;cursor:pointer}
    .menu label:hover{background:#223}
    .hide{display:none}
    .mk{position:absolute;width:26px;height:26px;background:#ff00ff}
  </style>
  <canvas id="cv"></canvas>
  <div class="panel" id="panel">
    <div class="bar">
      <button data-side="our" class="on" title="Équipe analysée">NOUS</button>
      <button data-side="them" title="Équipe analysée">EUX</button>
      <span style="width:6px"></span>
      <button data-mode="team" class="on">Équipe</button>
      <button data-mode="players">Joueur</button>
      <button id="pickBtn" title="Choisir le joueur isolé">Choisir</button>
      <button id="swapBtn" title="Inverser : quelle couleur de maillot est quelle équipe">Maillots ⇄</button>
      <button id="idBtn" title="Cliquer un joueur pour donner son nom">Identifier</button>
      <button id="locBtn" title="Retrouver la vidéo à l'écran">Recadrer</button>
      <button id="hideBtn" title="Masquer (Alt+H)">–</button>
      <span class="dot" id="dot"></span>
    </div>
    <div class="st" id="status">moteur non connecté</div>
    <div id="content"></div>
  </div>
  <div class="menu hide" id="picker"></div>
  <div class="menu hide" id="idmenu"></div>
  <div id="markers" class="hide"><div class="mk" style="left:0;top:0"></div><div class="mk" style="right:0;top:0"></div>
    <div class="mk" style="left:0;bottom:0"></div><div class="mk" style="right:0;bottom:0"></div></div>`;
  const $ = (id) => root.getElementById(id);
  const cv = $("cv"), ctx = cv.getContext("2d"), panel = $("panel");
  const modeBtns = [...root.querySelectorAll("button[data-mode]")];
  const focusBtns = [...root.querySelectorAll("button[data-side]")];
  focusBtns.forEach((b) => b.onclick = () => {
    focus = b.dataset.side; selected = [];
    send({ type: "focus", side: focus }); buildPicker();
    focusBtns.forEach((x) => x.classList.toggle("on", x === b));
  });
  modeBtns.forEach((b) => b.onclick = () => { mode = b.dataset.mode; send({ type: "mode", mode }); });
  $("pickBtn").onclick = () => $("picker").classList.toggle("hide");
  $("idBtn").onclick = () => { picking = !picking; cv.classList.toggle("pick", picking); $("idBtn").classList.toggle("on", picking); };
  $("locBtn").onclick = () => send({ type: "locate_request" }) || showMarkers();
  $("hideBtn").onclick = () => toggleHidden();
  $("swapBtn").onclick = () => send({ type: "swap_colors" });
  document.addEventListener("keydown", (e) => {
    if (e.altKey && (e.key === "h" || e.key === "H")) toggleHidden();
    const typing = /INPUT|TEXTAREA/.test((document.activeElement || {}).tagName || "");
    if (!typing && !e.altKey && !e.ctrlKey && (e.key === "i" || e.key === "I")) {   // touche I : identifier
      picking = !picking; cv.classList.toggle("pick", picking); $("idBtn").classList.toggle("on", picking);
    }
  }, true);
  function toggleHidden() { hidden = !hidden; panel.style.opacity = hidden ? "0.25" : "1"; }

  // marqueurs magenta aux 4 coins : le moteur s'en sert pour trouver la vidéo à l'écran
  function showMarkers() {
    markersOn = true;
    $("markers").classList.remove("hide");
    requestAnimationFrame(() => requestAnimationFrame(() => send({ type: "locate_ready" })));
    setTimeout(() => { markersOn = false; }, 3500);
  }

  // ------------------------------------------------- choix des joueurs
  function buildPicker() {
    const el = $("picker");
    if (!roster) return;
    el.innerHTML = `<h4>${roster.names[focus] || ""} · joueur à isoler</h4>` + roster[focus].map((p) =>
      `<label><input type="radio" name="pl" data-num="${p.num}" ${selected[0] && selected[0].num === p.num ? "checked" : ""}>
       <b>${p.num}</b> ${p.name} <span style="color:${C.dim}">${p.pos || ""}</span></label>`).join("");
    el.querySelectorAll("input").forEach((inp) => inp.onchange = () => {
      selected = [{ side: focus, num: inp.dataset.num }];
      send({ type: "select", players: selected });
      if (mode !== "players") { mode = "players"; send({ type: "mode", mode }); }
      el.classList.add("hide");
    });
  }

  // identification par clic : on clique un joueur, on choisit son nom
  function playerAt(e) {
    const r = cv.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    let best = null, bd = 1e9;
    for (const p of (state ? state.players : [])) {
      const [x1, y1, x2, y2] = p.box;
      const d = Math.hypot((x1 + x2) / 2 - x, (y1 + y2) / 2 - y);
      if (x > x1 - 0.02 && x < x2 + 0.02 && y > y1 - 0.03 && y < y2 + 0.02 && d < bd) { best = p; bd = d; }
    }
    return best;
  }
  cv.addEventListener("mousemove", (e) => { if (picking) { const p = playerAt(e); hover = p ? p.tid : null; } });
  // Identification : clic sur le squelette -> pause, choix du nom, reprise automatique
  let resumeAfter = false, menuOpen = false;
  function closeMenu() {
    $("idmenu").classList.add("hide");
    menuOpen = false;
    picking = false; cv.classList.remove("pick"); $("idBtn").classList.remove("on");
    if (resumeAfter && video) { const pr = video.play(); if (pr && pr.catch) pr.catch(() => {}); }
    resumeAfter = false;
  }
  function openMenu(best, clientX) {
    const r = cv.getBoundingClientRect();
    if (video && !video.paused) { video.pause(); resumeAfter = true; }
    const m = $("idmenu");
    const list = (side) => (roster ? roster[side] : []).map((p) =>
      `<label data-side="${side}" data-num="${p.num}"><b>${p.num}</b> ${p.name}
       <span style="color:${C.dim}">${p.pos || ""}</span></label>`).join("");
    m.innerHTML = `<h4>${roster ? roster.names[focus] : ""} · qui est-ce ?${best.num ? ` (actuellement #${best.num})` : ""}</h4>
      ${list(focus)}<label data-cancel="1" style="color:${C.dim}">✕ Annuler (Échap)</label>`;
    m.style.left = Math.max(0, Math.min(clientX - r.left, r.width - 210)) + "px";
    m.style.top = "8px";
    m.classList.remove("hide");
    menuOpen = true;
    m.querySelectorAll("label").forEach((l) => l.onclick = (ev) => {
      ev.stopPropagation();
      if (!l.dataset.cancel) send({ type: "assign", tid: best.tid, side: l.dataset.side, num: l.dataset.num, box: best.box });
      closeMenu();
    });
  }
  document.addEventListener("keydown", (e) => { if (menuOpen && e.key === "Escape") { e.preventDefault(); closeMenu(); } }, true);
  cv.addEventListener("click", (e) => {
    if (!picking || !state) return;
    const best = playerAt(e);
    if (best) openMenu(best, e.clientX);
  });
  // clic direct sur un joueur de l'équipe analysée, même hors du mode « Identifier » : on
  // intercepte le clic avant le lecteur vidéo (sinon il mettrait juste en pause / lecture)
  function playerAtPoint(clientX, clientY) {
    if (!state || !video || hidden) return null;
    const r = video.getBoundingClientRect();
    if (clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom) return null;
    const p = playerAt({ clientX, clientY });
    return p && p.side === focus && p.kp ? p : null;
  }
  for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
    window.addEventListener(type, (e) => {
      if (picking || menuOpen || e.button !== 0) return;
      if (e.composedPath && e.composedPath().includes(host)) return;     // clic dans nos panneaux
      const p = playerAtPoint(e.clientX, e.clientY);
      if (!p) return;
      e.preventDefault(); e.stopImmediatePropagation();
      if (type === "click") openMenu(p, e.clientX);
    }, true);
  }

  // ------------------------------------------------------------ panneau
  const fmt = (s) => s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const shiftColor = (s) => s < SHIFT_WARN ? C.ok : (s < SHIFT_LONG ? C.warn : C.bad);
  function renderPanel() {
    const s = state;
    const st = s.status || {};
    $("dot").style.background = engineOn ? (st.calib === "ok" ? C.ok : C.warn) : C.bad;
    const calib = { ok: "patinoire calée", calcul: "calibration de la patinoire…", attente: "calibration en attente",
                    perdu: "patinoire non repérée", "échec": "calibration impossible" }[st.calib] || st.calib;
    const clk = s.clock && s.clock.remaining != null ? ` · P${s.clock.period} ${fmt(s.clock.remaining)}${s.clock.running ? "" : " ⏸"}` : "";
    const mail = st.our_color && roster && roster.abbr ? ` · ${roster.abbr.our} en ${st.our_color}` : "";
    const pz = s.paused ? " · ⏸ pause : identification (clique « Identifier »)" : "";
    $("status").textContent = `${st.match || "sans feuille de match"}${clk} · ${calib}${mail} · ${s.fps} im/s${pz}`;
    let h = "";
    for (const p of s.pp || []) {
      const who = p.side === "our" ? (roster ? roster.abbr.our : "NOUS") : (roster ? roster.abbr.them : "EUX");
      h += `<span class="pp">${who} #${p.num} pénalité ${fmt(p.remaining)}</span> `;
    }
    const ab = roster && roster.abbr ? roster.abbr[focus] : (focus === "our" ? "NOUS" : "EUX");
    const fc = C[focus];
    if (s.mode === "team") {
      const rows = (s.onice || []).map((p) => `<div class="row"><span class="num" style="color:${fc}">${p.num}</span>
        <span>${p.name || ""}</span><span class="t" style="background:${shiftColor(p.shift)}">${fmt(p.shift)}</span></div>`).join("")
        || `<div class="st">aucun nom encore : mets en pause, touche <b>I</b> (ou « Identifier »), puis clique un joueur</div>`;
      const pr = s.pressure || {};
      const pct = (v) => Math.round((v || 0) * 100);
      const gauge = pr.known ? `<div class="gauge"><div style="width:${pct(pr.off)}%;background:${C.ok}"></div>
        <div style="width:${pct(pr.neu)}%;background:#55606e"></div><div style="width:${pct(pr.dfd)}%;background:${C.bad}"></div></div>
        <div class="st">2 min : attaque ${pct(pr.off)} % · neutre ${pct(pr.neu)} % · défense ${pct(pr.dfd)} %</div>`
        : `<div class="st">en attente du sens du jeu</div>`;
      h += `<div class="cols"><div class="col"><h4 style="color:${fc}">${ab} SUR LA GLACE</h4>${rows}</div>
        <div class="col"><h4>PRESSION ${ab}</h4>${gauge}
        <div class="st">vitesse vs début de match : ${pr.fatigue ? Math.round(pr.fatigue * 100) + " %" : "–"}</div></div></div>`;
    } else {
      const p = (s.selected || [])[0];
      if (!p) h += `<div class="st">« Choisir » un joueur de ${ab}, ou « Identifier » puis clique-le.</div>`;
      else {
        h += `<div class="card"><div class="row"><span class="num" style="color:${fc};font-size:15px">${p.num}</span>
          <b>${p.name}</b><span class="st">${p.pos || ""}</span>
          <span class="st" style="margin-left:auto">${p.visible ? "suivi" : "hors champ"}${p.on_ice ? "" : " · au banc ?"}</span></div>
          <div class="cols"><div class="col"><div><span class="big">${p.speed != null ? Math.round(p.speed) : "–"}</span> <span class="st">km/h</span></div>
            <div class="st">max ${p.top != null ? Math.round(p.top) : "–"} km/h</div></div>
          <div class="col"><div><span class="big">${p.dist}</span> <span class="st">m mesurés</span></div>
            <div class="st">vu ${fmt(p.seen)}</div></div>
          <div class="col"><div><span class="big" style="color:${p.on_ice ? shiftColor(p.shift) : C.dim}">${p.on_ice ? fmt(p.shift) : "–"}</span> <span class="st">présence</span></div>
            <div class="st">${p.shifts} présences · moy. ${fmt(p.avg)} · total ${fmt(p.toi)}</div></div></div>
          <canvas id="spark" width="300" height="34" style="position:static;width:100%;height:34px"></canvas></div>`;
      }
    }
    $("content").innerHTML = h;
    const sp = root.getElementById("spark"), p0 = (s.selected || [])[0];
    if (sp && p0 && p0.speeds && p0.speeds.length > 1) {      // vitesse sur la dernière minute
      const g = sp.getContext("2d"), v = p0.speeds, mx = Math.max(30, ...v);
      g.clearRect(0, 0, 300, 34); g.strokeStyle = C[focus]; g.lineWidth = 2; g.beginPath();
      v.forEach((x, i) => { const X = i / (v.length - 1) * 298 + 1, Y = 32 - x / mx * 30; i ? g.lineTo(X, Y) : g.moveTo(X, Y); });
      g.stroke(); g.fillStyle = C.dim; g.font = "9px sans-serif"; g.fillText("vitesse · dernière minute", 4, 9);
    }
  }

  // ------------------------------------------------------------- dessin
  function findVideo() {
    let best = null, area = 0;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      const a = r.width * r.height;
      if (r.width > 320 && r.height > 180 && a > area && getComputedStyle(v).visibility !== "hidden") { best = v; area = a; }
    }
    return best;
  }
  function placeHost() {
    const fs = document.fullscreenElement;
    const parent = fs && fs.tagName !== "VIDEO" ? fs : document.documentElement;
    if (host.parentNode !== parent) parent.appendChild(host);
  }
  function sendUI() {   // zones couvertes par nos panneaux : le moteur ne les analyse pas
    send({ type: "ui", rects: [[0.34, 0, 0.71, TOP + 0.02], [0.72, 0, 1.0, TOP + 0.02]] });
  }

  function drawRink(x0, y0, w, h) {
    const rk = roster && roster.rink;
    if (!rk) return null;
    const sx = w / rk.L, sy = h / rk.W;
    const X = (x) => x0 + (x + rk.L / 2) * sx, Y = (y) => y0 + (rk.W / 2 - y) * sy;
    ctx.fillStyle = "rgba(10,14,22,0.82)";
    ctx.beginPath(); ctx.roundRect(x0, y0, w, h, rk.r * sx); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 1; ctx.stroke();
    const line = (x, col) => { ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(X(x), y0 + 1); ctx.lineTo(X(x), y0 + h - 1); ctx.stroke(); };
    line(0, "rgba(231,76,60,.8)"); line(-rk.blue, "rgba(74,158,255,.8)"); line(rk.blue, "rgba(74,158,255,.8)");
    line(-rk.goal, "rgba(231,76,60,.5)"); line(rk.goal, "rgba(231,76,60,.5)");
    return { X, Y };
  }

  function frame() {
    requestAnimationFrame(frame);
    const v = findVideo();
    if (v !== video) video = v;
    if (!video) { host.style.display = "none"; return; }
    if (!port && !wsDirect) connect();
    placeHost();
    host.style.display = "block";
    const r = video.getBoundingClientRect();
    // vidéo déplacée / redimensionnée / plein écran : on redonne sa position au moteur
    const sig = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height),
                 window.screenX, window.screenY, !!document.fullscreenElement].join(",");
    if (sig !== lastSig) { lastSig = sig; sigT = performance.now(); relocated = false; }
    else if (!relocated && engineOn && performance.now() - sigT > 700) { relocated = true; showMarkers(); }
    const fs = document.fullscreenElement && document.fullscreenElement.tagName !== "VIDEO" ? document.fullscreenElement.getBoundingClientRect() : { left: 0, top: 0 };
    Object.assign(host.style, { left: r.left - fs.left + "px", top: r.top - fs.top + "px", width: r.width + "px", height: r.height + "px" });
    $("markers").style.cssText = `position:absolute;left:0;top:0;width:${r.width}px;height:${r.height}px;`;
    $("markers").classList.toggle("hide", !markersOn);
    // panneaux dans le bandeau du haut (tribunes), à droite du bandeau TV
    Object.assign(panel.style, { left: r.width * 0.34 + "px", top: "4px", width: r.width * 0.37 + "px",
      maxHeight: r.height * (state && state.mode === "players" ? 0.3 : TOP) + "px" });
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(r.width * dpr) || cv.height !== Math.round(r.height * dpr)) {
      cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
      cv.style.width = r.width + "px"; cv.style.height = r.height + "px";
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    if (!state || hidden || markersOn) return;
    const W = r.width, H = r.height;
    // extrapolation du mouvement (l'analyse a ~0,1-0,2 s de retard)
    const dtNow = Math.min(performance.now() - lastStateT, 300);
    const fs2 = Math.max(11, Math.min(15, H / 60));
    ctx.font = `600 ${fs2}px Inter, Segoe UI, sans-serif`;
    ctx.textBaseline = "middle";
    for (const p of state.players) {
      let [x1, y1, x2, y2] = p.box;
      const pv = prevBoxes[p.tid];
      if (pv && lastStateT > pv.t) {
        const k = dtNow / (lastStateT - pv.t);
        if (k < 3) { const dx = ((x1 + x2) - (pv.box[0] + pv.box[2])) / 2 * k, dy = (y2 - pv.box[3]) * k; x1 += dx; x2 += dx; y1 += dy; y2 += dy; }
      }
      const cx = (x1 + x2) / 2 * W, top = y1 * H;
      const col = p.side ? C[p.side] : "rgba(255,255,255,.6)";
      // squelette : épaisseur plus forte en mode identification et pour le joueur survolé
      if (p.kp && (p.side === focus || picking)) {
        const dx = (x1 - p.box[0]) * W, dy = (y1 - p.box[1]) * H;
        const hot = picking && hover === p.tid;
        ctx.strokeStyle = col; ctx.globalAlpha = p.side === focus ? (hot ? 1 : 0.75) : 0.35;
        ctx.lineWidth = hot ? 4 : (picking ? 2.5 : 1.6); ctx.lineCap = "round";
        ctx.beginPath();
        for (const [a, b] of BONES) {
          const A = p.kp[a], B = p.kp[b];
          if (!A || !B) continue;
          ctx.moveTo(A[0] * W + dx, A[1] * H + dy); ctx.lineTo(B[0] * W + dx, B[1] * H + dy);
        }
        ctx.stroke();
        const nose = p.kp[0] || p.kp[1] || p.kp[2];
        if (nose) { ctx.beginPath(); ctx.arc(nose[0] * W + dx, nose[1] * H + dy, hot ? 5 : 3.5, 0, 7); ctx.stroke(); }
        ctx.globalAlpha = 1;
      }
      if (!p.num) {                    // pas encore identifié : simple point de couleur d'équipe
        if (p.side && state.mode === "team") { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(cx, top - 6, 3.5, 0, 7); ctx.fill(); }
        continue;
      }
      let txt = `${p.num} ${p.name || ""}`.trim();
      if (state.mode === "players" && p.speed != null) txt += ` · ${Math.round(p.speed)} km/h · ${p.dist} m`;
      const tw = ctx.measureText(txt).width;
      let sw = 0, stxt = "";
      if (state.mode === "team" && p.shift != null && p.side === "our") { stxt = fmt(p.shift); sw = ctx.measureText(stxt).width + 10; }
      const bw = tw + 12 + sw, bh = fs2 + 6, bx = cx - bw / 2, by = top - bh - 6;
      ctx.fillStyle = "rgba(10,14,22,0.8)"; ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 4); ctx.fill();
      ctx.fillStyle = col; ctx.fillRect(bx, by, 3, bh);
      ctx.fillStyle = C.text; ctx.fillText(txt, bx + 7, by + bh / 2 + 1);
      if (sw) { ctx.fillStyle = shiftColor(p.shift); ctx.beginPath(); ctx.roundRect(bx + tw + 10, by + 2, sw - 2, bh - 4, 3); ctx.fill();
        ctx.fillStyle = "#081018"; ctx.fillText(stxt, bx + tw + 14, by + bh / 2 + 1); }
    }
    // mini vue tactique (coin haut droit, dans les tribunes)
    const mh = H * TOP - 8, mw = Math.min(W * 0.27, mh * 2);
    if (state.mode === "players") {
      const p0 = (state.selected || [])[0];
      const R = p0 ? drawRink(W - mw - 6, 4, mw, mh) : null;
      if (R && p0.trail && p0.trail.length) {          // trajet de la dernière minute
        ctx.strokeStyle = C[focus]; ctx.lineWidth = 1.5; ctx.beginPath();
        p0.trail.forEach(([x, y], i) => i ? ctx.lineTo(R.X(x), R.Y(y)) : ctx.moveTo(R.X(x), R.Y(y)));
        ctx.stroke();
        const [lx, ly] = p0.trail[p0.trail.length - 1];
        ctx.fillStyle = C[focus]; ctx.beginPath(); ctx.arc(R.X(lx), R.Y(ly), 5, 0, 7); ctx.fill();
      }
    }
    if (state.mode === "team") {
      const R = drawRink(W - mw - 6, 4, mw, mh);
      if (R) for (const [x, y, side, num] of state.minimap || []) {
        ctx.fillStyle = C[side]; ctx.beginPath(); ctx.arc(R.X(x), R.Y(y), num ? 5 : 3.5, 0, 7); ctx.fill();
        if (num) { ctx.fillStyle = "#081018"; ctx.font = "700 7px sans-serif"; ctx.textAlign = "center"; ctx.fillText(num, R.X(x), R.Y(y) + 0.5); ctx.textAlign = "start"; }
      }
    }
  }
  requestAnimationFrame(frame);
  setInterval(() => { if (engineOn) sendUI(); }, 5000);
})();
