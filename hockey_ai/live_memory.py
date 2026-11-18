"""Mémoire du match : qui est qui, à chaque instant de jeu, d'un visionnage à l'autre.

Repère temporel = chrono du bandeau TV (temps de jeu écoulé, au 1/4 de seconde) ; pendant un
arrêt de jeu (chrono figé), on ajoute le temps écoulé depuis l'arrêt.

Pour chaque instant on garde, pour les joueurs identifiés : camp, numéro, position à l'écran
(pieds, normalisée), hauteur, source ("main" ou "auto"), heure de l'identification.

Règles de priorité à la relecture :
  1. identification faite à la main pendant CETTE séance (la dernière l'emporte) ;
  2. identification à la main enregistrée (la plus récente l'emporte) ;
  3. identification automatique enregistrée ;
  4. lecture automatique du numéro.
Une identification à la main corrige aussi, en arrière, toute la séquence du joueur.
Aucune image n'est enregistrée.
"""
import json
import os
import time

Q = 4            # 4 pas par seconde de jeu


def tc_key(game_s, frac, stop_s=None):
    """Clé d'instant : quart de seconde de jeu, + demi-secondes d'arrêt de jeu."""
    k = str(int(round((game_s + frac) * Q)))
    if stop_s is not None and stop_s > 0.6:
        k += "s" + str(int(stop_s * 2))
    return k


class MatchMemory:
    def __init__(self, path, log=print):
        self.path, self.log = path, log
        self.frames = {}         # clé -> [[camp, num, x, y, h, src, ts, séance, tid]]
        self.session = str(int(time.time()))
        self.dirty = False
        self.last_save = time.time()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.frames = json.load(f).get("frames", {})
                n_man = sum(1 for v in self.frames.values() for e in v if e[5] == "main")
                log(f"[mémoire] {len(self.frames)} instants déjà connus pour ce match "
                    f"({n_man} identifications à la main)")
            except Exception as e:
                log(f"[mémoire] fichier illisible ({e}), on repart de zéro")
        self.track_keys = {}     # tid (séance) -> [clés où il a été enregistré]

    # ------------------------------------------------------------ écriture
    def record(self, key, entries):
        """entries : [(camp, num, x, y, h, src, tid)] pour cet instant (séance courante)."""
        now = time.time()
        old = [e for e in self.frames.get(key, []) if e[7] != self.session]
        new = []
        for side, num, x, y, h, src, tid in entries:
            new.append([side, num, round(x, 4), round(y, 4), round(h, 4), src, round(now, 1), self.session, tid])
            self.track_keys.setdefault(tid, []).append(key)
        self.frames[key] = old + new
        self.dirty = True

    def correct_track(self, tid, side, num):
        """Correction à la main : toute la séquence déjà vue de ce joueur prend ce nom."""
        now = round(time.time(), 1)
        n = 0
        for key in set(self.track_keys.get(tid, [])):
            for e in self.frames.get(key, []):
                if e[7] == self.session and e[8] == tid:
                    e[0], e[1], e[5], e[6] = side, num, "main", now
                    n += 1
        # le même numéro ne peut pas être porté par un autre joueur au même instant
        for key in set(self.track_keys.get(tid, [])):
            self.frames[key] = [e for e in self.frames.get(key, [])
                                if not (e[0] == side and e[1] == num and not (e[7] == self.session and e[8] == tid))]
        self.dirty = True
        return n

    # ------------------------------------------------------------ lecture
    def lookup(self, game_s, frac, stop_s=None, window=2):
        """Entrées enregistrées autour de cet instant (± window quarts de seconde)."""
        base = int(round((game_s + frac) * Q))
        suffix = ""
        if stop_s is not None and stop_s > 0.6:
            suffix = "s" + str(int(stop_s * 2))
        out = []
        for d in range(-window, window + 1):
            out += self.frames.get(str(base + d) + suffix, [])
        return out

    @staticmethod
    def best_identity(entries):
        """Parmi des entrées concurrentes pour un même joueur : main la plus récente, sinon auto la plus récente."""
        man = [e for e in entries if e[5] == "main"]
        pool = man or entries
        return max(pool, key=lambda e: e[6]) if pool else None

    # ------------------------------------------------------------ disque
    def save(self, force=False):
        if not self.dirty or (not force and time.time() - self.last_save < 30):
            return
        self.last_save = time.time()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(dict(version=1, frames=self.frames), f, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False
