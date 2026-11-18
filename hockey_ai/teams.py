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


# ------------------------------------------------------------------ arbitres
def _torso_crop(frame, box, kp=None, min_conf=0.4):
    x1, y1, x2, y2 = [float(v) for v in box]
    if kp is not None and kp[5, 2] > min_conf and kp[6, 2] > min_conf:
        ls, rs = kp[5], kp[6]
        sw = abs(rs[0] - ls[0])
        top = min(ls[1], rs[1])
        if kp[11, 2] > min_conf and kp[12, 2] > min_conf:
            bot = top + 0.8 * ((kp[11, 1] + kp[12, 1]) / 2 - top)
        else:
            bot = top + 0.35 * (y2 - y1)
        cx1, cx2 = min(ls[0], rs[0]) + 0.05 * sw, max(ls[0], rs[0]) - 0.05 * sw
        if cx2 - cx1 < 6:                       # de profil : on prend la largeur du rectangle
            cx1, cx2 = x1 + 0.25 * (x2 - x1), x2 - 0.25 * (x2 - x1)
        cy1, cy2 = top + 0.08 * (bot - top), bot
    else:
        w, h = x2 - x1, y2 - y1
        cx1, cx2, cy1, cy2 = x1 + 0.25 * w, x2 - 0.25 * w, y1 + 0.18 * h, y1 + 0.48 * h
    H, W = frame.shape[:2]
    cx1, cy1, cx2, cy2 = int(max(0, cx1)), int(max(0, cy1)), int(min(W, cx2)), int(min(H, cy2))
    if cx2 - cx1 < 6 or cy2 - cy1 < 6:
        return None
    return frame[cy1:cy2, cx1:cx2]


def referee_score(frame, box, kp=None):
    """0..1 : probabilité que ce soit un arbitre (maillot rayé noir/blanc vertical,
    brassard rouge/orange sur le haut du bras).

    Pièges évités : le filet de protection (mailles claires sur maillot noir, sombres sur
    maillot blanc) est gommé ; un maillot noir avec numéro blanc reste globalement sombre ;
    les bandes doivent avoir la largeur de rayures d'arbitre."""
    crop = _torso_crop(frame, box, kp)
    if crop is None:
        return 0.0
    # filet : mailles fines claires (ouverture) et sombres (fermeture), à la résolution d'origine
    k = np.ones((3, 3), np.uint8)
    crop = cv2.morphologyEx(cv2.morphologyEx(crop, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
    s = 64.0 / crop.shape[1]
    crop = cv2.resize(crop, (64, max(8, int(crop.shape[0] * s))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    if np.median(chroma) > 18:                  # maillot coloré : pas un arbitre
        return 0.0
    if not (85 < L.mean() < 175):               # trop sombre (maillot noir + numéro) ou trop clair
        return 0.0
    hgt = L.shape[0]
    if hgt < 9:
        return 0.0
    bands = [L[i * hgt // 3:(i + 1) * hgt // 3].mean(0) for i in range(3)]
    prof = np.mean(bands, 0)
    lo, hi = np.percentile(prof, 10), np.percentile(prof, 90)
    contrast = hi - lo
    if contrast < 50:
        return 0.0
    b = prof > (lo + hi) / 2
    edges = np.flatnonzero(b[1:] != b[:-1])
    trans = len(edges)
    dark = 1.0 - b.mean()
    stripes = 0.0
    if trans >= 6 and 0.35 < dark < 0.65:
        runs = np.diff(edges)
        mean_run = runs.mean()
        regular = runs.std() / max(mean_run, 1e-6) < 0.5              # bandes de largeur régulière
        width_ok = 64 / 16 <= mean_run <= 64 / 5                      # 5 à 16 bandes sur le torse
        cors = [np.corrcoef(bands[i], bands[j])[0, 1] for i, j in ((0, 1), (1, 2), (0, 2))]
        vertical = np.nanmin(cors) > 0.6                              # de haut en bas du torse
        if regular and width_ok and vertical:
            stripes = min(1.0, 0.2 * (trans - 4)) * min(1.0, contrast / 90.0)
    # brassard : rouge/orange sur le haut d'un bras
    band = 0.0
    if kp is not None:
        for sh, el in ((5, 7), (6, 8)):
            if kp[sh, 2] > 0.4 and kp[el, 2] > 0.4:
                a, e = kp[sh, :2], kp[el, :2]
                L_ = np.hypot(*(e - a))
                m = a + 0.45 * (e - a)
                r = max(3, int(0.3 * L_))
                x1, y1 = int(max(0, m[0] - r)), int(max(0, m[1] - r))
                arm = frame[y1:int(m[1] + r), x1:int(m[0] + r)]
                if arm.size:
                    la = cv2.cvtColor(arm, cv2.COLOR_BGR2LAB).astype(np.int16)
                    red = ((la[..., 1] - 128) > 25).mean()
                    band = max(band, min(1.0, red / 0.2))
    return float(min(1.0, stripes + 0.4 * band * (stripes > 0.2)))


# ------------------------------------------------------------- équipes (règles)
def kit_features(frame, box, kp=None):
    """(luminosité moyenne du torse, part de rouge sur la culotte) ou None.
    Torse cadré par le squelette, filet gommé ; culotte entre hanches et genoux."""
    crop = _torso_crop(frame, box, kp)
    if crop is None:
        return None
    # filet : filtre médian (efface les mailles d'1 pixel, claires ou sombres, sans effacer
    # des rayures de 2 pixels ou plus)
    crop = cv2.medianBlur(crop, 3)
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.int16)
    # moyenne (et non médiane) : un maillot rayé noir/blanc tombe au milieu, donc dans la
    # zone de doute, au lieu de basculer au hasard côté noir ou côté blanc
    torso_L = float(lab[..., 0].mean())
    x1, y1, x2, y2 = [float(v) for v in box]
    w, h = x2 - x1, y2 - y1
    if kp is not None and kp[11, 2] > 0.4 and kp[12, 2] > 0.4:
        hy = (kp[11, 1] + kp[12, 1]) / 2
        if kp[13, 2] > 0.4 and kp[14, 2] > 0.4:
            ky = (kp[13, 1] + kp[14, 1]) / 2
        else:
            ky = hy + 0.2 * h
        px1, px2 = min(kp[11, 0], kp[12, 0]) - 0.1 * w, max(kp[11, 0], kp[12, 0]) + 0.1 * w
        py1, py2 = hy, hy + 0.8 * (ky - hy)
    else:
        px1, px2, py1, py2 = x1 + 0.25 * w, x2 - 0.25 * w, y1 + 0.52 * h, y1 + 0.68 * h
    H, W = frame.shape[:2]
    px1, py1, px2, py2 = int(max(0, px1)), int(max(0, py1)), int(min(W, px2)), int(min(H, py2))
    red = 0.0
    if px2 - px1 > 3 and py2 - py1 > 3:
        pl = cv2.cvtColor(frame[py1:py2, px1:px2], cv2.COLOR_BGR2LAB).astype(np.int16)
        red = float((((pl[..., 1] - 128) > 22) & (pl[..., 0] > 40)).mean())
    return torso_L, red


def otsu_threshold(values, default=110.0):
    """Seuil séparant deux groupes de luminosités (maillots sombres / clairs)."""
    v = np.asarray(values, float)
    if len(v) < 20:
        return default
    hist, edges = np.histogram(v, bins=64, range=(0, 255))
    cen = (edges[:-1] + edges[1:]) / 2
    best, thr = -1, default
    w0 = np.cumsum(hist)
    m0 = np.cumsum(hist * cen)
    tot, mt = w0[-1], m0[-1]
    for i in range(1, 63):
        if w0[i] == 0 or w0[i] == tot:
            continue
        mu0, mu1 = m0[i] / w0[i], (mt - m0[i]) / (tot - w0[i])
        between = w0[i] * (tot - w0[i]) * (mu0 - mu1) ** 2
        if between > best:
            best, thr = between, cen[i]
    return float(thr)
