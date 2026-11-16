# Hockey Raban

Mini-site d'aide au coaching pour les Albatros de Brest (Division 2, saison 2026-27, avec l'historique 2024-25 et 2025-26).
Toutes les données viennent des feuilles de match publiées par la FFHG et tiennent dans un seul fichier SQLite.

## Contenu

- `docs/` : le site (statique). `app.js` calcule les statistiques, `analyse.js` produit analyses et préconisations. Il lit `docs/data/d2.sqlite` directement dans le navigateur.
  - Accueil : prochain match, points clés de l'adversaire, classement de la poule.
  - Fiche de Brest : analyse des tendances par rapport au reste de la D2 et axes de progression.
  - Fiche adversaire : préconisations face à cette équipe, joueurs absents, historique des confrontations.
  - Toutes les fiches : buts par tranche de 5 minutes, scénarios de match,
    situations spéciales, joueurs, duos, discipline, gardiens, tirs et engagements. Bouton « Imprimer » pour le vestiaire.
- `scrape.py` : télécharge les matchs joués depuis hockeyfrance.com vers `data/raw/`.
- `build_db.py` : reconstruit `docs/data/d2.sqlite` à partir de `data/raw/`.

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

- Tirs et engagements ne sont saisis en détail qu'aux matchs joués à Brest.
- Les joueurs présents sur la glace lors d'un but ne sont pas toujours tous notés : le +/− est une tendance.
- Les occasions de supériorité sont estimées à partir des pénalités mineures.
