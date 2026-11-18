# Analyse en direct : overlay coach

Pendant que tu regardes le match dans le navigateur (FFHG TV), un moteur sur le PC analyse
l'image affichée à l'écran, et une extension dessine les infos **par-dessus la vidéo**.

- Aucune vidéo n'est enregistrée : chaque image est analysée en mémoire puis oubliée.
- Seules les infos du match sont gardées, dans `matchs/<date>_<équipes>.json` :
  présences, temps de glace, distances, vitesses, positions (1 fois/s), événements.
- La feuille de match est lue sur le site FFHG : équipes, numéros, postes, pénalités.

## Installation (une seule fois, Windows 10 + carte NVIDIA)

1. Installe Python 3.11 depuis python.org (coche « Add Python to PATH »).
2. Ouvre un terminal dans le dossier `hockey_ai` et installe PyTorch pour la carte graphique,
   puis le reste :

   ```bash
   py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
   ```

   ```bash
   py -m pip install -r requirements.txt
   ```

3. Installe l'extension dans Chrome ou Edge :
   - ouvre `chrome://extensions` (ou `edge://extensions`) ;
   - active le **mode développeur** ;
   - clique « Charger l'extension non empaquetée » et choisis le dossier `hockey_ai/extension`.

## Pendant un match

1. Double-clique `direct.bat` et laisse la fenêtre ouverte. Le moteur trouve tout seul le
   match de Brest du jour sur le site FFHG.
2. Ouvre le match sur FFHG TV, **de préférence en plein écran** : les numéros sont plus gros,
   donc mieux lus.
3. Quatre petits carrés magenta clignotent un instant aux coins de la vidéo : c'est le moteur
   qui repère où se trouve la vidéo à l'écran. Ça se refait tout seul si tu passes en plein
   écran ou si tu déplaces la fenêtre (bouton « Recadrer » au besoin).
4. **Premier match à la Rïnkla seulement** : la calibration de la patinoire prend 1 à 2 minutes
   (le point du panneau reste orange). Elle est ensuite gardée dans `patinoires/` et les matchs
   suivants démarrent tout de suite.

Garde la souris hors de la vidéo : la barre de commandes du lecteur gêne l'analyse.

### Le panneau

| Bouton | Rôle |
|---|---|
| **BRE / ORL** | équipe analysée (une seule à la fois) |
| **Équipe** | qui est sur la glace avec le chrono de présence (vert < 40 s, orange < 55 s, rouge au-delà), pression sur les 2 dernières minutes, vitesse comparée au début du match, mini-carte |
| **Joueur** | un seul joueur isolé : vitesse, vitesse max, distance mesurée, présence en cours, nombre et durée moyenne des présences, temps de glace, courbe de vitesse et trajet sur la mini-carte |
| **Choisir** | liste des joueurs de l'équipe analysée (feuille de match) pour isoler un joueur |
| **Identifier** | clique un joueur sur la vidéo et choisis son nom : il est reconnu tout de suite et garde ce nom ensuite |
| **–** ou Alt+H | estompe l'overlay |

## Comment les joueurs sont reconnus

1. Les couleurs de maillot sont apprises dans les premières secondes.
2. Le numéro est lu dans le dos ou sur les manches, **seulement le temps qu'il soit lisible**,
   puis il est gardé : le joueur suivi conserve son nom, même de dos ou de face.
3. S'il disparaît (caché derrière un autre, sorti du champ) puis réapparaît au même endroit,
   il retrouve son nom sans relire le numéro.
4. Seuls les numéros présents sur la feuille de match sont acceptés, et un numéro n'est porté
   que par un joueur à la fois.
5. Si un numéro reste illisible, « Identifier » + un clic suffit.

## Tester sur un replay

Le replay FFHG TV fonctionne exactement comme le direct. Comme ce n'est pas le match du jour,
donne le numéro du match (il est dans l'adresse de la page FFHG, `.../rencontre/84389/`) :

```bash
py live_engine.py --match 84389
```

## Limites à connaître

- **Distances** : comptées seulement quand le joueur est à l'écran (« m mesurés »).
- **Présences** : la caméra ne montre qu'une partie de la glace et les changements se font
  souvent hors champ ; le début d'une présence peut avoir quelques secondes de décalage.
- **Numéros** : la lecture automatique est la partie la plus fragile (images compressées,
  filet de protection). Plein écran et quelques clics « Identifier » en début de match aident
  beaucoup.
- **Palet** : pas encore suivi (prévu dans une étape suivante, avec un modèle dédié).
- **Overlay** : le moteur ignore la zone des panneaux, mais les étiquettes au-dessus des joueurs
  sont visibles à l'écran : garde-les petites (Alt+H pour les estomper).

## Annoter un extrait (3 minutes) pour rendre l'outil plus précis

1. Capture : lance le replay (ou le direct) et le moteur avec l'option `--annoter` :

   ```bash
   py live_engine.py --match 84389 --annoter 180
   ```

   Une image est enregistrée toutes les 1,5 s pendant 180 s, avec le time code du bandeau et
   les joueurs détectés (dans `annotations/`, sur ton PC uniquement).
2. Annotation : `py annoter.py` ouvre une page dans le navigateur.
   - clique un joueur encadré et choisis son nom (ou « n° inconnu », « Arbitre », « Pas un joueur ») ;
   - trace un cadre autour d'un joueur oublié par le détecteur ;
   - le nom se propage aux autres images du même joueur (pointillé = proposé) : corrige ce qui est faux ;
   - flèches ← → pour changer d'image ; tout est enregistré automatiquement.
3. **Exporter** (bouton en haut à droite) :
   - les identités vont dans la mémoire du match : à la prochaine lecture, les noms collent à la
     vidéo d'après le time code ;
   - vignettes pour `py train_numeros.py` (reconnaissance des joueurs) et pour un futur
     entraînement des équipes et du détecteur (`dataset_equipes/`, `dataset_detection/`).

## Réglages utiles

| Option | Rôle |
|---|---|
| `--match 84389` | force le match FFHG (replay, match d'une autre équipe) |
| `--equipe BREST` | équipe dont on cherche le match du jour |
| `--rink iihf` / `iihf_26` | force les dimensions (sinon déduites à la première calibration) |
| `--recalib` | refait la calibration de la patinoire |
| `--our-color sombre` | maillot de ton équipe, si les numéros ne suffisent pas à le deviner |
| `--fps 8` | moins d'images analysées par seconde (PC plus lent) |
| `--weights yolov8m.pt` | détecteur plus précis (plus lourd) |
