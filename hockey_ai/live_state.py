"""Calculs pour le banc : cinématique par joueur, présences sur la glace, pression, enregistrement."""
import json
import os
import time
from collections import deque

import numpy as np

MAX_KMH = 45.0
SHIFT_GAP = 30.0        # s sans être vu -> sorti de la glace, la présence est terminée
SKATERS = 5


class Kin:
    """Position lissée, vitesse (km/h), distance (m) d'un joueur."""

    def __init__(self):
        self.pos = None
        self.hist = deque(maxlen=40)
        self.speed = 0.0
        self.top = 0.0
        self.dist = 0.0
        self.seen = 0.0
        self.last_t = None

    def update(self, t, xy):
        xy = np.asarray(xy, float)
        if self.last_t is not None and t - self.last_t > 1.0:     # trou : on repart sans compter le saut
            self.pos, self.hist = None, deque(maxlen=40)
        if self.pos is None:
            self.pos = xy
        else:
            step = np.linalg.norm(xy - self.pos) * 0.35
            self.pos = self.pos + 0.35 * (xy - self.pos)
            if step > 0.02:
                self.dist += step
        if self.last_t is not None:
            self.seen += min(t - self.last_t, 0.5)
        self.last_t = t
        self.hist.append((t, self.pos.copy()))
        old = [h for h in self.hist if t - h[0] >= 0.5]
        if old:
            t0, p0 = old[-1]
            v = min(np.linalg.norm(self.pos - p0) / max(t - t0, 1e-3) * 3.6, MAX_KMH)
            self.speed = 0.7 * self.speed + 0.3 * v
            if self.seen > 1.0:
                self.top = max(self.top, self.speed)
        return self


class Shifts:
    """Qui est sur la glace et depuis combien de temps (temps de jeu)."""

    def __init__(self, goalies=()):
        self.goalies = set(goalies)
        self.on = {}            # num -> dict(start, last_game, last_wall)
        self.done = []          # (num, début, fin)

    def seen(self, num, game_t, wall_t):
        if num in self.goalies:
            return
        s = self.on.get(num)
        if s is None:
            self.on[num] = dict(start=game_t, last_game=game_t, last_wall=wall_t)
        else:
            s["last_game"], s["last_wall"] = game_t, wall_t

    def tick(self, wall_t):
        for num, s in list(self.on.items()):
            if wall_t - s["last_wall"] > SHIFT_GAP:
                self._end(num)
        if len(self.on) > SKATERS:          # plus de 5 joueurs : le moins récemment vu est sorti
            for num in sorted(self.on, key=lambda n: self.on[n]["last_wall"])[: len(self.on) - SKATERS]:
                self._end(num)

    def _end(self, num):
        s = self.on.pop(num)
        self.done.append((num, s["start"], s["last_game"]))

    def current(self, game_t):
        return {n: max(0.0, game_t - s["start"]) for n, s in self.on.items()}

    def stats(self, num):
        sh = [(b - a) for n, a, b in self.done if n == num and b > a]
        return dict(count=len(sh), total=sum(sh), avg=(sum(sh) / len(sh) if sh else 0.0))


class Pressure:
    """Temps passé par zone sur les 2 dernières minutes + vitesse moyenne vs début de match."""

    def __init__(self, rink, window=120.0):
        self.rink, self.window = rink, window
        self.samples = deque()          # (t, zone, v_our, v_them) ; zone : +1 off, 0 neutre, -1 def
        self.defend_votes = np.zeros(2)  # notre gardien vu à gauche (0) / à droite (1)
        self.base = {"our": [], "them": []}
        self.t_start = None

    def goalie_seen(self, x, weight=1):
        """x : position (m) d'un gardien de NOTRE équipe (ou opposée à celle d'un gardien adverse)."""
        self.defend_votes[0 if x < 0 else 1] += weight

    def reset_side(self):
        self.defend_votes[:] = 0

    @property
    def defend_sign(self):
        if self.defend_votes.sum() < 15:
            return None
        return -1 if self.defend_votes[0] > self.defend_votes[1] else +1

    def add(self, t, xs, speeds):
        if self.t_start is None:
            self.t_start = t
        zone = None
        if xs and self.defend_sign is not None:
            px = float(np.median(xs)) * self.defend_sign      # >0 : côté de notre cage
            bx = self.rink.blue_x
            zone = -1 if px > bx else (1 if px < -bx else 0)
        vo = np.mean(speeds["our"]) if speeds["our"] else None
        vt = np.mean(speeds["them"]) if speeds["them"] else None
        self.samples.append((t, zone, vo, vt))
        if t - self.t_start < 300:
            for k, v in (("our", vo), ("them", vt)):
                if v is not None:
                    self.base[k].append(v)
        while self.samples and t - self.samples[0][0] > self.window:
            self.samples.popleft()

    def state(self):
        zs = [s[1] for s in self.samples if s[1] is not None]
        n = max(len(zs), 1)
        out = dict(off=zs.count(1) / n, neu=zs.count(0) / n, dfd=zs.count(-1) / n, known=bool(zs))
        fat = {}
        for k, i in (("our", 2), ("them", 3)):
            cur = [s[i] for s in self.samples if s[i] is not None]
            if len(cur) > 20 and len(self.base[k]) > 50:
                fat[k] = float(np.mean(cur) / max(np.mean(self.base[k]), 1e-3))
        out["fatigue"] = fat
        return out


class Recorder:
    """Garde uniquement les infos du match (aucune image) et les écrit en JSON."""

    def __init__(self, folder, name):
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, name + ".json")
        self.positions = []         # 1 échantillon / s : [t_jeu, [[camp, num, x, y], ...]]
        self.events = []
        self.last_pos_t = 0.0
        self.last_save = time.time()

    def positions_tick(self, wall_t, game_t, items):
        if wall_t - self.last_pos_t >= 1.0 and items:
            self.last_pos_t = wall_t
            self.positions.append([round(game_t, 1), [[s, n, round(x, 1), round(y, 1)] for s, n, x, y in items]])

    def event(self, game_t, kind, **kw):
        self.events.append(dict(t=game_t, type=kind, **kw))

    def save(self, payload, force=False):
        if not force and time.time() - self.last_save < 60:
            return
        self.last_save = time.time()
        payload = dict(payload, positions=self.positions, events_detectes=self.events)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.path)
