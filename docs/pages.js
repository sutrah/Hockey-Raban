/* Pages du site : accueil, fiche de match, débrief, fiche équipe, comparateur de lignes, analyses vidéo. */
const moi = () => D.ab[ME].id;
const prochain = () => D.matchs.find(m => m.etat !== 'T' && (m.dom_id === moi() || m.ext_id === moi()));
const advDe = m => m.dom_id === moi() ? m.ext_id : m.dom_id;
const mmPP = s => Math.round(s / 60) + ' min';
const lienFiche = O => `<a class="btn" href="#/fiche/${D.eq[O].abreviation}">Ouvrir la fiche de match →</a>`;

/* ---------- forme récente des joueurs ---------- */
function formeJ(T) {
  const ms = D.finis.filter(m => m.saison === CUR && m.phase === 'P' && (m.dom_id === T || m.ext_id === T)), k = ms.length >= 10 ? 5 : 3, j = {};
  if (ms.length < k + 2) return { k, j };
  const rec = new Set(ms.slice(-k).map(m => m.id)), all = new Set(ms.map(m => m.id)), g = id => j[id] = j[id] || { pr: 0, mr: 0, pa: 0, ma: 0 };
  D.compositions.forEach(c => { if (c.equipe_id === T && all.has(c.match_id)) rec.has(c.match_id) ? g(c.joueur_id).mr++ : g(c.joueur_id).ma++; });
  D.buts.forEach(b => { if (b.equipe_id === T && all.has(b.match_id)) [b.buteur_id, b.assist1_id, b.assist2_id].forEach(x => x && (rec.has(b.match_id) ? g(x).pr++ : g(x).pa++)); });
  for (const id in j) { const x = j[id], d = x.mr >= 2 && x.ma >= 2 ? x.pr / x.mr - x.pa / x.ma : 0; x.tend = d >= 0.4 ? 'up' : d <= -0.4 ? 'down' : ''; }
  return { k, j };
}
const fleche = (F, id) => { const x = F.j[id]; if (!x || !x.tend) return ''; const t = `${x.pr} pt(s) sur les ${F.k} derniers matchs, ${x.pa} sur les précédents`;
  return x.tend === 'up' ? ` <span class="tag fort" data-tip="${t}">▲ en hausse</span>` : ` <span class="tag faible" data-tip="${t}">▼ en baisse</span>`; };

/* ---------- points clés face à un adversaire ---------- */
const TYPE = { 'Ne pas subir l\'entame': 'v', 'Imposer le rythme dès le 1er tiers': 'e', 'Le 1er tiers sera décisif': 'v', 'Garder de la fraîcheur pour le 3e tiers': 'v', 'Insister en fin de match': 'e',
  'Rester hors du banc des pénalités': 'v', 'Provoquer des fautes': 'e', 'Ne pas compter sur la supériorité': 'v', 'Prudence en supériorité': 'v', 'Joueurs à neutraliser': 'v', 'Marquer en premier': 'e',
  'Ouvrir le score ne suffit pas': 'v', 'Juste après un but': 'e', 'Profiter d\'un banc court': 'e', 'Arrivées en cours de saison': 'v', 'Absents à leur dernier match': 'e' };
function pointsCles(O, M) {
  const B = moi(), r = preconisations(O, M), nB = D.eq[B].nom_court, nO = D.eq[O].nom_court;
  const e = r.filter(x => TYPE[x[0]] === 'e'), v = r.filter(x => TYPE[x[0]] === 'v').sort((a, b) => (b[0] === 'Joueurs à neutraliser') - (a[0] === 'Joueurs à neutraliser'));
  const co = Object.fromEntries(comparer(O).map(c => [c.k, c]));
  comparer(B).filter(c => !/^pts/.test(c.k) && co[c.k]).sort((x, y) => Math.abs(co[y.k].rang - y.rang) - Math.abs(co[x.k].rang - x.rang)).forEach(c => {
    const o = co[c.k], d = o.rang - c.rang, txt = `${nB} ${c.fmt(c.v)} (${c.rang}e en D2), ${nO} ${o.fmt(o.v)} (${o.rang}e).`;
    if (d >= 6) e.push(['Avantage Brest : ' + c.lab.toLowerCase(), txt]); else if (d <= -6) v.push(['Avantage adverse : ' + c.lab.toLowerCase(), txt]);
  });
  const E = e.slice(0, 3), V = v.slice(0, 3);
  return { e: E, v: V, reste: r.filter(x => !E.includes(x) && !V.includes(x)) };
}
const blocPoints = k => `<div class="grid2"><div><h3 class="e">▲ À exploiter</h3>${k.e.length ? cartes(k.e, 'e', true) : '<p class="muted">Aucun point net dans les chiffres.</p>'}</div>
  <div><h3 class="v">⚠ Points de vigilance</h3>${k.v.length ? cartes(k.v, 'v', true) : '<p class="muted">Aucun point net dans les chiffres.</p>'}</div></div>`;

/* ---------- lignes ---------- */
function blocLignes(T) {
  const L = lignes(T), R = lignes(T, 1), dm = profil(T).S.js.slice(-1)[0];
  if (!L.trios.length && !L.paires.length) return '';
  const neuf = dm && R.trios.length && !R.trios.every(r => L.trios.some(l => memeTrio(r, l)));
  return `${legende([['pour', 'Buts marqués'], ['contre', 'Buts encaissés']])}
    ${neuf ? `<h3>Trios du dernier match (${dateFr(dm.m.date)}, ${esc(D.eq[dm.adv].nom_court)})</h3>${barresLignes(R.trios.map(l => ({ ...l, neuf: !L.trios.some(x => memeTrio(l, x)) })), L.couvOk)}` : ''}
    <h3>Trios d'attaque${neuf ? ' sur la saison' : ''}</h3>${L.trios.length ? barresLignes(L.trios, L.couvOk) : '<p class="muted">Pas assez de buts pour les déduire.</p>'}
    <h3>Paires de défense</h3>${L.paires.length ? barresLignes(L.paires, L.couvOk) : '<p class="muted">Pas assez de buts pour les déduire.</p>'}
    <p class="note">Déduites des joueurs présents ensemble sur ${L.nb} buts à égalité numérique. ${L.couvOk ? `Buts encaissés : joueurs notés sur ${L.couv.ok} des ${L.couv.tot}.` : `Buts encaissés non affichés (n. n.) : joueurs notés sur ${L.couv.ok} des ${L.couv.tot} seulement.`}</p>`;
}

/* ---------- fiche équipe ---------- */
const filtreBar = () => `<div class="seg" role="group" aria-label="Période analysée">${[['saison', 'Saison'], ['5', '5 derniers'], ['dom', 'Domicile'], ['ext', 'Extérieur']].map(([k, l]) => `<button type="button" data-filtre="${k}" class="${FILTRE === k ? 'on' : ''}">${l}</button>`).join('')}</div>`;

function pageEquipe(ab) {
  const e = D.ab[ab]; if (!e) return `<p>Équipe inconnue.</p>`;
  const T = e.id, me = moi(), nous = T === me, P = profil(T), S = P.S, n = S.js.length, cl = classement(T), rang = cl.findIndex(r => r.id === T) + 1;
  const tous = sansFiltre(() => matchsDe(T)), F = formeJ(T), bp = S.pour.length, bc = S.contre.length;
  let h = `<h1>${esc(e.nom)}</h1><p class="sub">${tous.length ? `Saison ${sais(CUR)} · ${rang}<sup>e</sup> de la poule sur ${cl.length} · ${tous.length} matchs joués · forme : ${forme(tous.slice(-5).map(j => j.v ? 'V' : 'D'))}` : `Pas de match en D2 en ${sais(CUR)}`}</p>`;
  if (tous.length) h += filtreBar();
  if (n) {
    h += `<div class="tiles">${tile(S.js.reduce((s, j) => s + j.pts, 0), 'points', bilan(S.js))}${tile(`${bp} / ${bc}`, 'buts marqués / encaissés', `${(bp / n).toFixed(1)} / ${(bc / n).toFixed(1)} par match`)}
      ${tile(pct(S.ppB, S.occ.sup), 'supériorité', `${S.ppB} buts en ${S.occ.sup} séquences`)}${tile(pct(S.occ.inf - S.pkB, S.occ.inf), 'infériorité', `${S.pkB} buts encaissés en ${S.occ.inf}`)}
      ${tile((S.pens.reduce((s, p) => s + (p.minutes || 0), 0) / n).toFixed(1), 'min de pénalité / match', `${S.pens.length} pénalités`)}</div>`;
    if (nous) {
      const ax = axes(T);
      h += `<div class="card">${h2('Analyse : où se situe notre équipe', 'mesuré', n + ' matchs')}${rangs(comparer(T), e.nom_court)}
        <details class="sous"><summary>Ce que disent les chiffres, en détail</summary><ul class="keys">${tendances(T).map(l => `<li>${esc(l)}</li>`).join('')}</ul></details></div>
        <div class="card">${h2('Axes de progression', 'déduit')}${ax.length ? cartes(ax, 'a', true) : '<p class="muted">Aucun point faible net dans les chiffres pour l\'instant.</p>'}
        <p class="note">Ils indiquent où regarder, pas la cause : à confirmer à la vidéo et à l'entraînement.</p></div>`;
    } else {
      const k = pointsCles(T);
      h += `<div class="card">${h2('Face à ' + esc(e.nom_court) + ' : l\'essentiel', 'déduit')}${blocPoints(k)}<p style="margin:12px 0 0">${lienFiche(T)}</p></div>
        <div class="card">${h2('Nous contre eux', 'mesuré', n + ' matchs')}${rangs(comparer(me), D.eq[me].nom_court, comparer(T), e.nom_court)}
        <p class="note">Chaque point place l'équipe parmi les 20 de D2 : plus il est à droite, mieux elle est classée. Passez sur un point pour le détail.</p></div>
        ${k.reste.length ? pli('Toutes les préconisations', cartes(k.reste, 'i'), false, 'déduit') : ''}`;
    }
    const bl = blocLignes(T);
    if (bl) h += `<div class="card">${h2('Lignes probables', 'déduit')}${bl}</div>`;
    h += `<div class="card">${h2('Quand ils marquent et quand ils encaissent', 'mesuré', `${bp + bc} buts`)}${chartTranches(S)}</div>`;
    const champ = S.joueurs.filter(p => p.poste !== 'GB'), det = S.detail.length > 0;
    h += `<div class="card">${h2('Joueurs', 'mesuré')}${barresJoueurs(champ.slice(0, 10), p => fleche(F, p.id))}
      <details class="sous"><summary>Tableau complet</summary>${table(['N°', 'Joueur', 'Poste', 'MJ', 'B', 'A', 'Pts', 'Pts sup.', '+/−', 'Sur glace but enc.', 'Pén. min'].concat(det ? ['Tirs'] : [], ['Saisons précédentes en D2 (B+A)']),
      champ.map(p => [esc(p.num), esc(D.j[p.id]), p.poste || '', p.mj, p.b, p.a, `<b>${p.b + p.a}</b>`, p.pp, S.couvOk ? (p.pm > 0 ? '+' : '') + p.pm : '–', S.couvOk ? p.enc : '–', p.pim].concat(det ? [p.tirs] : [], [passe(p.id)])),
      ['n', '', '', 'n', 'n', 'n', 'n', 'n', 'n', 'n', 'n', det ? 'n' : '', ''])}
      <p class="note">+/− à égalité numérique. Joueurs présents notés sur ${S.couv.ok} des ${S.couv.tot} buts encaissés${S.couvOk ? '' : ' : trop peu pour un +/− fiable'}.${det ? ` Tirs cadrés sur ${S.detail.length} match(s).` : ''} ▲▼ : points par match sur les ${F.k} derniers matchs comparés aux précédents.</p></details></div>`;
    h += `<div class="grid2"><div class="card">${h2('Scénarios de match', 'mesuré')}${barresVD([['Marque en premier', S.sc.premier], ['Encaisse en premier', S.sc.second], ['Mène après 2 tiers', S.sc.menant], ['Égalité après 2 tiers', S.sc.egal], ['Menée après 2 tiers', S.sc.mene]])}
      <p class="note">Dans les 2 minutes après un but : marque ${S.repP} fois après avoir encaissé, encaisse ${S.repC} fois après avoir marqué.</p></div>
      <div class="card">${h2('Supériorité et infériorité', 'mesuré')}
      <div class="spe"><div><b>${pct(S.ppB, S.occ.sup)}</b><span>en supériorité</span><small>${S.ppB} buts · ${S.occ.sup} séquences · ${mmPP(S.occ.supSec)}</small>${S.ppDelais.length ? barresDelais(S.ppDelais) : ''}</div>
      <div><b>${pct(S.occ.inf - S.pkB, S.occ.inf)}</b><span>en infériorité</span><small>${S.pkB} buts encaissés · ${S.occ.inf} séquences · ${mmPP(S.occ.infSec)}</small>${S.pkDelais.length ? barresDelais(S.pkDelais) : ''}</div></div>
      <p class="note">Les barres montrent combien de temps après le début de la pénalité les buts tombent. Marqués en infériorité : ${S.shP}. Encaissés en supériorité : ${S.shC}.</p></div></div>`;
    h += pli('Résultats', table(['J', 'Date', '', 'Adversaire', 'Résultat', 'Par tiers', 'Pén. (min)', 'Joueurs alignés'], S.js.map(j => {
      const bm = D.butsM[j.m.id] || [], per = [1, 2, 3].map(p => `${bm.filter(b => b.periode === p && b.equipe_id === T).length}-${bm.filter(b => b.periode === p && b.equipe_id !== T).length}`).join(' / ');
      const comp = (D.compM[j.m.id] || []).filter(c => c.equipe_id === T), pm = (D.penM[j.m.id] || []).filter(p => p.equipe_id === T).reduce((s, p) => s + (p.minutes || 0), 0);
      return [`J${j.m.journee}`, dateFr(j.m.date), j.dom ? 'Dom.' : 'Ext.', lienEq(j.adv), score(j) + (nous ? ` <a href="#/debrief/${j.m.id}">débrief</a>` : ''), per, pm, `${comp.filter(c => c.poste !== 'GB').length} + ${comp.filter(c => c.poste === 'GB').length} G`];
    }), ['', '', '', '', '', '', 'n', 'n']), false, 'mesuré');
    const types = Object.entries(by(S.pens, p => p.libelle || '?')).map(([l, a]) => [l, a.length]).sort((a, b) => b[1] - a[1]).slice(0, 6);
    h += pli('Discipline et duos', `<div class="grid2"><div>${table(['Type de pénalité', 'Nombre'], types.map(t => [esc(t[0]), t[1]]), ['', 'n'])}<p class="note">Par tiers : ${[1, 2, 3].map(p => S.pens.filter(x => x.periode === p).length).join(' / ')}.</p></div>
      <div>${S.duos.length ? table(['Duo (buteur et passeur)', 'Buts ensemble'], S.duos.map(d => [`${esc(D.j[d.a])} + ${esc(D.j[d.b])}`, d.n]), ['', 'n']) : '<p class="muted">Pas encore assez de buts.</p>'}</div></div>`, false, 'mesuré');
    h += pli('Gardiens', table(['Gardien', 'Titulaire', 'Minutes', 'Buts enc.', 'Moy. / match', '% arrêts', 'Tirs reçus', 'Saisons précédentes en D2'], S.gardiens.map(g => [esc(D.j[g.id]), g.tit, Math.round(g.sec / 60), g.enc, g.sec ? (g.enc * 3600 / g.sec).toFixed(2) : '–',
      g.tirs ? (100 * g.tirs / (g.tirs + g.encDet)).toFixed(1) + ' %' : '–', g.tirs ? g.tirs + g.encDet : '–', passe(g.id, true)]), ['', 'n', 'n', 'n', 'n', 'n', 'n', '']) + `<p class="note">% d'arrêts : seulement sur les ${S.detail.length} match(s) où les tirs sont saisis.</p>`, false, 'partiel', `tirs sur ${S.detail.length}/${n} matchs`);
    if (det) h += pli('Tirs et engagements', `<div class="grid2"><div>${table(['Date', 'Adversaire', 'Tirs pour', 'Tirs contre', 'Engagements gagnés'], S.detail.map(d => [dateFr(d.j.m.date), lienEq(d.j.adv), d.tp, d.tc, `${d.eg}/${d.et} (${pct(d.eg, d.et)})`]), ['', '', 'n', 'n', 'n'])}</div>
      <div>${table(['Joueur', 'Gagnés', 'Joués', '%'], S.enga.map(x => [esc(D.j[x.id] || '?'), x.g, x.n, pct(x.g, x.n)]), ['', 'n', 'n', 'n'])}</div></div>`, false, 'partiel', `${S.detail.length}/${n} matchs`);
  } else if (tous.length) h += `<div class="card"><p class="muted">Aucun match pour ce filtre.</p></div>`;

  if (!nous) {
    const vs = D.matchs.filter(m => (m.dom_id === T && m.ext_id === me) || (m.dom_id === me && m.ext_id === T)), fj = vs.filter(m => m.etat === 'T').map(m => jeu(me, m)), chez = fj.filter(j => !j.dom);
    if (vs.length) h += pli('Face à ' + esc(D.eq[me].nom_court) + ' : confrontations', `${fj.length ? `<p class="sub">Depuis ${sais(Math.min(...fj.map(j => j.m.saison)))} : ${esc(D.eq[me].nom_court)} ${bilan(fj)}, ${fj.reduce((a, j) => a + j.bp, 0)} buts à ${fj.reduce((a, j) => a + j.bc, 0)}${chez.length ? ` · chez eux : ${bilan(chez)}` : ''}.</p>` : ''}
      ${table(['Saison', 'Match', 'Date', 'Lieu', 'Résultat'], [...vs].reverse().map(m => { const j = jeu(me, m); return [sais(m.saison), m.phase === 'P' ? `J${m.journee}` : esc(m.tour || 'Séries'), dateFr(m.date), (m.dom_id === me ? 'Dom. ' : 'Ext. ') + esc(m.patinoire || ''), m.etat === 'T' ? `${esc(D.eq[me].nom_court)} ${score(j)}` : 'à jouer']; }))}`, false, 'mesuré');
  }
  const H = sansFiltre(() => historique(T)), c = H.cont;
  if (H.lignes.some(l => l.s < CUR)) {
    let txt = '';
    if (c.now) { const meme = c.coachNow && c.coachPrec && c.coachNow === c.coachPrec;
      txt = `<ul class="keys"><li>Entraîneur : ${c.coachNow ? esc(c.coachNow) : 'non renseigné'}${c.coachPrec ? (meme ? `, déjà en poste en ${sais(c.prec)}.` : `. En ${sais(c.prec)} c'était ${esc(c.coachPrec)} : le style de jeu a pu changer.`) : '.'}</li>
        <li>Effectif : ${c.restes} des ${c.now} joueurs alignés cette saison étaient déjà au club en ${sais(c.prec)} ; ils avaient marqué ${c.butsRestes} des ${c.buts} buts (${pct(c.butsRestes, c.buts)}).</li>
        <li>Arrivées : ${c.recrues} joueur(s) venu(s) d'un autre club de D2, ${c.nouveaux} sans match de D2 sur les deux saisons précédentes.</li></ul>`; }
    h += pli('Saisons précédentes : ce qui reste valable', txt + table(['Saison', 'Entraîneur', 'Rang', 'MJ', 'Pts', 'Buts pour / match', 'Buts contre / match', 'Sup.', 'Inf.', 'Domicile', 'Extérieur', 'Séries'],
      H.lignes.map(l => ({ cls: l.s === CUR ? 'me' : '', c: [sais(l.s), esc(l.coach || '–'), l.rang ? `${l.rang}/${l.taille}` : '–', l.n, l.pts, l.bp.toFixed(1), l.bc.toFixed(1), l.pp, l.pk, l.dom, l.ext, esc(l.series)] })),
      ['', '', 'n', 'n', 'n', 'n', 'n', 'n', 'n', '', '', '']), false, 'mesuré');
  }
  const proch = D.matchs.filter(m => m.etat !== 'T' && (m.dom_id === T || m.ext_id === T)).slice(0, 4);
  if (proch.length) h += pli('Prochains matchs', table(['J', 'Date', '', 'Adversaire'], proch.map(m => [`J${m.journee}`, dateFr(m.date) + ' ' + heure(m.date), m.dom_id === T ? 'Dom.' : 'Ext.', lienEq(m.dom_id === T ? m.ext_id : m.dom_id)])));
  return h;
}

/* ---------- fiche de match (une page) ---------- */
function pageFiche(ab) {
  const me = moi(), p = prochain(), O = ab && D.ab[ab] ? D.ab[ab].id : p ? advDe(p) : null;
  if (!O || O === me) return `<h1>Fiche de match</h1><p class="muted">Aucun match à venir.</p>`;
  const M = D.matchs.find(m => m.etat !== 'T' && ((m.dom_id === me && m.ext_id === O) || (m.dom_id === O && m.ext_id === me)));
  return sansFiltre(() => {
    const k = pointsCles(O, M), Q = profil(O), S = Q.S, F = formeJ(O), eq = D.eq[O], dom = M && M.dom_id === me;
    if (!Q.n) return `<h1>Fiche de match</h1><p class="muted">${esc(eq.nom)} n'a pas encore joué cette saison.</p>`;
    const top = S.joueurs.filter(x => x.poste !== 'GB').slice(0, 6), LO = lignes(O, 1).trios.length ? lignes(O, 1).trios : lignes(O).trios, LB = lignes(me, 1).trios.length ? lignes(me, 1).trios : lignes(me).trios;
    const g = S.gardiens.filter(x => x.sec >= 1800), ab2 = absences(O), arr = arrivees(O).filter(a => a.b + a.a > 0 || a.passe.length).slice(0, 2);
    const vs = D.finis.filter(m => (m.dom_id === O && m.ext_id === me) || (m.dom_id === me && m.ext_id === O)).map(m => jeu(me, m)).slice(-3).reverse();
    const cl = classement(me), rg = id => cl.findIndex(r => r.id === id) + 1;
    return `<div class="fiche"><p class="sur">Fiche de match${M ? ` · J${M.journee}` : ''}</p>
      <h1>${esc(D.eq[me].nom_court)} <span class="muted">contre</span> ${esc(eq.nom_court)}</h1>
      <p class="sub">${M ? `${dateFr(M.date)} à ${heure(M.date)} · ${dom ? 'à domicile' : 'à l\'extérieur'} · ${esc(M.patinoire || '')}` : 'Pas de match programmé entre les deux équipes'}${rg(O) ? ` · ${esc(eq.nom_court)} ${rg(O)}<sup>e</sup>, ${esc(D.eq[me].nom_court)} ${rg(me)}<sup>e</sup> de la poule` : ''}</p>
      ${blocPoints(k)}
      <div class="card zt" id="zones" data-o="${eq.abreviation}"></div>
      <div class="grid2"><div class="card">${h2('Nous contre eux', 'mesuré', `${profil(me).n} et ${Q.n} matchs`)}${rangs(comparer(me).filter(c => !/^(t2|premier|provm)/.test(c.k)), D.eq[me].nom_court, comparer(O), eq.nom_court)}</div>
      <div class="card">${h2('Leurs joueurs à surveiller', 'mesuré')}${barresJoueurs(top, x => fleche(F, x.id))}
        ${g[0] ? `<p class="lg"><b>Gardien :</b> ${g.slice(0, 2).map(x => `${esc(nomJ(x.id))}, ${x.tit} titularisation(s), ${f1(x.enc * 3600 / x.sec)} buts encaissés par match`).join(' ; ')}.</p>` : ''}</div></div>
      <div class="grid2"><div class="card">${h2('Leurs trios', 'déduit')}${LO.length ? `<ol class="trios">${LO.slice(0, 3).map(l => `<li>${l.j.map(x => esc(nomJ(x))).join(' – ')} <small>${l.p} buts</small></li>`).join('')}</ol>` : '<p class="muted">Non déductibles.</p>'}
        ${ab2.length ? `<p class="lg"><b>Absents au dernier match :</b> ${ab2.slice(0, 4).map(a => esc(nomJ(a.id))).join(', ')} (cause non publiée).</p>` : ''}
        ${arr.length ? `<p class="lg"><b>Arrivée récente :</b> ${arr.map(a => `${esc(nomJ(a.id))} (${a.b + a.a} pt(s) en ${a.mj} match(s))`).join(', ')}.</p>` : ''}</div>
      <div class="card">${h2('Nos trios au dernier match', 'déduit')}${LB.length ? `<ol class="trios">${LB.slice(0, 4).map(l => `<li>${l.j.map(x => esc(nomJ(x))).join(' – ')} <small>${l.p} pour, ${l.c} contre</small></li>`).join('')}</ol>` : ''}
        ${vs.length ? `<p class="lg"><b>Dernières confrontations :</b> ${vs.map(j => `${sais(j.m.saison)} ${j.dom ? 'dom.' : 'ext.'} ${score(j)}`).join(' · ')}</p>` : ''}</div></div>
      ${notes('fiche:' + (M ? M.id : O))}
      <p class="note">Tout est tiré des feuilles de match FFHG de la saison ${sais(CUR)}. « Déduit » : calculé par recoupement, à confirmer. La fiche complète de l'adversaire : <a href="#/equipe/${eq.abreviation}">${esc(eq.nom_court)}</a>.</p></div>`;
  });
}

/* ---------- débrief d'après-match ---------- */
function debrief(M) {
  const B = moi(), j = jeu(B, M), O = j.adv, bm = D.butsM[M.id] || [], nB = D.eq[B].nom_court, nO = D.eq[O].nom_court;
  const per = p => [bm.filter(b => b.periode === p && b.equipe_id === B).length, bm.filter(b => b.periode === p && b.equipe_id !== B).length];
  const sit = (eq, re) => bm.filter(b => (eq === B ? b.equipe_id === B : b.equipe_id !== B) && re.test(b.situation || '')).length;
  const pp = ppMatch(M), ppB = sit(B, /^SUP/), ppO = sit(O, /^SUP/), shO = sit(O, /^INF/);
  let apresP = 0, apresC = 0, avance = 0, perdue = false, sc = 0;
  bm.forEach((b, i) => { const nx = bm[i + 1]; if (nx && nx.temps - b.temps <= 120 && nx.equipe_id !== b.equipe_id) (b.equipe_id === B ? apresC++ : apresP++);
    sc += b.equipe_id === B ? 1 : -1; avance = Math.max(avance, sc); if (avance >= 2 && sc <= 0) perdue = true; });
  const cnt = (arr, f) => arr.filter(x => x.match_id === M.id && f(x)).length, tirs = M.detail_tirs ? { tp: cnt(D.tirs, t => t.equipe_id === B) + j.bp, tc: cnt(D.tirs, t => t.equipe_id !== B) + j.bc, eg: cnt(D.engagements, e => e.equipe_gagnante_id === B), et: cnt(D.engagements, () => true) } : null;
  const pts = {}; bm.filter(b => b.equipe_id === B).forEach(b => { (pts[b.buteur_id] = pts[b.buteur_id] || { id: b.buteur_id, b: 0, a: 0 }).b++; [b.assist1_id, b.assist2_id].forEach(a => a && ((pts[a] = pts[a] || { id: a, b: 0, a: 0 }).a++)); });
  const ptsO = id => bm.filter(b => [b.buteur_id, b.assist1_id, b.assist2_id].includes(id)).length;
  const fin10 = bm.filter(b => b.temps > 3000 && b.temps <= 3600), p1 = per(1), p3 = per(3), premier = bm[0];
  // ce que la fiche aurait annoncé avant ce match
  const av = aLaDate(M.date, () => ({ pre: preconisations(O, M), top: profil(O).S.joueurs.slice(0, 3).filter(p => p.b + p.a > 0).map(p => p.id) }));
  const tiers1 = () => [`1er tiers : ${nB} ${p1[0]}-${p1[1]}.`, p1[0] > p1[1] ? 'ok' : p1[0] < p1[1] ? 'ko' : ''], tiers3 = () => [`3e tiers : ${nB} ${p3[0]}-${p3[1]}.`, p3[0] > p3[1] ? 'ok' : p3[0] < p3[1] ? 'ko' : ''];
  const penB = () => [`${nB} a joué ${pp[O].n} infériorité(s) ; ${nO} a marqué ${ppO} but(s) en supériorité.`, ppO === 0 ? 'ok' : 'ko'], penO = () => [`${nB} a eu ${pp[B].n} supériorité(s) et a marqué ${ppB} but(s).`, ppB > 0 ? 'ok' : pp[B].n ? 'ko' : ''];
  const CHECK = { 'Ne pas subir l\'entame': tiers1, 'Imposer le rythme dès le 1er tiers': tiers1, 'Le 1er tiers sera décisif': tiers1, 'Garder de la fraîcheur pour le 3e tiers': tiers3,
    'Insister en fin de match': () => { const x = fin10.filter(b => b.equipe_id === B).length; return [`${x} but(s) de ${nB} dans les 10 dernières minutes.`, x ? 'ok' : '']; },
    'Rester hors du banc des pénalités': penB, 'Leur supériorité est peu dangereuse': penB, 'Provoquer des fautes': penO, 'Ne pas compter sur la supériorité': penO,
    'Prudence en supériorité': () => [`${nO} a marqué ${shO} but(s) en infériorité.`, shO ? 'ko' : 'ok'],
    'Joueurs à neutraliser': () => { const t = av.top.map(id => [id, ptsO(id)]), s = t.reduce((a, x) => a + x[1], 0); return [`${t.map(x => `${nomJ(x[0])} ${x[1]} pt(s)`).join(', ')}.`, s <= 2 ? 'ok' : s >= 5 ? 'ko' : '']; },
    'Marquer en premier': () => premier ? [`Premier but : ${D.eq[premier.equipe_id].nom_court} à ${mmss(premier.temps)}.`, premier.equipe_id === B ? 'ok' : 'ko'] : null,
    'Ouvrir le score ne suffit pas': () => premier ? [`Premier but : ${D.eq[premier.equipe_id].nom_court} à ${mmss(premier.temps)}.`, ''] : null,
    'Juste après un but': () => [`${nB} a marqué ${apresP} fois dans les 2 minutes après avoir encaissé, et encaissé ${apresC} fois juste après avoir marqué.`, apresC > apresP ? 'ko' : apresP > apresC ? 'ok' : ''],
    'Gardien': () => { const g = D.gardiens_presence.filter(x => x.match_id === M.id && x.equipe_id === O && x.entree === 0)[0]; return g ? [`Titulaire : ${nomJ(g.joueur_id)}, ${j.bp} but(s) encaissé(s) par ${nO}.`, ''] : null; } };
  const annonce = av.pre.map(([t, x]) => CHECK[t] ? [t, x, CHECK[t]()] : null).filter(r => r && r[2]);
  // réussites et problèmes du match
  const R = [], Pb = [], P = (a, w, t, x) => a.push({ w, t, x });
  [1, 2, 3].forEach(p => { const [a, b] = per(p), l = ['1er', '2e', '3e'][p - 1]; if (a - b >= 2) P(R, a - b, `${l} tiers gagné ${a}-${b}`, ''); if (b - a >= 2) P(Pb, b - a + (p === 3 ? 1 : 0), `${l} tiers perdu ${a}-${b}`, ''); });
  if (ppB) P(R, 2 + ppB, 'Supériorité efficace', `${ppB} but(s) en ${pp[B].n} séquence(s).`); else if (pp[B].n >= 3) P(Pb, 2, 'Supériorité muette', `Aucun but en ${pp[B].n} séquences (${mmPP(pp[B].sec)}).`);
  if (pp[O].n >= 2 && !ppO) P(R, 2 + pp[O].n / 2, 'Infériorité sans faute', `${pp[O].n} pénalités tuées.`); if (ppO) P(Pb, 2 + ppO, 'Buts encaissés en infériorité', `${ppO} but(s) en ${pp[O].n} séquence(s).`);
  if (pp[O].n >= 5) P(Pb, pp[O].n / 2, 'Trop de pénalités', `${pp[O].n} infériorités à jouer.`); else if (pp[O].n <= 2) P(R, 1.5, 'Discipline', `${pp[O].n} infériorité(s) seulement.`);
  if (apresC) P(Pb, 2 + apresC, 'But encaissé juste après avoir marqué', `${apresC} fois dans les 2 minutes.`); if (apresP) P(R, 1.5 + apresP, 'Réaction immédiate', `${apresP} but(s) dans les 2 minutes après en avoir encaissé un.`);
  if (perdue) P(Pb, 4, 'Avance de deux buts perdue', 'L\'adversaire est revenu à égalité ou devant.');
  if (premier && premier.equipe_id !== B && j.v) P(R, 3, 'Victoire après avoir encaissé le premier but', '');
  const f5 = fin10.filter(b => b.equipe_id !== B && b.temps > 3300).length; if (f5 >= 2) P(Pb, 2 + f5, 'Fin de match', `${f5} buts encaissés dans les 5 dernières minutes.`);
  if (tirs) { if (tirs.tp - tirs.tc >= 8) P(R, 2, 'Domination aux tirs', `${tirs.tp} tirs contre ${tirs.tc}.`); if (tirs.tc - tirs.tp >= 8) P(Pb, 2, 'Dominés aux tirs', `${tirs.tp} tirs contre ${tirs.tc}.`);
    if (tirs.et >= 20) { const r = tirs.eg / tirs.et; if (r >= 0.55) P(R, 2, 'Engagements', `${pct(tirs.eg, tirs.et)} gagnés.`); if (r < 0.45) P(Pb, 2, 'Engagements', `${pct(tirs.eg, tirs.et)} gagnés seulement.`); } }
  Object.values(pts).filter(x => x.b + x.a >= 3).forEach(x => P(R, x.b + x.a, nomJ(x.id), `${x.b} but(s), ${x.a} passe(s).`));
  const tri = a => a.sort((x, y) => y.w - x.w).slice(0, 3).map(x => [x.t, x.x]);
  return { j, O, per, tirs, annonce, R: tri(R), Pb: tri(Pb), pts: Object.values(pts).sort((a, b) => (b.b + b.a) - (a.b + a.a)), pp, ppB, ppO };
}

function pageDebrief(id) {
  const me = moi(), tous = sansFiltre(() => matchsDe(me)), M = id ? D.m[id] : tous.length ? tous[tous.length - 1].m : null;
  if (!M || M.etat !== 'T' || (M.dom_id !== me && M.ext_id !== me)) return `<h1>Débrief</h1><p class="muted">Aucun match joué à débriefer.</p>`;
  const d = debrief(M), j = d.j, nO = D.eq[d.O].nom_court, num = Object.fromEntries((D.compM[M.id] || []).map(c => [c.joueur_id, c.numero]));
  const V = { ok: '<span class="tag fort">✔ favorable</span>', ko: '<span class="tag faible">✖ défavorable</span>', '': '<span class="tag">• neutre</span>' };
  return `<p class="sur">Débrief · J${M.journee} · ${dateFr(M.date)} · ${j.dom ? 'domicile' : 'extérieur'}</p>
    <h1>${esc(D.eq[me].nom_court)} ${j.bp} – ${j.bc} ${esc(nO)} <span class="${j.v ? 'V' : 'D'}">${j.v ? 'Victoire' : 'Défaite'}${M.fin === 'S' ? '' : M.fin === 'PRL' ? ' en prolongation' : ' aux tirs au but'}</span></h1>
    <p class="sub">Par tiers : ${[1, 2, 3].map(p => d.per(p).join('-')).join(' / ')}${d.tirs ? ` · tirs ${d.tirs.tp}-${d.tirs.tc} · engagements ${pct(d.tirs.eg, d.tirs.et)}` : ''} · supériorité ${d.ppB}/${d.pp[me].n} · infériorité ${d.pp[d.O].n - d.ppO}/${d.pp[d.O].n}</p>
    <label class="choix">Autre match : <select id="debrief-select">${[...tous].reverse().map(x => `<option value="${x.m.id}"${x.m.id === M.id ? ' selected' : ''}>J${x.m.journee} · ${esc(D.eq[x.adv].nom_court)} (${x.bp}-${x.bc})</option>`).join('')}</select></label>
    <div class="grid2"><div><h3 class="e">▲ Ce qui a marché</h3>${d.R.length ? cartes(d.R, 'e') : '<p class="muted">Rien de net dans les chiffres.</p>'}</div>
    <div><h3 class="v">⚠ Ce qui a posé problème</h3>${d.Pb.length ? cartes(d.Pb, 'v') : '<p class="muted">Rien de net dans les chiffres.</p>'}</div></div>
    <div class="card">${h2('Ce que la fiche annonçait, ce qui s\'est passé', 'déduit')}${d.annonce.length ? `<div class="ann">${d.annonce.map(([t, x, [fait, v]]) => `<div><div><b>${esc(t)}</b><p>${esc(x)}</p></div><div>${V[v]}<p>${esc(fait)}</p></div></div>`).join('')}</div>` : '<p class="muted">Pas assez de matchs joués avant celui-ci pour établir une fiche.</p>'}
      <p class="note">La colonne de gauche est recalculée avec les seuls matchs joués avant celui-ci. Elle sert à juger si les préconisations voyaient juste.</p></div>
    ${d.pts.length ? `<div class="card">${h2('Pointeurs du match', 'mesuré')}${barresJoueurs(d.pts.map(x => ({ ...x, mj: 1, num: num[x.id] })))}</div>` : ''}
    ${notes('debrief:' + M.id)}`;
}

/* ---------- comparateur de lignes ---------- */
const LG = { T: null, sel: new Set() };
function resLignes() {
  const T = LG.T, sel = [...LG.sel], ids = new Set(sansFiltre(() => matchsDe(T)).map(j => j.m.id)), S = sansFiltre(() => profil(T).S);
  const buts = D.buts.filter(b => ids.has(b.match_id) && b.situation === 'EG').map(b => ({ b, pour: b.equipe_id === T, j: (D.glace[b.id] || []).filter(x => x.equipe_id === T).map(x => x.joueur_id) }));
  const ens = l => { const g = buts.filter(x => l.every(id => x.j.includes(id))); return { j: l, p: g.filter(x => x.pour).length, c: g.filter(x => !x.pour).length, g }; };
  const couv = `<p class="note">Seuls les buts à égalité numérique comptent. Joueurs présents notés sur ${S.couv.ok} des ${S.couv.tot} buts encaissés${S.couvOk ? '' : ' : les buts encaissés ne sont pas affichés'}. Aucun temps de glace n'est publié : deux joueurs peuvent avoir joué ensemble sans but, et ne pas apparaître ici.</p>`;
  if (!sel.length) return `<p class="muted">Cochez un joueur pour voir ses meilleurs partenaires, ou plusieurs pour voir leur bilan commun.</p>${blocLignes(T)}`;
  if (sel.length === 1) {
    const part = S.joueurs.filter(p => p.poste !== 'GB' && p.id !== sel[0]).map(p => ens([sel[0], p.id])).filter(x => x.p + x.c > 0).sort((a, b) => b.p - a.p || a.c - b.c).slice(0, 10);
    return `<h3>Partenaires de ${esc(nomJ(sel[0]))}</h3>${legende([['pour', 'Buts marqués ensemble'], ['contre', 'Buts encaissés ensemble']])}${part.length ? barresLignes(part.map(x => ({ ...x, j: [x.j[1]] })), S.couvOk) : '<p class="muted">Aucun but avec ce joueur sur la glace.</p>'}${couv}`;
  }
  const e = ens(sel), paires = sel.length >= 3 ? sel.flatMap((a, i) => sel.slice(i + 1).map(b => ens([a, b]))) : [];
  return `<div class="tiles">${tile(e.p, 'buts marqués ensemble')}${tile(S.couvOk ? e.c : 'n. n.', 'buts encaissés ensemble')}${tile(e.g.length ? new Set(e.g.map(x => x.b.match_id)).size : 0, 'matchs concernés')}</div>
    ${e.g.length < 4 ? `<p class="alerte">Échantillon très faible (${e.g.length} but(s)) : à lire comme une indication, pas comme une preuve.</p>` : ''}
    ${paires.length ? `<h3>Deux par deux</h3>${legende([['pour', 'Buts marqués'], ['contre', 'Buts encaissés']])}${barresLignes(paires, S.couvOk)}` : ''}
    ${e.g.length ? `<details class="sous"><summary>Les ${e.g.length} buts concernés</summary>${table(['Date', 'Adversaire', 'Temps', '', 'Buteur'], e.g.map(x => { const m = D.m[x.b.match_id]; return [dateFr(m.date), esc(D.eq[m.dom_id === T ? m.ext_id : m.dom_id].nom_court), mmss(x.b.temps), x.pour ? '<span class="V">marqué</span>' : '<span class="D">encaissé</span>', esc(nomJ(x.b.buteur_id))]; }))}</details>` : ''}${couv}`;
}
function pageLignes(ab) {
  const T = ab && D.ab[ab] ? D.ab[ab].id : moi(); if (LG.T !== T) { LG.T = T; LG.sel = new Set(); }
  const S = sansFiltre(() => profil(T).S), col = (lab, f) => `<fieldset><legend>${lab}</legend>${S.joueurs.filter(f).map(p => `<label><input type="checkbox" data-jl="${p.id}"${LG.sel.has(p.id) ? ' checked' : ''}> ${esc(nomJ(p.id))} <small>${p.mj} MJ</small></label>`).join('')}</fieldset>`;
  return `<h1>Comparateur de lignes</h1><p class="sub">${esc(D.eq[T].nom)} · associations réellement observées sur les buts, saison ${sais(CUR)}</p>
    <div class="card choixj">${col('Attaquants', p => p.poste !== 'GB' && p.poste !== 'D')}${col('Défenseurs', p => p.poste === 'D')}</div>
    <div class="card">${h2('Résultat', 'déduit')}<div id="res-lignes">${resLignes()}</div></div>`;
}

/* ---------- analyses vidéo ---------- */
function pageVideo() {
  return `<div id="tirs"><p class="muted">Chargement de la carte des tirs…</p></div>
    <details class="card"><summary>Autres analyses prévues</summary>${cartes([
      ['Entrées et sorties de zone', 'Réussies ou perdues, en contrôle ou par dégagement, par joueur et par ligne.'],
      ['Pertes et récupérations de palet', 'Où elles se produisent sur la glace et qui est concerné.'],
      ['Localisation des buts marqués', 'Même carte pour les buts de Brest, puis pour les adversaires.'],
      ['Temps de glace et lignes réelles', 'Qui joue avec qui et combien de temps, au lieu de le déduire des seuls buts.'],
      ['Origine des buts', 'Ce qui s\'est passé dans les secondes qui précèdent chaque but marqué ou encaissé.']], 'i')}</details>`;
}

function monterZones(ab) {
  let el = $('#zones');
  if (!el && ab && D.ab[ab]) {                       // fiche équipe : encart ajouté avant « Lignes probables » (sinon à la fin)
    el = document.createElement('div');
    el.className = 'card zt'; el.id = 'zones';
    const ref = [...document.querySelectorAll('#app .card')].find(c => /Lignes probables/.test(c.textContent.slice(0, 40)));
    ref ? ref.before(el) : $('#app').append(el);
  }
  if (!el || !window.ZonesTir) return;
  ZonesTir.donnees().then(d => {
    if (!d || !document.body.contains(el)) return el && el.remove();
    if (ab) return ZonesTir.equipe(el, d, ab, D.ab[ab].nom_court);
    const me = moi(), O = el.dataset.o;
    ZonesTir.fiche(el, d, D.eq[me].abreviation, D.eq[me].nom_court, O, D.ab[O] ? D.ab[O].nom_court : O);
  });
}
function monterTirs() {
  fetch('data/buts.json', { cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(d => { const el = $('#tirs'); if (el) TirsBrest(el, d); })
    .catch(() => { const el = $('#tirs'); if (el) el.innerHTML = '<p class="muted">Carte des tirs indisponible pour le moment.</p>'; });
}

/* ---------- accueil ---------- */
function pageAccueil() {
  const me = moi(), p = prochain(), adv = p && advDe(p), js = matchsDe(me);
  let h = `<h1>${esc(D.eq[me].nom)}</h1><p class="sub">Division 2, saison régulière ${sais(CUR)}</p>`;
  if (p) h += `<div class="card prochain"><p class="sur">Prochain match · J${p.journee}</p><div class="next"><b>${p.dom_id === me ? 'Domicile' : 'Extérieur'} · ${esc(D.eq[adv].nom)}</b>
    <span>${dateFr(p.date)} à ${heure(p.date)} · ${esc(p.patinoire || '')}</span></div>${blocPoints(pointsCles(adv, p))}<p style="margin:12px 0 0">${lienFiche(adv)}</p></div>`;
  const last = js[js.length - 1];
  if (last) h += `<div class="card"><div class="next"><span class="sur">Dernier match</span><b>${esc(D.eq[last.adv].nom_court)} ${score(last)}</b><a class="btn" href="#/debrief/${last.m.id}">Voir le débrief →</a></div></div>`;
  h += `<div class="card"><h2>Classement de la poule</h2>${tableClassement(me, [me, adv])}</div>`;
  h += `<div class="grid2"><div class="card"><h2>Derniers résultats</h2>${table(['J', 'Date', '', 'Adversaire', 'Résultat'], js.slice(-6).reverse().map(j => [`J${j.m.journee}`, dateFr(j.m.date), j.dom ? 'Dom.' : 'Ext.', lienEq(j.adv), score(j)]))}</div>
    <div class="card"><h2>Calendrier à venir</h2>${table(['J', 'Date', '', 'Adversaire'], D.matchs.filter(m => m.etat !== 'T' && (m.dom_id === me || m.ext_id === me)).slice(0, 6).map(m => [`J${m.journee}`, dateFr(m.date) + ' ' + heure(m.date), m.dom_id === me ? 'Dom.' : 'Ext.', lienEq(advDe(m))]))}</div></div>`;
  return h;
}

/* ---------- navigation ---------- */
function route(garder) {
  const m = location.hash.match(/^#\/(\w+)(?:\/(\w+))?/), page = m ? m[1] : 'home', arg = m && m[2] ? m[2] : '';
  if (page !== 'equipe') FILTRE = 'saison';
  const ab = page === 'equipe' ? arg.toUpperCase() : '';
  $('#app').innerHTML = accord(page === 'equipe' ? pageEquipe(ab) : page === 'fiche' ? pageFiche(arg.toUpperCase()) : page === 'debrief' ? pageDebrief(+arg || 0)
    : page === 'lignes' ? pageLignes(arg.toUpperCase()) : page === 'video' ? pageVideo() : pageAccueil());
  $('#app').classList.toggle('large', page === 'video');
  if (page === 'video') monterTirs();
  if (page === 'fiche' || page === 'equipe') monterZones(page === 'equipe' ? ab : '');
  const cur = page === 'equipe' ? (ab === ME ? 'equipe' : '') : page;
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('on', a.dataset.nav === cur));
  $('#team-select').value = page === 'equipe' && ab !== ME ? ab : '';
  document.title = ab && D.ab[ab] ? `${D.ab[ab].nom_court} – Hockey Raban` : 'Hockey Raban – D2';
  if (garder !== true) window.scrollTo(0, 0);
}

load().then(() => {
  const mine = new Set(poule(moi())), opt = e => `<option value="${e.abreviation}">${esc(e.nom_court)}</option>`, tri = (a, b) => a.nom_court.localeCompare(b.nom_court);
  $('#team-select').innerHTML = `<option value="">Voir une équipe…</option><optgroup label="Notre poule">${D.equipes.filter(e => mine.has(e.id) && e.id !== moi()).sort(tri).map(opt).join('')}</optgroup><optgroup label="Autre poule">${D.equipes.filter(e => !mine.has(e.id) && D.actuelles.has(e.id)).sort(tri).map(opt).join('')}</optgroup>`;
  $('#team-select').onchange = e => { location.hash = e.target.value ? `#/equipe/${e.target.value}` : '#/'; };
  $('#print').onclick = () => window.print();
  window.addEventListener('beforeprint', () => document.querySelectorAll('details.card, details.sous').forEach(d => d.open = true));
  const last = D.joues[D.joues.length - 1];
  $('#maj').textContent = last ? `Données à jour au ${dateFr(last.date)} (${D.joues.length} matchs joués cette saison, ${D.finis.length} sur trois saisons)` : '';
  const tip = $('#tip');
  document.addEventListener('mousemove', e => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip; tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 12, innerWidth - 250) + 'px'; tip.style.top = e.clientY + 14 + 'px';
  });
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-filtre]'); if (b) { FILTRE = b.dataset.filtre; route(true); } });
  document.addEventListener('change', e => {
    if (e.target.id === 'debrief-select') location.hash = '#/debrief/' + e.target.value;
    if (e.target.dataset && e.target.dataset.jl) { const id = +e.target.dataset.jl; e.target.checked ? LG.sel.add(id) : LG.sel.delete(id); $('#res-lignes').innerHTML = accord(resLignes()); }
  });
  document.addEventListener('input', e => { const k = e.target.dataset && e.target.dataset.note; if (k) try { localStorage.setItem('note:' + k, e.target.value); } catch (err) { /* stockage indisponible */ } });
  window.addEventListener('hashchange', route); route();
}).catch(err => { $('#app').innerHTML = `<p>Impossible de charger les données : ${esc(err.message)}</p>`; });
