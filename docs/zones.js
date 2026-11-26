// « Zones de tir » : mini-cartes + conseils calculés à partir des positions publiées (data/buts.json).
// Fiche de match : où l'adversaire marque / où nous encaissons (et l'inverse quand c'est saisi).
// Fiche équipe : où l'équipe marque / où elle encaisse.
window.ZonesTir = (function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fr = x => x.toFixed(1).replace('.', ',');
  const pc = (a, b) => b ? Math.round(100 * a / b) : 0;
  const COUL = { EG: '#c62828', SUP: '#d97000', INF: '#7b1fa2' };
  const coul = b => COUL[(b.situation || 'EG').slice(0, 3)] || COUL.EG;
  const proche = b => b.distance <= 3.5 || (b.prof >= 0 && b.prof <= 9 && Math.abs(b.lat) <= 4.5);
  const loin = b => b.distance >= 10;
  const angle = b => b.prof >= 0 && !proche(b) && Math.abs(b.lat) > 4.5 && b.prof <= 16;
  const MIN = 8;                                   // en dessous : pas de conseil, trop peu de buts
  const nomP = s => s;

  function buts(data, ab, sens) {
    return data.matchs.filter(m => m.analyse && m.analyse.equipe === ab && m.analyse.sens === sens)
      .flatMap(m => m.buts.filter(b => b.distance != null).map(b => ({ ...b, m })));
  }
  function mini(L, titre) {
    const W = 30, c = 15, bord = 8.5 - Math.sqrt(8.5 ** 2 - 4.5 ** 2);
    const rond = l => `<circle cx="${c + l}" cy="10" r="4.5" fill="none" stroke="#d33" stroke-width=".15"/><circle cx="${c + l}" cy="10" r=".3" fill="#d33"/>`;
    // demi-zone seulement (jusqu'à la ligne bleue) : c'est là que partent presque tous les tirs
    return `<figure class="zt-f"><svg viewBox="-.5 -.5 31 24.5" role="img" aria-label="${esc(titre)}">
      <path d="M0 23.5V8.5A8.5 8.5 0 0 1 8.5 0H21.5A8.5 8.5 0 0 1 30 8.5V23.5" fill="#f8fbff" stroke="#333" stroke-width=".25"/>
      <path d="M${bord} 4H${W - bord}" stroke="#d33" stroke-width=".18"/><path d="M0 22.86H${W}" stroke="#27c" stroke-width=".35"/>
      <path d="M${c - 1.83} 4A1.83 1.83 0 0 0 ${c + 1.83} 4Z" fill="#cfe4ff" stroke="#27c" stroke-width=".12"/>
      <path d="M${c - .915} 4V3H${c + .915}V4" fill="none" stroke="#d33" stroke-width=".2"/>${rond(-7)}${rond(7)}
      <rect x="${c - 4.5}" y="4" width="9" height="9" fill="#c62828" opacity=".05" stroke="#c62828" stroke-opacity=".35" stroke-width=".1" stroke-dasharray=".4 .3"/>
      ${L.map(b => `<circle cx="${Math.max(.5, Math.min(29.5, c + b.lat))}" cy="${Math.max(.5, Math.min(23, 4 + b.prof))}" r=".6" fill="${coul(b)}" stroke="#fff" stroke-width=".15"><title>${esc(b.buteur)} · ${fr(b.distance)} m</title></circle>`).join('')}
    </svg><figcaption>${esc(titre)} <small>(${L.length})</small></figcaption></figure>`;
  }
  // profil d'un ensemble de buts : zones, tiers, côté, actions, buteurs
  function profil(L) {
    const n = L.length, act = L.filter(b => b.action && b.action !== 'autre');
    const tiers = [1, 2, 3].map(p => L.filter(b => b.periode === p).length);
    const g = L.filter(b => b.lat > 1.5).length, d = L.filter(b => b.lat < -1.5).length;   // carte : lat > 0 = droite de la carte = gauche du gardien
    const par = {};
    L.forEach(b => (par[b.buteur] = par[b.buteur] || []).push(b));
    return { n, proche: L.filter(proche).length, loin: L.filter(loin).length, angle: L.filter(angle).length, tiers,
      gaucheG: g, droiteG: d, act: act.length, reception: act.filter(b => /réception/.test(b.action)).length, rebond: act.filter(b => b.action === 'rebond').length,
      eg: L.filter(b => (b.situation || '').startsWith('EG')).length, buteurs: Object.entries(par).filter(([, v]) => v.length >= 3).sort((a, b) => b[1].length - a[1].length) };
  }
  const med = a => { const s = [...a].sort((x, y) => x - y), k = s.length; return k % 2 ? s[(k - 1) / 2] : (s[k / 2 - 1] + s[k / 2]) / 2; };
  function joueurs(P) {
    return P.buteurs.map(([nom, v]) => {
      const m = med(v.map(b => b.distance)), num = v[0].numero ? 'n°' + v[0].numero + ' ' : '';
      return m <= 4.5 ? `${num}${nomP(nom)} (${v.length} buts, à ${fr(m)} m de la cage en médiane : devant le but)`
        : m >= 10 ? `${num}${nomP(nom)} (${v.length} buts, à ${fr(m)} m en médiane : tir de loin)` : `${num}${nomP(nom)} (${v.length} buts, ${fr(m)} m en médiane)`;
    });
  }
  // conseils défensifs : A = où l'adversaire marque, B = où nous encaissons
  function conseilsDefense(A, B, nO, nB) {
    const r = [];
    if (A.n < MIN) return r;
    if (A.proche / A.n >= .6) r.push(['v', 'Fermer l\'enclave et le devant de cage',
      `${pc(A.proche, A.n)} % des buts de ${nO} partent de moins de 9 m dans l'axe (${A.proche} sur ${A.n}), seulement ${A.loin} de 10 m ou plus et ${A.angle} d'un angle fermé.` +
      (B && B.n >= MIN ? ` ${nB} encaisse ${pc(B.proche, B.n)} % de ses buts dans cette zone et ${B.loin} de loin : ce n'est pas l'arme de ${nO}.` : '')]);
    else if (A.loin / A.n >= .35) r.push(['v', 'Gêner les tirs de loin', `${pc(A.loin, A.n)} % des buts de ${nO} partent de 10 m ou plus (${A.loin} sur ${A.n}) : fermer les lignes de tir, bloquer, masquer le moins possible le gardien.`]);
    if (A.act >= 6 && A.reception / A.act >= .35) r.push(['v', 'Couper les passes vers l\'enclave', `${A.reception} des ${A.act} buts dont l'action est connue sont des tirs sur réception : rester entre le passeur et l'enclave plutôt que monter sur le porteur.`]);
    if (A.act >= 6 && A.rebond / A.act >= .3) r.push(['v', 'Dégager les rebonds', `${A.rebond} des ${A.act} buts dont l'action est connue viennent d'un rebond.`]);
    const tm = Math.max(...A.tiers), tp = A.tiers.indexOf(tm) + 1;
    if (A.n >= 10 && tm / A.n >= .45) r.push(['v', `Vigilance au ${tp === 1 ? '1er' : tp + 'e'} tiers`, `${tm} des ${A.n} buts de ${nO} tombent au ${tp === 1 ? '1er' : tp + 'e'} tiers.` + (tp === 2 ? ' Au 2e, le banc est loin de la zone défensive : présences courtes, ne pas changer en zone défensive.' : '')]);
    const cg = A.gaucheG, cd = A.droiteG;
    if (cg + cd >= 10 && Math.max(cg, cd) >= 2 * Math.min(cg, cd)) r.push(['a', `Côté ${cg > cd ? 'gauche' : 'droit'} du gardien`, `${Math.max(cg, cd)} buts de ${nO} à sa ${cg > cd ? 'gauche' : 'droite'} contre ${Math.min(cg, cd)} de l'autre côté (vu du gardien) : tendance à montrer au gardien.`]);
    const J = joueurs(A);
    if (J.length) r.push(['v', 'Leurs buteurs et leur zone', J.join(' ; ') + '.']);
    if (A.eg / A.n >= .85) r.push(['a', 'Danger surtout à 5 contre 5', `${A.eg} des ${A.n} buts de ${nO} sont marqués à égalité numérique.`]);
    return r;
  }
  // conseils offensifs : C = où l'adversaire encaisse, E = où nous marquons
  function conseilsAttaque(C, E, nO, nB) {
    const r = [];
    if (!C || C.n < MIN) return r;
    if (C.proche / C.n >= .6) r.push(['e', 'Aller au filet', `${nO} encaisse ${pc(C.proche, C.n)} % de ses buts devant la cage ou dans l'enclave (${C.proche} sur ${C.n}).` + (E && E.n >= MIN ? ` ${nB} marque ${pc(E.proche, E.n)} % de ses buts dans cette zone.` : '')]);
    if (C.loin / C.n >= .3) r.push(['e', 'Tirer de loin', `${pc(C.loin, C.n)} % des buts encaissés par ${nO} partent de 10 m ou plus : mettre du monde devant le gardien et tirer.`]);
    if (C.act >= 6 && C.rebond / C.act >= .3) r.push(['e', 'Suivre les tirs', `${C.rebond} des ${C.act} buts encaissés par ${nO} (action connue) viennent d'un rebond.`]);
    if (C.act >= 6 && C.reception / C.act >= .35) r.push(['e', 'Jeu de passes latérales', `${C.reception} des ${C.act} buts encaissés par ${nO} (action connue) sont des tirs sur réception.`]);
    return r;
  }
  const bloc = r => r.length ? `<div class="pts">${r.map(([t, ti, x]) => `<div class="ptc ${t}"><b>${t === 'e' ? '▲' : '⚠'} ${esc(ti)}</b><p>${esc(x)}</p></div>`).join('')}</div>` : '';
  const pied = (n, maj) => `<p class="note">Positions pointées à la main sur la vidéo (précision environ 1 m), ${n} buts au total. Rectangle pointillé : enclave (moins de 9 m, dans l'axe). Point orange : supériorité du tireur, violet : infériorité. Tendances sur peu de buts, à confirmer à la vidéo. Mis à jour le ${esc(maj)}.</p>`;

  // fiche de match : B = nous (Brest), O = adversaire
  function fiche(el, data, ab, nB, abO, nO) {
    const A = buts(data, abO, 'marques'), B = buts(data, ab, 'encaisses'), C = buts(data, abO, 'encaisses'), E = buts(data, ab, 'marques');
    if (!A.length && !C.length) { el.remove(); return; }
    const PA = profil(A), PB = profil(B), PC = profil(C), PE = profil(E);
    const def = conseilsDefense(PA, PB, nO, nB), att = conseilsAttaque(PC, PE, nO, nB);
    const cartes = [A.length && mini(A, `Où ${nO} marque`), B.length && mini(B, `Où ${nB} encaisse`), C.length && mini(C, `Où ${nO} encaisse`), E.length && mini(E, `Où ${nB} marque`)].filter(Boolean);
    el.innerHTML = `<h2>Zones de tir <span class="badge b-p">partiel</span> <small class="muted">vidéo</small></h2>
      <div class="zt-c">${cartes.join('')}</div>${bloc(att)}${bloc(def)}${!def.length && !att.length ? `<p class="muted">Pas encore assez de buts placés pour des conseils (${MIN} au moins).</p>` : ''}
      ${pied(A.length + B.length + C.length + E.length, data.maj)}`;
  }
  // fiche équipe : où elle marque, où elle encaisse
  function equipe(el, data, ab, nom) {
    const A = buts(data, ab, 'marques'), C = buts(data, ab, 'encaisses');
    if (!A.length && !C.length) { el.remove(); return; }
    const PA = profil(A), PC = profil(C), r = [];
    if (PA.n >= MIN) r.push(['e', 'Où elle marque', `${pc(PA.proche, PA.n)} % de ses buts partent de moins de 9 m dans l'axe, ${PA.loin} de 10 m ou plus, ${PA.angle} d'un angle fermé.` + (joueurs(PA).length ? ' Buteurs : ' + joueurs(PA).join(' ; ') + '.' : '')]);
    if (PC.n >= MIN) r.push(['v', 'Où elle encaisse', `${pc(PC.proche, PC.n)} % des buts encaissés partent de moins de 9 m dans l'axe, ${PC.loin} de 10 m ou plus, ${PC.angle} d'un angle fermé. Par tiers : ${PC.tiers.join(' / ')}.`]);
    el.innerHTML = `<h2>Zones de tir <span class="badge b-p">partiel</span> <small class="muted">vidéo</small></h2>
      <div class="zt-c">${[A.length && mini(A, `Où ${nom} marque`), C.length && mini(C, `Où ${nom} encaisse`)].filter(Boolean).join('')}</div>${bloc(r)}${pied(A.length + C.length, data.maj)}`;
  }
  let cache = null;
  function donnees() {
    if (!cache) cache = fetch('data/buts.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).catch(() => null);
    return cache;
  }
  return { fiche, equipe, donnees };
})();
