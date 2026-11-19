# Contexte du projet (à lire par Claude au début de chaque session)

Outil d'analyse vidéo de hockey sur glace pour le staff de Brest (matchs à domicile à la
Rïnkla Stadium, patinoire 60×30 m). L'utilisateur parle français et n'est pas développeur :
réponses simples, pas à pas, commandes Windows (`py` = Python 3.14, Windows 10).
PC : i7-4790, 16 Go, RTX 3070. Dossier de travail : `C:\HRB4\hockey_ai`.

## Ce qui existe
- `live_engine.py` : analyse en direct de l'écran (flux FFHG TV dans le navigateur), overlay
  dessiné par l'extension Chrome `extension/` (WebSocket 127.0.0.1:8765). YOLOv8s-pose +
  BoT-SORT, équipes par couleur (Brest maillot noir = sombre ; Orléans maillot blanc,
  écriture jaune, culotte rouge = clair), numéros par EasyOCR, chrono/score lus sur le bandeau.
  Feuille de match lue sur le site FFHG (`live_ffhg.py`). Match de test : `--match 84389`
  (Brest – Orléans).
- `live_memory.py` : mémoire du match par instant de jeu (chrono du bandeau au 1/4 s),
  `matchs/memoire_84389.json`. Priorité : identification à la main la plus récente.
- `annoter.py` + `annoter.html` : outil d'annotation dans le navigateur. Séance faite par
  l'utilisateur : `annotations/84389/20261118_191507` (113 images, 1 toutes les 1,5 s,
  passage P1 17:52 → P1 15:42, 377 identifications). **Ne jamais écraser ce travail.**
- `preparer_video.py` : réduit une vidéo en 720p et la découpe en morceaux < 24 Mo.
- `autocalib.py` : calibration automatique de la patinoire (encore peu fiable sur le vrai
  flux : patinoire repérée ~10 % du temps).

## Règles voulues par l'utilisateur
- Arbitres : jamais reconnus automatiquement, seulement désignés à la main ; affichés en
  encadré vert « ARB ». Faux positifs : « Pas un joueur » / touche X.
- Encadrés (pas de squelettes) ; un joueur identifié garde son nom, oublié après 30 s hors glace.
- Une seule équipe analysée à la fois (BRE/ORL).
- Rien n'est enregistré en direct sauf les données (sauf demande explicite, ex. extrait vidéo).

## Problèmes connus en direct
- 5–7 images/s seulement, pistes de suivi qui se cassent (15–25 nouvelles pistes / 10 s),
  noms qui sautent d'un joueur à l'autre, détecteur qui rate les joueurs flous au fond.

## Objectif actuel
Traiter HORS DIRECT un extrait de 2–3 minutes du même match (le passage annoté), enregistré
par l'utilisateur avec Win+Alt+R et placé dans `videos/` :
1. retrouver dans la vidéo les 113 images annotées (comparaison d'images) et y recoller les
   noms ; propager en avant/arrière avec un suivi hors ligne (détecteur plus gros, toutes les
   images) ;
2. détecter le palet (difficile : petit, flou ; prévoir clics de l'utilisateur + interpolation
   si l'automatique ne suffit pas) ;
3. produire une vidéo MP4 annotée (encadrés couleur d'équipe, noms, arbitres en vert, palet)
   à montrer, + un fichier de positions.

Branche git : `claude/gifted-cerf-9qliog` (dépôt sutrah/Hockey-Raban).
