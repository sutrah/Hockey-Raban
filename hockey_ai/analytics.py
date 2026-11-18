"""Métriques tactiques : vitesse, distance, contrôle d'espace (Voronoï),
espacement (enveloppe convexe), espace libre, équipe en attaque."""
from collections import deque

import numpy as np
from scipy.spatial import ConvexHull, cKDTree

MAX_SPEED_KMH = 45.0     # au-delà : bruit d'homographie
GOALIE_RADIUS = 2.6      # m autour du centre de la cage -> considéré gardien


class Track:
    __slots__ = ("tid", "team", "hist", "pos", "speed", "dist", "last_t",
                 "top", "seen", "_last_dist_pos")

    def __init__(self, tid, team):
        self.tid, self.team = tid, team
        self.hist = deque(maxlen=60)
        self.pos = None
        self.speed = 0.0
        self.dist = 0.0
        self.last_t = -1
        self.top = 0.0
        self.seen = 0
        self._last_dist_pos = None


class Analytics:
    def __init__(self, rink, fps, team_names, attack_dirs=None, grid_res=0.5):
        self.rink, self.fps = rink, fps
        self.names = team_names
        self.tracks = {}
        # attack_dirs[k] = +1 si l'équipe k attaque vers la droite (x>0)
        self.attack_dirs = attack_dirs
        self.defend_votes = np.zeros((2, 2))  # [équipe, côté(0=gauche,1=droite)]
        xs = np.arange(-rink.hx + grid_res / 2, rink.hx, grid_res)
        ys = np.arange(-rink.hy + grid_res / 2, rink.hy, grid_res)
        self.gx, self.gy = np.meshgrid(xs, ys)
        self.gmask = rink.inside_mask(self.gx, self.gy)
        self.gpts = np.stack([self.gx[self.gmask], self.gy[self.gmask]], 1)
        self.attacking = None
        self.possessions = 0
        self.frames_tracked = 0
        self.acc = {k: dict(zone_ctrl=[], open=[], hull=[]) for k in (0, 1)}
        self.cur = {}
        self.voronoi = None
        self.retired_dist = [0.0, 0.0]
        self.retired_top = (0.0, 0)

    # ------------------------------------------------------------- per frame
    def update(self, t_idx, players):
        """players : liste (tid, team, x, y) en mètres, team ∈ {0,1} (arbitres exclus)."""
        dt = 1.0 / self.fps
        visible = []
        for tid, team, x, y in players:
            tr = self.tracks.get(tid)
            if tr is None or (t_idx - tr.last_t) > self.fps:   # piste reprise après >1 s
                if tr is not None:
                    self.retired_dist[tr.team] += tr.dist
                    if tr.top > self.retired_top[0]:
                        self.retired_top = (tr.top, tr.team)
                tr = self.tracks[tid] = Track(tid, team)
            tr.team = team
            # lissage exponentiel de la position
            if tr.pos is None:
                tr.pos = np.array([x, y], float)
            else:
                a = 0.35
                tr.pos = (1 - a) * tr.pos + a * np.array([x, y])
            tr.hist.append((t_idx, tr.pos.copy()))
            tr.last_t = t_idx
            tr.seen += 1
            # vitesse sur une fenêtre de ~0,5 s
            win = max(3, int(0.5 * self.fps))
            if len(tr.hist) > win:
                t0, p0 = tr.hist[-win - 1]
                v = np.linalg.norm(tr.pos - p0) / ((t_idx - t0) * dt) * 3.6
                v = min(v, MAX_SPEED_KMH)
                tr.speed = 0.7 * tr.speed + 0.3 * v
                if tr.seen > self.fps:   # top speed seulement sur pistes stables
                    tr.top = max(tr.top, tr.speed)
            # distance : échantillonnée tous les ~0,2 s pour éviter de cumuler le bruit
            if tr._last_dist_pos is None:
                tr._last_dist_pos = tr.pos.copy()
            elif tr.seen % max(1, int(0.2 * self.fps)) == 0:
                step = np.linalg.norm(tr.pos - tr._last_dist_pos)
                if step < MAX_SPEED_KMH / 3.6 * 0.25:
                    tr.dist += step
                tr._last_dist_pos = tr.pos.copy()
            visible.append(tr)

        self._metrics(visible)
        return visible

    def _is_goalie(self, p):
        g = self.rink.goal_x
        return min(np.hypot(p[0] - g, p[1]), np.hypot(p[0] + g, p[1])) < GOALIE_RADIUS

    def _metrics(self, visible):
        R = self.rink
        cur = {"control": None, "zone_control": None, "hull": [0.0, 0.0],
               "open": None, "dist": [0.0, 0.0], "count": [0, 0]}
        cur["dist"] = list(self.retired_dist)
        for tr in self.tracks.values():
            cur["dist"][tr.team] += tr.dist
        pts = {0: [], 1: []}
        for tr in visible:
            pts[tr.team].append(tr.pos)
            cur["count"][tr.team] += 1

        # --- votes "quelle équipe défend quel côté" (le joueur le plus proche d'une cage)
        for side, gx in ((0, -R.goal_x), (1, R.goal_x)):
            best, bd = None, 4.0
            for tr in visible:
                d = np.hypot(tr.pos[0] - gx, tr.pos[1])
                if d < bd:
                    best, bd = tr, d
            if best is not None:
                self.defend_votes[best.team, side] += 1
        dirs = self.attack_dirs or self._infer_dirs()

        # --- zone de jeu et équipe en attaque
        allp = [tr.pos for tr in visible]
        if allp and dirs:
            mx = float(np.mean([p[0] for p in allp]))
            zone = 1 if mx > R.blue_x * 0.8 else (-1 if mx < -R.blue_x * 0.8 else 0)
            if zone != 0:
                att = 0 if dirs[0] == zone else 1
                if att != self.attacking:
                    self.possessions += 1
                    self.attacking = att
        cur["attacking"] = self.attacking
        cur["dirs"] = dirs

        # --- Voronoï : chaque case de glace -> équipe du joueur le plus proche
        self.voronoi = None
        if len(pts[0]) >= 2 and len(pts[1]) >= 2:
            P = np.array(pts[0] + pts[1])
            lab = np.array([0] * len(pts[0]) + [1] * len(pts[1]))
            _, nn = cKDTree(P).query(self.gpts)
            owner = lab[nn]
            grid = np.full(self.gx.shape, -1, np.int8)
            grid[self.gmask] = owner
            self.voronoi = grid
            cur["control"] = [float((owner == 0).mean()), float((owner == 1).mean())]
            if self.attacking is not None and dirs:
                s = dirs[self.attacking]
                zx = self.gpts[:, 0] * s > R.blue_x          # zone offensive de l'attaquant
                if zx.any():
                    zc = float((owner[zx] == self.attacking).mean())
                    zz = [0.0, 0.0]
                    zz[self.attacking], zz[1 - self.attacking] = zc, 1 - zc
                    cur["zone_control"] = zz

        # --- espacement : aire de l'enveloppe convexe (gardiens exclus)
        for k in (0, 1):
            sk = [p for p in pts[k] if not self._is_goalie(p)]
            if len(sk) >= 3:
                try:
                    cur["hull"][k] = float(ConvexHull(np.array(sk)).volume)
                except Exception:
                    pass

        # --- espace libre : distance moyenne attaquant -> défenseur le plus proche
        if self.attacking is not None:
            a, d = self.attacking, 1 - self.attacking
            att = [p for p in pts[a] if not self._is_goalie(p)]
            dfd = pts[d]
            if att and dfd:
                dd, _ = cKDTree(np.array(dfd)).query(np.array(att))
                cur["open"] = float(np.mean(dd))

        # --- cumul pour le récap
        if visible:
            self.frames_tracked += 1
        if self.attacking is not None:
            a = self.attacking
            if cur["zone_control"] is not None:
                self.acc[a]["zone_ctrl"].append(cur["zone_control"][a])
            if cur["open"] is not None:
                self.acc[a]["open"].append(cur["open"])
        self.cur = cur

    def _infer_dirs(self):
        v = self.defend_votes
        if v.sum() < 2 * self.fps:        # pas encore assez d'indices
            return None
        # équipe 0 défend le côté où elle a le plus de votes relatifs
        score0 = (v[0, 0] - v[1, 0]) - (v[0, 1] - v[1, 1])
        d0 = +1 if score0 > 0 else -1      # défend à gauche -> attaque à droite
        return {0: d0, 1: -d0}

    # ----------------------------------------------------------------- recap
    def recap(self):
        top = max(self.tracks.values(), key=lambda t: t.top, default=None)
        best = (top.top, top.team) if top else (0.0, 0)
        if self.retired_top[0] > best[0]:
            best = self.retired_top
        dist = list(self.retired_dist)
        for tr in self.tracks.values():
            dist[tr.team] += tr.dist
        r = {"dist": dist, "possessions": self.possessions,
             "seconds": self.frames_tracked / self.fps,
             "top_speed": best,
             "zone_ctrl": [np.mean(self.acc[k]["zone_ctrl"]) if self.acc[k]["zone_ctrl"] else None for k in (0, 1)],
             "open": [np.mean(self.acc[k]["open"]) if self.acc[k]["open"] else None for k in (0, 1)]}
        return r
