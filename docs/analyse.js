/* Analyse : tendances d'une équipe, axes de progression, préconisations face à un adversaire.
   Tout est déduit des feuilles de match ; chaque phrase cite les chiffres qui la justifient. */
const _P = {};
const f1 = v => v.toFixed(1).replace('.', ',');
const sg = v => Math.abs(v) < 0.05 ? '0,0' : (v > 0 ? '+' : '') + f1(v);
const pc = v => Math.round(100 * v) + ' %';
const nomJ = id => D.j[id] || '?';
const liste = a => a.length > 1 ? a.slice(0, -1).join(', ') + ' et ' + a[a.length - 1] : a.join('');

function profil(T) {
  if (_P[T]) return _P[T];
  const S = stats(T), n = S.js.length || 1, dom = S.js.filter(j => j.dom), ext = S.js.filter(j => !j.dom);
  const ptsm = a => a.length ? a.reduce((s, j) => s + j.pts, 0) / a.length : null, d = i => (S.tiers[i].p - S.tiers[i].c) / n;
  const champ = a => a.length ? a.reduce((s, j) => s + (D.compM[j.m.id] || []).filter(c => c.equipe_id === T && c.poste !== 'GB').length, 0) / a.length : null;
  return _P[T] = { T, S, n: S.js.length, dom, ext, bpm: S.pour.length / n, bcm: S.contre.length / n,
    pp: S.occ.sup ? S.ppB / S.occ.sup : null, pk: S.occ.inf ? 1 - S.pkB / S.occ.inf : null, penm: S.occ.inf / n, provm: S.occ.sup / n,
    t1: d(0), t2: d(1), t3: d(2), ptsDom: ptsm(dom), ptsExt: ptsm(ext), premier: S.sc.premier.length / n, champDom: champ(dom), champExt: champ(ext) };
}

const IND = [
  ['Buts marqués par match', 'bpm', 1, f1], ['Buts encaissés par match', 'bcm', -1, f1],
  ['Supériorité (réussite)', 'pp', 1, pc], ['Infériorité (réussite)', 'pk', 1, pc],
  ['Pénalités mineures prises par match', 'penm', -1, f1], ['Pénalités provoquées par match', 'provm', 1, f1],
  ['Différence de buts au 1er tiers, par match', 't1', 1, sg], ['Différence de buts au 2e tiers, par match', 't2', 1, sg], ['Différence de buts au 3e tiers, par match', 't3', 1, sg],
  ['Points par match à domicile', 'ptsDom', 1, f1], ['Points par match à l\'extérieur', 'ptsExt', 1, f1], ['Matchs où l\'équipe ouvre le score', 'premier', 1, pc]];

// place l'équipe parmi toutes les équipes de D2 de la saison sur chaque indicateur
function comparer(T) {
  const tous = [...D.actuelles].map(profil).filter(p => p.n > 0), moi = profil(T);
  return IND.map(([lab, k, sens, fmt]) => {
    const vals = tous.map(p => p[k]).filter(v => v != null), v = moi[k];
    if (v == null) return null;
    const rang = 1 + vals.filter(x => sens * x > sens * v).length, sur = vals.length;
    return { lab, k, v, fmt, moy: vals.reduce((a, b) => a + b, 0) / sur, rang, sur, niveau: rang <= Math.ceil(sur / 3) ? 'fort' : rang > sur - Math.ceil(sur / 3) ? 'faible' : '' };
  }).filter(Boolean);
}

// joueurs habituellement alignés et absents des derniers matchs (la cause n'est pas publiée)
function absences(T) {
  const js = matchsDe(T); if (js.length < 4) return [];
  const pres = js.map(j => new Set((D.compM[j.m.id] || []).filter(c => c.equipe_id === T).map(c => c.joueur_id))), out = [];
  for (const id of new Set(pres.flatMap(s => [...s]))) {
    const nb = pres.filter(s => s.has(id)).length; let manq = 0;
    for (let i = pres.length - 1; i >= 0 && !pres[i].has(id); i--) manq++;
    if (manq > 0 && nb >= 3 && nb / (js.length - manq) >= 0.6) out.push({ id, manq, nb });
  }
  return out.sort((a, b) => b.nb - a.nb);
}

// points par match cette saison comparés à la saison précédente
function evolution(T) {
  return profil(T).S.joueurs.filter(p => p.poste !== 'GB' && p.mj >= 4).map(p => {
    const av = Object.values(D.car[p.id] || {}).filter(x => x.s === CUR - 1), mj = av.reduce((s, x) => s + x.mj, 0);
    return mj >= 8 ? { id: p.id, now: (p.b + p.a) / p.mj, avant: av.reduce((s, x) => s + x.b + x.a, 0) / mj } : null;
  }).filter(Boolean).map(x => ({ ...x, d: x.now - x.avant })).sort((a, b) => b.d - a.d);
}

/* ---------- lignes probables, déduites des joueurs présents ensemble sur les buts à égalité numérique ---------- */
const _L = {};
function lignes(T, der = 0) {   // der = 1 : seulement le dernier match
  const cle = T + '|' + der;
  if (_L[cle]) return _L[cle];
  const S = profil(T).S, ids = new Set((der ? S.js.slice(-der) : S.js).map(j => j.m.id)), grp = {}, minTrio = der ? 3 : 5, minPaire = 2;
  S.joueurs.forEach(p => grp[p.id] = p.poste === 'GB' ? 'G' : p.poste === 'D' ? 'D' : 'A');
  const buts = D.buts.filter(b => ids.has(b.match_id) && b.situation === 'EG').map(b => ({ pour: b.equipe_id === T, j: (D.glace[b.id] || []).filter(x => x.equipe_id === T).map(x => x.joueur_id) }));
  const co = {}, k = (a, b) => a < b ? a + '|' + b : b + '|' + a, n = (a, b) => co[k(a, b)] || 0;
  for (const b of buts) for (const g of ['A', 'D']) { const l = b.j.filter(x => grp[x] === g); for (let i = 0; i < l.length; i++) for (let m = i + 1; m < l.length; m++) co[k(l[i], l[m])] = n(l[i], l[m]) + 1; }
  const bilanL = (membres, min) => { const l = buts.filter(b => membres.filter(x => b.j.includes(x)).length >= min); return { p: l.filter(b => b.pour).length, c: l.filter(b => !b.pour).length }; };
  // attaque : trios dont les trois joueurs se retrouvent le plus souvent ensemble
  let libres = S.joueurs.filter(p => grp[p.id] === 'A').map(p => p.id); const trios = [];
  while (libres.length >= 3) {
    let best = null;
    for (let a = 0; a < libres.length; a++) for (let b = a + 1; b < libres.length; b++) for (let c = b + 1; c < libres.length; c++) {
      const x = libres[a], y = libres[b], z = libres[c], sc = n(x, y) + n(x, z) + n(y, z), mn = Math.min(n(x, y), n(x, z), n(y, z));
      if (mn >= 1 && (!best || sc > best.sc)) best = { j: [x, y, z], sc };
    }
    if (!best || best.sc < minTrio) break;
    trios.push({ ...best, ...bilanL(best.j, 2) }); libres = libres.filter(x => !best.j.includes(x));
  }
  // défense : paires
  let dl = S.joueurs.filter(p => grp[p.id] === 'D').map(p => p.id); const paires = [];
  while (dl.length >= 2) {
    let best = null;
    for (let a = 0; a < dl.length; a++) for (let b = a + 1; b < dl.length; b++) { const sc = n(dl[a], dl[b]); if (!best || sc > best.sc) best = { j: [dl[a], dl[b]], sc }; }
    if (!best || best.sc < minPaire) break;
    paires.push({ ...best, ...bilanL(best.j, 2) }); dl = dl.filter(x => !best.j.includes(x));
  }
  return _L[cle] = { trios, paires, nb: buts.length, couv: S.couv, couvOk: S.couvOk };
}
const ligneDe = (T, id) => lignes(T, 1).trios.find(l => l.j.includes(id)) || lignes(T).trios.find(l => l.j.includes(id));
const memeTrio = (a, b) => a.j.every(x => b.j.includes(x));
// joueurs apparus en cours de saison (absents des trois premiers matchs au moins)
function arrivees(T) {
  const js = matchsDe(T); if (js.length < 4) return [];
  const pres = js.map(j => new Set((D.compM[j.m.id] || []).filter(c => c.equipe_id === T).map(c => c.joueur_id))), S = profil(T).S;
  return S.joueurs.filter(p => p.poste !== 'GB').map(p => ({ p, premier: pres.findIndex(s => s.has(p.id)) })).filter(x => x.premier >= 3)
    .map(x => ({ ...x.p, depuis: js[x.premier].m, passe: Object.values(D.car[x.p.id] || {}).filter(c => c.s < CUR).sort((a, b) => b.s - a.s) }))
    .sort((a, b) => (b.b + b.a) - (a.b + a.a));
}
function phraseArrivee(T, a) {
  const l = lignes(T, 1).trios.find(x => x.j.includes(a.id)), mates = l ? l.j.filter(x => x !== a.id).map(nomJ) : [];
  const passe = a.passe.length ? ` En D2 auparavant : ${a.passe.map(c => `${sais(c.s)} ${D.eq[c.eq] ? D.eq[c.eq].nom_court : '?'}, ${c.mj} matchs, ${c.b} buts et ${c.a} passes`).join(' ; ')}.` : ' Aucun match de D2 sur les deux saisons précédentes.';
  return `${nomJ(a.id)}, aligné depuis le ${dateFr(a.depuis.date)} : ${a.mj} match(s), ${a.b} but(s), ${a.a} passe(s).${mates.length ? ` Au dernier match avec ${liste(mates)}.` : ''}${passe}`;
}
function carteLignes(T) {
  const L = lignes(T); if (!L.trios.length && !L.paires.length) return '';
  const rows = a => a.map((l, i) => [i + 1, l.j.map(x => esc(nomJ(x))).join(' – '), l.p, L.couvOk ? l.c : '–', L.couvOk ? `<b>${(l.p - l.c > 0 ? '+' : '') + (l.p - l.c)}</b>` : '–']);
  return `<div class="card"><h2>Lignes d'attaque probables</h2>${L.trios.length ? table(['', 'Trio', 'Buts pour', 'Buts contre', 'Diff.'], rows(L.trios), ['n', '', 'n', 'n', 'n']) : '<p class="muted">Pas assez de buts pour les déduire.</p>'}
    <h2 style="margin-top:16px">Paires de défense probables</h2>${L.paires.length ? table(['', 'Paire', 'Buts pour', 'Buts contre', 'Diff.'], rows(L.paires), ['n', '', 'n', 'n', 'n']) : '<p class="muted">Pas assez de buts pour les déduire.</p>'}
    <p class="note">Déduites des joueurs présents ensemble sur les ${L.nb} buts à égalité numérique (marqués et encaissés). Buts comptés quand au moins deux joueurs de la ligne sont sur la glace. Une ligne modifiée récemment ou peu impliquée dans les buts peut échapper au calcul. ${L.couvOk ? `Buts contre : joueurs notés sur ${L.couv.ok} des ${L.couv.tot} buts encaissés.` : `Buts contre non affichés : les joueurs présents ne sont notés que sur ${L.couv.ok} des ${L.couv.tot} buts encaissés par cette équipe.`}</p>${(() => {
      const R = lignes(T, 1), dm = profil(T).S.js.slice(-1)[0];
      if (!dm || !R.trios.length || R.trios.every(r => L.trios.some(l => memeTrio(r, l)))) return '';
      return `<h2 style="margin-top:16px">Trios vus au dernier match (${dateFr(dm.m.date)}, ${esc(D.eq[dm.adv].nom_court)})</h2>
        ${table(['', 'Trio', 'Buts pour', 'Buts contre', ''], R.trios.map((l, i) => [i + 1, l.j.map(x => esc(nomJ(x))).join(' – '), l.p, L.couvOk ? l.c : '–', L.trios.some(x => memeTrio(l, x)) ? '' : '<span class="tag">nouvelle composition</span>']), ['n', '', 'n', 'n', ''])}
        <p class="note">Sur un seul match, seules les lignes impliquées dans un but apparaissent. Elles priment sur le tableau de la saison quand elles diffèrent.</p>`;
    })()}</div>`;
}

/* ---------- notre équipe : tendances et axes de progression ---------- */
function tendances(T) {
  const P = profil(T), S = P.S, n = P.n, L = [];
  if (!n) return L;
  const d5 = S.js.slice(-3), av = S.js.slice(0, -3), m = (a, k) => a.reduce((s, j) => s + j[k], 0) / a.length;
  if (av.length >= 3) L.push(`Forme : sur les 3 derniers matchs, ${f1(m(d5, 'bp'))} buts marqués et ${f1(m(d5, 'bc'))} encaissés par match, contre ${f1(m(av, 'bp'))} et ${f1(m(av, 'bc'))} sur les ${av.length} précédents.`);
  L.push(`Par tiers : ${S.tiers.slice(0, 3).map((t, i) => `${['1er', '2e', '3e'][i]} ${t.p}-${t.c}`).join(', ')}. ${S.tranches[10].c + S.tranches[11].c} but(s) encaissé(s) et ${S.tranches[10].p + S.tranches[11].p} marqué(s) dans les 10 dernières minutes.`);
  const top = S.joueurs.slice(0, 3), ids = new Set(top.map(p => p.id)), impl = S.pour.filter(b => [b.buteur_id, b.assist1_id, b.assist2_id].some(x => ids.has(x))).length;
  const buteurs = new Set(S.pour.map(b => b.buteur_id)).size, bd = S.joueurs.filter(p => p.poste === 'D').reduce((s, p) => s + p.b, 0);
  L.push(`Production : ${impl} des ${S.pour.length} buts (${pct(impl, S.pour.length)}) impliquent ${liste(top.map(p => nomJ(p.id)))}. ${buteurs} buteurs différents, dont ${bd} but(s) de défenseurs.`);
  const pp = S.joueurs.filter(p => p.pp > 0).sort((a, b) => b.pp - a.pp).slice(0, 4);
  if (pp.length) L.push(`Supériorité : les points viennent de ${liste(pp.map(p => `${nomJ(p.id)} (${p.pp})`))}.`);
  const champ = S.joueurs.filter(p => p.poste !== 'GB' && p.mj >= Math.max(3, n / 2)), encEq = S.contre.filter(b => b.situation === 'EG').length;
  const pire = [...champ].sort((a, b) => b.enc - a.enc)[0], best = [...champ].sort((a, b) => b.pm - a.pm)[0];
  if (pire && best && encEq && S.couvOk) L.push(`À égalité numérique : ${nomJ(best.id)} a le meilleur +/− (${sg(best.pm).replace(',0', '')}). ${nomJ(pire.id)} est sur la glace pour ${pire.enc} des ${encEq} buts encaissés (+/− ${sg(pire.pm).replace(',0', '')}).`);
  const pims = [...S.joueurs].sort((a, b) => b.pim - a.pim).slice(0, 3).filter(p => p.pim > 0), totPim = S.pens.reduce((s, p) => s + (p.minutes || 0), 0);
  if (pims.length) L.push(`Discipline : ${liste(pims.map(p => `${nomJ(p.id)} (${p.pim} min)`))} cumulent ${pims.reduce((s, p) => s + p.pim, 0)} des ${totPim} minutes de pénalité. Tiers le plus pénalisé : le ${['1er', '2e', '3e'][[1, 2, 3].map(p => S.pens.filter(x => x.periode === p).length).reduce((bi, v, i, a) => v > a[bi] ? i : bi, 0)]}.`);
  if (S.detail.length) {
    const tp = S.detail.reduce((s, d) => s + d.tp, 0), tc = S.detail.reduce((s, d) => s + d.tc, 0), eg = S.detail.reduce((s, d) => s + d.eg, 0), et = S.detail.reduce((s, d) => s + d.et, 0), k = S.detail.length;
    const bDet = S.detail.reduce((s, d) => s + d.j.bp, 0), sec = S.joueurs.filter(p => p.tirs >= 10 && p.bDet === 0).map(p => `${nomJ(p.id)} (${p.tirs} tirs)`);
    L.push(`Tirs (${k} matchs saisis) : ${f1(tp / k)} pour et ${f1(tc / k)} contre par match, ${pct(bDet, tp)} de réussite au tir.${sec.length ? ` Sans but malgré le volume : ${liste(sec)}.` : ''}`);
    const e = S.enga.filter(x => x.n >= 15);
    if (et) L.push(`Engagements : ${pct(eg, et)} gagnés (${eg}/${et}).${e.length ? ' ' + e.map(x => `${nomJ(x.id)} ${pct(x.g, x.n)} sur ${x.n}`).join(', ') + '.' : ''}`);
  }
  const ev = evolution(T), haut = ev.filter(x => x.d >= 0.3).slice(0, 3), bas = ev.filter(x => x.d <= -0.3).slice(-3).reverse(), fe = x => `${nomJ(x.id)} (${f1(x.now)} pt/match contre ${f1(x.avant)})`;
  if (haut.length || bas.length) L.push(`Par rapport à ${sais(CUR - 1)} : ${haut.length ? 'en hausse ' + liste(haut.map(fe)) : ''}${haut.length && bas.length ? ' ; ' : ''}${bas.length ? 'en baisse ' + liste(bas.map(fe)) : ''}.`);
  const g = S.gardiens.filter(x => x.sec >= 1800);
  if (g[0]) L.push(`Gardiens : ${g.map(x => `${nomJ(x.id)} ${x.tit} titularisation(s), ${f1(x.enc * 3600 / (x.sec || 1))} but(s) encaissé(s) par match${x.tirs ? `, ${f1(100 * x.tirs / (x.tirs + x.encDet))} % d'arrêts sur ${x.tirs + x.encDet} tirs` : ''}`).join(' ; ')}.`);
  arrivees(T).filter(a => a.b + a.a > 0 || a.passe.length).slice(0, 3).forEach(a => L.push('Arrivée en cours de saison : ' + phraseArrivee(T, a)));
  const ab = absences(T);
  if (ab.length) L.push(`Absents du dernier match parmi les joueurs habituels : ${liste(ab.map(a => `${nomJ(a.id)} (${a.manq} match(s) manqué(s) de suite)`))}.`);
  return L;
}

function axes(T) {
  const P = profil(T), S = P.S, n = P.n, C = Object.fromEntries(comparer(T).map(c => [c.k, c])), A = [];
  if (n < 3) return A;
  const rg = c => `${c.rang}e sur ${c.sur} en D2, moyenne ${c.fmt(c.moy)}`;
  if (P.ptsExt != null && P.ptsDom != null && P.ext.length >= 2 && P.ptsDom - P.ptsExt >= 1) {
    const b = a => f1(a.reduce((s, j) => s + j.bp, 0) / a.length);
    A.push(['Jouer à l\'extérieur comme à domicile', `${f1(P.ptsExt)} point par match en déplacement contre ${f1(P.ptsDom)} à domicile ; ${b(P.ext)} buts marqués par match à l'extérieur contre ${b(P.dom)} à domicile. Repère : au moins 1,5 point par déplacement.`]);
  }
  if (P.t3 < P.t1 - 0.4 || (C.t3 && C.t3.niveau === 'faible')) A.push(['Finir les matchs', `3e tiers : ${S.tiers[2].p} buts marqués, ${S.tiers[2].c} encaissés, contre ${S.tiers[0].p}-${S.tiers[0].c} au 1er tiers (${rg(C.t3)}). Pistes à vérifier à la vidéo : longueur des présences en fin de match, rotation des lignes, gestion de l'avance. Repère : une différence de buts positive au 3e tiers.`]);
  if (C.pp && C.pp.v < C.pp.moy) A.push(['Supériorité numérique', `${pc(P.pp)} de réussite (${S.ppB} buts sur environ ${S.occ.sup} occasions), ${rg(C.pp)}. Repère : repasser au-dessus de la moyenne.`]);
  if (C.pk && C.pk.v < C.pk.moy) A.push(['Infériorité numérique', `${pc(P.pk)} de réussite (${S.pkB} buts encaissés sur environ ${S.occ.inf} pénalités), ${rg(C.pk)}.`]);
  if (C.penm && C.penm.v > C.penm.moy) {
    const pims = [...S.joueurs].sort((a, b) => b.pim - a.pim).slice(0, 3).filter(p => p.pim > 0);
    A.push(['Discipline', `${f1(P.penm)} pénalités mineures par match (${rg(C.penm)}). ${liste(pims.map(p => `${nomJ(p.id)} ${p.pim} min`))}. Repère : passer sous la moyenne.`]);
  }
  if (S.sc.mene.length >= 2 && !S.sc.mene.some(j => j.v)) A.push(['Revenir au score', `Menée après deux tiers : ${S.sc.mene.length} matchs, aucune victoire. Quand l'adversaire ouvre le score : ${bilan(S.sc.second)}.`]);
  if (S.repC >= 3) A.push(['La présence qui suit un but marqué', `${S.repC} buts encaissés dans les 2 minutes après avoir marqué (et ${S.repP} marqués dans les 2 minutes après en avoir encaissé un).`]);
  const top = S.joueurs.slice(0, 3), ids = new Set(top.map(p => p.id)), impl = S.pour.filter(b => [b.buteur_id, b.assist1_id, b.assist2_id].some(x => ids.has(x))).length;
  if (S.pour.length >= 10 && impl / S.pour.length >= 0.55) A.push(['Élargir la production offensive', `${pct(impl, S.pour.length)} des buts passent par ${liste(top.map(p => nomJ(p.id)))} : l'équipe devient lisible si ce trio est neutralisé. Repère : davantage de buts des autres lignes.`]);
  const champ = S.joueurs.filter(p => p.poste !== 'GB' && p.mj >= Math.max(3, n / 2)), encEq = S.contre.filter(b => b.situation === 'EG').length, pire = [...champ].sort((a, b) => b.enc - a.enc)[0];
  const diffEq = S.pour.filter(b => b.situation === 'EG').length - encEq;
  if (S.couvOk && pire && encEq >= 8 && pire.enc / encEq >= 0.35 && pire.pm <= 0 && diffEq > 0) A.push(['Repli défensif de la ligne de ' + nomJ(pire.id), `${(() => { const l = ligneDe(T, pire.id); return l ? `Ligne probable : ${l.j.map(nomJ).join(' – ')} (${l.p} buts pour, ${l.c} contre). ` : ''; })()}Sur la glace pour ${pire.enc} des ${encEq} buts encaissés à égalité numérique, +/− de ${sg(pire.pm).replace(',0', '')} alors que l'équipe est à ${sg(diffEq).replace(',0', '')}. À recouper avec la vidéo : les joueurs présents ne sont notés que sur ${S.couv.ok} des ${S.couv.tot} buts encaissés.`]);
  if (S.detail.length) {
    const faibles = S.detail.filter(d => d.et >= 20 && d.eg / d.et < 0.45), e = S.enga.filter(x => x.n >= 15).sort((a, b) => b.g / b.n - a.g / a.n);
    if (faibles.length && e.length >= 2) A.push(['Engagements', `${faibles.length} match(s) sur ${S.detail.length} sous 45 % (${faibles.map(d => `${pct(d.eg, d.et)} contre ${D.eq[d.j.adv].nom_court}`).join(', ')}). ${nomJ(e[0].id)} gagne ${pct(e[0].g, e[0].n)} de ses mises en jeu, ${nomJ(e[e.length - 1].id)} ${pct(e[e.length - 1].g, e[e.length - 1].n)} : répartir les engagements clés en conséquence.`]);
    const sec = S.joueurs.filter(p => p.tirs >= 10 && p.bDet === 0);
    if (sec.length) A.push(['Finition', `${liste(sec.map(p => `${nomJ(p.id)} : ${p.tirs} tirs cadrés sans but`))}. À regarder : d'où partent ces tirs et s'il y a du trafic devant le gardien.`]);
  }
  return A;
}

/* ---------- préconisations face à un adversaire ---------- */
function preconisations(O) {
  const B = D.ab[ME].id, P = profil(B), Q = profil(O), S = Q.S, R = [];
  if (!Q.n || !P.n) return R;
  const nB = D.eq[B].nom_court, nO = D.eq[O].nom_court, C = Object.fromEntries(comparer(O).map(c => [c.k, c])), CB = Object.fromEntries(comparer(B).map(c => [c.k, c]));
  const proch = D.matchs.find(m => m.etat !== 'T' && ((m.dom_id === B && m.ext_id === O) || (m.dom_id === O && m.ext_id === B))), chezNous = proch ? proch.dom_id === B : null;
  // lieu
  if (proch) {
    const a = chezNous ? Q.ext : Q.dom, b = chezNous ? P.dom : P.ext;
    if (a.length && b.length) R.push(['Contexte', `Match ${chezNous ? 'à Brest' : 'chez eux'}. ${nO} ${chezNous ? 'à l\'extérieur' : 'à domicile'} : ${bilan(a)}, ${f1(a.reduce((s, j) => s + j.bp, 0) / a.length)} buts marqués et ${f1(a.reduce((s, j) => s + j.bc, 0) / a.length)} encaissés par match. ${nB} ${chezNous ? 'à domicile' : 'à l\'extérieur'} : ${bilan(b)}, ${f1(b.reduce((s, j) => s + j.bp, 0) / b.length)} marqués et ${f1(b.reduce((s, j) => s + j.bc, 0) / b.length)} encaissés.`]);
  }
  // entame
  const t = S.tiers, tb = P.S.tiers;
  if (t[0].p - t[0].c >= 3 && tb[0].p - tb[0].c >= 3) R.push(['Le 1er tiers sera décisif', `Les deux équipes font la différence d'entrée : ${nO} ${t[0].p}-${t[0].c} au 1er tiers, ${nB} ${tb[0].p}-${tb[0].c}. Celle qui impose son rythme prive l'autre de son point fort.`]);
  else if (t[0].p - t[0].c >= 3) R.push(['Ne pas subir l\'entame', `${nO} fait la différence d'entrée : ${t[0].p} buts marqués et ${t[0].c} encaissés au 1er tiers. ${nB} : ${tb[0].p}-${tb[0].c} sur le même tiers.`]);
  else if (tb[0].p - tb[0].c >= 3) R.push(['Imposer le rythme dès le 1er tiers', `C'est le point fort de ${nB} (${tb[0].p}-${tb[0].c} au 1er tiers) et ${nO} n'y est pas dominant (${t[0].p}-${t[0].c}).`]);
  // fin de match
  const finC = S.tranches[10].c + S.tranches[11].c, finP = S.tranches[10].p + S.tranches[11].p;
  if (t[2].p - t[2].c >= 3 && tb[2].p - tb[2].c <= 0) R.push(['Garder de la fraîcheur pour le 3e tiers', `${nO} finit fort (${t[2].p}-${t[2].c} au 3e tiers, dont ${finP} but(s) dans les 10 dernières minutes) alors que ${nB} est à ${tb[2].p}-${tb[2].c}. Raccourcir les présences et faire tourner le banc plus tôt.`]);
  else if (S.contre.length && finC / S.contre.length >= 0.25) R.push(['Insister en fin de match', `${finC} des ${S.contre.length} buts encaissés par ${nO} tombent dans les 10 dernières minutes.`]);
  // discipline
  if (Q.pp != null) {
    const pp = S.joueurs.filter(p => p.pp > 0).sort((a, b) => b.pp - a.pp).slice(0, 3), qui = pp.length ? ` Points en supériorité : ${liste(pp.map(p => `${nomJ(p.id)} (${p.pp})`))}.` : '';
    if (C.pp.v >= C.pp.moy) R.push(['Rester hors du banc des pénalités', `Leur supériorité tourne à ${pc(Q.pp)} (${S.ppB} buts, ${C.pp.rang}e sur ${C.pp.sur} en D2). ${nB} prend ${f1(P.penm)} pénalités mineures par match.${qui}`]);
    else R.push(['Leur supériorité est peu dangereuse', `${pc(Q.pp)} de réussite (${S.ppB} buts sur environ ${S.occ.sup} occasions, ${C.pp.rang}e sur ${C.pp.sur}).${qui}`]);
  }
  if (Q.pk != null) {
    const pims = [...S.joueurs].sort((a, b) => b.pen - a.pen).slice(0, 3).filter(p => p.pen >= 2), ppB = P.S.joueurs.filter(p => p.pp > 0).sort((a, b) => b.pp - a.pp).slice(0, 3);
    if (C.pk.v < C.pk.moy || C.penm.v > C.penm.moy) R.push(['Provoquer des fautes', `${nO} prend ${f1(Q.penm)} pénalités mineures par match (moyenne D2 ${f1(C.penm.moy)}) et en tue ${pc(Q.pk)} (${C.pk.rang}e sur ${C.pk.sur}).${pims.length ? ` Les plus pénalisés : ${liste(pims.map(p => `${nomJ(p.id)} (${p.pen} pénalités, ${p.pim} min)`))}.` : ''}${ppB.length ? ` En supériorité, ${nB} s'appuie sur ${liste(ppB.map(p => nomJ(p.id)))}.` : ''}`]);
    else R.push(['Ne pas compter sur la supériorité', `${nO} est discipliné (${f1(Q.penm)} pénalités mineures par match) et tue ${pc(Q.pk)} des pénalités (${C.pk.rang}e sur ${C.pk.sur}) : la différence devra se faire à égalité numérique.`]);
  }
  if (S.shP >= 2) R.push(['Prudence en supériorité', `${nO} a marqué ${S.shP} buts en infériorité : sécuriser la ligne bleue et le dernier défenseur.`]);
  // joueurs à surveiller
  const top = S.joueurs.slice(0, 3).filter(p => p.b + p.a > 0), ids = new Set(top.map(p => p.id)), impl = S.pour.filter(b => [b.buteur_id, b.assist1_id, b.assist2_id].some(x => ids.has(x))).length;
  if (top.length) {
    const defs = P.S.joueurs.filter(p => p.poste === 'D' && p.mj >= P.n / 2).sort((a, b) => b.pm - a.pm).slice(0, 2), duo = S.duos[0];
    const ligne = ligneDe(O, top[0].id), paire = [...lignes(B).paires].sort((a, b) => (b.p - b.c) - (a.p - a.c))[0];
    R.push(['Joueurs à neutraliser', `${liste(top.map(p => `${nomJ(p.id)} (${p.b} buts, ${p.a} passes)`))} sont impliqués dans ${impl} des ${S.pour.length} buts (${pct(impl, S.pour.length)}).${ligne ? ` Ligne probable : ${ligne.j.map(nomJ).join(' – ')} (${ligne.p} buts marqués à égalité numérique${lignes(O).couvOk ? `, ${ligne.c} encaissés` : ''}).` : duo ? ` Duo principal : ${nomJ(duo.a)} + ${nomJ(duo.b)} (${duo.n} buts ensemble).` : ''}${paire ? ` Meilleure paire de défense de ${nB} : ${paire.j.map(nomJ).join(' – ')} (${paire.p} buts pour, ${paire.c} contre)` : ''}${paire ? (chezNous === true ? ' ; à domicile, le dernier changement permet de l\'opposer à cette ligne.' : chezNous === false ? ' ; à l\'extérieur, sans le dernier changement, privilégier des présences courtes quand cette ligne est sur la glace.' : '.') : defs.length ? ` Défenseurs de ${nB} les plus solides à égalité numérique : ${liste(defs.map(p => `${nomJ(p.id)} (${sg(p.pm).replace(',0', '')})`))}${chezNous === true ? ' ; à domicile, le dernier changement permet de les opposer à cette ligne.' : chezNous === false ? ' ; à l\'extérieur, sans le dernier changement, privilégier des présences courtes quand cette ligne est sur la glace.' : '.'}` : ''}`]);
  }
  // gardien
  const g = S.gardiens.filter(x => x.sec >= 1800);
  if (g[0]) {
    const tirsB = P.S.detail.length ? ` ${nB} tire ${f1(P.S.detail.reduce((s, d) => s + d.tp, 0) / P.S.detail.length)} fois par match à domicile.` : '';
    R.push(['Gardien', `${g.slice(0, 2).map(x => `${nomJ(x.id)} : ${x.tit} titularisation(s), ${f1(x.enc * 3600 / (x.sec || 1))} but(s) encaissé(s) par match${x.tirs ? `, ${f1(100 * x.tirs / (x.tirs + x.encDet))} % d'arrêts sur ${x.tirs + x.encDet} tirs` : ''}`).join(' ; ')}. Moyenne D2 : ${f1(C.bcm.moy)} buts encaissés par match.${Q.bcm > C.bcm.moy ? ' Tirer tôt et souvent.' : ''}${tirsB}`]);
  }
  // scénario
  if (S.sc.second.length >= 2) R.push([S.sc.second.filter(j => j.v).length / S.sc.second.length < 0.5 ? 'Marquer en premier' : 'Ouvrir le score ne suffit pas', `Quand ${nO} encaisse le premier but : ${bilan(S.sc.second)}. Quand il ouvre le score : ${bilan(S.sc.premier)}.${S.sc.mene.length ? ` Mené après deux tiers : ${bilan(S.sc.mene)}.` : ''}`]);
  if (S.repC >= 2 || S.repP >= 2) R.push(['Juste après un but', `${S.repC >= 2 ? `${nO} encaisse ${S.repC} fois dans les 2 minutes après avoir marqué : envoyer la meilleure ligne sur la présence suivante. ` : ''}${S.repP >= 2 ? `${nO} marque ${S.repP} fois dans les 2 minutes après avoir encaissé : vigilance sur la présence qui suit un but de ${nB}.` : ''}`]);
  // banc
  const ch = chezNous === true ? Q.champExt : chezNous === false ? Q.champDom : null;
  if (ch != null && ch <= 15.5) R.push(['Profiter d\'un banc court', `${nO} aligne en moyenne ${f1(ch)} joueurs de champ ${chezNous ? 'en déplacement' : 'à domicile'} : jeu en vitesse et échec-avant soutenu pour les user avant le 3e tiers.`]);
  const arr = arrivees(O).filter(a => a.b + a.a > 0 || a.passe.length).slice(0, 3);
  if (arr.length) R.push(['Arrivées en cours de saison', arr.map(a => phraseArrivee(O, a)).join(' ')]);
  const ab = absences(O);
  if (ab.length) R.push(['Absents à leur dernier match', `${liste(ab.map(a => `${nomJ(a.id)} (${a.manq} match(s) manqué(s), ${(() => { const p = S.joueurs.find(x => x.id === a.id); return p ? `${p.b + p.a} pts en ${p.mj} matchs` : ''; })()})`))}. Cause non publiée (blessure, suspension ou choix) : à vérifier avant le match.`]);
  if (P.S.enga.length) { const e = P.S.enga.filter(x => x.n >= 15).sort((a, b) => b.g / b.n - a.g / a.n)[0]; if (e) R.push(['Engagements clés', `Mises en jeu défensives et fins de période : ${nomJ(e.id)} est le plus fiable de ${nB} (${pct(e.g, e.n)} sur ${e.n}).`]); }
  const H = historique(O).cont;
  if (H.now && H.buts) R.push(['Ce que vaut l\'historique', `${H.restes} des ${H.now} joueurs étaient déjà au club en ${sais(H.prec)} et pesaient ${pct(H.butsRestes, H.buts)} des buts : ${H.butsRestes / H.buts < 0.4 ? 'l\'équipe a beaucoup changé, se fier d\'abord aux matchs de cette saison.' : 'les tendances de la saison passée restent parlantes.'}`]);
  return R;
}

/* ---------- rendu ---------- */
const blocs = a => `<dl class="reco">${a.map(([t, x]) => `<dt>${esc(t)}</dt><dd>${esc(x)}</dd>`).join('')}</dl>`;
function carteAnalyse(T) {
  const c = comparer(T), t = tendances(T), a = axes(T), nous = T === D.ab[ME].id;
  if (!profil(T).n) return '';
  return `<div class="card"><h2>${nous ? 'Analyse : tendances de notre équipe' : 'Profil par rapport au reste de la D2'}</h2>
    ${table(['Indicateur', D.eq[T].nom_court, 'Moyenne D2', 'Rang', ''], c.map(x => [x.lab, `<b>${x.fmt(x.v)}</b>`, x.fmt(x.moy), `${x.rang}/${x.sur}`, x.niveau === 'fort' ? '<span class="tag fort">▲ point fort</span>' : x.niveau === 'faible' ? '<span class="tag faible">▼ point faible</span>' : '']), ['', 'n', 'n', 'n', ''])}
    ${nous ? `<ul class="keys" style="margin-top:12px">${t.map(l => `<li>${esc(l)}</li>`).join('')}</ul>` : ''}
    <p class="note">Rang parmi les ${c[0] ? c[0].sur : 20} équipes de D2 cette saison (les deux poules). Petit échantillon : ${profil(T).n} matchs.</p></div>
    ${nous ? `<div class="card"><h2>Axes de progression</h2>${a.length ? blocs(a) : '<p class="muted">Aucun point faible net dans les chiffres pour l\'instant.</p>'}
    <p class="note">Déduits des chiffres ci-dessus. Ils indiquent où regarder, pas la cause : à confirmer à la vidéo et à l'entraînement.</p></div>` : ''}`;
}
function cartePreco(O, max) {
  const r = preconisations(O); if (!r.length) return '';
  return `<div class="card"><h2>Tendances et préconisations face à ${esc(D.eq[O].nom_court)}</h2>${blocs(max ? r.slice(0, max) : r)}
    ${max ? '' : '<p class="note">Générées à partir des feuilles de match des deux équipes. Elles ne remplacent pas la vidéo : système de jeu, sorties de zone et placement ne figurent pas dans ces données.</p>'}</div>`;
}
