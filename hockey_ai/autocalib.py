"""Calibration automatique de la caméra à partir de la vidéo seule (aucun clic).

Principe :
  * on repère sur l'image la glace (zone claire et peu colorée, bandes comprises),
    les lignes rouges (ligne centrale, lignes de but, cercles) et bleues (lignes bleues,
    rond central) ;
  * on cherche la caméra (position, orientation, zoom) dont la patinoire projetée
    colle le mieux à ces repères : recouvrement de la glace + distance aux lignes ;
  * la position de la caméra est estimée une fois sur quelques images, puis seuls
    le panoramique, l'inclinaison et le zoom sont suivis image par image (flux optique
    + recalage sur les lignes toutes les quelques images, recherche complète après
    une coupure de plan ou un replay).

Interface identique à camera.CameraTracker : update(idx, frame, boxes) -> H (image -> monde)
ou None quand la patinoire n'est pas reconnaissable (gros plan, public…).
"""
import math

import cv2
import numpy as np
from scipy.optimize import least_squares, minimize

BOARDS_H = 1.0          # hauteur des bandes (m) : blanches comme la glace sur l'image
WORK_W = 480            # largeur de travail pour l'analyse des lignes
SCALES = np.array([3.0, 4.0, 3.0, 5.0, 3.0, 6.0])   # pas typiques (cx, cy, h, tx, ty, fov)


# --------------------------------------------------------------- caméra
def camera_from_params(p, size):
    """p = (cx, cy, h, tx, ty, fov_deg) -> (K, R, C).
    Caméra en C=(cx,cy,h) qui vise le point (tx,ty,0) de la glace, sans roulis."""
    cx, cy, h, tx, ty, fov = p
    w, hh = size
    f = (w / 2) / math.tan(math.radians(np.clip(fov, 3, 150)) / 2)
    K = np.array([[f, 0, w / 2], [0, f, hh / 2], [0, 0, 1.0]])
    C = np.array([cx, cy, h], float)
    z = np.array([tx, ty, 0.0]) - C
    z /= np.linalg.norm(z) + 1e-12
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x) + 1e-12
    y = np.cross(z, x)
    return K, np.stack([x, y, z]), C


def homographies(p, size):
    """-> (Hw : sol -> image, H : image -> sol)."""
    K, R, C = camera_from_params(p, size)
    t = -R @ C
    Hw = K @ np.c_[R[:, 0], R[:, 1], t]
    return Hw, np.linalg.inv(Hw)


def _clip_near(Xc, near=0.5):
    """Sutherland-Hodgman sur le plan z=near (repère caméra), polygone fermé."""
    out = []
    n = len(Xc)
    for i in range(n):
        a, b = Xc[i], Xc[(i + 1) % n]
        ia, ib = a[2] >= near, b[2] >= near
        if ia:
            out.append(a)
        if ia != ib:
            t = (near - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out) if len(out) >= 3 else None


# --------------------------------------------------------------- modèle
class RinkModel:
    """Marquages de la patinoire classés par couleur, échantillonnés en 3D (z=0)."""

    def __init__(self, rink, step=0.4):
        self.rink = rink
        red, blue = [], []
        for poly, role in rink.markings():
            if role in ("red", "goal", "circle", "crease"):
                red.append(poly)
            elif role in ("blue", "center_circle"):
                blue.append(poly)
        self.lines = {"red": red, "blue": blue}
        self.samples = {k: self._sample(v, step) for k, v in self.lines.items()}
        o = rink.outline(24)
        self.outline0 = np.c_[o, np.zeros(len(o))]
        self.outline1 = np.c_[o, np.full(len(o), BOARDS_H)]

    @staticmethod
    def _sample(polys, step):
        pts = []
        for p in polys:
            for a, b in zip(p[:-1], p[1:]):
                n = max(1, int(np.linalg.norm(b - a) / step))
                for t in np.arange(n) / n:
                    pts.append(a + t * (b - a))
        pts = np.array(pts, float)
        return np.c_[pts, np.zeros(len(pts))]


# ------------------------------------------------------------ repères image
class FrameFeatures:
    """Glace, lignes rouges/bleues et cartes de distance, à basse résolution."""

    def __init__(self, frame, boxes=(), static_mask=None, work_w=WORK_W):
        h0, w0 = frame.shape[:2]
        s = work_w / w0
        self.s = s
        self.size = (work_w, int(round(h0 * s)))
        w, h = self.size
        small = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.int16)
        L, a, b = lab[..., 0], lab[..., 1] - 128, lab[..., 2] - 128

        valid = np.ones((h, w), bool)
        if static_mask is not None:
            valid &= cv2.resize(static_mask, self.size, interpolation=cv2.INTER_NEAREST) == 0
        players = np.zeros((h, w), np.uint8)
        for x1, y1, x2, y2 in boxes:
            cv2.rectangle(players, (int(x1 * s) - 1, int(y1 * s) - 1), (int(x2 * s) + 1, int(y2 * s) + 1), 255, -1)
        self.valid = valid & (players == 0)

        # glace + bandes : clair et peu coloré (seuil adaptatif sur la luminosité)
        chroma = np.hypot(a, b)
        low = chroma < 22
        Lthr = 135
        if low.sum() > 0.1 * w * h:
            Lthr = max(120, min(185, np.percentile(L[low], 45)))
        ice = (low & (L > Lthr) & valid).astype(np.uint8) * 255
        k = max(3, int(w / 80)) | 1
        ice = cv2.morphologyEx(ice, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        n, lbl, st, _ = cv2.connectedComponentsWithStats(ice, 8)
        keep = np.zeros(n, bool)
        keep[1:] = st[1:, cv2.CC_STAT_AREA] > 0.02 * w * h
        ice = keep[lbl].astype(np.uint8) * 255
        # bouche les trous (lignes, joueurs, logos) qui ne touchent pas le bord
        inv = 255 - ice
        n, lbl, st, _ = cv2.connectedComponentsWithStats(inv, 4)
        for i in range(1, n):
            x, y, ww, hh, area = st[i]
            if x > 0 and y > 0 and x + ww < w and y + hh < h:
                ice[lbl == i] = 255
        self.ice = ice > 0
        self.ice_frac = float(self.ice.mean())
        near_ice = cv2.dilate(ice, np.ones((k, k), np.uint8)) > 0

        self.tau = max(3.0, 0.012 * w)
        # couleurs détectées en double résolution (lignes fines), puis ramenées par « max »
        big = cv2.resize(frame, (2 * w, 2 * h), interpolation=cv2.INTER_AREA)
        lab2 = cv2.cvtColor(big, cv2.COLOR_BGR2LAB).astype(np.int16)
        L2, a2, b2 = lab2[..., 0], lab2[..., 1] - 128, lab2[..., 2] - 128
        # écart à la teinte locale de la glace : les lignes fines/lointaines sont pâles
        kb = (int(w / 12) | 1, int(w / 12) | 1)
        da = a2 - cv2.blur(a2.astype(np.float32), kb)
        db = b2 - cv2.blur(b2.astype(np.float32), kb)
        red2 = ((da > 8) & (a2 > 4) & (a2 > np.abs(b2) * 0.6) & (L2 > 35)).astype(np.uint8)
        blue2 = ((db < -8) & (b2 < -4) & (-b2 > a2 * 0.8) & (L2 > 25)).astype(np.uint8)
        pool = lambda m: cv2.resize(m * 255, self.size, interpolation=cv2.INTER_AREA) > 0
        base = near_ice & self.valid
        red, blue = pool(red2) & base, pool(blue2) & base
        self.maps = {"red": self._linelike(red), "blue": self._linelike(blue)}
        self.dt = {}
        self.npx = {}
        for c, m in self.maps.items():
            self.npx[c] = int(m.sum())
            self.dt[c] = cv2.distanceTransform(np.where(m, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)
        self.ice_area = max(1, int(self.ice.sum()))

    def _linelike(self, m):
        """Garde les tracés fins et longs (lignes, cercles), pas les maillots ni les logos."""
        m0 = m
        m = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8))   # relie les pointillés
        n, lbl, st, _ = cv2.connectedComponentsWithStats(m, 8)
        w = self.size[0]
        keep = np.zeros(n, bool)
        for i in range(1, n):
            x, y, ww, hh, area = st[i]
            big = max(ww, hh)
            if big < 0.035 * w or area < 6:
                continue
            fill = area / float(ww * hh)
            pts = np.column_stack(np.nonzero(lbl[y:y + hh, x:x + ww] == i)).astype(np.float32)
            (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
            elong = max(rw, rh) / max(1.0, min(rw, rh))
            if elong > 3.5 or (fill < 0.3 and big > 0.06 * w):
                keep[i] = True
        return keep[lbl] & m0

    @property
    def usable(self):
        return self.ice_frac > 0.2


# ---------------------------------------------------------------- score
class Scorer:
    def __init__(self, model, feats):
        self.m, self.f = model, feats
        w, h = feats.size
        self.size = (w, h)
        self.buf = np.zeros((h, w), np.uint8)

    def _project(self, K, R, C, X):
        Xc = (X - C) @ R.T
        ok = Xc[:, 2] > 0.5
        uv = np.zeros((len(X), 2))
        uv[ok] = (Xc[ok] @ K.T)[:, :2] / Xc[ok, 2:3]
        return uv, ok

    def _fill(self, K, R, C, X):
        Xc = _clip_near((X - C) @ R.T)
        if Xc is None:
            return
        uv = (Xc @ K.T)[:, :2] / Xc[:, 2:3]
        uv = np.clip(uv, -4 * self.size[0], 5 * self.size[0])
        cv2.fillPoly(self.buf, [np.int32(np.round(uv))], 255)

    def __call__(self, p, detail=False):
        w, h = self.size
        if not (2.0 < p[2] < 60.0) or not (8 < p[5] < 120):
            return 10.0
        K, R, C = camera_from_params(p, self.size)
        if R[2, 2] > -0.02:          # la caméra doit regarder vers le bas
            return 10.0
        f = self.f
        # 1) recouvrement glace + bandes
        self.buf[:] = 0
        self._fill(K, R, C, self.m.outline0)
        self._fill(K, R, C, self.m.outline1)
        model = (self.buf > 0) & f.valid
        inter = np.logical_and(model, f.ice).sum()
        union = np.logical_or(model, f.ice).sum()
        iou = inter / max(union, 1)
        cost = 1.5 * (1 - iou)
        # 2) lignes : modèle -> image et image -> modèle
        parts = {}
        for c in ("red", "blue"):
            uv, ok = self._project(K, R, C, self.m.samples[c])
            ins = ok & (uv[:, 0] >= 0) & (uv[:, 0] < w - 1) & (uv[:, 1] >= 0) & (uv[:, 1] < h - 1)
            fwd, rev = 1.0, 1.0
            if ins.sum() >= 5:
                q = np.int32(uv[ins])
                vis = f.valid[q[:, 1], q[:, 0]] & f.ice[q[:, 1], q[:, 0]]
                if vis.sum() >= 5:
                    d = f.dt[c][q[vis, 1], q[vis, 0]]
                    fwd = float(np.mean(np.minimum(d, f.tau)) / f.tau)
            if f.npx[c] > 20:
                self.buf[:] = 0
                for poly in self.m.lines[c]:
                    X = np.c_[poly, np.zeros(len(poly))]
                    uvp, okp = self._project(K, R, C, X)
                    if okp.all():
                        uvp = np.clip(uvp, -4 * w, 5 * w)
                        cv2.polylines(self.buf, [np.int32(np.round(uvp))], False, 255,
                                      int(2 * f.tau) | 1)
                rev = 1.0 - float(np.logical_and(self.buf > 0, f.maps[c]).sum()) / f.npx[c]
                cost += 1.0 * fwd + 0.7 * rev
            else:
                cost += 0.5 * fwd + 0.35
            parts[c] = (fwd, rev)
        if detail:
            return cost, dict(iou=iou, **{f"{c}_fwd": v[0] for c, v in parts.items()},
                              **{f"{c}_rev": v[1] for c, v in parts.items()})
        return cost


# ---------------------------------------------------------------- recherche
def _nm(fun, x0, scales, iters=250):
    x0 = np.asarray(x0, float)
    simplex = [x0] + [x0 + np.eye(len(x0))[i] * scales[i] for i in range(len(x0))]
    r = minimize(fun, x0, method="Nelder-Mead",
                 options=dict(initial_simplex=np.array(simplex), maxfev=iters, xatol=1e-3, fatol=1e-4))
    return r.x, r.fun


def global_search(rink, scorer, rng, center=None, n_random=1500, n_refine=10):
    """Recherche complète. center=(cx,cy,h) connu -> seulement (tx, ty, fov)."""
    hx, hy = rink.hx, rink.hy
    cands = []
    for _ in range(n_random):
        if center is None:
            c = [rng.uniform(-12, 12), rng.uniform(-hy - 40, -hy - 2), rng.uniform(3, 25)]
        else:
            c = list(center)
        p = c + [rng.uniform(-hx + 2, hx - 2), rng.uniform(-hy * 0.8, hy * 0.6), rng.uniform(12, 90)]
        cands.append((scorer(p), p))
    cands.sort(key=lambda t: t[0])
    best = (1e9, None)
    picked = []
    for c, p in cands:
        if len(picked) >= n_refine:
            break
        if any(np.linalg.norm((np.array(p) - q) / SCALES) < 1.5 for q in picked):
            continue
        picked.append(np.array(p))
    for p in picked:
        if center is None:
            x, fx = _nm(scorer, p, SCALES, 500)
        else:
            fun = lambda q: scorer(list(center) + list(q))
            q, fx = _nm(fun, p[3:], SCALES[3:], 200)
            x = np.r_[center, q]
        if fx < best[0]:
            best = (fx, np.array(x))
    return best


def estimate_camera_center(video_path, rink, frame_ids, static_mask=None, log=print, seed=0):
    """Estime la position de la caméra sur quelques images (médiane des meilleures)."""
    model = RinkModel(rink)
    rng = np.random.default_rng(seed)
    cap = cv2.VideoCapture(video_path)
    res = []
    for fi in frame_ids:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi))
        ok, frame = cap.read()
        if not ok:
            continue
        feats = FrameFeatures(frame, static_mask=static_mask)
        if not feats.usable:
            continue
        sc = Scorer(model, feats)
        cost, p = global_search(rink, sc, rng)
        if p is not None:
            res.append((cost, p, fi))
            log(f"  image {fi} : score {cost:.2f} · caméra ≈ ({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}) m")
    cap.release()
    if not res:
        return None, None
    res.sort(key=lambda t: t[0])
    good = [r for r in res if r[0] <= res[0][0] * 1.25 + 0.1][:max(3, len(res) // 2)]
    center = np.median([r[1][:3] for r in good], 0)
    return center, res[0][0]


# ---------------------------------------------------------------- suivi
class AutoCameraTracker:
    """Même rôle que CameraTracker, sans calibration manuelle."""

    def __init__(self, rink, frame_size, center, static_mask=None, refine_every=4,
                 cut_threshold=0.55, accept=1.9, log=print, seed=0):
        self.rink = rink
        self.model = RinkModel(rink)
        self.size = tuple(frame_size)
        self.center = np.asarray(center, float)
        self.static = static_mask
        self.refine_every = refine_every
        self.cut_threshold = cut_threshold
        self.accept = accept
        self.log = log
        self.rng = np.random.default_rng(seed)
        self.q = None                 # (tx, ty, fov) courant
        self.prev_gray = self.prev_hist = self.prev_mask = None
        self.since = 0
        self.retry_in = 0
        self.bad = 0
        fw, fh = self.size
        self.fs = 0.5
        self.sw, self.sh = int(fw * self.fs), int(fh * self.fs)
        if static_mask is not None:
            self.static_small = cv2.resize(static_mask, (self.sw, self.sh), interpolation=cv2.INTER_NEAREST)
        else:
            self.static_small = None
        self.stats = dict(frames=0, located=0, searches=0)

    # -------------------------------------------------------------- outils
    def _p(self, q):
        return np.r_[self.center, q]

    def H_of(self, q):
        return homographies(self._p(q), self.size)[1]

    def _scorer(self, frame, boxes):
        feats = FrameFeatures(frame, boxes, self.static)
        return Scorer(self.model, feats), feats

    def _refine(self, sc, q, iters=80):
        fun = lambda qq: sc(self._p(qq))
        return _nm(fun, q, np.array([1.0, 0.8, 1.5]), iters)

    def _search(self, sc):
        self.stats["searches"] += 1
        cost, p = global_search(self.rink, sc, self.rng, center=self.center, n_random=500, n_refine=5)
        return (p[3:] if p is not None else None), cost

    def _fit_to_H(self, H, q0):
        """Paramètres (tx, ty, fov) dont l'homographie approche H (issue du flux optique)."""
        w, h = self.size
        g = np.array([[u, v] for u in np.linspace(0.1, 0.9, 4) * w for v in np.linspace(0.35, 0.95, 3) * h])
        Wp = cv2.perspectiveTransform(g.reshape(-1, 1, 2), H).reshape(-1, 2)

        def res(q):
            Hw = homographies(self._p(q), self.size)[0]
            P = np.c_[Wp, np.ones(len(Wp))] @ Hw.T
            return ((P[:, :2] / P[:, 2:3]) - g).ravel() / w * 100

        try:
            r = least_squares(res, q0, x_scale=[2, 2, 3], max_nfev=40)
            return r.x
        except Exception:
            return q0

    def _mask(self, boxes):
        m = np.full((self.sh, self.sw), 255, np.uint8)
        if self.static_small is not None:
            m[self.static_small > 0] = 0
        for x1, y1, x2, y2 in boxes:
            pad = 0.15 * (x2 - x1)
            cv2.rectangle(m, (int((x1 - pad) * self.fs), int((y1 - pad) * self.fs)),
                          (int((x2 + pad) * self.fs), int((y2 + pad) * self.fs)), 0, -1)
        return m

    def _flow(self, gray, mask):
        if self.prev_gray is None:
            return None
        pts = cv2.goodFeaturesToTrack(self.prev_gray, 600, 0.01, 8, mask=self.prev_mask)
        if pts is None or len(pts) < 20:
            return None
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, pts, None, winSize=(21, 21), maxLevel=3)
        ok = st.ravel() == 1
        if ok.sum() < 20:
            return None
        M, inl = cv2.findHomography(nxt[ok].reshape(-1, 2), pts[ok].reshape(-1, 2), cv2.RANSAC, 2.0)
        if M is None or inl.sum() < 15:
            return None
        S = np.diag([self.fs, self.fs, 1.0])
        return np.linalg.inv(S) @ M @ S        # image courante -> image précédente (pleine rés.)

    # ---------------------------------------------------------------- main
    def update(self, idx, frame, boxes):
        self.stats["frames"] += 1
        small = cv2.resize(frame, (self.sw, self.sh))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        mask = self._mask(boxes)
        cut = self.prev_hist is not None and \
            cv2.compareHist(self.prev_hist, hist, cv2.HISTCMP_CORREL) < self.cut_threshold

        if self.q is not None and not cut:
            M = self._flow(gray, mask)
            if M is not None:
                self.q = self._fit_to_H(self.H_of(self.q) @ M, self.q)
            self.since += 1
            if self.since >= self.refine_every or M is None:
                self.since = 0
                sc, _ = self._scorer(frame, boxes)
                q, cost = self._refine(sc, self.q)
                if cost <= self.accept:
                    self.q = self.q + 0.6 * (q - self.q)    # recalage doux (pas de saccade)
                    self.bad = 0
                else:
                    self.bad += 1
                    if self.bad >= 3:
                        self.q = None
                        self.retry_in = 0
        else:
            if cut:
                self.q = None
                self.retry_in = 0
            if self.retry_in <= 0:
                sc, feats = self._scorer(frame, boxes)
                if feats.usable:
                    q, cost = self._search(sc)
                    if q is not None and cost <= self.accept:
                        self.q, self.bad, self.since = q, 0, 0
                if self.q is None:
                    self.retry_in = 10          # pas une vue de la glace : réessaie plus tard
            else:
                self.retry_in -= 1

        self.prev_gray, self.prev_hist, self.prev_mask = gray, hist, mask
        if self.q is None:
            return None
        self.stats["located"] += 1
        return self.H_of(self.q)


def auto_calibrate(video_path, rink, f0, f1, fps, frame_size, static_mask=None, n_frames=6, log=print):
    """Estime la position de la caméra sur n_frames images du segment [f0, f1)."""
    span = max(1, f1 - f0)
    ids = [f0 + int(span * (i + 0.5) / n_frames) for i in range(n_frames)]
    log(f"[auto] calibration automatique sur {len(ids)} images…")
    center, cost = estimate_camera_center(video_path, rink, ids, static_mask, log)
    if center is None:
        raise SystemExit("[auto] patinoire introuvable sur la vidéo : utilise calibrate.py (calibration manuelle).")
    log(f"[auto] caméra estimée en x={center[0]:.1f} m, y={center[1]:.1f} m, hauteur {center[2]:.1f} m "
        f"(meilleur score {cost:.2f})")
    return center
