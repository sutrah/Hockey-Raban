"""Géométrie de la patinoire (repère monde en mètres).

Repère : origine au point d'engagement central.
  x : sens de la longueur (gauche -> droite à l'écran de la vue tactique)
  y : sens de la largeur, +y = bande du fond (côté opposé à la caméra principale)
"""
import json
import math

import numpy as np

PRESETS = {
    # IIHF (Rule Book) : 60 x 30, lignes de but à 4 m, bleues à 22,86 m des bandes
    "iihf": dict(length=60.0, width=30.0, corner_radius=8.5, goal_line=4.0,
                 blue_from_end=22.86, circle_r=4.5, end_dot_from_goal=6.0,
                 dot_y=7.0, neutral_dot_from_blue=1.5, crease_r=1.83),
    # Patinoire européenne étroite (beaucoup de patinoires françaises)
    "iihf_26": dict(length=60.0, width=26.0, corner_radius=8.5, goal_line=4.0,
                    blue_from_end=22.86, circle_r=4.5, end_dot_from_goal=6.0,
                    dot_y=6.71, neutral_dot_from_blue=1.5, crease_r=1.83),
    # NHL : 200 x 85 ft
    "nhl": dict(length=60.96, width=25.91, corner_radius=8.53, goal_line=3.35,
                blue_from_end=22.86, circle_r=4.57, end_dot_from_goal=6.10,
                dot_y=6.71, neutral_dot_from_blue=1.52, crease_r=1.83),
}


class Rink:
    def __init__(self, spec="iihf"):
        if isinstance(spec, str):
            if spec in PRESETS:
                d = dict(PRESETS[spec])
            else:  # chemin vers un JSON perso
                with open(spec) as f:
                    d = json.load(f)
        else:
            d = dict(spec)
        self.__dict__.update(d)
        self.L, self.W = self.length, self.width
        self.hx, self.hy = self.L / 2, self.W / 2
        self.r = self.corner_radius
        self.goal_x = self.hx - self.goal_line          # |x| des lignes de but
        self.blue_x = self.hx - self.blue_from_end      # |x| des lignes bleues
        self.end_dot_x = self.goal_x - self.end_dot_from_goal
        self.neutral_dot_x = self.blue_x - self.neutral_dot_from_blue

    # ------------------------------------------------------------------ zones
    def inside(self, x, y, margin=0.0):
        """Point dans la patinoire (coins arrondis), avec marge tolérée."""
        x, y = abs(x), abs(y)
        hx, hy, r = self.hx + margin, self.hy + margin, self.r
        if x > hx or y > hy:
            return False
        cx, cy = self.hx - r, self.hy - r
        if x > cx and y > cy:
            return math.hypot(x - cx, y - cy) <= r + margin
        return True

    def inside_mask(self, X, Y):
        ax, ay = np.abs(X), np.abs(Y)
        cx, cy = self.hx - self.r, self.hy - self.r
        m = (ax <= self.hx) & (ay <= self.hy)
        corner = (ax > cx) & (ay > cy)
        m &= ~corner | (np.hypot(ax - cx, ay - cy) <= self.r)
        return m

    def boards_y_at(self, x):
        """Ordonnée de la bande (côté +y) à l'abscisse x."""
        ax = abs(x)
        cx = self.hx - self.r
        if ax <= cx:
            return self.hy
        dx = min(ax - cx, self.r)
        return (self.hy - self.r) + math.sqrt(max(self.r ** 2 - dx ** 2, 0))

    # ------------------------------------------------------- repères calibrage
    def landmarks(self):
        """Points identifiables sur une image broadcast -> coordonnées monde."""
        b, g, hy = self.blue_x, self.goal_x, self.hy
        gy = self.boards_y_at(g)
        pts = {
            "centre (rond central)": (0, 0),
            "ligne rouge x bande FOND": (0, hy),
            "ligne rouge x bande PRES": (0, -hy),
            "bleue GAUCHE x bande FOND": (-b, hy),
            "bleue GAUCHE x bande PRES": (-b, -hy),
            "bleue DROITE x bande FOND": (b, hy),
            "bleue DROITE x bande PRES": (b, -hy),
            "but GAUCHE x bande FOND": (-g, gy),
            "but GAUCHE x bande PRES": (-g, -gy),
            "but DROITE x bande FOND": (g, gy),
            "but DROITE x bande PRES": (g, -gy),
            "cage GAUCHE (centre)": (-g, 0),
            "cage DROITE (centre)": (g, 0),
            "point zone GAUCHE fond": (-self.end_dot_x, self.dot_y),
            "point zone GAUCHE pres": (-self.end_dot_x, -self.dot_y),
            "point zone DROITE fond": (self.end_dot_x, self.dot_y),
            "point zone DROITE pres": (self.end_dot_x, -self.dot_y),
            "point neutre GAUCHE fond": (-self.neutral_dot_x, self.dot_y),
            "point neutre GAUCHE pres": (-self.neutral_dot_x, -self.dot_y),
            "point neutre DROITE fond": (self.neutral_dot_x, self.dot_y),
            "point neutre DROITE pres": (self.neutral_dot_x, -self.dot_y),
            "rond central x ligne rouge FOND": (0, self.circle_r),
            "rond central x ligne rouge PRES": (0, -self.circle_r),
        }
        return {k: (float(v[0]), float(v[1])) for k, v in pts.items()}

    # ------------------------------------------------------------ marquages
    def outline(self, n=24):
        """Contour de la bande (polygone fermé)."""
        r, cx, cy = self.r, self.hx - self.r, self.hy - self.r
        pts = []
        for sx, sy, a0 in [(1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)]:
            for a in np.linspace(a0, a0 + 90, n):
                t = math.radians(a)
                pts.append((sx * cx + r * math.cos(t), sy * cy + r * math.sin(t)))
        return np.array(pts, np.float32)

    def _circle(self, x, y, r, n=64):
        t = np.linspace(0, 2 * np.pi, n)
        return np.stack([x + r * np.cos(t), y + r * np.sin(t)], 1).astype(np.float32)

    def markings(self):
        """Liste de (polyligne Nx2 en mètres, rôle) pour dessiner/projeter."""
        out = [(self.outline(), "boards")]
        out.append((np.array([[0, -self.hy], [0, self.hy]], np.float32), "red"))
        for s in (-1, 1):
            bx = s * self.blue_x
            out.append((np.array([[bx, -self.hy], [bx, self.hy]], np.float32), "blue"))
            gx = s * self.goal_x
            gy = self.boards_y_at(gx)
            out.append((np.array([[gx, -gy], [gx, gy]], np.float32), "goal"))
            for dy in (-self.dot_y, self.dot_y):
                out.append((self._circle(s * self.end_dot_x, dy, self.circle_r), "circle"))
                out.append((self._circle(s * self.end_dot_x, dy, 0.3, 12), "dot"))
                out.append((self._circle(s * self.neutral_dot_x, dy, 0.3, 12), "dot"))
            t = np.linspace(-np.pi / 2, np.pi / 2, 24)
            cr = self.crease_r
            arc = np.stack([gx - s * cr * np.cos(t), cr * np.sin(t)], 1).astype(np.float32)
            out.append((arc, "crease"))
        out.append((self._circle(0, 0, self.circle_r), "center_circle"))
        out.append((self._circle(0, 0, 0.15, 12), "center_dot"))
        return out
