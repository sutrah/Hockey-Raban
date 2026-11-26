// Présentation « D'où partent les buts encaissés » : patinoire et tableau en vis-à-vis, filtres en haut.
// Le même fichier sert en local et dans l'onglet « Analyses vidéo IA » du site.
// data = { maj, matchs: [{ id, date, dom, ext, taille, domicile, buts: [...] }] }
window.TirsBrest = function (el, data) {
  'use strict';
  const donneesInit = data;
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fr = x => x.toFixed(1).replace('.', ',');
  const jj = d => d.slice(8, 10) + '/' + d.slice(5, 7);
  const hm = s => s == null ? '' : Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  const tiers = p => p > 3 ? 'Prol.' : 'P' + p;
  const SIT = [['EG', 'Égalité', '#c62828'], ['SUP', 'Supériorité du tireur', '#d97000'], ['INF', 'Infériorité du tireur', '#7b1fa2']];
  const sit = b => SIT.find(s => (b.situation || '').startsWith(s[0])) || SIT[0];
  // plusieurs analyses possibles (buts encaissés par Brest, marqués par Luxembourg…) : une seule affichée à la fois
  const cle = m => { const a = m.analyse || { equipe: 'BRE', nom: 'BREST', sens: 'encaisses' }; return a.equipe + '|' + a.sens; };
  const AN = [...new Map(data.matchs.map(m => [cle(m), m.analyse || { equipe: 'BRE', nom: 'BREST', sens: 'encaisses' }])).entries()];
  const nomEq = n => n === 'BREST' ? 'Brest' : n.charAt(0) + n.slice(1).toLowerCase();
  const titreAn = a => `Buts ${a.sens === 'marques' ? 'marqués' : 'encaissés'} par ${nomEq(a.nom)}`;
  const choix = new URLSearchParams(location.hash.split('?')[1] || '').get('analyse');
  const A = (AN.find(x => x[0] === choix) || AN[0]);
  const EQ = A[1];
  data = { ...data, matchs: data.matchs.filter(m => cle(m) === A[0]) };
  const NE = nomEq(EQ.nom);
  let n = 0;
  const tous = data.matchs.flatMap(m => m.buts.map(b => ({ ...b, m, num: ++n })));
  const uniq = k => [...new Set(tous.map(b => b[k]).filter(Boolean))].sort();
  const sel = (id, lab, opts) => `<label>${lab}<select data-f="${id}"><option value="">${id === 'match' ? 'Tous les matchs' : 'Tous'}</option>${opts.map(o => `<option value="${esc(o[0])}">${esc(o[1])}</option>`).join('')}</select></label>`;
  const adv = m => (m.domicile ? m.ext : m.dom);
  el.innerHTML = `<div class="tb"><h1>${titreAn(EQ)} : d’où partent les tirs</h1>
  <p class="sub">Saison 2026-27. Chaque point est le départ du tir, la cage ${EQ.sens === 'marques' ? 'visée' : 'de ' + NE} est en haut.</p>
  <div class="tb-f">${AN.length > 1 ? `<label>Analyse<select data-an>${AN.map(x => `<option value="${x[0]}"${x[0] === A[0] ? ' selected' : ''}>${titreAn(x[1])}</option>`).join('')}</select></label>` : ''}${sel('match', 'Match', data.matchs.map(m => [m.id, `${jj(m.date)} ${m.domicile ? NE + ' – ' + adv(m) : adv(m) + ' – ' + NE}${m.buts.some(b => b.distance != null) ? '' : ' (à placer)'}`]))}
  ${sel('lieu', 'Lieu', [['d', 'Domicile'], ['e', 'Extérieur']])}${sel('per', 'Tiers', [[1, '1er'], [2, '2e'], [3, '3e'], [4, 'Prolongation']])}
  ${sel('sit', 'Situation', SIT.map(s => [s[0], s[1]]))}${sel('gar', 'Gardien', uniq('gardien').map(g => [g, g]))}
  ${sel('ges', 'Geste', uniq('geste').map(g => [g, g]))}${sel('act', 'Action', uniq('action').map(g => [g, g]))}
  <button type="button" data-raz>Effacer les filtres</button></div>
  <p class="tb-imp"></p><div class="tb-t"></div>
  <div class="tb-v"><div class="card tb-r"></div><div class="card tb-l"></div></div>
  <p class="note">Positions pointées à la main sur la vidéo, puis converties en mètres. Le halo est la marge d’erreur de la position ; les pastilles trop proches sont légèrement écartées pour rester lisibles. Sans halo et avec * dans le tableau : position ajustée à la main. Mis à jour le ${esc(data.maj)}.</p></div>`;
  const $ = s => el.querySelector(s), V = {};
  document.querySelectorAll('.tb-tip').forEach(t => t.remove());       // une seule infobulle, même après changement d'onglet
  const tip = document.createElement('div');
  tip.className = 'tb-tip';
  tip.hidden = true;
  el.append(tip);
  const rang = k => k + (k === 1 ? 're' : 'e');
  function montrer(b, g) {
    const t = b.temps != null ? b.temps : b.reste != null ? b.periode * 1200 - b.reste : null;
    const quand = b.periode > 3 ? 'Prolongation' : `${b.periode === 1 ? '1er' : b.periode + 'e'} tiers`;
    tip.innerHTML = `<b>${esc(b.buteur)}</b><span>${b.m.domicile ? NE + ' – ' + esc(adv(b.m)) : esc(adv(b.m)) + ' – ' + NE}, ${jj(b.m.date)}</span>
    <span>${quand}${t != null ? `, ${rang(Math.floor(t / 60) + 1)} minute` : ''}${b.reste != null ? ` (chrono ${hm(b.reste)})` : ''}</span>`;
    tip.style.setProperty('--c', sit(b)[2]);
    tip.hidden = false;
    const r = g.querySelector('.p').getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const haut = r.top - h - 8;
    tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
    tip.style.top = (haut > 8 ? haut : r.bottom + 8) + 'px';
  }
  const cacher = () => { tip.hidden = true; };
  addEventListener('scroll', cacher, { passive: true });
  el.addEventListener('click', e => { if (!e.target.closest('.tb-r g[data-n]')) cacher(); });
  const sa = el.querySelector('[data-an]');
  if (sa) sa.onchange = () => {
    const h = location.hash.split('?')[0] || '#/video';
    history.replaceState(null, '', h + '?analyse=' + encodeURIComponent(sa.value));
    window.TirsBrest(el, donneesInit);
  };
  el.querySelectorAll('[data-f]').forEach(s => { s.onchange = () => { V[s.dataset.f] = s.value; rendre(); }; });
  $('[data-raz]').onclick = () => { el.querySelectorAll('[data-f]').forEach(s => { s.value = ''; V[s.dataset.f] = ''; }); rendre(); };

  function ecarter(L, c, W) {
    const R = 1.3, p = L.map((b, i) => ({ x0: c + b.lat, y0: 4 + b.prof, x: c + b.lat + 1e-3 * Math.cos(i * 2.4), y: 4 + b.prof + 1e-3 * Math.sin(i * 2.4) }));
    for (let k = 0; k < 400; k++) {
      let bouge = false;
      for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) {
        const dx = p[j].x - p[i].x, dy = p[j].y - p[i].y, d = Math.hypot(dx, dy) || 1e-6;
        if (d >= R - 1e-3) continue;
        const f = (R - d) / 2 / d;
        p[i].x -= dx * f; p[i].y -= dy * f; p[j].x += dx * f; p[j].y += dy * f; bouge = true;
      }
      for (const q of p) { q.x = Math.max(.6, Math.min(W - .6, q.x)); q.y = Math.max(.6, Math.min(29.4, q.y)); }
      if (!bouge) break;
    }
    return p;
  }
  function patinoire(L) {
    const W = 30, c = 15, bord = 8.5 - Math.sqrt(8.5 ** 2 - 4.5 ** 2);
    const rond = (l, r) => `<circle cx="${c + l}" cy="10" r="${r}" fill="${r < 1 ? '#d33' : 'none'}" stroke="#d33" stroke-width=".12"/>`;
    let s = `<svg viewBox="-.5 -.5 31 31" role="img" aria-label="Carte des tirs"><path d="M0 30V8.5A8.5 8.5 0 0 1 8.5 0H21.5A8.5 8.5 0 0 1 30 8.5V30Z" fill="#f8fbff" stroke="#333" stroke-width=".2"/>
    <path d="M${bord} 4H${W - bord}M0 30H${W}" stroke="#d33" stroke-width=".15"/><path d="M0 22.86H${W}" stroke="#27c" stroke-width=".3"/>
    <path d="M${c - 1.83} 4A1.83 1.83 0 0 0 ${c + 1.83} 4Z" fill="#cfe4ff" stroke="#27c" stroke-width=".1"/>
    <path d="M${c - .915} 4V3H${c + .915}V4" fill="none" stroke="#d33" stroke-width=".18"/>${rond(-7, 4.5)}${rond(7, 4.5)}${rond(-7, .3)}${rond(7, .3)}`;
    const P = ecarter(L, c, W);
    L.forEach((b, i) => {
      const { x0, y0, x, y } = P[i], f = sit(b)[2];
      s += `<g data-n="${b.num}">${b.ajuste ? '' : `<circle cx="${x0}" cy="${y0}" r="${Math.min(4, b.marge || 0)}" fill="${f}" opacity=".12"/>`}
      <circle class="p" cx="${x}" cy="${y}" r=".6" fill="${f}" stroke="#fff" stroke-width=".14"/>
      <text x="${x}" y="${y + .25}" font-size=".7" text-anchor="middle" fill="#fff" font-weight="600">${num(b)}</text></g>`;
    });
    return s + '</svg><div class="tb-leg">' + SIT.filter(q => L.some(b => sit(b) === q)).map(q => `<span><i style="background:${q[2]}"></i>${q[1]}</span>`).join('') + '</div>';
  }
  const num = b => V.match ? b.n : b.num;
  function rendre() {
    cacher();
    const F = tous.filter(b => (!V.match || String(b.m.id) === V.match) && (!V.lieu || b.m.domicile === (V.lieu === 'd')) && (!V.per || b.periode === +V.per)
      && (!V.sit || sit(b)[0] === V.sit) && (!V.gar || b.gardien === V.gar) && (!V.ges || b.geste === V.ges) && (!V.act || b.action === V.act));
    const choisis = [...el.querySelectorAll('[data-f]')].filter(s => s.value).map(s => s.closest('label').firstChild.textContent + ' : ' + s.selectedOptions[0].text);
    $('.tb-imp').textContent = 'Filtres : ' + (choisis.length ? choisis.join(' · ') : 'aucun (tous les buts)');
    const L = F.filter(b => b.distance != null), d = L.map(b => b.distance).sort((a, b) => a - b), k = d.length;
    const med = k ? (k % 2 ? d[(k - 1) / 2] : (d[k / 2 - 1] + d[k / 2]) / 2) : null, pres = d.filter(x => x < 5).length;
    $('.tb-t').innerHTML = `<div class="tiles"><div class="tile"><b>${k} <small style="display:inline">sur ${F.length}</small></b><span>buts placés sur la carte</span></div>
    <div class="tile"><b>${med == null ? '–' : fr(med) + ' m'}</b><span>distance médiane à la cage</span></div>
    <div class="tile"><b>${k ? pres + ' sur ' + k : '–'}</b><span>à moins de 5 m</span></div>
    <div class="tile"><b>${L.filter(b => b.action === 'rebond').length} / ${L.filter(b => /réception/.test(b.action || '')).length}</b><span>rebonds / sur réception</span></div></div>`;
    $('.tb-r').innerHTML = patinoire(L);
    $('.tb-l').innerHTML = k ? `<div class="scroll"><table><thead><tr><th>N°</th>${V.match ? '' : '<th>Match</th>'}<th>Chrono</th><th>Buteur</th><th class="n">Distance</th><th class="n">Angle</th><th>Situation</th><th>Geste</th><th>Action</th></tr></thead><tbody>` +
      L.map(b => `<tr data-n="${b.num}" tabindex="0"><td><i class="tb-p" style="background:${sit(b)[2]}">${num(b)}</i></td>${V.match ? '' : `<td>${jj(b.m.date)} ${esc(adv(b.m))} <small class="muted">${b.m.domicile ? 'dom.' : 'ext.'}</small></td>`}
      <td>${tiers(b.periode)} ${hm(b.reste)}</td><td>${esc(b.buteur)}</td><td class="n">${fr(b.distance)} m${b.ajuste ? ' *' : ` <small class="muted">± ${fr(b.margeD)}</small>`}</td><td class="n">${b.angle}°</td>
      <td>${sit(b)[1]}</td><td>${esc(b.geste) || '<span class="muted">–</span>'}</td><td>${esc(b.action) || '<span class="muted">–</span>'}${b.ecran ? ', écran' : ''}${b.devie ? ', dévié' : ''}</td></tr>`).join('') +
      `</tbody></table></div>${L.some(b => b.ajuste) ? '<p class="note">* position ajustée à la main</p>' : ''}` : '<p class="muted">Aucun but placé pour ces filtres.</p>';
    el.querySelectorAll('.tb-v [data-n]').forEach(e => {
      const on = v => el.querySelectorAll(`.tb-v [data-n="${e.dataset.n}"]`).forEach(q => q.classList.toggle('vu', v));
      const b = tous[e.dataset.n - 1], point = e.closest('.tb-r');
      e.onmouseenter = () => { on(true); if (point) montrer(b, e); };
      e.onmouseleave = () => { on(false); if (point) cacher(); };
      if (point) e.onclick = () => montrer(b, e);                      // doigt sur téléphone
      else { e.onfocus = () => on(true); e.onblur = () => on(false); }
    });
  }
  rendre();
};
