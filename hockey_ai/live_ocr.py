"""Lecture de texte : bandeau TV (score, période, chrono) et numéros dans le dos des joueurs."""
import queue
import re
import threading
import time
from collections import Counter, defaultdict

import cv2
import numpy as np

CLOCK_RE = re.compile(r"^(\d{1,2})[:.;,](\d{2})$")
PERIOD_RE = re.compile(r"^(?:P|PER|PÉR)?\s*([1-3])$|^(OT|PR|TB|SO)$", re.I)


class OCR:
    """EasyOCR chargé à la demande (GPU si disponible), partagé entre les fils."""
    _reader = None
    lock = threading.Lock()

    @classmethod
    def reader(cls):
        if cls._reader is None:
            import easyocr
            try:
                import torch
                gpu = torch.cuda.is_available()
                if not gpu:             # sans carte graphique : ne pas affamer l'analyse principale
                    torch.set_num_threads(1)
            except Exception:
                gpu = False
            cls._reader = easyocr.Reader(["en"], gpu=gpu, verbose=False)
        return cls._reader

    @classmethod
    def read(cls, img, allowlist=None, **kw):
        try:
            r = cls.reader()
            with cls.lock:
                return r.readtext(img, allowlist=allowlist, detail=1, paragraph=False, **kw)
        except Exception:
            return []


# --------------------------------------------------------------- bandeau TV
class Scoreboard:
    """Trouve le bandeau TV (abréviations des équipes + chrono), puis le relit 1 fois/s.

    Donne : période, temps restant (s), chrono en marche ou arrêté, score."""

    def __init__(self, abbrs=("", ""), period_len=1200, log=print):
        self.abbrs = [a.upper() for a in abbrs if a]
        self.period_len = period_len
        self.log = log
        self.box = None             # zone du bandeau (pixels de l'image capturée)
        self.next_search = 0.0
        self.next_read = 0.0
        self.period = None
        self.remaining = None
        self.score = None
        self.abbr_seen = []
        self.history = []           # (t_mur, temps restant)
        self.running = False
        self.last_change = 0.0

    # temps de jeu écoulé depuis le début du match (s), ou None
    @property
    def game_seconds(self):
        if self.period is None or self.remaining is None:
            return None
        p = min(self.period, 4)
        return (p - 1) * self.period_len + (self.period_len - self.remaining)

    def _parse(self, res, ox=0, oy=0):
        toks = []
        for box, text, conf in res:
            t = text.strip().upper().replace("O", "0") if any(c.isdigit() for c in text) else text.strip().upper()
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            toks.append(dict(t=t, raw=text.strip().upper(), conf=conf,
                             x1=min(xs) + ox, y1=min(ys) + oy, x2=max(xs) + ox, y2=max(ys) + oy))
        clock = [k for k in toks if CLOCK_RE.match(k["t"].replace(" ", ""))]
        abbr = [k for k in toks if k["raw"] in self.abbrs or (not self.abbrs and re.fullmatch(r"[A-Z]{3}", k["raw"]))]
        return toks, clock, abbr

    def _locate(self, frame):
        h, w = frame.shape[:2]
        found = None
        for (x0, x1) in ((0, w // 2), (w // 2, w)):
            for (y0, y1) in ((0, int(h * 0.22)), (int(h * 0.78), h)):
                crop = frame[y0:y1, x0:x1]
                toks, clock, abbr = self._parse(OCR.read(crop), x0, y0)
                if clock and (abbr or not self.abbrs):
                    c = clock[0]
                    near = [k for k in toks if abs((k["y1"] + k["y2"]) / 2 - (c["y1"] + c["y2"]) / 2) < (c["y2"] - c["y1"])]
                    X1 = min(k["x1"] for k in near) - 10
                    X2 = max(k["x2"] for k in near) + 10
                    Y1 = min(k["y1"] for k in near) - 6
                    Y2 = max(k["y2"] for k in near) + 6
                    found = (max(0, int(X1)), max(0, int(Y1)), min(w, int(X2)), min(h, int(Y2)))
                    self.abbr_seen = [k["raw"] for k in abbr]
                    break
            if found:
                break
        return found

    def update(self, frame, now=None):
        now = now or time.time()
        if self.box is None:
            if now >= self.next_search:
                self.box = self._locate(frame)
                self.next_search = now + 5.0
                if self.box:
                    self.log(f"[bandeau] trouvé en {self.box}, équipes lues : {self.abbr_seen}")
            return
        if now < self.next_read:
            return
        self.next_read = now + 1.0
        x1, y1, x2, y2 = self.box
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            self.box = None
            return
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        toks, clock, abbr = self._parse(OCR.read(crop))
        if not clock:
            self.misses = getattr(self, "misses", 0) + 1
            if self.misses > 10:          # bandeau disparu (pause, publicité) : on le recherchera
                self.box, self.misses = None, 0
            return
        self.misses = 0
        mm, ss = CLOCK_RE.match(clock[0]["t"].replace(" ", "")).groups()
        rem = int(mm) * 60 + int(ss)
        if rem > self.period_len + 5:
            return
        if self.remaining is not None and rem != self.remaining:
            self.last_change = now
        self.running = (now - self.last_change) < 2.5
        # nouvelle période : le chrono remonte
        if self.remaining is not None and rem > self.remaining + 60 and self.period:
            self.period += 1
        self.remaining = rem
        # période et score : jetons courts à gauche du chrono
        cx = clock[0]["x1"]
        for k in toks:
            m = re.fullmatch(r"P?([1-3])", k["t"])
            if m and k["x2"] <= cx + 2 and k["raw"].startswith("P"):
                self.period = int(m.group(1))
        if self.period is None:
            self.period = 1
        nums = [k for k in sorted(toks, key=lambda k: k["x1"]) if re.fullmatch(r"\d{1,2}", k["t"]) and k["x2"] <= cx
                and not k["raw"].startswith("P")]
        if len(nums) >= 2:
            self.score = (int(nums[0]["t"]), int(nums[1]["t"]))

    def state(self):
        return dict(found=self.box is not None, period=self.period, remaining=self.remaining,
                    running=self.running, score=self.score, game_s=self.game_seconds)


# --------------------------------------------------------------- numéros
def back_crop(frame, box, sleeves=False):
    """Zone du dos où se trouve le numéro (haut du corps) ; sleeves=True : toute la largeur
    des épaules, pour attraper aussi les numéros sur les manches."""
    x1, y1, x2, y2 = [int(v) for v in box]
    w, h = x2 - x1, y2 - y1
    mx = 0.0 if sleeves else 0.12
    cx1, cx2 = x1 + int(mx * w), x2 - int(mx * w)
    cy1, cy2 = y1 + int(0.12 * h), y1 + int((0.45 if sleeves else 0.58) * h)
    H, W = frame.shape[:2]
    cx1, cy1, cx2, cy2 = max(cx1, 0), max(cy1, 0), min(cx2, W), min(cy2, H)
    if cx2 - cx1 < 8 or cy2 - cy1 < 10:
        return None
    crop = frame[cy1:cy2, cx1:cx2]
    s = 96.0 / crop.shape[0]
    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    # filet devant la caméra : on efface les mailles fines
    crop = cv2.morphologyEx(crop, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return crop


def read_number(crop):
    """-> [(numéro, confiance)] (1 ou 2 chiffres). Reconnaissance directe sur la zone du dos
    (la détection de texte d'EasyOCR échoue sur des chiffres petits et flous)."""
    if crop is None:
        return []
    try:
        r = OCR.reader()
    except Exception:
        return []
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    cl = cv2.createCLAHE(3.0, (4, 4)).apply(g)
    h = g.shape[0]
    out = []
    for img in (cl[: int(h * 0.75)], 255 - cl[: int(h * 0.75)], g):
        img = cv2.resize(img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        try:
            with OCR.lock:
                res = r.recognize(img, horizontal_list=[[0, img.shape[1], 0, img.shape[0]]], free_list=[],
                                  allowlist="0123456789", detail=1)
        except Exception:
            continue
        for _, t, conf in res:
            t = t.strip()
            if 1 <= len(t) <= 2 and conf > 0.3:
                out.append((t.lstrip("0") or "0", float(conf)))
    return out


class NumberVotes:
    """Votes par piste de suivi, contraints par la feuille de match.

    Prudence sur les numéros à un chiffre : une lecture partielle d'un numéro à deux
    chiffres (« 5 » pour « 53 ») ressemble à un numéro à un chiffre. Il faut donc plus
    de lectures concordantes, et aucune lecture d'un numéro à deux chiffres qui le contient."""

    MIN_CONF = 0.5

    def __init__(self):
        self.votes = defaultdict(Counter)       # tid -> Counter(numéro -> poids)
        self.reads = defaultdict(Counter)       # tid -> Counter(numéro -> nombre de lectures)
        self.raw2 = defaultdict(Counter)        # tid -> numéros à 2 chiffres lus (même hors feuille)
        self.manual = {}                        # tid -> numéro fixé par le coach

    def add(self, tid, readings, allowed=None):
        best = {}
        for num, conf in readings:              # une lecture = un dos, quelles que soient les variantes
            if conf < self.MIN_CONF:
                continue
            if len(num) == 2:
                self.raw2[tid][num] += 1
            if allowed is not None and num not in allowed:
                if len(num) != 2:               # pas de correction pour un seul chiffre
                    continue
                # tolère une erreur d'un chiffre sur 2 (ex. 23 lu au lieu de 33)
                close = [a for a in allowed if len(a) == 2 and sum(x != y for x, y in zip(a, num)) == 1]
                if len(close) != 1:
                    continue
                num, conf = close[0], conf * 0.5
            best[num] = max(best.get(num, 0.0), conf)
        for num, conf in best.items():
            self.votes[tid][num] += conf
            self.reads[tid][num] += 1

    def get(self, tid, min_votes=1.2, ratio=0.65):
        if tid in self.manual:
            return self.manual[tid], 1.0
        c = self.votes.get(tid)
        if not c:
            return None, 0.0
        (num, v), tot = c.most_common(1)[0], sum(c.values())
        n = self.reads[tid][num]
        if len(num) == 1:
            if n < 3 or any(num in k for k in self.raw2[tid]):
                return None, v / tot
        elif n < 2:
            return None, v / tot
        if v >= min_votes and v / tot >= ratio:
            return num, v / tot
        return None, v / tot

    def forget(self, tid):
        for d in (self.votes, self.reads, self.raw2, self.manual):
            d.pop(tid, None)


class Worker(threading.Thread):
    """Exécute une fonction en arrière-plan sur des tâches déposées dans une file courte
    (les tâches en trop sont abandonnées : le direct ne prend jamais de retard)."""

    def __init__(self, fn, maxsize=6):
        super().__init__(daemon=True)
        self.fn = fn
        self.q = queue.Queue(maxsize=maxsize)
        self.out = queue.Queue()
        self.start()

    def put(self, job):
        try:
            self.q.put_nowait(job)
            return True
        except queue.Full:
            return False

    def run(self):
        while True:
            job = self.q.get()
            try:
                self.out.put((job, self.fn(job)))
            except Exception:
                pass

    def results(self):
        while True:
            try:
                yield self.out.get_nowait()
            except queue.Empty:
                return
