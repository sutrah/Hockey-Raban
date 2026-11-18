"""Identification des équipes par la couleur du maillot (+ détection des arbitres rayés)."""
from collections import Counter, defaultdict

import cv2
import numpy as np
from sklearn.cluster import KMeans

REF = -1      # arbitre / juge de ligne
UNK = -2      # pas encore classé


def hex_to_bgr(h):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def bgr_to_lab(bgr):
    px = np.uint8([[bgr]])
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def jersey_feature(frame, box, poly=None):
    """Couleur médiane du torse (Lab) + score de rayures.
    Retourne (vec[L,a,b], stripe_score) ou None."""
    x1, y1, x2, y2 = [int(v) for v in box]
    w, h = x2 - x1, y2 - y1
    if w < 8 or h < 16:
        return None
    # torse : 15-50 % de la hauteur, 25-75 % de la largeur
    tx1, tx2 = x1 + int(0.25 * w), x1 + int(0.75 * w)
    ty1, ty2 = y1 + int(0.15 * h), y1 + int(0.50 * h)
    H, W = frame.shape[:2]
    tx1, ty1 = max(tx1, 0), max(ty1, 0)
    tx2, ty2 = min(tx2, W), min(ty2, H)
    if tx2 - tx1 < 3 or ty2 - ty1 < 3:
        return None
    crop = frame[ty1:ty2, tx1:tx2]
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    if poly is not None and len(poly) >= 3:
        m = np.zeros((ty2 - ty1, tx2 - tx1), np.uint8)
        cv2.fillPoly(m, [np.int32(poly - [tx1, ty1])], 255)
        sel = m.reshape(-1) > 0
        if sel.sum() > 20:
            lab = lab[sel]
    L = lab[:, 0]
    # rayures noir/blanc : luminance bimodale et faible chroma
    chroma = np.hypot(lab[:, 1] - 128, lab[:, 2] - 128)
    dark = (L < 70).mean()
    light = (L > 170).mean()
    stripe = min(dark, light) * 2 * (1.0 if np.median(chroma) < 15 else 0.3)
    return np.median(lab, 0), float(stripe)


class TeamClassifier:
    def __init__(self, team_jerseys=None, ref_stripe_thr=0.35, detect_refs=True, outlier=0.75):
        """team_jerseys : [hexA, hexB] facultatif pour fixer quelle couleur = équipe A/B."""
        self.samples, self.stripes = [], []
        self.centers = None
        self.team_jerseys = team_jerseys
        self.ref_thr = ref_stripe_thr
        self.detect_refs = detect_refs
        self.outlier = outlier
        self.votes = defaultdict(Counter)
        self.jersey_bgr = [None, None]

    def add_sample(self, feat):
        if feat is None:
            return
        v, s = feat
        self.samples.append(v)
        self.stripes.append(s)

    @staticmethod
    def _w(v):
        # pondération : la teinte (a,b) compte plus que la luminosité
        v = np.atleast_2d(v)
        return np.stack([v[:, 0] * 0.6, v[:, 1] * 1.4, v[:, 2] * 1.4], 1)

    def fit(self):
        X = np.array(self.samples, np.float32)
        S = np.array(self.stripes)
        if len(X) < 10:
            raise RuntimeError("Pas assez de joueurs détectés pour apprendre les couleurs d'équipe")
        keep = S < self.ref_thr if self.detect_refs else np.ones(len(X), bool)
        Xp = X[keep] if keep.sum() >= 10 else X
        if self.team_jerseys:
            self.centers = np.array([bgr_to_lab(hex_to_bgr(c)) for c in self.team_jerseys])
            # affine sur les données : 1 itération de k-means initialisée sur les couleurs données
            km = KMeans(2, init=self._w(self.centers), n_init=1, max_iter=20).fit(self._w(Xp))
        else:
            km = KMeans(2, n_init=10, random_state=0).fit(self._w(Xp))
        lab = km.labels_
        self.centers = np.array([np.median(Xp[lab == k], 0) for k in range(2)])
        for k in range(2):
            lab_px = np.uint8([[self.centers[k]]])
            self.jersey_bgr[k] = tuple(int(c) for c in cv2.cvtColor(lab_px, cv2.COLOR_LAB2BGR)[0, 0])
        return self

    def predict(self, feat):
        if feat is None or self.centers is None:
            return UNK
        v, s = feat
        if self.detect_refs and s >= self.ref_thr:
            return REF
        d = np.linalg.norm(self._w(self.centers) - self._w(v), axis=1)
        gap = np.linalg.norm(self._w(self.centers[0]) - self._w(self.centers[1]))
        if d.min() > self.outlier * gap:     # ni l'une ni l'autre équipe (arbitre, public…)
            return UNK
        return int(np.argmin(d))

    def vote(self, track_id, label):
        """Vote majoritaire par piste : stabilise l'équipe d'un joueur suivi."""
        if label != UNK:
            self.votes[track_id][label] += 1
        c = self.votes[track_id]
        if not c:
            return UNK
        return c.most_common(1)[0][0]
