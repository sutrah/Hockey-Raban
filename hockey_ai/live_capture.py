"""Capture de l'écran (zone de la vidéo) ou lecture d'un fichier en temps réel (tests, replays).

Aucune image n'est écrite sur le disque : chaque image est analysée en mémoire puis oubliée.
"""
import time

import cv2
import numpy as np

MARKER_BGR = (255, 0, 255)      # magenta pur, affiché par l'extension aux 4 coins de la vidéo


def _full_screen_grabber():
    """Renvoie une fonction grab(region=None) -> image BGR, la plus rapide disponible."""
    try:
        import dxcam        # Windows : capture par la carte graphique (Desktop Duplication)
        cam = dxcam.create(output_color="BGR")

        def grab(region=None):
            f = cam.grab(region=region)
            return None if f is None else np.ascontiguousarray(f)
        grab.backend = "dxcam"
        grab.cam = cam
        return grab
    except Exception:
        pass
    import mss
    sct = mss.mss()

    def grab(region=None):
        if region is None:
            mon = sct.monitors[1]
            region = (mon["left"], mon["top"], mon["left"] + mon["width"], mon["top"] + mon["height"])
        l, t, r, b = region
        img = np.asarray(sct.grab(dict(left=l, top=t, width=r - l, height=b - t)))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    grab.backend = "mss"
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

    def __init__(self, region=None, log=print):
        self.grab = _full_screen_grabber()
        self.region = region
        self.log = log
        self.idx = 0
        self.last = None
        log(f"[capture] écran via {self.grab.backend}")

    def full(self):
        return self.grab(None)

    def locate(self, tries=12, wait=0.12):
        """Cherche les marqueurs magenta affichés par l'extension."""
        for _ in range(tries):
            img = self.full()
            if img is not None:
                reg = find_markers(img)
                if reg:
                    self.region = reg
                    return reg
            time.sleep(wait)
        return None

    def read(self):
        if self.region is None:
            return None, None
        f = self.grab(self.region)
        if f is None:                       # dxcam : rien n'a changé depuis la dernière image
            f = self.last
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
