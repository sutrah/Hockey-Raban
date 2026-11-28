// Encart « Forecheck » des fiches de match et d'équipe : bilan issu de l'analyse vidéo (data/forecheck.json)
window.Forecheck = (function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pc = (a, b) => b ? Math.round(100 * a / b) + ' %' : '–';
  const court = h => String(h).split(' ')[0];
  const nomP = s => s.charAt(0) + s.slice(1).toLowerCase();
  const T = ['', '1er', '2e', '3e'];
  // schémas types (pas des positions mesurées) : zone vue de dessus, ligne de but à gauche, ligne bleue à droite
  // équipe qui relance : porteur derrière la cage, défenseur au coin opposé, un attaquant à la bande
  const ADV = [[-.6, -2.2], [1.5, 8.5], [8, -12.5]];
  const POS = {
    '1-2-2': [[2.6, -3.6, 1], [10, -11], [10, 11], [17, -6], [17, 6]],
    '2-1-2': [[2.6, -3.6], [4, 7], [10, 0], [17, -6], [17, 6]],
    '1-3-1': [[2.6, -3.6], [10, -10], [10, 0], [10, 10], [17, 0]],
  };
  // demi-patinoire vue de dessus, cage en haut, jusqu'à la ligne rouge : x = 15 + latéral, y = 4 + profondeur (m)
  function schema(sy) {
    const p = POS[sy];
    if (!p) return '';
    const P = (q, c, r) => `<circle cx="${15 + q[1]}" cy="${4 + q[0]}" r="${r}" fill="${c}" stroke="#fff" stroke-width=".15"/>`;
    const bord = 8.5 - Math.sqrt(8.5 ** 2 - 4.5 ** 2);
    return `<svg viewBox="-.6 -.6 31.2 31.2" role="img" aria-label="Schéma ${esc(sy)}" class="fc-s">
      <path d="M0 30V8.5A8.5 8.5 0 0 1 8.5 0H21.5A8.5 8.5 0 0 1 30 8.5V30Z" fill="#f4f8fd" stroke="#556" stroke-width=".25"/>
      <path d="M${bord} 4H${30 - bord}" stroke="#d33" stroke-width=".18"/><path d="M0 22.86H30" stroke="#27c" stroke-width=".45"/>
      <path d="M0 30H30" stroke="#d33" stroke-width=".45"/>
      <path d="M13.17 4A1.83 1.83 0 0 0 16.83 4Z" fill="#cfe4ff" stroke="#27c" stroke-width=".12"/><path d="M14.085 4V3H15.915V4" fill="none" stroke="#d33" stroke-width=".22"/>
      <circle cx="8" cy="10" r="4.5" fill="none" stroke="#d33" stroke-width=".14"/><circle cx="22" cy="10" r="4.5" fill="none" stroke="#d33" stroke-width=".14"/>
      <circle cx="8" cy="10" r=".3" fill="#d33"/><circle cx="22" cy="10" r=".3" fill="#d33"/><circle cx="8" cy="24.4" r=".3" fill="#d33"/><circle cx="22" cy="24.4" r=".3" fill="#d33"/>
      <path d="M10.5 30A4.5 4.5 0 0 1 19.5 30" fill="none" stroke="#27c" stroke-width=".14"/>
      ${ADV.map(q => P(q, '#e0701f', .8)).join('')}${p.map(q => P(q, '#1f4fa3', .9)).join('')}</svg>`;
  }
  function pistes(d, nom, nous, buts) {
    const r = [], n = d.n, sy = Object.fromEntries(d.systemes), ht = Object.fromEntries(d.hauteur);
    const p122 = (sy['1-2-2'] || 0) / n, p212 = (sy['2-1-2'] || 0) / n, p131 = (sy['1-3-1'] || 0) / n;
    if (p122 >= .5) r.push(['Battre le premier joueur', `En 1-2-2 (${pc(sy['1-2-2'], n)}), un seul joueur de ${nom} vient sur le porteur : relance rapide avant son arrivée, ou changement de côté derrière la cage, et les deux joueurs de bande sont pris à contre-pied.`]);
    if (p212 >= .15) r.push(['Repérer le 2-1-2', `Quand deux joueurs plongent au fond (${pc(sy['2-1-2'], n)} des cas), le milieu se vide : la passe vers le centre qui remonte est la sortie la plus directe.`]);
    if (p131 >= .15) r.push(['Face au 1-3-1', `${pc(sy['1-3-1'], n)} des cas : la largeur est fermée, chercher la passe dans l'axe ou le dégagement contrôlé.`]);
    let best = null;
    for (const p of ['1', '2', '3']) {
      const t = d.tiers[p]; if (!t || t.n < 6) continue;
      const h = Object.fromEntries(t.hauteur)['haute (dans le coin)'] || 0;
      if (!best || h / t.n > best.r) best = { p, r: h / t.n, h, n: t.n };
    }
    const hg = (ht['haute (dans le coin)'] || 0) / n;
    // buts de l'équipe par tiers (carte des tirs) : le tiers où elle presse le plus haut est-il aussi celui où elle marque le plus ?
    let marque = '';
    if (best && buts && buts.length >= 8) {
      const parT = [1, 2, 3].map(p => buts.filter(b => b.periode === p).length), mx = Math.max(...parT);
      if (parT[best.p - 1] === mx) marque = ` C'est aussi le tiers où ${nom} marque le plus (${mx} de ses ${buts.length} buts placés sur la carte).`;
    }
    if (best && best.r >= hg + .08) r.push([`${T[best.p]} tiers : simple et sûr`, `${nom} presse plus haut au ${T[best.p]} tiers (${best.h} fois sur ${best.n} jusque dans le coin).${marque} Sortir le palet vite, éviter le jeu risqué derrière la cage${best.p === '2' ? ', présences courtes (le banc est loin)' : ''}.`]);
    const tot = Object.values(d.efficacite).reduce((a, e) => ({ s: a.s + (e.sortie || 0), n: a.n + e.n }), { s: 0, n: 0 });
    const e1 = d.efficacite['1-2-2'], e2 = d.efficacite['2-1-2'];
    const comp = e1 && e2 && e2.n >= 8 && e1.n >= 8 && (e2.perte || 0) / e2.n > (e1.perte || 0) / e1.n + .03
      ? ` Le 2-1-2, plus agressif, provoque un peu plus de pertes (${pc(e2.perte || 0, e2.n)} contre ${pc(e1.perte || 0, e1.n)} en 1-2-2), mais sur ${e2.n} cas seulement.` : '';
    if (tot.n >= 10) r.push([tot.s / tot.n >= .65 ? 'Un forecheck qui gêne peu' : 'Un forecheck qui gêne', `L'adversaire est sorti proprement de sa zone ${pc(tot.s, tot.n)} du temps face à ${nom} (${tot.s} fois sur ${tot.n}).${comp}`]);
    return r;
  }
  function rendre(el, data, ab, nom, nous, buts) {
    const d = data && data.equipes && data.equipes[ab];
    if (!d || !d.n) { el.remove(); return; }
    const n = d.n, sy = d.systemes, ht = d.hauteur;
    const haut = ht.filter(([h]) => !/basse/.test(h)).reduce((a, [, x]) => a + x, 0);
    const eff = Object.entries(d.efficacite).sort((a, b) => b[1].n - a[1].n);
    const tot = eff.reduce((a, [, e]) => ({ s: a.s + (e.sortie || 0), n: a.n + e.n }), { s: 0, n: 0 });
    const P = pistes(d, nomP(nom), nous, buts);
    el.innerHTML = `<h2>Forecheck de ${esc(nomP(nom))} <span class="badge b-p">partiel</span> <small class="muted">vidéo</small></h2>
      <div class="tiles fc-t"><div class="tile"><b>${n}</b><span>forechecks vérifiés</span></div>
        <div class="tile"><b>${esc(sy[0][0])} ${pc(sy[0][1], n)}</b><span>système principal</span></div>
        <div class="tile"><b>${pc(haut, n)}</b><span>hauteur haute ou moyenne</span></div>
        <div class="tile"><b>${pc(tot.s, tot.n)}</b><span>de sorties de zone réussies face à eux</span></div></div>
      <div class="fc-g">
        <div class="fc-sch">${sy.filter(([s]) => POS[s]).slice(0, 2).map(([s, x]) => `<figure>${schema(s)}<figcaption><b>${esc(s)}</b> · ${pc(x, n)}</figcaption></figure>`).join('')}</div>
        <div class="fc-tab"><div class="scroll"><table><thead><tr><th>Tiers</th><th class="n">Cas</th><th>Systèmes</th><th>Hauteur dominante</th></tr></thead><tbody>
          ${['1', '2', '3'].map(p => { const t = d.tiers[p] || { n: 0, systemes: [], hauteur: [] }; return `<tr><td>${T[p]}</td><td class="n">${t.n}</td><td>${t.systemes.map(([s, x]) => `${esc(s)} ${x}`).join(' · ')}</td><td>${t.hauteur[0] ? `${esc(court(t.hauteur[0][0]))} (${t.hauteur[0][1]})` : ''}</td></tr>`; }).join('')}
        </tbody></table></div>
        <div class="scroll"><table><thead><tr><th>Face au…</th><th class="n">Cas</th><th class="n">Sortie réussie</th><th class="n">Palet perdu</th></tr></thead><tbody>
          ${eff.map(([s, e]) => `<tr><td>${esc(s)}</td><td class="n">${e.n}</td><td class="n">${pc(e.sortie || 0, e.n)}</td><td class="n">${pc(e.perte || 0, e.n)}</td></tr>`).join('')}
        </tbody></table></div>
        <p class="note">Hauteur du 1er joueur : ${ht.map(([h, x]) => `${esc(court(h))} ${pc(x, n)}`).join(' · ')}.</p></div>
      </div>
      ${P.length ? `<div class="pts">${P.map(([t, x]) => `<div class="ptc e"><b>▲ ${esc(t)}</b><p>${esc(x)}</p></div>`).join('')}</div>` : ''}
      <p class="note">Analyse vidéo de ${d.matchs.map(m => `${esc(nomP(nom))} – ${esc(nomP(m.adversaire || ''))} (${m.date.slice(8, 10)}/${m.date.slice(5, 7)}, ${m.n} cas)`).join(', ')} : situations de relance sous pression repérées automatiquement sur les images, puis vérifiées une à une. Schémas types, pas des positions mesurées. ${d.matchs.length < 2 ? 'Un seul match : à lire comme une tendance.' : ''} Mis à jour le ${esc(data.maj)}.</p>`;
  }
  let cache = null;
  const donnees = () => cache || (cache = fetch('data/forecheck.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).catch(() => null));
  return { rendre, donnees };
})();
