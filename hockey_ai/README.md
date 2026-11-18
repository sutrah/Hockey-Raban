# HOCKEY AI ANALYTICS

Analyse tactique d'une vidéo de match de hockey sur glace, même rendu que la vidéo basket :
suivi des joueurs, identification des équipes, calibration caméra, vue tactique
avec contrôle d'espace (Voronoï), stats en direct et récap final. Sortie verticale 1080×1350.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```
Les poids YOLO (`yolov8s-seg.pt`) se téléchargent tout seuls au premier lancement.

## Utilisation : donne juste la vidéo

```bash
python hockey_analytics.py match.mp4
```

C'est tout. L'outil produit `match_analyse.mp4` à côté de la vidéo. Sans argument
(`python hockey_analytics.py`), une fenêtre s'ouvre pour choisir la vidéo. Sous Windows, tu
peux aussi glisser la vidéo sur `analyser.bat`.

Ce qui se fait tout seul :
- **calibration de la caméra** : l'outil repère la glace, les lignes rouges et bleues et les
  cercles, et en déduit la position de la caméra (≈ 30 s au début), puis suit le
  panoramique et le zoom image par image. Après un replay ou un changement de plan, il se
  recale tout seul. Le résultat est gardé dans `match.autocalib.json` (relancer l'analyse est
  donc immédiat ; `--recalib` pour la refaire) ;
- **couleurs des équipes** : apprises sur les premières secondes ;
- **sens d'attaque** : déduit de la position des gardiens ;
- **calcul** : GPU NVIDIA ou Mac Apple Silicon utilisés s'ils sont présents.

Pour avoir les vrais noms à l'écran :

```bash
python hockey_analytics.py match.mp4 --teams BREST ANGERS
```

Patinoire étroite (60×26, fréquent en France) : ajoute `--rink iihf_26`.

### Si la calibration automatique échoue

Elle a besoin d'une vue de côté, en hauteur, où la glace et ses lignes sont visibles
(caméra TV ou caméra fixe en tribune). Pour une vue derrière une cage ou une image très
déformée (fish-eye), calibre à la main une fois :

```bash
python calibrate.py --video match.mp4 --rink iihf --out calib.json
python hockey_analytics.py match.mp4 --calib calib.json
```

Dans `calibrate.py` : clique au moins 4 repères bien répartis (`n`/`p` pour changer de repère),
`k` enregistre l'image clé, `d`/`a` = ±1 s, `q` = enregistrer et quitter.

## Options

| Option | Rôle |
|---|---|
| `--teams A B` | noms affichés |
| `--out fichier.mp4` | vidéo produite (défaut : `<vidéo>_analyse.mp4`) |
| `--calib calib.json` | calibration manuelle au lieu de l'automatique |
| `--recalib` | refait la calibration automatique |
| `--rink` | `iihf` (défaut), `iihf_26`, `nhl` ou un JSON perso |
| `--jerseys HEX HEX` | vraies couleurs des maillots → fixe quelle équipe est A ou B |
| `--colors HEX HEX` | couleurs de l'interface (défaut bleu / ambre) |
| `--attack A:droite` | sens d'attaque (sinon déduit de la position des gardiens) |
| `--start / --end` | extrait à analyser (secondes) |
| `--device` | auto par défaut ; sinon `mps` (Mac M1–M4), `0` (GPU NVIDIA), `cpu` |
| `--weights` | `yolov8s.pt` par défaut ; `yolov8m.pt` = plus précis, plus lent |
| `--imgsz` | 1280 par défaut (joueurs petits en plan large) ; 960 = plus rapide |
| `--no-seg` | pas de teinte des joueurs, plus rapide |
| `--debug-lines` | dessine les lignes projetées sur la vidéo pour vérifier la calibration |
| `--export stats.csv` | positions (m), vitesse, distance par joueur et par frame |
| `--lang en` | interface en anglais |

## Ce qui est calculé

- **Vitesse** (km/h) : position lissée sur la glace, fenêtre de 0,5 s, plafonnée à 45 km/h.
- **Distance parcourue** : cumul par équipe des joueurs visibles.
- **Contrôle d'espace** : Voronoï sur la glace ; la stat porte sur la zone offensive de
  l'équipe qui attaque (l'équivalent du « frontcourt »).
- **Espacement** : aire de l'enveloppe convexe des joueurs de champ (gardiens exclus).
- **Espace libre** : écart moyen entre chaque attaquant et le défenseur le plus proche.
- **Équipe en attaque** : zone où se trouve le jeu + côté défendu par chaque gardien.
- **Arbitres** : repérés par les rayures noir/blanc et exclus des stats.

## Limites à connaître

- Pas de suivi du palet (trop petit et trop rapide pour YOLO générique) : la
  « possession » est déduite de la zone où se trouve le jeu.
- Seuls les joueurs visibles à l'écran sont pris en compte (comme sur la vidéo basket).
- Les identifiants des joueurs peuvent changer après une occlusion ou un changement de ligne.
  Les distances par équipe restent justes, mais pas forcément celles par joueur.
- La précision dépend de la calibration. Vérifie-la avec `--debug-lines` : les lignes blanches
  dessinées doivent coller aux vraies lignes de la glace. Sur la vidéo de test, la calibration
  automatique place les joueurs à ~30 cm près.
- La calibration automatique suppose une caméra de côté (TV ou tribune). Les grands logos
  colorés sous la glace peuvent la gêner : dans ce cas, `--calib`.

## Test sans vraie vidéo

```bash
python tests/make_synthetic.py synth
python hockey_analytics.py synth/synthetic.mp4 --gt synth/gt.json --teams BREST ANGERS --debug-lines
```
