/* Briques graphiques : barres de rang, cartes colorées, barres de joueurs et de lignes. */
const NIV = { 'mesuré': 'm', 'partiel': 'p', 'déduit': 'd' };
const badge = (niv, detail) => `<span class="badge b-${NIV[niv]}" data-tip="${niv === 'mesuré' ? 'Compté directement sur les feuilles de match.' : niv === 'partiel' ? 'Donnée saisie sur une partie des matchs seulement.' : 'Calculé par recoupement, pas noté tel quel sur les feuilles.'}">${niv}${detail ? ' · ' + detail : ''}</span>`;
const h2 = (t, niv, detail) => `<h2>${t}${niv ? ' ' + badge(niv, detail) : ''}</h2>`;
const pli = (titre, contenu, ouvert, niv, detail) => `<details class="card"${ouvert ? ' open' : ''}><summary>${titre}${niv ? ' ' + badge(niv, detail) : ''}</summary>${contenu}</details>`;
const legende = items => `<div class="legend">${items.map(([c, l]) => `<span><i style="background:var(--${c})"></i>${l}</span>`).join('')}</div>`;

// Position de l'équipe parmi les équipes de D2 sur chaque indicateur : à droite = parmi les meilleures.
function rangs(a, nomA, b, nomB) {
  const pos = c => c.sur > 1 ? 100 * (c.sur - c.rang) / (c.sur - 1) : 50, B = b ? Object.fromEntries(b.map(c => [c.k, c])) : {};
  const tip = (c, nom) => `${nom} : ${c.fmt(c.v)} · ${c.rang}e sur ${c.sur} · moyenne D2 ${c.fmt(c.moy)}`;
  return `${b ? legende([['pour', esc(nomA)], ['contre', esc(nomB)]]) : ''}
  <div class="rg rg-h"><span></span><div class="rg-axe"><span>bas de tableau</span><span>milieu</span><span>haut de tableau</span></div><span></span></div>
  ${a.map(c => { const o = B[c.k]; return `<div class="rg"><span class="rg-l">${c.lab}</span>
    <div class="rg-t"><i></i><i></i><i></i>${o ? `<b class="dot eux" style="left:${pos(o)}%" data-tip="${esc(tip(o, nomB))}"></b>` : ''}<b class="dot nous" style="left:${pos(c)}%" data-tip="${esc(tip(c, nomA))}"></b></div>
    <span class="rg-v">${c.fmt(c.v)}${o ? ` <small>/ ${o.fmt(o.v)}</small>` : ` <small>${c.rang}e</small>`}</span></div>`; }).join('')}`;
}

// Cartes colorées : e = à exploiter (vert), v = vigilance (rouge), a = axe de travail (ambre), i = information
const ICONE = { e: '▲', v: '⚠', a: '➜', i: '•' };
// court = true : seule la première phrase est visible, le reste se déplie
const cartes = (l, type, court) => `<div class="pts">${l.map(([t, x]) => { const i = court ? x.search(/\. (?=[A-ZÀ-Ü])/) : -1, tete = i > 0 ? x.slice(0, i + 1) : x, suite = i > 0 ? x.slice(i + 2) : '';
  return `<div class="ptc ${type}"><b><span class="ic">${ICONE[type]}</span> ${esc(t)}</b><p>${esc(tete)}</p>${suite ? `<details class="plus"><summary>détail</summary><p>${esc(suite)}</p></details>` : ''}</div>`; }).join('')}</div>`;
// accorde « 1 but(s) » en « 1 but » et « 3 but(s) » en « 3 buts »
const accord = h => h.replace(/(\d+)((?:\s[^\d.;:,()<>]*?\(s\))+)/g, (m, n, r) => n + r.replace(/\(s\)/g, +n > 1 ? 's' : ''));

// Barres buts + passes par joueur
function barresJoueurs(ps, extra) {
  const max = Math.max(1, ...ps.map(p => p.b + p.a));
  return legende([['pour', 'Buts'], ['passes', 'Passes']]) + `<div class="bj">${ps.map(p => `<div class="bj-r" data-tip="${esc(nomJ(p.id))} : ${p.b} but(s), ${p.a} passe(s) en ${p.mj} match(s)">
    <span class="bj-l"><small>${esc(p.num || '')}</small> ${esc(nomJ(p.id))}${extra ? extra(p) : ''}</span>
    <span class="bj-b"><i class="s-b" style="width:${100 * p.b / max}%"></i><i class="s-a" style="width:${100 * p.a / max}%"></i></span><span class="bj-v">${p.b + p.a}</span></div>`).join('')}</div>`;
}

// Lignes : buts encaissés à gauche, buts marqués à droite
function barresLignes(ls, couvOk) {
  const max = Math.max(1, ...ls.map(l => Math.max(l.p, couvOk ? l.c : 0)));
  return ls.map(l => `<div class="bl" data-tip="${l.p} but(s) marqué(s)${couvOk ? `, ${l.c} encaissé(s)` : ''} avec au moins deux joueurs de la ligne sur la glace">
    <span class="bl-l">${l.j.map(x => esc(nomJ(x))).join(' – ')}${l.neuf ? ' <span class="tag neuf">nouveau</span>' : ''}</span>
    <span class="bl-c">${couvOk ? `<em>${l.c}</em><i style="width:${70 * l.c / max}%"></i>` : '<em class="muted">n. n.</em>'}</span>
    <span class="bl-p"><i style="width:${70 * l.p / max}%"></i><em>${l.p}</em></span></div>`).join('');
}

// Bilans victoires / défaites par situation
function barresVD(rows) {
  const max = Math.max(1, ...rows.map(r => r[1].length));
  return rows.map(([lab, js]) => { const v = js.filter(j => j.v).length, d = js.length - v;
    return `<div class="vd"><span class="vd-l">${lab}</span><span class="vd-b" style="width:${js.length ? Math.max(18, 100 * js.length / max) : 0}%">${v ? `<i class="v" style="flex:${v}">V ${v}</i>` : ''}${d ? `<i class="d" style="flex:${d}">D ${d}</i>` : ''}</span>${js.length ? '' : '<span class="muted">aucun match</span>'}</div>`; }).join('');
}

// À quel moment de la pénalité les buts tombent
function barresDelais(arr) {
  const g = [arr.filter(x => x <= 30).length, arr.filter(x => x > 30 && x <= 60).length, arr.filter(x => x > 60).length], max = Math.max(1, ...g);
  return `<div class="dl">${['0–30 s', '30–60 s', 'après 60 s'].map((l, i) => `<div><i style="height:${6 + 34 * g[i] / max}px"></i><b>${g[i]}</b><span>${l}</span></div>`).join('')}</div>`;
}

// Notes du staff, gardées dans le navigateur de l'appareil
function notes(cle) {
  let v = ''; try { v = localStorage.getItem('note:' + cle) || ''; } catch (e) { /* stockage indisponible */ }
  return `<div class="card"><h2>Notes du staff</h2><textarea class="notes" data-note="${esc(cle)}" placeholder="Observations vidéo, consignes, retours des joueurs…">${esc(v)}</textarea>
    <p class="note">Enregistrées sur cet appareil uniquement : elles ne sont pas partagées avec les autres membres du staff.</p></div>`;
}
