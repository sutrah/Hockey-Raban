"""Capture de l'écran (zone de la vidéo) ou lecture d'un fichier en temps réel (tests, replays).

Aucune image n'est écrite sur le disque : chaque image est analysée en mémoire puis oubliée.
"""
import time

import cv2
import numpy as np

MARKER_BGR = (255, 0, 255)      # magenta pur, affiché par l'extension aux 4 coins de la vidéo


def _full_screen_grabber(use_dxcam=False):
    """Renvoie une fonction grab(region=None) -> image BGR.

    Par défaut : mss (capture GDI, simple et fiable, ~20 ms en 1080p). dxcam (capture par la
    carte graphique) peut rester bloqué après un passage en plein écran : seulement sur demande.
    Les coordonnées sont celles de l'écran virtuel (tous les moniteurs)."""
    if use_dxcam:
        try:
            import dxcam
            cam = dxcam.create(output_color="BGR")

            def grab(region=None):
                f = cam.grab(region=region)
                return None if f is None else np.ascontiguousarray(f)
            grab.backend = "dxcam"
            grab.origin = (0, 0)
            return grab
        except Exception:
            pass
    import mss
    sct = mss.mss()
    virt = sct.monitors[0]              # tous les écrans réunis

    def grab(region=None):
        if region is None:
            l, t = virt["left"], virt["top"]
            r, b = l + virt["width"], t + virt["height"]
        else:
            l, t, r, b = region
        img = np.asarray(sct.grab(dict(left=int(l), top=int(t), width=int(r - l), height=int(b - t))))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    grab.backend = "mss"
    grab.origin = (virt["left"], virt["top"])
    return grab


def find_markers(img):
    """Repère les 4 carrés magenta posés par l'extension -> (left, top, right, bottom) ou None."""
    b, g, r = img[..., 0].astype(np.int16), img[..., 1].astype(np.int16), img[..., 2].astype(np.int16)
    m = ((r > 200) & (b > 200) & (g < 60)).astype(np.uint8)
    n, lbl, st, cen = cv2.connectedComponentsWithStats(m, 8)
    blobs = [st[i] for i in range(1, n) if st[i][4] > 30 and 0.5 < st[i][2] / max(st[i][3], 1) < 2.0]
    if len(blobs) < 4:
        return None
    blobs = sorted(blobs, key=lambda s: -s[4])[:4]
    xs1 = [s[0] for s in blobs]
    ys1 = [s[1] for s in blobs]
    xs2 = [s[0] + s[2] for s in blobs]
    ys2 = [s[1] + s[3] for s in blobs]
    l, t, r, bb = min(xs1), min(ys1), max(xs2), max(ys2)
    if r - l < 200 or bb - t < 120:
        return None
    return int(l), int(t), int(r), int(bb)


class ScreenSource:
    """Capture de la zone de l'écran où s'affiche la vidéo."""

    def __init__(self, region=None, log=print, use_dxcam=False):
        self.use_dxcam = use_dxcam
        self.grab = _full_screen_grabber(use_dxcam)
        self.region = region
        self.log = log
        self.idx = 0
        self.last = None
        self.none_streak = 0
        self.same_streak = 0
        log(f"[capture] écran via {self.grab.backend}")

    def reset(self):
        """Recrée la capture. La dernière image est gardée : pendant une pause, l'analyse continue dessus."""
        try:
            self.grab = _full_screen_grabber(self.use_dxcam)
        except Exception as e:
            self.log(f"[capture] impossible de recréer la capture : {e}")
        self.none_streak = self.same_streak = 0

    def full(self):
        return self.grab(None)

    def locate(self, tries=12, wait=0.12):
        """Cherche les marqueurs magenta affichés par l'extension."""
        self.reset()                    # l'affichage a pu changer (plein écran, fenêtre…)
        for _ in range(tries):
            img = self.full()
            if img is not None:
                reg = find_markers(img)
                if reg:
                    ox, oy = getattr(self.grab, "origin", (0, 0))
                    reg = (reg[0] + ox, reg[1] + oy, reg[2] + ox, reg[3] + oy)
                    self.region = reg
                    return reg
            time.sleep(wait)
        return None

    def read(self):
        if self.region is None:
            return None, None
        try:
            f = self.grab(self.region)
        except Exception:
            f = None
        if f is None:                       # dxcam : rien n'a changé depuis la dernière image
            self.none_streak += 1
            if self.none_streak > 40:       # ~4 s sans image nouvelle (pause, ou capture bloquée) :
                self.reset()                # on recrée la capture, sans conséquence si c'est une pause
                self.none_streak = 0
            f = self.last
        else:
            self.none_streak = 0
            if f.size and f.max() < 12:     # image noire (transition plein écran)
                self.same_streak += 1
                if self.same_streak > 40:
                    self.log("[capture] image noire persistante, redémarrage de la capture")
                    self.reset()
            else:
                self.same_streak = 0
        if f is None:
            return None, None
        self.last = f
        self.idx += 1
        return self.idx, f

    @property
    def size(self):
        if self.region is None:
            return None
        l, t, r, b = self.region
        return r - l, b - t


class FileSource:
    """Lit une vidéo comme si c'était du direct : on prend toujours l'image « actuelle »
    selon l'horloge, en sautant celles qu'on n'a pas eu le temps de traiter."""

    def __init__(self, path, speed=1.0, start=0.0, loop=False):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise SystemExit(f"Impossible d'ouvrir {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.speed = speed
        self.loop = loop
        self.f0 = int(start * self.fps)
        self.t0 = None
        self.pos = self.f0 - 1
        self.region = (0, 0, int(self.cap.get(3)), int(self.cap.get(4)))
        self.finished = False

    @property
    def size(self):
        return self.region[2], self.region[3]

    def locate(self, *a, **k):
        return self.region

    def read(self):
        if self.t0 is None:
            self.t0 = time.time()
        target = self.f0 + int((time.time() - self.t0) * self.fps * self.speed)
        if target >= self.n:
            if not self.loop:
                self.finished = True
                return None, None
            self.t0, target = time.time(), self.f0
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.f0)
            self.pos = self.f0 - 1
        if target <= self.pos:          # pas encore d'image nouvelle
            return None, None
        if target - self.pos > 90:      # très en retard : saut direct (les sauts sont imprécis)
            self.seeks = getattr(self, "seeks", 0) + 1
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            self.pos = target - 1
        while self.pos < target - 1:    # images sautées : décodées sans conversion
            if not self.cap.grab():
                self.finished = True
                return None, None
            self.pos += 1
        ok, frame = self.cap.read()
        if not ok:
            self.finished = True
            return None, None
        self.pos += 1
        return self.pos, frame


def static_mask_from_frames(frames, thr=2.0):
    """Incrustations fixes (bandeau TV, logo) : pixels immobiles alors que l'image bouge."""
    if len(frames) < 8:
        return None
    small = [cv2.cvtColor(cv2.resize(f, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    std = np.stack(small).std(0)
    m = (std < thr).astype(np.uint8) * 255
    if m.mean() / 255 > 0.4:            # caméra immobile : on ne peut pas savoir
        return None
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.dilate(m, np.ones((7, 7), np.uint8))
    h, w = frames[0].shape[:2]
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
