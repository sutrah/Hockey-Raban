# Hockey Raban

Mini-site d'aide au coaching pour les Albatros de Brest (Division 2, saison 2026-27, avec l'historique 2024-25 et 2025-26).
Toutes les données viennent des feuilles de match publiées par la FFHG et tiennent dans un seul fichier SQLite.

## Contenu

- `docs/` : le site (statique). Il lit `docs/data/d2.sqlite` directement dans le navigateur.
  - **Accueil** : prochain match avec trois points à exploiter et trois points de vigilance, classement.
  - **Fiche de match** : une page A4 imprimable (nous contre eux, joueurs à surveiller, trios, absents).
  - **Débrief** : ce qui a marché, ce qui a posé problème, et ce que la fiche annonçait comparé à ce qui s'est passé.
  - **Brest / fiche équipe** : position parmi les 20 équipes de D2, axes de progression, lignes probables,
    filtre saison / 5 derniers matchs / domicile / extérieur. Les tableaux chiffrés sont repliés par défaut.
  - **Lignes** : comparateur d'associations de joueurs réellement observées sur les buts.
  - **Analyses vidéo IA** : onglet d'attente, rien n'y est encore calculé.
- Code du site : `app.js` (données et statistiques), `analyse.js` (tendances, lignes, préconisations),
  `visuels.js` (graphiques), `pages.js` (pages et navigation).
- `scrape.py` : télécharge les matchs joués depuis hockeyfrance.com vers `data/raw/`.
- `build_db.py` : reconstruit `docs/data/d2.sqlite` à partir de `data/raw/`.
- `hockey_ai/` : analyse tactique d'une vidéo de match (suivi des joueurs, équipes, vue tactique,
  stats). Tout est automatique : `python hockey_analytics.py match.mp4` (voir `hockey_ai/README.md`).

Chaque encart porte un badge : « mesuré » (compté sur les feuilles), « partiel » (saisi sur une partie des
matchs), « déduit » (calculé par recoupement).

## Mettre à jour après une journée

```bash
python3 scrape.py
```

```bash
python3 build_db.py
```

Puis enregistrer (commit) et pousser les fichiers modifiés. Python 3 suffit, aucune dépendance.

## Voir le site

En local :

```bash
python3 -m http.server 8000 --directory docs
```

puis ouvrir http://localhost:8000. En ligne : activer GitHub Pages sur le dossier `/docs`.

## Limites des données

- Tirs et engagements ne sont saisis en détail que sur une partie des matchs (cette saison, ceux joués à Brest).
- Les joueurs présents sur la glace sont notés sur 91 % des buts marqués mais 28 % des buts encaissés :
  le +/− et les buts contre des lignes sont masqués pour les équipes mal renseignées.
- Les supériorités sont reconstruites à partir du début et de la fin de chaque pénalité ; 92 % des buts
  marqués en supériorité tombent bien dans une séquence ainsi calculée.
- Lignes et paires sont déduites des buts : aucun temps de glace n'est publié.
- Les notes du staff restent dans le navigateur de l'appareil où elles sont saisies.
