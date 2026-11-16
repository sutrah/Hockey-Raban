/* Hockey Raban – lit data/d2.sqlite dans le navigateur (sql.js) et calcule tout côté client. */
const ME = 'BRE';                       // notre équipe
let CUR = 0;                            // saison en cours (2027 = 2026-27)
const sais = s => `${s - 1}-${String(s).slice(2)}`;
const D = {};                           // tables en mémoire
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pct = (a, b) => b ? Math.round(100 * a / b) + ' %' : '–';
const mmss = t => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
const dateFr = s => new Date(s.replace(' ', 'T')).toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
const heure = s => s.slice(11, 16).replace(':', 'h');
const by = (arr, f) => arr.reduce((m, x) => { const k = f(x); (m[k] = m[k] || []).push(x); return m; }, {});

async function load() {
  const SQL = await initSqlJs({ locateFile: f => 'vendor/' + f });
  const buf = await (await fetch('data/d2.sqlite', { cache: 'no-cache' })).arrayBuffer();
  const db = new SQL.Database(new Uint8Array(buf));
  const q = sql => { const r = db.exec(sql)[0]; return r ? r.values.map(v => Object.fromEntries(r.columns.map((c, i) => [c, v[i]]))) : []; };
  for (const t of ['equipes', 'joueurs', 'matchs', 'compositions', 'buts', 'buts_sur_glace', 'penalites', 'tirs', 'engagements', 'gardiens_presence', 'staff']) D[t] = q(`SELECT * FROM ${t}`);
  D.eq = Object.fromEntries(D.equipes.map(e => [e.id, e]));

  D.j = Object.fromEntries(D.joueurs.map(j => [j.id, j.nom]));
  D.m = Object.fromEntries(D.matchs.map(m => [m.id, m]));
  D.matchs.sort((a, b) => a.date.localeCompare(b.date));
  CUR = Math.max(...D.matchs.map(m => m.saison));
  D.finis = D.matchs.filter(m => m.etat === 'T');
  D.joues = D.finis.filter(m => m.saison === CUR && m.phase === 'P');
  D.actuelles = new Set(D.matchs.filter(m => m.saison === CUR).flatMap(m => [m.dom_id, m.ext_id]));
  // abréviation -> équipe ; en cas de doublon, l'équipe engagée cette saison l'emporte
  D.ab = {}; D.equipes.filter(e => !D.actuelles.has(e.id)).concat(D.equipes.filter(e => D.actuelles.has(e.id))).forEach(e => D.ab[e.abreviation] = e);
  // parcours de chaque joueur par saison et par équipe (toutes phases)
  D.car = {};
  const car = (j, mid, eq) => { const m = D.m[mid], a = D.car[j] = D.car[j] || {}, k = m.saison + '|' + eq; return a[k] = a[k] || { s: m.saison, eq, mj: 0, b: 0, a: 0, sec: 0, enc: 0 }; };
  D.compositions.forEach(c => car(c.joueur_id, c.match_id, c.equipe_id).mj++);
  D.buts.forEach(b => { car(b.buteur_id, b.match_id, b.equipe_id).b++; [b.assist1_id, b.assist2_id].forEach(a => a && car(a, b.match_id, b.equipe_id).a++);
    if (b.gardien_id && !b.cage_vide) { const m = D.m[b.match_id]; car(b.gardien_id, b.match_id, b.equipe_id === m.dom_id ? m.ext_id : m.dom_id).enc++; } });
  D.gardiens_presence.forEach(g => { car(g.joueur_id, g.match_id, g.equipe_id).sec += (g.sortie ?? 3600) - g.entree; });
  D.butsM = by(D.buts, b => b.match_id); D.penM = by(D.penalites, p => p.match_id);
  D.compM = by(D.compositions, c => c.match_id); D.glace = by(D.buts_sur_glace, s => s.but_id);
  for (const k in D.butsM) D.butsM[k].sort((a, b) => a.temps - b.temps);
}

/* ---------- calculs ---------- */
const jeu = (T, m) => {            // un match vu depuis l'équipe T
  const dom = m.dom_id === T, bp = dom ? m.buts_dom : m.buts_ext, bc = dom ? m.buts_ext : m.buts_dom, v = bp > bc;
  return { m, dom, adv: dom ? m.ext_id : m.dom_id, bp, bc, v, pts: m.fin === 'S' ? (v ? 3 : 0) : (v ? 2 : 1) };
};
const matchsDe = (T, s = CUR, ph = 'P') => D.finis.filter(m => m.saison === s && m.phase === ph && (m.dom_id === T || m.ext_id === T)).map(m => jeu(T, m));
const poule = (T, sa = CUR) => { const s = new Set([T]); D.matchs.filter(m => m.saison === sa && m.phase === 'P').forEach(m => { if (m.dom_id === T) s.add(m.ext_id); if (m.ext_id === T) s.add(m.dom_id); }); return [...s]; };
const bilan = js => { const v = js.filter(j => j.v).length; return `${v} V – ${js.length - v} D`; };

function classement(T, s = CUR) {
  return poule(T, s).map(id => {
    const js = matchsDe(id, s);
    return { id, mj: js.length, pts: js.reduce((s, j) => s + j.pts, 0), v: js.filter(j => j.v && j.m.fin === 'S').length,
      vp: js.filter(j => j.v && j.m.fin !== 'S').length, dp: js.filter(j => !j.v && j.m.fin !== 'S').length,
      d: js.filter(j => !j.v && j.m.fin === 'S').length, bp: js.reduce((s, j) => s + j.bp, 0), bc: js.reduce((s, j) => s + j.bc, 0),
      forme: js.slice(-5).map(j => j.v ? 'V' : 'D') };
  }).sort((a, b) => b.pts - a.pts || (b.bp - b.bc) - (a.bp - a.bc) || b.bp - a.bp);
}

// occasions en supériorité ≈ pénalités mineures adverses, hors pénalités simultanées qui s'annulent
function occasions(T, js) {
  let sup = 0, inf = 0;
  for (const { m } of js) {
    const g = by((D.penM[m.id] || []).filter(p => p.minutes === 2), p => p.temps);
    for (const t in g) { const n = g[t].filter(p => p.equipe_id === T).length, a = g[t].length - n; if (a > n) sup += a - n; if (n > a) inf += n - a; }
  }
  return { sup, inf };
}

function stats(T, s = CUR) {
  const js = matchsDe(T, s), ids = new Set(js.map(j => j.m.id));
  const buts = D.buts.filter(b => ids.has(b.match_id)), pour = buts.filter(b => b.equipe_id === T), contre = buts.filter(b => b.equipe_id !== T);
  const S = { T, js, pour, contre };
  // tranches de 5 minutes (12 + prolongation)
  const tr = b => b.temps > 3600 ? 12 : Math.min(11, Math.floor(Math.max(b.temps - 1, 0) / 300));
  S.tranches = Array.from({ length: 13 }, (_, i) => ({ p: pour.filter(b => tr(b) === i).length, c: contre.filter(b => tr(b) === i).length }));
  S.tiers = [1, 2, 3, 4].map(p => ({ p: pour.filter(b => b.periode === p).length, c: contre.filter(b => b.periode === p).length }));
  // scénarios
  const sc = { premier: [], second: [], mene: [], egal: [], menant: [] };
  let repP = 0, repC = 0;
  for (const j of js) {
    const bm = (D.butsM[j.m.id] || []);
    if (bm.length) (bm[0].equipe_id === T ? sc.premier : sc.second).push(j);
    const a2 = bm.filter(b => b.temps <= 2400), p2 = a2.filter(b => b.equipe_id === T).length, c2 = a2.length - p2;
    (p2 > c2 ? sc.menant : p2 < c2 ? sc.mene : sc.egal).push(j);
    bm.forEach((b, i) => { const n = bm[i + 1]; if (n && n.temps - b.temps <= 120 && n.equipe_id !== b.equipe_id) (b.equipe_id === T ? repC++ : repP++); });
  }
  S.sc = sc; S.repP = repP; S.repC = repC;
  // situations spéciales
  const sit = (arr, re) => arr.filter(b => re.test(b.situation || '')).length;
  S.occ = occasions(T, js);
  S.ppB = sit(pour, /^SUP/); S.pkB = sit(contre, /^SUP/); S.shP = sit(pour, /^INF/); S.shC = sit(contre, /^INF/);
  // joueurs
  const P = {};
  const pl = id => P[id] = P[id] || { id, mj: 0, b: 0, a: 0, pm: 0, pp: 0, pim: 0, pen: 0, tirs: 0, bDet: 0, num: '', poste: '', enc: 0 };
  D.compositions.filter(c => c.equipe_id === T && ids.has(c.match_id)).forEach(c => { const p = pl(c.joueur_id); p.mj++; p.num = c.numero; p.poste = c.poste; });
  for (const b of pour) {
    pl(b.buteur_id).b++; if (/^SUP/.test(b.situation)) pl(b.buteur_id).pp++;
    for (const a of [b.assist1_id, b.assist2_id]) if (a) { pl(a).a++; if (/^SUP/.test(b.situation)) pl(a).pp++; }
    if (D.m[b.match_id].detail_tirs) { pl(b.buteur_id).tirs++; pl(b.buteur_id).bDet++; }
  }
  for (const b of buts) if (b.situation === 'EG') for (const s of D.glace[b.id] || []) if (s.equipe_id === T) { pl(s.joueur_id).pm += s.signe; if (s.signe < 0) pl(s.joueur_id).enc++; }
  const pens = D.penalites.filter(p => ids.has(p.match_id) && p.equipe_id === T);
  pens.forEach(p => { if (p.joueur_id) { pl(p.joueur_id).pim += p.minutes || 0; pl(p.joueur_id).pen++; } });
  D.tirs.filter(t => ids.has(t.match_id) && t.equipe_id === T).forEach(t => t.tireur_id && pl(t.tireur_id).tirs++);
  S.joueurs = Object.values(P).sort((a, b) => (b.b + b.a) - (a.b + a.a) || b.b - a.b || b.mj - a.mj);
  S.pens = pens; S.pensAdv = D.penalites.filter(p => ids.has(p.match_id) && p.equipe_id !== T);
  // duos
  const duo = {};
  for (const b of pour) { const g = [b.buteur_id, b.assist1_id, b.assist2_id].filter(Boolean); for (let i = 0; i < g.length; i++) for (let k = i + 1; k < g.length; k++) { const key = [g[i], g[k]].sort().join('-'); duo[key] = (duo[key] || 0) + 1; } }
  S.duos = Object.entries(duo).map(([k, n]) => ({ a: +k.split('-')[0], b: +k.split('-')[1], n })).filter(d => d.n >= 2).sort((a, b) => b.n - a.n).slice(0, 8);
  // gardiens
  const G = {};
  D.gardiens_presence.filter(g => ids.has(g.match_id) && g.equipe_id === T).forEach(g => {
    const m = D.m[g.match_id], fin = m.fin === 'S' ? 3600 : Math.max(3600, ...(D.butsM[m.id] || []).map(b => b.temps));
    const x = G[g.joueur_id] = G[g.joueur_id] || { id: g.joueur_id, tit: 0, sec: 0, enc: 0, tirs: 0, encDet: 0 };
    if (g.entree === 0) x.tit++; x.sec += (g.sortie ?? fin) - g.entree;
  });
  contre.forEach(b => { const x = G[b.gardien_id]; if (x && !b.cage_vide) { x.enc++; if (D.m[b.match_id].detail_tirs) x.encDet++; } });
  D.tirs.filter(t => ids.has(t.match_id) && t.equipe_id !== T).forEach(t => G[t.gardien_id] && G[t.gardien_id].tirs++);
  S.gardiens = Object.values(G).sort((a, b) => b.sec - a.sec);
  // tirs et engagements (matchs où ils sont saisis)
  S.detail = js.filter(j => j.m.detail_tirs).map(j => {
    const id = j.m.id, n = (arr, f) => arr.filter(x => x.match_id === id && f(x)).length;
    return { j, tp: n(D.tirs, t => t.equipe_id === T) + j.bp, tc: n(D.tirs, t => t.equipe_id !== T) + j.bc,
      eg: n(D.engagements, e => e.equipe_gagnante_id === T), et: n(D.engagements, () => true) };
  });
  const E = {};
  D.engagements.filter(e => ids.has(e.match_id)).forEach(e => { const id = D.m[e.match_id].dom_id === T ? e.joueur_dom_id : e.joueur_ext_id; if (!id) return; const x = E[id] = E[id] || { id, g: 0, n: 0 }; x.n++; if (e.equipe_gagnante_id === T) x.g++; });
  S.enga = Object.values(E).filter(x => x.n >= 5).sort((a, b) => b.n - a.n);
  return S;
}

function aRetenir(S) {
  const L = [], n = S.js.length, T = S.T, nom = D.eq[T].nom_court;
  if (!n) return L;
  const dom = S.js.filter(j => j.dom), ext = S.js.filter(j => !j.dom), moy = (a, k) => a.length ? (a.reduce((s, j) => s + j[k], 0) / a.length).toFixed(1) : '–';
  L.push(`Domicile : ${bilan(dom)}, ${moy(dom, 'bp')} buts marqués et ${moy(dom, 'bc')} encaissés par match. Extérieur : ${bilan(ext)}, ${moy(ext, 'bp')} marqués et ${moy(ext, 'bc')} encaissés.`);
  const t = S.tiers, lab = ['1er tiers', '2e tiers', '3e tiers'], diff = t.slice(0, 3).map(x => x.p - x.c);
  const best = diff.indexOf(Math.max(...diff)), worst = diff.indexOf(Math.min(...diff));
  if (best !== worst) L.push(`Meilleur tiers : ${lab[best]} (${t[best].p} marqués, ${t[best].c} encaissés). Plus faible : ${lab[worst]} (${t[worst].p} marqués, ${t[worst].c} encaissés).`);
  const fin = S.tranches[10].c + S.tranches[11].c;
  if (S.contre.length && fin / S.contre.length >= 0.25) L.push(`${fin} des ${S.contre.length} buts encaissés tombent dans les 10 dernières minutes.`);
  if (S.sc.premier.length || S.sc.second.length) L.push(`Ouvre le score dans ${S.sc.premier.length} match(s) sur ${n} : ${bilan(S.sc.premier)}. Quand l'adversaire marque en premier : ${bilan(S.sc.second)}.`);
  if (S.sc.mene.length) L.push(`Menée après deux tiers : ${bilan(S.sc.mene)}.`);
  L.push(`Supériorité : ${S.ppB} but(s) pour environ ${S.occ.sup} occasions (${pct(S.ppB, S.occ.sup)}). Infériorité : ${S.pkB} but(s) encaissé(s) pour environ ${S.occ.inf} pénalités (${pct(S.occ.inf - S.pkB, S.occ.inf)} de réussite).`);
  const top = S.joueurs.slice(0, 3).filter(p => p.b + p.a > 0), ids = new Set(top.map(p => p.id));
  const impl = S.pour.filter(b => [b.buteur_id, b.assist1_id, b.assist2_id].some(x => ids.has(x))).length;
  if (top.length === 3) L.push(`${impl} des ${S.pour.length} buts impliquent ${top.map(p => D.j[p.id]).join(', ')}.`);
  if (S.duos[0]) L.push(`Duo le plus productif : ${D.j[S.duos[0].a]} + ${D.j[S.duos[0].b]} (${S.duos[0].n} buts ensemble).`);
  const pim = [...S.joueurs].sort((a, b) => b.pim - a.pim)[0];
  if (pim && pim.pim >= 6) L.push(`Joueur le plus pénalisé : ${D.j[pim.id]}, ${pim.pim} min en ${pim.pen} pénalité(s).`);
  const g = S.gardiens[0];
  if (g) L.push(`Gardien habituel : ${D.j[g.id]}, titulaire ${g.tit} fois sur ${n}, ${(g.enc * 3600 / g.sec).toFixed(2)} buts encaissés par match.`);
  return L;
}

// saisons précédentes d'un joueur en D2 : « 25-26 ORL 22 MJ 12+9 »
const abr = id => D.eq[id] ? D.eq[id].abreviation : '?';
function passe(id, gardien) {
  const l = Object.values(D.car[id] || {}).filter(x => x.s < CUR).sort((a, b) => b.s - a.s);
  if (!l.length) return '<span class="muted">pas en D2</span>';
  return l.map(x => `${String(x.s - 1).slice(2)}-${String(x.s).slice(2)} ${abr(x.eq)} ${x.mj} MJ ` + (gardien ? (x.sec ? `${(x.enc * 3600 / x.sec).toFixed(2)} enc./match` : '') : `${x.b}+${x.a}`)).join(' · ');
}
function entraineur(T, s) {
  const c = {}; D.staff.filter(x => x.equipe_id === T && /principal/i.test(x.role || '') && D.m[x.match_id] && D.m[x.match_id].saison === s).forEach(x => c[x.nom] = (c[x.nom] || 0) + 1);
  const e = Object.entries(c).sort((a, b) => b[1] - a[1])[0]; return e ? e[0] : null;
}
const effectif = (T, s) => new Set(D.compositions.filter(c => c.equipe_id === T && D.m[c.match_id].saison === s).map(c => c.joueur_id));
function historique(T) {
  const saisons = [...new Set(D.finis.filter(m => m.dom_id === T || m.ext_id === T).map(m => m.saison))].sort((a, b) => b - a);
  const lignes = saisons.map(s => {
    const S = stats(T, s), n = S.js.length, cl = classement(T, s), dom = S.js.filter(j => j.dom), ext = S.js.filter(j => !j.dom);
    const po = D.finis.filter(m => m.saison === s && m.phase !== 'P' && (m.dom_id === T || m.ext_id === T)).map(m => jeu(T, m));
    const dernier = po.length ? po[po.length - 1].m : null;
    return { s, n, rang: n ? cl.findIndex(r => r.id === T) + 1 : 0, taille: cl.length, pts: S.js.reduce((a, j) => a + j.pts, 0), bp: n ? S.pour.length / n : 0, bc: n ? S.contre.length / n : 0,
      pp: pct(S.ppB, S.occ.sup), pk: pct(S.occ.inf - S.pkB, S.occ.inf), dom: bilan(dom), ext: bilan(ext), coach: entraineur(T, s),
      series: po.length ? `${dernier.phase === 'C' ? 'Maintien' : (dernier.tour || 'Séries')} (${bilan(po)})` : '–' };
  });
  // continuité avec la saison précédente
  const prec = saisons.find(s => s < CUR), cont = { prec };
  if (prec && saisons.includes(CUR)) {
    const now = effectif(T, CUR), avant = effectif(T, prec), restes = [...now].filter(j => avant.has(j));
    const butsPrec = D.buts.filter(b => b.equipe_id === T && D.m[b.match_id].saison === prec);
    Object.assign(cont, { now: now.size, restes: restes.length, buts: butsPrec.length, butsRestes: butsPrec.filter(b => now.has(b.buteur_id)).length,
      coachNow: entraineur(T, CUR), coachPrec: entraineur(T, prec),
      recrues: [...now].filter(j => !avant.has(j) && Object.values(D.car[j] || {}).some(x => x.s < CUR)).length, nouveaux: [...now].filter(j => !Object.values(D.car[j] || {}).some(x => x.s < CUR)).length });
  }
  return { lignes, cont };
}

/* ---------- rendu ---------- */
const lienEq = id => `<a href="#/equipe/${D.eq[id].abreviation}">${esc(D.eq[id].nom_court)}</a>`;
const table = (head, rows, cls = []) => `<div class="scroll"><table><thead><tr>${head.map((h, i) => `<th class="${cls[i] || ''}">${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr class="${r.cls || ''}">${(r.c || r).map((c, i) => `<td class="${cls[i] || ''}">${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const tile = (v, l, s = '') => `<div class="tile"><b>${v}</b><span>${l}</span>${s ? `<small>${s}</small>` : ''}</div>`;
const forme = f => f.map(x => `<span class="${x}">${x}</span>`).join(' ');
const score = j => `<span class="${j.v ? 'V' : 'D'}">${j.v ? 'V' : 'D'}</span> ${j.bp}-${j.bc}${j.m.fin === 'S' ? '' : ' ' + (j.m.fin === 'PRL' ? 'prol.' : 't.a.b.')}`;

function tableClassement(T, focus = []) {
  return table(['#', 'Équipe', 'MJ', 'Pts', 'V', 'Vp', 'Dp', 'D', 'BP', 'BC', 'Diff', '5 derniers'],
    classement(T).map((r, i) => ({ cls: focus.includes(r.id) ? 'me' : '', c: [i + 1, lienEq(r.id), r.mj, `<b>${r.pts}</b>`, r.v, r.vp, r.dp, r.d, r.bp, r.bc, (r.bp - r.bc > 0 ? '+' : '') + (r.bp - r.bc), forme(r.forme)] })),
    ['n', '', 'n', 'n', 'n', 'n', 'n', 'n', 'n', 'n', 'n', '']);
}

function chartTranches(S) {
  const W = 720, H = 250, L = 30, R = 8, mid = 116, max = Math.max(2, ...S.tranches.map(t => Math.max(t.p, t.c)));
  const hasOT = S.tranches[12].p + S.tranches[12].c > 0, n = hasOT ? 13 : 12, bw = (W - L - R) / n, u = 92 / max, w = bw - 10;
  const bar = (x, y, h, up, cls) => h <= 0 ? '' : `<path fill="var(--${cls})" d="${up
    ? `M${x},${y} v${-(h - Math.min(4, h))} q0,${-Math.min(4, h)} ${Math.min(4, h)},${-Math.min(4, h)} h${w - 2 * Math.min(4, h)} q${Math.min(4, h)},0 ${Math.min(4, h)},${Math.min(4, h)} v${h - Math.min(4, h)} z`
    : `M${x},${y} v${h - Math.min(4, h)} q0,${Math.min(4, h)} ${Math.min(4, h)},${Math.min(4, h)} h${w - 2 * Math.min(4, h)} q${Math.min(4, h)},0 ${Math.min(4, h)},${-Math.min(4, h)} v${-(h - Math.min(4, h))} z`}"/>`;
  let g = '';
  const step = max > 6 ? 2 : 1;
  for (let v = step; v <= max; v += step) for (const s of [-1, 1]) g += `<line x1="${L}" x2="${W - R}" y1="${mid - s * v * u}" y2="${mid - s * v * u}" stroke="var(--grid)"/><text x="${L - 6}" y="${mid - s * v * u + 4}" text-anchor="end">${v}</text>`;
  S.tranches.slice(0, n).forEach((t, i) => {
    const x = L + i * bw + 5, lab = i === 12 ? 'Prolongation' : `${i * 5}–${i * 5 + 5} min`;
    g += bar(x, mid - 1, t.p * u, true, 'pour') + bar(x, mid + 1, t.c * u, false, 'contre');
    g += `<rect class="hit" x="${L + i * bw}" y="8" width="${bw}" height="${H - 50}" data-tip="${lab} : ${t.p} marqué(s), ${t.c} encaissé(s)"/>`;
    g += `<text x="${L + i * bw + bw / 2}" y="${H - 28}" text-anchor="middle">${i === 12 ? 'Prol.' : i * 5 + '–' + (i * 5 + 5)}</text>`;
  });
  for (let p = 1; p < (hasOT ? 4 : 3); p++) g += `<line x1="${L + p * 4 * bw}" x2="${L + p * 4 * bw}" y1="8" y2="${H - 20}" stroke="var(--axis)" stroke-dasharray="3 3"/>`;
  ['1er tiers', '2e tiers', '3e tiers'].forEach((l, p) => g += `<text class="per" x="${L + (p * 4 + 2) * bw}" y="${H - 8}" text-anchor="middle">${l} : ${S.tiers[p].p} marqués, ${S.tiers[p].c} encaissés</text>`);
  g += `<line x1="${L}" x2="${W - R}" y1="${mid}" y2="${mid}" stroke="var(--axis)"/>`;
  return `<div class="legend"><span><i style="background:var(--pour)"></i>Buts marqués</span><span><i style="background:var(--contre)"></i>Buts encaissés</span></div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Buts marqués et encaissés par tranche de cinq minutes">${g}</svg>`;
}

function pageEquipe(ab) {
  const e = D.ab[ab]; if (!e) return `<p>Équipe inconnue.</p>`;
  const T = e.id, S = stats(T), n = S.js.length, cl = classement(T), rang = cl.findIndex(r => r.id === T) + 1, me = D.ab[ME].id;
  const pts = S.js.reduce((s, j) => s + j.pts, 0), bp = S.pour.length, bc = S.contre.length;
  const proch = D.matchs.filter(m => m.etat !== 'T' && (m.dom_id === T || m.ext_id === T)).slice(0, 4);
  let h = `<h1>${esc(e.nom)}</h1><p class="sub">${n ? `Saison ${sais(CUR)} · ${rang}<sup>e</sup> de la poule sur ${cl.length} · ${n} matchs joués · forme : ${forme(S.js.slice(-5).map(j => j.v ? 'V' : 'D'))}` : `Pas de match en D2 en ${sais(CUR)}`}</p>`;
  h += `<div class="tiles">${tile(pts, 'points', bilan(S.js))}${tile(`${bp} / ${bc}`, 'buts marqués / encaissés', n ? `${(bp / n).toFixed(1)} / ${(bc / n).toFixed(1)} par match` : '')}
    ${tile(pct(S.ppB, S.occ.sup), 'supériorité', `${S.ppB} buts / ≈${S.occ.sup} occasions`)}${tile(pct(S.occ.inf - S.pkB, S.occ.inf), 'infériorité', `${S.pkB} buts encaissés / ≈${S.occ.inf}`)}
    ${tile(n ? (S.pens.reduce((s, p) => s + (p.minutes || 0), 0) / n).toFixed(1) : '–', 'min de pénalité / match', `${S.pens.length} pénalités`)}</div>`;
  if (T === me) h += carteAnalyse(T);
  else if (n) h += cartePreco(T) + `<div class="card"><h2>À retenir sur ${esc(e.nom_court)}</h2><ul class="keys">${aRetenir(S).map(l => `<li>${esc(l)}</li>`).join('')}</ul></div>`;

  if (T !== me) {
    const vs = D.matchs.filter(m => (m.dom_id === T && m.ext_id === me) || (m.dom_id === me && m.ext_id === T));
    const fj = vs.filter(m => m.etat === 'T').map(m => jeu(me, m)), chez = fj.filter(j => !j.dom);
    if (vs.length) h += `<div class="card"><h2>Face à ${esc(D.eq[me].nom_court)}</h2>${fj.length ? `<p class="sub">Depuis ${sais(Math.min(...fj.map(j => j.m.saison)))} : ${esc(D.eq[me].nom_court)} ${bilan(fj)}, ${fj.reduce((a, j) => a + j.bp, 0)} buts à ${fj.reduce((a, j) => a + j.bc, 0)}${chez.length ? ` · chez eux : ${bilan(chez)}` : ''}.</p>` : ''}
      ${table(['Saison', 'Match', 'Date', 'Lieu', 'Résultat'], [...vs].reverse().map(m => {
      const j = jeu(me, m); return [sais(m.saison), m.phase === 'P' ? `J${m.journee}` : esc(m.tour || 'Séries'), dateFr(m.date), (m.dom_id === me ? 'Dom. ' : 'Ext. ') + esc(m.patinoire || ''), m.etat === 'T' ? `${esc(D.eq[me].nom_court)} ${score(j)}` : 'à jouer'];
    }))}</div>`;
  }

  if (T !== me) h += carteAnalyse(T);
  h += `<div class="card"><h2>Quand ils marquent et quand ils encaissent</h2>${chartTranches(S)}</div>`;

  const scRow = (l, a) => [l, a.length, a.filter(j => j.v).length, a.length - a.filter(j => j.v).length];
  h += `<div class="grid2"><div class="card"><h2>Scénarios de match</h2>${table(['Situation', 'Matchs', 'V', 'D'],
    [scRow('Marque en premier', S.sc.premier), scRow('Encaisse en premier', S.sc.second), scRow('Mène après 2 tiers', S.sc.menant), scRow('Égalité après 2 tiers', S.sc.egal), scRow('Menée après 2 tiers', S.sc.mene)], ['', 'n', 'n', 'n'])}
    <p class="note">Réaction dans les 2 minutes : marque ${S.repP} fois juste après avoir encaissé, encaisse ${S.repC} fois juste après avoir marqué.</p></div>
    <div class="card"><h2>Situations spéciales</h2>${table(['', 'Buts', 'Occasions', '%'], [
      ['Supériorité (buts marqués)', S.ppB, '≈' + S.occ.sup, pct(S.ppB, S.occ.sup)],
      ['Infériorité (buts encaissés)', S.pkB, '≈' + S.occ.inf, pct(S.pkB, S.occ.inf)],
      ['Buts marqués en infériorité', S.shP, '', ''], ['Buts encaissés en supériorité', S.shC, '', '']], ['', 'n', 'n', 'n'])}
    <p class="note">Occasions estimées à partir des pénalités mineures, hors pénalités simultanées.</p></div></div>`;

  h += `<div class="card"><h2>Résultats</h2>${table(['J', 'Date', '', 'Adversaire', 'Résultat', 'Par tiers', 'Pén. (min)', 'Joueurs alignés'], S.js.map(j => {
    const bm = D.butsM[j.m.id] || [], per = [1, 2, 3].map(p => `${bm.filter(b => b.periode === p && b.equipe_id === T).length}-${bm.filter(b => b.periode === p && b.equipe_id !== T).length}`).join(' / ');
    const comp = (D.compM[j.m.id] || []).filter(c => c.equipe_id === T), pm = (D.penM[j.m.id] || []).filter(p => p.equipe_id === T).reduce((s, p) => s + (p.minutes || 0), 0);
    return [`J${j.m.journee}`, dateFr(j.m.date), j.dom ? 'Dom.' : 'Ext.', lienEq(j.adv), score(j), per, pm, `${comp.filter(c => c.poste !== 'GB').length} + ${comp.filter(c => c.poste === 'GB').length} G`];
  }), ['', '', '', '', '', '', 'n', 'n'])}</div>`;

  const det = S.detail.length > 0;
  h += `<div class="card"><h2>Joueurs</h2>${table(['N°', 'Joueur', 'Poste', 'MJ', 'B', 'A', 'Pts', 'Pts sup.', '+/−', 'Sur glace but enc.', 'Pén. min'].concat(det ? ['Tirs*'] : [], ['Saisons précédentes en D2 (B+A)']),
    S.joueurs.filter(p => p.poste !== 'GB').map(p => [esc(p.num), esc(D.j[p.id]), p.poste || '', p.mj, p.b, p.a, `<b>${p.b + p.a}</b>`, p.pp, (p.pm > 0 ? '+' : '') + p.pm, p.enc, p.pim].concat(det ? [p.tirs] : [], [passe(p.id)])),
    ['n', '', '', 'n', 'n', 'n', 'n', 'n', 'n', 'n', 'n', det ? 'n' : '', ''])}
    <p class="note">+/− et présence sur les buts encaissés : à égalité numérique uniquement ; les feuilles de match ne listent pas toujours tous les joueurs.${det ? ` *Tirs cadrés, sur les ${S.detail.length} matchs où ils sont saisis.` : ''}</p></div>`;

  const types = Object.entries(by(S.pens, p => p.libelle || '?')).map(([l, a]) => [l, a.length]).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const perPen = [1, 2, 3].map(p => S.pens.filter(x => x.periode === p).length);
  h += `<div class="grid2"><div class="card"><h2>Duos qui produisent</h2>${S.duos.length ? table(['Joueurs', 'Buts ensemble'], S.duos.map(d => [`${esc(D.j[d.a])} + ${esc(D.j[d.b])}`, d.n]), ['', 'n']) : '<p class="muted">Pas encore assez de buts.</p>'}</div>
    <div class="card"><h2>Discipline</h2>${table(['Type de pénalité', 'Nombre'], types.map(t => [esc(t[0]), t[1]]), ['', 'n'])}
    <p class="note">Par tiers : ${perPen.join(' / ')}. Pénalités provoquées chez l'adversaire : ${S.pensAdv.length}.</p></div></div>`;

  h += `<div class="card"><h2>Gardiens</h2>${table(['Gardien', 'Titulaire', 'Minutes', 'Buts enc.', 'Moy. / match', '% arrêts*', 'Tirs reçus*', 'Saisons précédentes en D2'], S.gardiens.map(g => [esc(D.j[g.id]), g.tit, Math.round(g.sec / 60), g.enc, g.sec ? (g.enc * 3600 / g.sec).toFixed(2) : '–',
    g.tirs ? (100 * g.tirs / (g.tirs + g.encDet)).toFixed(1) + ' %' : '–', g.tirs ? g.tirs + g.encDet : '–', passe(g.id, true)]), ['', 'n', 'n', 'n', 'n', 'n', 'n', ''])}
    <p class="note">*Uniquement sur les matchs où les tirs sont saisis (en pratique, les matchs joués à Brest).</p></div>`;

  if (det) h += `<div class="grid2"><div class="card"><h2>Tirs et engagements par match</h2>${table(['Date', 'Adversaire', 'Tirs pour', 'Tirs contre', 'Engagements gagnés'],
    S.detail.map(d => [dateFr(d.j.m.date), lienEq(d.j.adv), d.tp, d.tc, `${d.eg}/${d.et} (${pct(d.eg, d.et)})`]), ['', '', 'n', 'n', 'n'])}</div>
    <div class="card"><h2>Engagements par joueur</h2>${table(['Joueur', 'Gagnés', 'Joués', '%'], S.enga.map(x => [esc(D.j[x.id] || '?'), x.g, x.n, pct(x.g, x.n)]), ['', 'n', 'n', 'n'])}</div></div>`;

  const H = historique(T), c = H.cont;
  if (H.lignes.some(l => l.s < CUR)) {
    let txt = '';
    if (c.now) {
      const meme = c.coachNow && c.coachPrec && c.coachNow === c.coachPrec;
      txt = `<ul class="keys"><li>Entraîneur : ${c.coachNow ? esc(c.coachNow) : 'non renseigné'}${c.coachPrec ? (meme ? `, déjà en poste en ${sais(c.prec)} : les tendances de la saison passée restent une bonne indication.` : `. En ${sais(c.prec)} c'était ${esc(c.coachPrec)} : le style de jeu a pu changer.`) : '.'}</li>
        <li>Effectif : ${c.restes} des ${c.now} joueurs alignés cette saison étaient déjà au club en ${sais(c.prec)}. Ils avaient marqué ${c.butsRestes} des ${c.buts} buts de l'équipe (${pct(c.butsRestes, c.buts)}).</li>
        <li>Arrivées : ${c.recrues} joueur(s) venu(s) d'un autre club de D2 (leur passé figure dans le tableau des joueurs), ${c.nouveaux} sans match de D2 sur les deux saisons précédentes.</li></ul>`;
    } else if (!n) txt = `<p class="sub">Cette équipe ne joue pas en D2 cette saison.</p>`;
    h += `<div class="card"><h2>Saisons précédentes : ce qui reste valable</h2>${txt}
      ${table(['Saison', 'Entraîneur', 'Rang', 'MJ', 'Pts', 'Buts pour / match', 'Buts contre / match', 'Sup.', 'Inf.', 'Domicile', 'Extérieur', 'Séries'],
        H.lignes.map(l => ({ cls: l.s === CUR ? 'me' : '', c: [sais(l.s), esc(l.coach || '–'), l.rang ? `${l.rang}/${l.taille}` : '–', l.n, l.pts, l.bp.toFixed(1), l.bc.toFixed(1), l.pp, l.pk, l.dom, l.ext, esc(l.series)] })),
        ['', '', 'n', 'n', 'n', 'n', 'n', 'n', 'n', '', '', ''])}
      <p class="note">Saison régulière uniquement, sauf la colonne « Séries ». Seules les saisons jouées en D2 apparaissent.</p></div>`;
  }
  if (proch.length) h += `<div class="card"><h2>Prochains matchs</h2>${table(['J', 'Date', '', 'Adversaire'], proch.map(m => [`J${m.journee}`, dateFr(m.date) + ' ' + heure(m.date), m.dom_id === T ? 'Dom.' : 'Ext.', lienEq(m.dom_id === T ? m.ext_id : m.dom_id)]))}</div>`;
  return h;
}

const prochain = () => { const me = D.ab[ME].id; return D.matchs.find(m => m.etat !== 'T' && (m.dom_id === me || m.ext_id === me)); };

function pageAccueil() {
  const me = D.ab[ME].id, S = stats(me), p = prochain(), adv = p && (p.dom_id === me ? p.ext_id : p.dom_id);
  let h = `<h1>${esc(D.eq[me].nom)}</h1><p class="sub">Division 2, saison régulière 2026-27</p>`;
  if (p) {
    const A = stats(adv);
    h += `<div class="card"><h2>Prochain match</h2><div class="next"><b>${p.dom_id === me ? 'Domicile' : 'Extérieur'} · ${esc(D.eq[adv].nom)}</b>
      <span>${dateFr(p.date)} à ${heure(p.date)} · ${esc(p.patinoire || '')} · J${p.journee}</span>
      <a href="#/equipe/${D.eq[adv].abreviation}">Ouvrir la fiche adversaire →</a></div></div>${cartePreco(adv, 6)}`;
  }
  h += `<div class="card"><h2>Classement de la poule</h2>${tableClassement(me, [me, adv])}</div>`;
  h += `<div class="grid2"><div class="card"><h2>Derniers résultats</h2>${table(['J', 'Date', '', 'Adversaire', 'Résultat'], S.js.slice(-6).reverse().map(j => [`J${j.m.journee}`, dateFr(j.m.date), j.dom ? 'Dom.' : 'Ext.', lienEq(j.adv), score(j)]))}
    <p class="note"><a href="#/equipe/${ME}">Fiche complète de Brest →</a></p></div>
    <div class="card"><h2>Calendrier à venir</h2>${table(['J', 'Date', '', 'Adversaire'], D.matchs.filter(m => m.etat !== 'T' && (m.dom_id === me || m.ext_id === me)).slice(0, 6).map(m => [`J${m.journee}`, dateFr(m.date) + ' ' + heure(m.date), m.dom_id === me ? 'Dom.' : 'Ext.', lienEq(m.dom_id === me ? m.ext_id : m.dom_id)]))}</div></div>`;
  return h;
}

function route() {
  const m = location.hash.match(/^#\/equipe\/(\w+)/), ab = m && m[1].toUpperCase(), p = prochain();
  const advAb = p ? D.eq[p.dom_id === D.ab[ME].id ? p.ext_id : p.dom_id].abreviation : null;
  $('#app').innerHTML = ab ? pageEquipe(ab) : pageAccueil();
  $('#nav-adv').href = advAb ? `#/equipe/${advAb}` : '#/';
  const cur = !ab ? 'home' : ab === ME ? 'me' : ab === advAb ? 'adv' : '';
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('on', a.dataset.nav === cur));
  $('#team-select').value = ab || '';
  document.title = ab && D.ab[ab] ? `${D.ab[ab].nom_court} – Hockey Raban` : 'Hockey Raban – D2';
  window.scrollTo(0, 0);
}

load().then(() => {
  const me = D.ab[ME].id, mine = new Set(poule(me));
  const opt = e => `<option value="${e.abreviation}">${esc(e.nom_court)}</option>`, tri = (a, b) => a.nom_court.localeCompare(b.nom_court);
  $('#team-select').innerHTML = `<option value="">Autre équipe…</option><optgroup label="Notre poule">${D.equipes.filter(e => mine.has(e.id)).sort(tri).map(opt).join('')}</optgroup><optgroup label="Autre poule">${D.equipes.filter(e => !mine.has(e.id) && D.actuelles.has(e.id)).sort(tri).map(opt).join('')}</optgroup>`;
  $('#team-select').onchange = e => { location.hash = e.target.value ? `#/equipe/${e.target.value}` : '#/'; };
  $('#print').onclick = () => window.print();
  const last = D.joues[D.joues.length - 1];
  $('#maj').textContent = last ? `Données à jour au ${dateFr(last.date)} (${D.joues.length} matchs joués cette saison, ${D.finis.length} avec les saisons ${sais(Math.min(...D.finis.map(m => m.saison)))} et suivantes)` : '';
  const tip = $('#tip');
  document.addEventListener('mousemove', e => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip; tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 12, innerWidth - 250) + 'px'; tip.style.top = e.clientY + 14 + 'px';
  });
  window.addEventListener('hashchange', route); route();
}).catch(err => { $('#app').innerHTML = `<p>Impossible de charger les données : ${esc(err.message)}</p>`; });
