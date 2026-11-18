#!/usr/bin/env python3
"""HOCKEY AI — analyse EN DIRECT de la vidéo affichée à l'écran, avec overlay dans le navigateur.

  1. Installe l'extension (dossier extension/) dans Chrome ou Edge.
  2. Lance le moteur :      python live_engine.py
  3. Ouvre le match sur FFHG TV : l'overlay apparaît par-dessus la vidéo.

Le match du jour de Brest est trouvé tout seul sur le site FFHG (équipes, numéros, pénalités).
Aucune image n'est enregistrée : seules les infos (présences, distances, vitesses, zones,
événements) sont gardées dans matchs/<date>_<équipes>.json.

Tests sans direct (rejoue une vidéo comme si c'était le direct) :
  python live_engine.py --source match.mp4 --match-json ../data/raw/match_84389.json
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import queue
import re
import threading
import time
from collections import Counter, deque

import cv2
import numpy as np

from autocalib import AutoCameraTracker, estimate_camera_center
from live_capture import FileSource, ScreenSource, static_mask_from_frames
from live_ocr import NumberVotes, PlayerRecognizer, Scoreboard, Worker, back_crop, pose_crops, read_number
from live_memory import MatchMemory, tc_key
from live_state import Kin, Pressure, Recorder, Shifts
from rink import PRESETS, Rink
from teams import REF, UNK, TeamClassifier, jersey_feature, kit_features, otsu_threshold, referee_score

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
# dimensions connues : plus besoin de les deviner (1 800 m² = 60 x 30 m confirmé pour la Rïnkla)
KNOWN_RINKS = {"rinkla": "iihf"}
MEMORY_S = 30.0          # un joueur non vu depuis 30 s est sorti de la glace : on l'oublie
COLORS = {"our": "#4a9eff", "them": "#f5b928"}


def slug(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s or "patinoire").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "patinoire"


def calibrate_worker(frames, preset, static):
    """Exécuté dans un processus séparé : position de la caméra pour un format de patinoire."""
    c, cost = estimate_camera_center(None, Rink(preset), None, static, log=lambda *a: None,
                                     frames=list(enumerate(frames)))
    return (None if c is None else [float(v) for v in c]), (None if cost is None else float(cost))


# ------------------------------------------------------------------ détection
class Detector:
    """YOLO (personnes). track() : suivi plein écran ; detect() : image ou zone, sans suivi.
    Avec un modèle « -pose » (par défaut), chaque joueur a aussi un squelette de 17 points
    (tête, épaules, coudes, poignets, hanches, genoux, chevilles)."""

    def __init__(self, weights="yolov8s-pose.pt", imgsz=960, conf=0.3, device=None):
        from ultralytics import YOLO
        self.track_model = YOLO(weights)
        self.det_model = YOLO(weights)
        try:                                    # pas de messages répétés dans la console
            from ultralytics.utils import LOGGER
            LOGGER.setLevel("ERROR")
        except Exception:
            pass
        self.kw = dict(classes=[0], conf=conf, iou=0.5, verbose=False)
        if device:
            self.kw["device"] = device
        self.imgsz = imgsz

    @staticmethod
    def _parse(r, ox=0, oy=0, tracked=False):
        out = []
        if r.boxes is None or len(r.boxes) == 0:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy()
        ids = r.boxes.id.cpu().numpy().astype(int) if tracked and r.boxes.id is not None else None
        kps = None
        if getattr(r, "keypoints", None) is not None and r.keypoints.data is not None and len(r.keypoints.data):
            kps = r.keypoints.data.cpu().numpy()          # (N, 17, 3) : x, y, confiance
        for i, b in enumerate(xyxy):
            if tracked and ids is None:
                continue
            kp = None
            if kps is not None and i < len(kps):
                kp = kps[i].copy()
                kp[:, 0] += ox
                kp[:, 1] += oy
            out.append(dict(tid=int(ids[i]) if ids is not None else -1,
                            box=np.array([b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy]), poly=None, kp=kp))
        return out

    def track(self, frame, idx=None):
        r = self.track_model.track(frame, persist=True, tracker=os.path.join(HERE, "botsort_live.yaml"), imgsz=self.imgsz, **self.kw)[0]
        return self._parse(r, tracked=True)

    def detect(self, frame, rect=None, imgsz=None, idx=None):
        if rect is None:
            r = self.det_model.predict(frame, imgsz=imgsz or self.imgsz, **self.kw)[0]
            return self._parse(r)
        x1, y1, x2, y2 = rect
        r = self.det_model.predict(frame[y1:y2, x1:x2], imgsz=imgsz or 320, **self.kw)[0]
        return self._parse(r, x1, y1)

    def reset(self):
        try:
            self.track_model.predictor.trackers[0].reset()
        except Exception:
            pass


class GTDetector:
    """Détections « vérité terrain » de la vidéo synthétique (tests sans YOLO)."""

    def __init__(self, path):
        with open(path) as f:
            self.frames = json.load(f)["frames"]

    def _get(self, idx):
        fr = self.frames[idx] if idx is not None and idx < len(self.frames) else {"players": []}
        return [dict(tid=p["id"], box=np.array(p["box"], float), poly=None, kp=self._fake_kp(p["box"]))
                for p in fr["players"]]

    @staticmethod
    def _fake_kp(b):
        """Squelette approximatif (tests) déduit du rectangle : vu de dos."""
        x1, y1, x2, y2 = b
        w, h = x2 - x1, y2 - y1
        P = lambda fx, fy: [x1 + fx * w, y1 + fy * h, 0.9]
        kp = [P(.5, .08), [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], P(.25, .22), P(.75, .22), P(.15, .38), P(.85, .38),
              P(.12, .52), P(.88, .52), P(.35, .55), P(.65, .55), P(.33, .75), P(.67, .75), P(.32, .97), P(.68, .97)]
        kp = np.array(kp, float)
        kp[0, 2] = 0.1                       # visage non visible : de dos
        return kp

    def track(self, frame, idx=None):
        return self._get(idx)

    def detect(self, frame, rect=None, imgsz=None, idx=None):
        d = self._get(idx)
        if rect is not None:
            x1, y1, x2, y2 = rect
            d = [p for p in d if x1 <= (p["box"][0] + p["box"][2]) / 2 <= x2 and y1 <= (p["box"][1] + p["box"][3]) / 2 <= y2]
        for p in d:
            p["tid"] = -1
        return d

    def reset(self):
        pass


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)


def auto_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
    except Exception:
        pass
    return "cpu"


# --------------------------------------------------------------- serveur WS
class Hub:
    """Serveur WebSocket local : envoie l'état à l'extension, reçoit ses commandes."""

    def __init__(self, port=PORT, log=print):
        self.port, self.log = port, log
        self.clients = set()
        self.inbox = queue.Queue()
        self.loop = None
        self.last_hello = {}

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        while self.loop is None:
            time.sleep(0.01)

    def _run(self):
        import websockets
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def handler(ws, path=None):
            self.clients.add(ws)
            self.inbox.put({"type": "hello"})
            try:
                async for raw in ws:
                    try:
                        self.inbox.put(json.loads(raw))
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                self.clients.discard(ws)

        async def main():
            async with websockets.serve(handler, "127.0.0.1", self.port, max_size=2 ** 22):
                await asyncio.Future()

        try:
            self.loop.run_until_complete(main())
        except OSError:
            self.log(f"[erreur] le port {self.port} est déjà pris : un autre moteur tourne déjà ? "
                     "Ferme-le ou lance avec --port 8766 (et change l'adresse dans l'extension).")
            os._exit(1)

    def send(self, msg):
        if not self.clients:
            return
        data = json.dumps(msg, ensure_ascii=False, default=float)

        async def go():
            for ws in list(self.clients):
                try:
                    await ws.send(data)
                except Exception:
                    self.clients.discard(ws)
        asyncio.run_coroutine_threadsafe(go(), self.loop)


# ------------------------------------------------------------------- moteur
class Engine:
    def __init__(self, args):
        self.a = args
        self.log = print
        self.hub = Hub(args.port)
        # source
        if args.source:
            self.src = FileSource(args.source, speed=args.speed, start=args.start, loop=args.loop)
        else:
            self.src = ScreenSource(use_dxcam=args.dxcam)
        # feuille de match
        self.feed = None
        self.match = None
        self._load_match()
        # détection
        if args.gt:
            self.det = GTDetector(args.gt)
        else:
            self.det = Detector(args.weights, args.imgsz, args.conf, args.device or auto_device())
        # Tout charger ICI, avant de lancer les fils en parallèle : sinon deux fils importent
        # torchvision en même temps et Python s'arrête (« deadlock detected by _ModuleLock »).
        self._preload()
        # état
        self.mode = "team"
        self.focus = "our"                  # équipe analysée : une seule à la fois ("our" / "them")
        self.selected = []                  # [(camp, num)] : un seul joueur isolé
        self.detail = {}                    # (camp, num) -> dict(speeds=deque, trail=deque)
        self.locked = {}                    # (camp, num) -> dict(box, t, vel) en mode joueurs
        self.clf = TeamClassifier(detect_refs=True)
        self.clf_ready = False
        self.cluster_side = {}              # cluster (0/1) -> "our"/"them"
        self.side_votes = np.zeros((2, 2))  # [cluster, camp(our, them)]
        self.votes = NumberVotes()
        self.next_ocr = {}
        self.kin = {}                       # (camp, num) -> Kin
        self.kin_tid = {}                   # tid -> Kin (joueurs non identifiés)
        self.ui_rects = []
        self.static = None
        self.cam = None
        self.calib_state = "attente"
        self.calib_frames = []
        self.last_frame_t = 0
        self.size = None
        self.sb = Scoreboard(self._abbrs(), (self.match.period_len if self.match else 1200), self.log)
        self.rink = Rink(args.rink or "iihf")
        self.pressure = Pressure(self.rink)
        self.shifts = {"our": Shifts(self._goalies("our")), "them": Shifts(self._goalies("them"))}
        self.pseudo_game = 0.0
        self.last_period = None
        self.fps = 0.0
        self.last_send = 0
        self.last_full = 0
        self.t0 = time.time()
        name = dt.date.today().isoformat()
        if self.match:
            name += f"_{self.match.abbr['home']}-{self.match.abbr['away']}"
        self.rec = Recorder(os.path.join(HERE, "matchs"), name)
        # mémoire du match (identités par instant de jeu), partagée entre tous les visionnages
        mid = str(self.a.match or (os.path.splitext(os.path.basename(self.a.match_json))[0] if self.a.match_json
                                   else getattr(self.feed, "mid", name)))
        self.memory = MatchMemory(os.path.join(HERE, "matchs", f"memoire_{mid}.json"), self.log)
        self.mem_key = None
        self.last_score = None
        self.calib_t = 0.0
        self.last_cluster = {}
        self.manual_side = {}
        self.known = {}          # tid -> dict(key, cluster, t, world, center, h, valid) : joueurs identifiés
        self.validated = {}      # tid -> (camp, num) : identité sûre, le numéro n'est plus relu
        self.recent_lost = set() # (camp, num) sortis du champ depuis moins de 30 s
        self.sides_locked = False
        self.our_color = None
        self.need_check = {}     # tid -> instant : identité à revérifier (croisement de joueurs)
        self.next_verify = {}
        self.contra = {}
        self.prev_small = None
        self.collect_next = {}
        self.track_hist = {}
        self.ref_hits = {}
        self.ref_obs = {}
        self.lum_samples = []
        self.red_samples = []
        self.red_team = None
        self.lum_thr = 110.0
        self.team_votes = {}
        self.striped = {}
        self.ref_disabled = False
        self.next_recog = {}
        self.pose_smooth = {}
        self.goalie_track = {}
        self.ice_hist = {}
        self.seen_tids = set()
        self.new_tracks = 0
        self.goalie_logged = set()
        self.defends = {}
        self.vel_smooth = {}
        self.crossings = {}
        self.collect_count = {}
        self.still_since = None
        self.paused = False
        self.pause_done = set()
        # lecture du bandeau (1 fois/s) et des numéros : en arrière-plan
        self.latest = None
        if not os.environ.get("HOCKEY_NO_SB"):
            threading.Thread(target=self._scoreboard_loop, daemon=True).start()
        self.ocr = Worker(lambda job: read_number(job["crop"]), maxsize=4)
        self.ocr_pause = Worker(lambda job: read_number(job["crop"]), maxsize=40)

    def _preload(self):
        self.log("[ok] chargement des modèles (détection des joueurs, lecture de texte)…")
        try:
            import torch  # noqa: F401
            import torchvision  # noqa: F401
        except Exception:
            pass
        try:
            from live_ocr import OCR
            OCR.reader()
        except Exception as ex:
            self.log(f"[ocr] lecture de texte indisponible ({ex}) : numéros et bandeau désactivés")
        # modèles de reconnaissance des joueurs entraînés sur les vignettes (train_numeros.py)
        self.recog = {}
        if self.match:
            for side in ("our", "them"):
                ab = self.match.abbr[self._side_key(side)]
                path = os.path.join(HERE, "modeles", f"joueurs_{ab}.pt")
                if os.path.exists(path):
                    try:
                        self.recog[side] = PlayerRecognizer(path)
                        acc = self.recog[side].acc
                        self.log(f"[modèle] reconnaissance des joueurs {ab} : {len(self.recog[side].classes)} joueurs"
                                 + (f", justesse {100 * acc:.0f} % à l'entraînement" if acc is not None else ""))
                    except Exception as ex:
                        self.log(f"[modèle] {path} illisible : {ex}")
        try:
            dummy = np.zeros((360, 640, 3), np.uint8)
            self.det.detect(dummy)
        except Exception as ex:
            self.log(f"[détection] préchauffage impossible : {ex}")

    # ----------------------------------------------------------- feuille FFHG
    def _load_match(self):
        from live_ffhg import FFHGFeed, OfflineFeed, pick_match
        a = self.a
        try:
            if a.match_json:
                self.feed = OfflineFeed(a.match_json, a.equipe)
            else:
                mid = a.match or pick_match(a.equipe, log=self.log)
                if mid:
                    self.feed = FFHGFeed(int(mid), a.equipe, log=self.log)
                    self.feed.start()
            if self.feed:
                self.match = self.feed.match
                m = self.match
                self.log(f"[ffhg] {m.short['home']} / {m.short['away']} · {m.rink_name} · "
                         f"{len(m.roster['home'])} + {len(m.roster['away'])} joueurs")
            else:
                self.log(f"[ffhg] aucun match de {a.equipe} aujourd'hui : analyse sans feuille de match")
        except Exception as e:
            self.log(f"[ffhg] feuille de match indisponible ({e}) : analyse sans noms")

    def _abbrs(self):
        return (self.match.abbr["home"], self.match.abbr["away"]) if self.match else ("", "")

    def _side_key(self, side):          # "our"/"them" -> "home"/"away"
        if not self.match:
            return None
        return self.match.our if side == "our" else self.match.them

    def _roster(self, side):
        k = self._side_key(side)
        return self.match.roster[k] if k else {}

    def _goalies(self, side):
        k = self._side_key(side)
        return self.match.goalies(k) if k else set()

    # ------------------------------------------------------------ calibration
    def _profile_path(self):
        name = self.match.rink_name if self.match else (self.a.patinoire or "patinoire")
        return os.path.join(HERE, "patinoires", slug(name) + ".json")

    def _calibrate_async(self):
        """Position de la caméra : profil de la patinoire si connu, sinon estimation (1 à 2 min)."""
        path = self._profile_path()
        known = next((pr for k, pr in KNOWN_RINKS.items() if k in slug(path)), None)
        p = None
        if os.path.exists(path) and not self.a.recalib:
            with open(path) as f:
                p = json.load(f)
            if known and p.get("rink") != known and not self.a.rink:
                self.log(f"[calib] ancien profil en {p.get('rink')} alors que la patinoire est en {known} : "
                         "nouvelle calibration")
                p = None
        if p is not None:
            self.rink = Rink(self.a.rink or p["rink"])
            self.pressure.rink = self.rink
            self.calib_score = p.get("score")
            self._start_tracker(np.array(p["camera"]))
            self.log(f"[calib] profil {os.path.basename(path)} : patinoire {self.rink.L:.0f}×{self.rink.W:.0f} m")
            return
        self.calib_state = "calcul"
        frames = list(self.calib_frames)
        self.log(f"[calib] calcul de la position de la caméra sur {len(frames)} images "
                 "(1 à 3 min, une seule fois pour cette patinoire)…")

        def work():
            from concurrent.futures import ProcessPoolExecutor
            known = next((p for k, p in KNOWN_RINKS.items() if k in slug(self._profile_path())), None)
            presets = [self.a.rink or known] if (self.a.rink or known) else ["iihf", "iihf_26"]
            best = (1e9, None, None)
            t0 = time.time()
            # un processus par format de patinoire : calcul en parallèle, hors du direct
            with ProcessPoolExecutor(max_workers=len(presets)) as ex:
                futs = {pr: ex.submit(calibrate_worker, frames, pr, self.static) for pr in presets}
                for pr, fu in futs.items():
                    try:
                        c, cost = fu.result()
                    except Exception as ex_:
                        self.log(f"[calib] {pr} : erreur {ex_}")
                        continue
                    rk = Rink(pr)
                    self.log(f"[calib] {pr} ({rk.L:.0f}×{rk.W:.0f}) : score "
                             f"{cost if cost is not None else float('nan'):.3f}")
                    if c is not None and cost < best[0]:
                        best = (cost, np.asarray(c), pr)
            self.log(f"[calib] terminé en {time.time() - t0:.0f} s")
            if best[1] is None:
                self.calib_state = "échec"
                self.log("[calib] patinoire introuvable : vérifie que la vidéo montre bien la glace")
                return
            self.rink = Rink(best[2])
            self.pressure.rink = self.rink
            self.calib_score = float(best[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(dict(rink=best[2], camera=[float(v) for v in best[1]], score=float(best[0]),
                               date=dt.date.today().isoformat()), f, indent=1)
            self.log(f"[calib] patinoire {self.rink.L:.0f}×{self.rink.W:.0f} m, caméra en "
                     f"({best[1][0]:.1f}, {best[1][1]:.1f}, {best[1][2]:.1f}) m -> {os.path.basename(path)}")
            self._start_tracker(best[1])

        threading.Thread(target=work, daemon=True).start()

    def _start_tracker(self, center):
        # seuil d'acceptation calé sur la qualité obtenue à la calibration de CETTE patinoire
        # (vraie vidéo compressée, filet : scores plus élevés que sur une image idéale)
        sc = getattr(self, "calib_score", None)
        accept = float(np.clip(1.3 * sc, 0.45, 0.7)) if sc else 0.55
        self.cam = AutoCameraTracker(self.rink, self.size, center, static_mask=self._mask(), log=self.log,
                                     background=True, accept=accept)
        self.cam.debug = bool(os.environ.get("HOCKEY_DEBUG"))
        self.calib_state = "ok"

    def _mask(self):
        """Zones à ignorer : incrustations TV fixes + panneaux de l'overlay."""
        if self.size is None:
            return None
        w, h = self.size
        m = np.zeros((h, w), np.uint8) if self.static is None else self.static.copy()
        for x1, y1, x2, y2 in self.ui_rects:
            m[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)] = 255
        if self.sb.box:
            x1, y1, x2, y2 = self.sb.box
            m[max(0, y1 - 4):y2 + 4, max(0, x1 - 4):x2 + 4] = 255
        return m if m.any() else None

    # ------------------------------------------------------------ messages
    def handle(self, msg):
        t = msg.get("type")
        if t == "hello":
            self.hub.send(self.roster_msg())
            if not self.a.source:
                self.hub.send({"type": "locate"})
        elif t == "locate_ready":              # l'extension affiche les marqueurs magenta
            reg = self.src.locate()
            self.hub.send({"type": "located", "ok": reg is not None})
            if reg:
                self.log(f"[capture] vidéo trouvée à l'écran : {reg}")
                self._new_size()
        elif t == "mode" and msg.get("mode") in ("team", "players"):
            self.mode = msg["mode"]
            self.locked.clear()
            self.det.reset()
            self.log(f"[mode] {'équipe' if self.mode == 'team' else 'joueurs choisis'}")
        elif t == "focus" and msg.get("side") in ("our", "them"):
            self.focus = msg["side"]
            self.selected = [k for k in self.selected if k[0] == self.focus]
            self.locked = {k: v for k, v in self.locked.items() if k[0] == self.focus}
            self.log(f"[focus] équipe analysée : {self.focus}")
        elif t == "select":
            self.selected = [(p["side"], str(p["num"])) for p in msg.get("players", [])
                             if p.get("side") == self.focus][:1]
            self.locked = {k: v for k, v in self.locked.items() if k in self.selected}
        elif t == "referee":                   # le coach désigne (ou retire) un arbitre
            tid = int(msg.get("tid"))
            self.ref_hits[tid] = 1000 if msg.get("is_ref") else -10000
            if msg.get("is_ref"):
                self.validated.pop(tid, None); self.votes.manual.pop(tid, None); self.manual_side.pop(tid, None)
            self.log(f"[arbitres] piste {tid} : {'arbitre' if msg.get('is_ref') else 'pas un arbitre'} (à la main)")
        elif t == "assign":                    # le coach a cliqué un joueur et choisi son nom
            tid, side, num = msg.get("tid"), msg.get("side"), str(msg.get("num"))
            if tid is not None:
                self.ref_hits[int(tid)] = -10000     # nommé comme joueur : jamais arbitre
            if tid is not None:
                self.votes.manual[int(tid)] = num
                self.manual_side[int(tid)] = side
                self.validated[int(tid)] = (side, num)
                for t in list(self.votes.manual):        # la dernière identification à la main l'emporte
                    if t != int(tid) and self.votes.manual[t] == num and self.manual_side.get(t) == side:
                        self.votes.manual.pop(t, None); self.manual_side.pop(t, None); self.validated.pop(t, None)
                for t, k in list(self.validated.items()):
                    if t != int(tid) and k == (side, num):
                        self.validated.pop(t, None)
                n = self.memory.correct_track(int(tid), side, num)
                if n:
                    self.log(f"[mémoire] séquence précédente du joueur corrigée ({n} instants)")
                info = self._roster(side).get(num, {})
                self.log(f"[clic] {self._name(side)} #{num} {info.get('short', '')} attribué")
                cl = self.last_cluster.get(int(tid))
                if cl in (0, 1):
                    self.side_votes[cl][0 if side == "our" else 1] += 10
            if self.mode == "players" and msg.get("box") and self.size:
                W, Hh = self.size
                b = np.array(msg["box"], float) * [W, Hh, W, Hh]
                self.locked[(side, num)] = dict(box=b, t=time.time(), vel=np.zeros(2))
        elif t == "swap_colors":
            self.swap_sides()
        elif t == "ui":
            self.ui_rects = [tuple(r) for r in msg.get("rects", [])]
            if self.cam is not None:
                self.cam.static = self._mask()

    def roster_msg(self):
        if not self.match:
            return {"type": "roster", "our": [], "them": [], "names": {}}
        s = self.match.summary()
        o, t = self.match.our, self.match.them
        return {"type": "roster", "our": s["roster"][o], "them": s["roster"][t],
                "names": {"our": s["home" if o == "home" else "away"], "them": s["home" if t == "home" else "away"]},
                "abbr": {"our": self.match.abbr[o], "them": self.match.abbr[t]}, "colors": COLORS,
                "rink": dict(L=self.rink.L, W=self.rink.W, blue=self.rink.blue_x, goal=self.rink.goal_x,
                             r=self.rink.r, dot_y=self.rink.dot_y, end_dot=self.rink.end_dot_x, circle=self.rink.circle_r)}

    def _new_size(self):
        self.size = self.src.size
        self.calib_frames.clear()
        self.static = None
        if self.cam is not None:
            self._start_tracker(self.cam.center)

    def _scoreboard_loop(self):
        while True:
            f = self.latest
            if f is not None:
                try:
                    self.sb.update(f)
                except Exception as ex:
                    self.log(f"[bandeau] erreur de lecture : {ex}")
            time.sleep(0.08)

    def timecode(self, now):
        """(temps de jeu, fraction, arrêt) d'après le chrono du bandeau, ou None."""
        gs = self.sb.game_seconds
        if gs is None or not self.sb.box:
            return None
        since = now - (self.sb.last_change or now)
        if self.sb.running:
            return gs, min(max(since, 0.0), 0.99), None
        return gs, 0.0, since

    def _restore_from_memory(self, dets, now):
        """Remet les identités enregistrées pour cet instant (relecture du même match)."""
        tc = self.timecode(now)
        if tc is None or not self.memory.frames:
            return
        entries = self.memory.lookup(*tc)
        if not entries:
            return
        W, Hh = self.size
        session_manual = {(self.manual_side.get(t), n) for t, n in self.votes.manual.items()}
        pts = []
        for d in dets:
            if d["tid"] < 0 or d["tid"] in self.votes.manual or d["cluster"] == REF:
                continue
            x1, y1, x2, y2 = d["box"]
            pts.append((d, ((x1 + x2) / 2 / W, y2 / Hh), (y2 - y1) / Hh))
        # regroupe les entrées par identité, garde la meilleure (main récente > auto)
        by_id = {}
        for en in entries:
            if en[1]:                                    # entrées sans nom : ignorées
                by_id.setdefault((en[0], en[1]), []).append(en)
        cands = []
        for ident, ens in by_id.items():
            if ident in session_manual:                  # déjà fixé à la main dans cette séance
                continue
            en = MatchMemory.best_identity(ens)
            for d, (x, y), h in pts:
                if d["side"] and d["side"] != en[0]:
                    continue
                dist = np.hypot(x - en[2], y - en[3])
                if dist < max(0.6 * h, 0.015):
                    cands.append((dist, ident, en, d))
        used_id, used_det = set(), set()
        # main avant auto ; entre deux identifications à la main, la plus récente l'emporte
        for dist, ident, en, d in sorted(cands, key=lambda c: (c[2][5] != "main", -c[2][6], c[0])):
            if ident in used_id or d["tid"] in used_det:
                continue
            cur = self.validated.get(d["tid"])
            if cur == ident:
                used_id.add(ident); used_det.add(d["tid"])
                continue
            if cur is not None and en[5] != "main":
                continue                                 # une identité auto n'écrase rien
            used_id.add(ident); used_det.add(d["tid"])
            side, num = ident
            self.validated[d["tid"]] = (side, num)
            if en[5] == "main":
                self.votes.manual[d["tid"]] = num
                self.manual_side[d["tid"]] = side
            for t, k in list(self.validated.items()):    # un numéro, un seul joueur
                if t != d["tid"] and k == ident and t not in self.votes.manual:
                    self.validated.pop(t, None)

    def _record_memory(self, dets, now):
        tc = self.timecode(now)
        if tc is None:
            return
        key = tc_key(*tc)
        if key == self.mem_key:
            return
        self.mem_key = key
        W, Hh = self.size
        entries = []
        for d in dets:
            # tous les joueurs suivis, même sans nom : un clic plus tard remplira leur passé
            if d.get("side") and d["tid"] >= 0 and d.get("cluster") != REF:
                x1, y1, x2, y2 = d["box"]
                num = d.get("num")
                src = "main" if d["tid"] in self.votes.manual else ("auto" if num else "?")
                entries.append((d["side"], num, (x1 + x2) / 2 / W, y2 / Hh, (y2 - y1) / Hh, src, int(d["tid"])))
        if entries:
            self.memory.record(key, entries)

    # ------------------------------------------------------------ temps de jeu
    def game_time(self, now, dtw):
        gs = self.sb.game_seconds
        if gs is not None:
            return float(gs)
        self.pseudo_game += dtw            # pas de bandeau : temps réel
        return self.pseudo_game

    # ---------------------------------------------------------------- boucle
    def run(self):
        self.hub.start()
        self.log(f"[ok] moteur prêt · overlay sur ws://127.0.0.1:{self.a.port} · Ctrl+C pour arrêter")
        if self.a.source:
            self._new_size()
        prev = time.time()
        try:
            while True:
                while not self.hub.inbox.empty():
                    self.handle(self.hub.inbox.get())
                if getattr(self.src, "finished", False):
                    break
                if self.size is None:
                    time.sleep(0.05)
                    continue
                target = self.a.fps if self.mode == "team" else self.a.fps_players
                wait = 1.0 / target - (time.time() - prev)
                if wait > 0:
                    time.sleep(wait)
                now = time.time()
                idx, frame = self.src.read()
                if frame is None:
                    continue
                if (frame.shape[1], frame.shape[0]) != tuple(self.size):
                    self.size = (frame.shape[1], frame.shape[0])
                dtw = now - prev
                prev = now
                self.process(idx, frame, now, dtw)
                self.fps = 0.9 * self.fps + 0.1 / max(dtw, 1e-3)
        except KeyboardInterrupt:
            pass
        self.save(force=True)
        self.log(f"[ok] infos du match enregistrées dans {self.rec.path}")

    # -------------------------------------------------------------- 1 image
    def process(self, idx, frame, now, dtw):
        a = self.a
        self.cap_ts = time.time()                         # instant de la capture (horloge du PC)
        self.latest = frame
        game_t = self.game_time(now, dtw)
        if self.sb.period and self.sb.period != self.last_period:
            if self.last_period is not None:       # changement de côté à chaque période
                self.pressure.reset_side()
                self.defends = {}
                self.rec.event(game_t, "periode", periode=self.sb.period)
            self.last_period = self.sb.period
        if self.sb.score and self.sb.score != self.last_score:
            if self.last_score is not None:
                self.rec.event(game_t, "score", score=list(self.sb.score))
            self.last_score = self.sb.score

        # (le lissage du squelette se fait après la détection, voir _smooth_pose)
        # --- vidéo en pause ? (image identique depuis plus d'1 s) -> lecture approfondie des numéros
        # vraie pause = image STRICTEMENT figée (en jeu, même caméra immobile, le bruit de la
        # compression et les joueurs font bouger l'image bien plus que ce seuil)
        g = cv2.cvtColor(cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if self.prev_small is not None and g.shape == self.prev_small.shape and np.abs(g - self.prev_small).mean() < 0.12:
            self.still_since = self.still_since or now
        else:
            if self.paused:
                self.log("[pause] lecture reprise")
            self.still_since, self.paused, self.pause_done = None, False, set()
        self.prev_small = g
        if self.still_since and now - self.still_since > 1.5 and not self.paused:
            self.paused = True
            self.log("[pause] vidéo en pause : lecture approfondie des numéros (et clics « Identifier »)")

        # --- détection
        dets = self._detect(idx, frame, now)
        # public, banc, tribunes : on ne garde que les personnes qui ont les pieds sur la glace
        dets = [d for d in dets if self._keep_on_ice(frame, d)]
        self._smooth_pose(dets, now)
        for d in dets:                                    # diagnostic : pistes nouvelles (= joueurs perdus puis retrouvés)
            if d["tid"] >= 0 and d["tid"] not in self.seen_tids:
                self.seen_tids.add(d["tid"])
                self.new_tracks += 1
        boxes = [d["box"] for d in dets]

        # --- calibration : on collecte quelques images, puis estimation en arrière-plan
        if self.cam is None and self.calib_state == "attente" and os.path.exists(self._profile_path()) \
                and not self.a.recalib:
            self._calibrate_async()
        if self.cam is None and self.calib_state == "attente":
            if not self.calib_frames or now - self.calib_t > 1.5:
                self.calib_frames.append(frame.copy())
                self.calib_t = now
                if len(self.calib_frames) >= 8:
                    self.static = static_mask_from_frames(self.calib_frames)
                    self._calibrate_async()
        H = self.cam.update(idx, frame, boxes) if self.cam is not None else None
        self._last_H = H

        # --- équipes : règles explicites, pas de regroupement automatique
        #   torse sombre = équipe en maillot foncé (cluster 0), torse clair = maillot clair (1) ;
        #   culotte rouge = équipe claire du jour (Orléans : maillot blanc, culotte rouge) ;
        #   doute = pas d'équipe (squelette gris). Le seuil sombre/clair est calculé sur les
        #   joueurs du match (méthode d'Otsu), puis voté par joueur suivi (70 % d'accord).
        Hh = frame.shape[0]
        feats = {}
        for d in dets:
            x1, y1, x2, y2 = d["box"]
            if (y2 - y1) < 40:
                continue
            f = kit_features(frame, d["box"], d.get("kp"))
            if f is not None:
                feats[id(d)] = f
                if not self.clf_ready and self.ref_hits.get(d["tid"], 0) < 100:
                    self.lum_samples.append(f[0])
                    self.red_samples.append(f[1])
        if not self.clf_ready and len(self.lum_samples) >= 80:
            self.lum_thr = otsu_threshold(self.lum_samples)
            dark = [v for v in self.lum_samples if v < self.lum_thr]
            light = [v for v in self.lum_samples if v >= self.lum_thr]
            self.clf_ready = True
            self.clf.centers = np.array([[np.median(dark) if dark else 50, 0, 0], [np.median(light) if light else 200, 0, 0]])
            # culotte rouge caractéristique d'une des deux équipes ? (ex. Orléans : maillot blanc, culotte rouge)
            rl = [r for v, r in zip(self.lum_samples, self.red_samples) if v >= self.lum_thr]
            rd = [r for v, r in zip(self.lum_samples, self.red_samples) if v < self.lum_thr]
            ml, md = (np.median(rl) if rl else 0), (np.median(rd) if rd else 0)
            self.red_team = 1 if (ml > 0.25 and md < 0.1) else (0 if (md > 0.25 and ml < 0.1) else None)
            if self.red_team is not None:
                self.log(f"[équipes] culotte rouge = équipe en maillot {'clair' if self.red_team == 1 else 'sombre'}")
            self.log(f"[équipes] maillots sombres ≈ {self.clf.centers[0][0]:.0f}, clairs ≈ {self.clf.centers[1][0]:.0f} "
                     f"(seuil {self.lum_thr:.0f})")
        self.last_cluster = {}
        for d in dets:
            f = feats.get(id(d))
            lab = UNK
            if self.clf_ready and f is not None:
                L, red = f
                margin = 0.22 * abs(self.clf.centers[1][0] - self.clf.centers[0][0])   # zone de doute
                rt = self.red_team
                if rt is not None and red > 0.3:
                    lab = rt                                  # culotte rouge : jamais l'autre équipe
                elif L < self.lum_thr - margin and not (rt == 1 and red > 0.15):
                    lab = 0
                elif L > self.lum_thr + margin and not (rt == 0 and red > 0.15):
                    lab = 1
            # maillot rayé (probable arbitre) : aucune équipe tant que le coach ne l'a pas désigné
            if lab in (0, 1) and (d["box"][3] - d["box"][1]) >= 40 and d["tid"] not in self.manual_side \
                    and referee_score(frame, d["box"], d.get("kp")) > 0.2:
                lab = UNK
                self.striped[d["tid"]] = self.striped.get(d["tid"], 0) + 1
            if d["tid"] >= 0:
                c = self.team_votes.setdefault(d["tid"], Counter())
                if lab in (0, 1):
                    c[lab] += 1
                if c:
                    (best, nb), tot = c.most_common(1)[0], sum(c.values())
                    lab = best if (nb >= 3 and nb / tot >= 0.7) else UNK
                    if self.striped.get(d["tid"], 0) >= 3 and self.striped[d["tid"]] >= 0.3 * tot:
                        lab = UNK                             # souvent rayé : on ne l'attribue pas
            # arbitres : seulement ceux désignés à la main par le coach (« ⚑ Arbitre »)
            if self.ref_hits.get(d["tid"], 0) >= 100:
                lab = REF
            d["cluster"] = lab
            self.last_cluster[d["tid"]] = lab
        self._resolve_sides()
        for d in dets:
            lab = d["cluster"]
            d["side"] = self.cluster_side.get(lab) if lab in (0, 1) else None
            if d["tid"] in self.manual_side:              # choix du coach : prioritaire
                d["side"] = self.manual_side[d["tid"]]

        # --- mémoire du match : identités déjà connues pour cet instant (relecture)
        self._restore_from_memory(dets, now)
        # --- numéros
        self._read_numbers(frame, dets, now)
        for d in dets:
            d["num"] = None
            if d.get("key"):
                d["side"], d["num"] = d["key"]
            elif d["tid"] in self.validated:
                d["side"], d["num"] = self.validated[d["tid"]]
            elif d["tid"] >= 0:
                num, conf = self.votes.get(d["tid"])
                if num and d["side"] and num in self._roster(d["side"]):
                    d["num"] = num
                    n = self.votes.reads[d["tid"]][num]
                    avg = self.votes.votes[d["tid"]][num] / max(n, 1)
                    if d["tid"] in self.votes.manual or (conf >= 0.85 and n >= 4 and avg >= 0.6):
                        self.validated[d["tid"]] = (d["side"], num)      # validé : plus de lecture

        self._record_memory(dets, now)
        if self.a.collecte:
            self._collect(frame, dets, now)

        # croisement de deux joueurs de la même équipe : le suivi peut échanger leurs identités
        self._crossing_guard(dets, now)

        # --- positions sur la glace, vitesses, présences
        world = []
        if H is not None and boxes:
            feet = np.float32([[(b[0] + b[2]) / 2, b[3]] for b in boxes]).reshape(-1, 1, 2)
            wp = cv2.perspectiveTransform(feet, H).reshape(-1, 2)
            for d, (x, y) in zip(dets, wp):
                d["world"] = (float(x), float(y)) if self.rink.inside(x, y, 1.5) else None
        self._detect_goalies(dets, now, game_t)
        self._persist_ids(dets, now)
        if self.mode == "players":            # joueur choisi reconnu à sa position : on le suit
            for d in dets:
                k = (d["side"], d["num"])
                if d["num"] and k in self.selected and k not in self.locked:
                    self.locked[k] = dict(box=d["box"], t=now, vel=np.zeros(2))
        speeds = {"our": [], "them": []}
        xs = []
        for d in dets:
            w = d.get("world")
            if w is None or d["side"] is None:
                continue
            key = (d["side"], d["num"]) if d["num"] else None
            xs.append(w[0])                     # le jeu (pression) se repère avec tous les joueurs
            if d["side"] != self.focus:
                continue
            if self.mode == "players" and key not in self.selected:
                continue
            k = self.kin.setdefault(key, Kin()) if key else self.kin_tid.setdefault(d["tid"], Kin())
            k.update(now, w)
            d["kin"] = k
            speeds[d["side"]].append(k.speed)
            if key in self.selected:
                dd = self.detail.setdefault(key, dict(speeds=deque(maxlen=120),
                                                      trail=deque(maxlen=240), t=0))
                if now - dd["t"] >= 0.5:
                    dd["t"] = now
                    dd["speeds"].append(round(k.speed, 1))
                    dd["trail"].append((round(k.pos[0], 1), round(k.pos[1], 1)))
            gx = self.rink.goal_x
            if d["num"] and d["num"] in self._goalies(d["side"]):
                self.pressure.goalie_seen(w[0] if d["side"] == "our" else -w[0], weight=3)
            elif abs(abs(w[0]) - (gx - 0.8)) < 1.4 and abs(w[1]) < 1.6 and k.speed < 8:
                # joueur quasi immobile dans le demi-cercle : très probablement le gardien
                self.pressure.goalie_seen(w[0] if d["side"] == "our" else -w[0])
            if d["num"]:
                self.shifts[d["side"]].seen(d["num"], game_t, now)
        for s in self.shifts.values():
            s.tick(now)
        if self.mode == "team" and H is not None:
            self.pressure.add(now, xs, speeds)
        self.rec.positions_tick(now, game_t, [(d["side"], d["num"], *d["world"]) for d in dets
                                              if d.get("world") and d["side"] and d["num"]])
        if now - getattr(self, "last_log", 0) > 10:     # bilan toutes les 10 s dans la console
            self.last_log = now
            st = self.cam.stats if self.cam is not None else None
            loc = f"{100 * st['located'] / max(st['frames'], 1):.0f} %" if st else self.calib_state
            if self.cam is not None and getattr(self.cam, "last_try", None):
                loc += f" (dernier essai {self.cam.last_try[1]:.2f}, seuil {self.cam.accept:.2f})"
            ident = sum(1 for d in dets if d["num"])
            lum = float(frame[::8, ::8].mean())
            loc += f" · nouvelles pistes {self.new_tracks}/10 s"
            self.new_tracks = 0
            self.log(f"[direct] {self.fps:.1f} im/s · patinoire repérée {loc} · {len(dets)} joueurs vus, "
                     f"{ident} identifiés · bandeau {'lu' if self.sb.box else 'non trouvé'} · "
                     f"image {frame.shape[1]}x{frame.shape[0]}{' NOIRE' if lum < 12 else ''}")
            if self.a.apercu:                  # diagnostic : une seule image, remplacée à chaque fois
                cv2.imwrite(os.path.join(HERE, "apercu_capture.jpg"), self._apercu(frame, H, dets))
        # --- envoi à l'overlay (10 fois/s au plus)
        if now - self.last_send >= 0.1:
            self.last_send = now
            self.hub.send(self.state_msg(dets, game_t, H is not None))
        self.save()

    # ----------------------------------------------------------- détection
    def _detect(self, idx, frame, now):
        if self.mode == "team":
            return self.det.track(frame, idx=idx)
        # mode joueurs : image entière 1 fois/s, sinon seulement autour des joueurs choisis
        full = (now - self.last_full >= 1.0) or not self.locked or len(self.locked) < len(self.selected)
        if full:
            self.last_full = now
            dets = self.det.detect(frame, idx=idx)
        else:
            dets = []
            W, Hh = self.size
            for key, L in self.locked.items():
                x1, y1, x2, y2 = L["box"]
                cx, cy = (x1 + x2) / 2 + L["vel"][0], (y1 + y2) / 2 + L["vel"][1]
                s = max(2.5 * (y2 - y1), 160)
                rect = (int(max(0, cx - s)), int(max(0, cy - s)), int(min(W, cx + s)), int(min(Hh, cy + s)))
                dets += self.det.detect(frame, rect=rect, imgsz=320, idx=idx)
        # association des joueurs choisis (suivi maison)
        for i, d in enumerate(dets):
            d["tid"] = d["tid"] if d["tid"] >= 0 else 100000 + i
        taken = set()
        for key, L in list(self.locked.items()):
            pc = np.array([(L["box"][0] + L["box"][2]) / 2, (L["box"][1] + L["box"][3]) / 2]) + L["vel"]
            best, bd = None, 0.6 * (L["box"][3] - L["box"][1]) + 10
            for i, d in enumerate(dets):
                c = np.array([(d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2])
                dd = np.linalg.norm(c - pc)
                if dd < bd and i not in taken:
                    best, bd = i, dd
            if best is None:
                if now - L["t"] > 1.5:
                    del self.locked[key]            # perdu : on le recherchera par son numéro
                continue
            taken.add(best)
            d = dets[best]
            c_new = np.array([(d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2])
            c_old = np.array([(L["box"][0] + L["box"][2]) / 2, (L["box"][1] + L["box"][3]) / 2])
            L["vel"] = 0.5 * L["vel"] + 0.5 * (c_new - c_old)
            L["box"], L["t"] = d["box"], now
            d["key"] = key
            d["tid"] = hash(key) % 90000 + 200000
        return dets

    def _persist_ids(self, dets, now):
        """Une fois identifié, un joueur garde son numéro :
        * même piste de suivi -> même numéro (on ne relit plus son dos) ;
        * piste perdue (joueur caché, sorti du champ) puis nouvelle piste au même endroit,
          même couleur de maillot -> le numéro est repris tout de suite ;
        * un numéro n'est porté que par un seul joueur à la fois."""
        # 1) unicité : si deux joueurs ont le même numéro, on garde le plus sûr
        owners = {}
        for d in dets:
            if d["num"] and d["side"]:
                k = (d["side"], d["num"])
                conf = 99 if (d.get("key") or d["tid"] in self.votes.manual) else \
                    self.votes.votes.get(d["tid"], {}).get(d["num"], 0)
                if k in owners and owners[k][0] >= conf:
                    d["num"] = None
                    continue
                if k in owners:
                    owners[k][1]["num"] = None
                owners[k] = (conf, d)
        # 2) reprise du numéro par une nouvelle piste
        visible = set(owners)
        lost = [m for t, m in self.known.items() if now - m["t"] > 0.15 and m["key"] not in visible]
        for d in dets:
            if d["num"] or d["cluster"] not in (0, 1) or d["tid"] < 0:
                continue
            c = np.array([(d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2])
            h = d["box"][3] - d["box"][1]
            best, bscore = None, 1e9
            for m in lost:
                if m["cluster"] != d["cluster"] or m["key"] in visible:
                    continue
                gap = now - m["t"]
                if gap > MEMORY_S:
                    continue
                if gap > 3.0:
                    continue        # absence plus longue : il faudra une lecture du numéro (une suffit)
                if d.get("world") and m["world"]:
                    dist = np.hypot(d["world"][0] - m["world"][0], d["world"][1] - m["world"][1])
                    ok = dist < 1.5 + 8.0 * gap              # un joueur fait au plus ~8-9 m/s
                    score = dist
                else:
                    dist = np.linalg.norm(c - m["center"])
                    ok = dist < max(1.2 * h, 0.8 * m["h"]) * (1 + 2 * gap)
                    score = dist / max(h, 1)
                if ok and score < bscore:
                    best, bscore = m, score
            if best is not None:
                side, num = best["key"]
                self.votes.votes[d["tid"]][num] += 2.0       # repris : vaut des lectures sûres
                self.votes.reads[d["tid"]][num] += 3
                if best.get("valid"):
                    self.validated[d["tid"]] = (side, num)
                d["side"], d["num"] = side, num
                visible.add(best["key"])
                self.known = {t: m for t, m in self.known.items() if m is not best}
        # 3) mémorise les joueurs identifiés de cette image
        for d in dets:
            if d["num"] and d["side"] and d["tid"] >= 0:
                self.known[d["tid"]] = dict(key=(d["side"], d["num"]), cluster=d["cluster"], t=now,
                                            valid=d["tid"] in self.validated,
                                            world=d.get("world"),
                                            center=np.array([(d["box"][0] + d["box"][2]) / 2,
                                                             (d["box"][1] + d["box"][3]) / 2]),
                                            h=d["box"][3] - d["box"][1])
        # au-delà de 30 s sans être vu : sorti de la glace, oublié jusqu'à son retour
        self.known = {t: m for t, m in self.known.items() if now - m["t"] < MEMORY_S}
        seen_now = {d["tid"] for d in dets}
        self.validated = {t: k for t, k in self.validated.items() if t in seen_now or t in self.known}
        vis = {(d["side"], d["num"]) for d in dets if d["num"]}
        self.recent_lost = {m["key"] for m in self.known.values() if m["key"] not in vis}

    def _detect_goalies(self, dets, now, game_t):
        """Cages : sur la ligne de but, au centre. Un joueur qui reste dans les ~3 m devant une
        cage plus de 2 s est le gardien de son équipe : il reçoit le numéro du gardien en jeu
        (feuille FFHG). On en déduit aussi la cage défendue par chaque équipe."""
        gx = self.rink.goal_x
        for d in dets:
            w = d.get("world")
            t = d["tid"]
            if w is None or t < 0 or d["side"] is None:
                continue
            near = None
            for s in (-1, 1):
                dx = (s * gx - w[0]) * s                     # > 0 : devant la cage, côté glace
                if -0.8 < dx < 3.2 and abs(w[1]) < 2.2:
                    near = s
            g = self.goalie_track.setdefault(t, dict(n=0, side=None, t0=now))
            if near is None:
                g["n"] = max(0, g["n"] - 1)
                continue
            g["n"] += 1
            if g["side"] != near:
                g["side"], g["t0"] = near, now
            if g["n"] >= 12 and now - g["t0"] > 2.0:          # ~2 s devant la même cage
                self.defends[d["side"]] = near
                d["goalie"] = True
                if self.match and t not in self.votes.manual:
                    num = self.match.goalie_at(self._side_key(d["side"]), game_t)
                    if num and self.validated.get(t) != (d["side"], num):
                        self.validated[t] = (d["side"], num)
                        d["num"] = num
                        for o, k in list(self.validated.items()):    # un seul gardien de ce numéro
                            if o != t and k == (d["side"], num) and o not in self.votes.manual:
                                self.validated.pop(o, None)
                        if (d["side"], num) not in self.goalie_logged:
                            self.goalie_logged.add((d["side"], num))
                            self.log(f"[gardien] {self._name(d['side'])} #{num} "
                                     f"{self._roster(d['side']).get(num, {}).get('short', '')} devant sa cage")
        if self.defends.get("our") is not None:
            # sens du jeu connu par la cage défendue : la jauge de pression n'a plus à le deviner
            self.pressure.defend_votes[:] = 0
            self.pressure.defend_votes[0 if self.defends["our"] < 0 else 1] = 50

    def cages_px(self, H):
        """Contour des deux cages à l'écran (pour l'overlay), si la patinoire est repérée."""
        if H is None or self.cam is None:
            return None
        from autocalib import camera_from_params
        K, R, C = camera_from_params(np.r_[self.cam.center, self.cam.q], self.size)
        gx, hw, hh, dp = self.rink.goal_x, 0.915, 1.22, 1.12
        out = []
        W, Hh = self.size
        for s in (-1, 1):
            x = s * gx
            pts = np.array([[x, -hw, 0], [x, -hw, hh], [x, hw, hh], [x, hw, 0],
                            [x + s * dp, hw, 0], [x + s * dp, -hw, 0], [x, -hw, 0]], float)
            Xc = (pts - C) @ R.T
            if (Xc[:, 2] <= 0.5).any():
                continue
            uv = (Xc @ K.T)[:, :2] / Xc[:, 2:3]
            if (uv[:, 0] < -0.2 * W).all() or (uv[:, 0] > 1.2 * W).all():
                continue
            out.append([[round(u / W, 4), round(v / Hh, 4)] for u, v in uv])
        return out

    def _smooth_pose(self, dets, now, a=0.55, hold=0.35):
        """Lissage du squelette par joueur suivi : chaque point est moyenné avec sa position
        précédente (exprimée par rapport aux pieds, pour ne pas freiner le déplacement), et un
        point qui manque est gardé brièvement (hold s) au lieu de faire disparaître le membre."""
        for d in dets:
            kp, t = d.get("kp"), d["tid"]
            if kp is None or t < 0:
                continue
            x1, y1, x2, y2 = d["box"]
            ref = np.array([(x1 + x2) / 2, y2])
            rel = kp[:, :2] - ref
            prev = self.pose_smooth.get(t)
            out = kp.copy()
            if prev is not None:
                prel, pconf, pt = prev
                for i in range(len(kp)):
                    if kp[i, 2] > 0.35 and pconf[i] > 0.35:
                        rel[i] = a * rel[i] + (1 - a) * prel[i]
                    elif kp[i, 2] <= 0.35 and pconf[i] > 0.35 and now - pt[i] < hold:
                        rel[i] = prel[i]                  # point momentanément caché : on le garde
                        out[i, 2] = pconf[i] * 0.9
            out[:, :2] = rel + ref
            seen = np.where(out[:, 2] > 0.35, now, prev[2] if prev is not None else now)
            self.pose_smooth[t] = (rel.copy(), out[:, 2].copy(), seen)
            d["kp"] = out
        if len(self.pose_smooth) > 400:
            live = {d["tid"] for d in dets}
            self.pose_smooth = {k: v for k, v in self.pose_smooth.items() if k in live}

    def _keep_on_ice(self, frame, d):
        """Filtre public/banc avec mémoire : un joueur déjà vu sur la glace (3 fois) reste
        gardé ; il n'est retiré qu'après 8 échecs de suite. Le public ne passe jamais."""
        t = d["tid"]
        ok = self._on_ice(frame, d)
        st = self.ice_hist.setdefault(t, [0, 0]) if t >= 0 else [0, 0]   # [réussites, échecs de suite]
        if ok:
            st[0] += 1
            st[1] = 0
            return True
        st[1] += 1
        return st[0] >= 3 and st[1] < 8

    def _on_ice(self, frame, d):
        """Pieds sur la glace ? Zone sous les pieds claire et peu colorée (filet gommé).
        Une fois la patinoire repérée, on vérifie aussi la position réelle sur la piste."""
        x1, y1, x2, y2 = d["box"]
        w, h = x2 - x1, y2 - y1
        H, W = frame.shape[:2]
        if self.cam is not None and getattr(self, "_last_H", None) is not None:
            p = cv2.perspectiveTransform(np.float32([[[(x1 + x2) / 2, y2]]]), self._last_H)[0, 0]
            if not self.rink.inside(p[0], p[1], 1.0):
                return False
        if y2 >= H - 2:
            return True                                   # pieds coupés par le bas de l'image
        # zone nettement SOUS les pieds (pas les patins ni les jambières, souvent noirs)
        px1, px2 = int(max(0, x1 - 0.2 * w)), int(min(W, x2 + 0.2 * w))
        py1, py2 = int(max(0, y2 + 0.01 * h)), int(min(H, y2 + 0.12 * h + 3))
        patch = frame[py1:py2, px1:px2]
        if patch.size == 0:
            return True
        patch = cv2.morphologyEx(patch, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).astype(np.int16)
        ice = (lab[..., 0] > 135) & (np.hypot(lab[..., 1] - 128, lab[..., 2] - 128) < 30)
        return ice.mean() > 0.2

    def _crossing_guard(self, dets, now):
        """Quand deux joueurs (même équipe) se superposent, le suivi peut échanger leurs pistes.
        On mémorise position et vitesse de chacun au début du croisement ; à la séparation,
        on regarde quelle attribution prolonge le mieux les trajectoires et on ré-échange les
        identités si le suivi s'est trompé. Si c'est indécidable : vérification par le numéro."""
        by = {d["tid"]: d for d in dets if d["tid"] >= 0}
        for t, d in by.items():
            c = np.array([(d["box"][0] + d["box"][2]) / 2, d["box"][3]])     # pieds
            h = self.track_hist.setdefault(t, deque(maxlen=8))
            h.append((now, c))
        ident = lambda t: t in self.validated or t in self.votes.manual
        # début de croisement
        items = list(by.values())
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a["cluster"] != b["cluster"] or a["cluster"] not in (0, 1):
                    continue
                if not (ident(a["tid"]) or ident(b["tid"])):
                    continue
                key = tuple(sorted((a["tid"], b["tid"])))
                if key in self.crossings or _iou(a["box"], b["box"]) < 0.15:
                    continue
                st = {}
                for t in key:
                    h = self.track_hist.get(t)
                    if not h or len(h) < 3:
                        break
                    (t0, c0), (t1, c1) = h[0], h[-1]
                    st[t] = (t1, c1, (c1 - c0) / max(t1 - t0, 1e-3))
                if len(st) == 2:
                    self.crossings[key] = dict(t=now, st=st, hpx=a["box"][3] - a["box"][1])
        # fin de croisement
        for key, X in list(self.crossings.items()):
            ta, tb = key
            if now - X["t"] > 4.0 or ta not in by or tb not in by:
                for t in key:
                    if t in self.validated:
                        self.need_check[t] = now
                del self.crossings[key]
                continue
            if _iou(by[ta]["box"], by[tb]["box"]) > 0.05:
                continue                                # toujours superposés
            pred = {}
            for t in key:
                t1, c1, v = X["st"][t]
                pred[t] = c1 + v * (now - t1)
            ca = np.array([(by[ta]["box"][0] + by[ta]["box"][2]) / 2, by[ta]["box"][3]])
            cb = np.array([(by[tb]["box"][0] + by[tb]["box"][2]) / 2, by[tb]["box"][3]])
            same = np.linalg.norm(ca - pred[ta]) + np.linalg.norm(cb - pred[tb])
            swap = np.linalg.norm(ca - pred[tb]) + np.linalg.norm(cb - pred[ta])
            if swap < 0.6 * same and same > 0.3 * X["hpx"]:
                self._swap_identities(ta, tb)
            elif not (same < 0.6 * swap):
                for t in key:                           # indécidable : on revérifie par le numéro
                    if t in self.validated:
                        self.need_check[t] = now
            del self.crossings[key]

    def _swap_identities(self, ta, tb):
        for dct in (self.validated, self.votes.manual, self.manual_side):
            va, vb = dct.pop(ta, None), dct.pop(tb, None)
            if vb is not None:
                dct[ta] = vb
            if va is not None:
                dct[tb] = va
        for dct in (self.votes.votes, self.votes.reads, self.votes.raw2):
            va, vb = dct.pop(ta, None), dct.pop(tb, None)
            if vb is not None:
                dct[ta] = vb
            if va is not None:
                dct[tb] = va
        na = self.validated.get(ta, ("", "?"))[1]
        nb = self.validated.get(tb, ("", "?"))[1]
        self.log(f"[croisement] le suivi avait échangé deux joueurs : identités remises (#{na} / #{nb})")

    def _apercu(self, frame, H, dets):
        """Image de diagnostic : lignes de la patinoire projetées (vert = suivi accepté,
        rouge = dernier essai refusé), joueurs détectés, état."""
        from autocalib import homographies
        v = frame.copy()
        Hw, col, txt = None, None, "patinoire : pas encore calibrée"
        if H is not None:
            Hw, col = np.linalg.inv(H), (0, 255, 0)
            txt = "patinoire reperee (vert)"
        elif self.cam is not None and getattr(self.cam, "last_try", None):
            q, cost = self.cam.last_try[:2]
            if q is not None:
                Hw = homographies(np.r_[self.cam.center, q], self.size)[0]
                col = (0, 0, 255)
                txt = f"essai refuse (rouge) score {cost:.2f} > seuil {self.cam.accept:.2f}"
        if Hw is not None:
            for poly, _ in self.rink.markings():
                P = np.c_[poly, np.ones(len(poly))] @ Hw.T
                if (P[:, 2] <= 0).any():
                    continue
                q2 = P[:, :2] / P[:, 2:3]
                if np.abs(q2).max() > 1e5:
                    continue
                cv2.polylines(v, [np.int32(q2)], False, col, 2, cv2.LINE_AA)
        for d in dets:
            x1, y1, x2, y2 = [int(a) for a in d["box"]]
            c = (255, 160, 60) if d.get("side") == "our" else ((60, 200, 255) if d.get("side") == "them" else (200, 200, 200))
            cv2.rectangle(v, (x1, y1), (x2, y2), c, 2)
            if d.get("num"):
                cv2.putText(v, d["num"], (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
        cv2.putText(v, txt, (20, v.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5)
        cv2.putText(v, txt, (20, v.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        return cv2.resize(v, None, fx=0.6, fy=0.6)

    def _collect(self, frame, dets, now):
        """Vignettes du dos (pas de vidéo) des joueurs nommés par un clic du coach : étiquettes
        sûres pour entraîner plus tard un lecteur de numéros spécialisé. 1 vignette/s par
        joueur, 40 au plus par joueur et par match, joueurs isolés seulement."""
        root = os.path.join(HERE, "dataset_numeros")
        for d in dets:
            tid = d["tid"]
            if tid not in self.votes.manual or now < self.collect_next.get(tid, 0):
                continue
            side = self.manual_side.get(tid, d["side"])
            num = self.votes.manual[tid]
            key = (side, num)
            if self.collect_count.get(key, 0) >= 40 or (d["box"][3] - d["box"][1]) < self.a.min_px:
                continue
            if any(o is not d and _iou(o["box"], d["box"]) > 0.1 for o in dets):
                continue                         # un autre joueur devant : vignette ambiguë
            x1, y1, x2, y2 = [int(v) for v in d["box"]]
            crop = frame[max(0, y1):y2, max(0, x1):x2]
            if crop.size == 0:
                continue
            folder = os.path.join(root, f"{self._name(side)}_{num}")
            os.makedirs(folder, exist_ok=True)
            n = self.collect_count.get(key, 0)
            cv2.imwrite(os.path.join(folder, f"{dt.date.today().isoformat()}_{tid}_{n:02d}.jpg"), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            self.collect_count[key] = n + 1
            self.collect_next[tid] = now + 1.0

    def _resolve_sides(self):
        """Quelle couleur de maillot = quelle équipe.

        Décidé par la COULEUR (sombre / clair), pas par les numéros lus (trop peu fiables) :
        option --our-color, sinon l'équipe à domicile est supposée en maillot sombre (Brest joue
        en noir à la Rïnkla). Un clic sur « Maillots ⇄ » dans l'overlay inverse si besoin.
        Une fois décidé, ce n'est plus remis en cause automatiquement."""
        if self.sides_locked or not self.clf_ready:
            return
        L = [c[0] for c in self.clf.centers]
        dark = int(np.argmin(L))
        our_color = self.a.our_color
        if our_color not in ("sombre", "clair"):
            our_color = "sombre" if (not self.match or self.match.our == "home") else "clair"
        mine = dark if our_color == "sombre" else 1 - dark
        self.cluster_side = {mine: "our", 1 - mine: "them"}
        self.our_color = our_color
        self.sides_locked = True
        self.log(f"[équipes] {self._name('our')} = maillots {our_color}s, {self._name('them')} = "
                 f"maillots {'clair' if our_color == 'sombre' else 'sombre'}s "
                 "(bouton « Maillots ⇄ » de l'overlay si c'est inversé)")

    def _name(self, side):
        if not self.match:
            return "nous" if side == "our" else "eux"
        return self.match.abbr[self._side_key(side)]

    def swap_sides(self):
        if len(self.cluster_side) != 2:
            return
        self.cluster_side = {k: ("them" if v == "our" else "our") for k, v in self.cluster_side.items()}
        self.our_color = "clair" if self.our_color == "sombre" else "sombre"
        # toutes les identités déduites jusque-là sont fausses : on repart de zéro (sauf clics du coach)
        self.validated.clear()
        self.known.clear()
        for t in list(self.votes.votes):
            if t not in self.votes.manual:
                self.votes.forget(t)
        self.log(f"[équipes] inversion : {self._name('our')} = maillots {self.our_color}s")

    def _read_numbers(self, frame, dets, now):
        """Envoie quelques dos à lire en arrière-plan et applique les résultats déjà prêts."""
        if not self.match:
            return
        by_tid = {d["tid"]: d for d in dets}
        import itertools
        for job, reads in itertools.chain(self.ocr.results(), self.ocr_pause.results()):
            if not reads:
                continue
            cl, side, tid = job["cluster"], job["side"], job["tid"]
            for num, conf in reads:              # quelle couleur de maillot = quelle équipe
                ino, inth = num in self._roster("our"), num in self._roster("them")
                if ino != inth and cl in (0, 1):
                    self.side_votes[cl][0 if ino else 1] += conf     # gardé pour information seulement
            if self.mode == "players":
                d = by_tid.get(tid)
                for num, conf in sorted(reads, key=lambda r: -r[1]):
                    key = (side, num)
                    if side and key in self.selected and key not in self.locked and conf > 0.6 and len(num) == 2:
                        self.locked[key] = dict(box=job["box"], t=now, vel=np.zeros(2))
                        if d is not None:
                            d["key"] = key
                        break
            elif tid in self.validated:
                # vérification : 2 lectures sûres d'un AUTRE numéro de la feuille -> identité retirée
                vs, vn = self.validated[tid]
                for num, conf in reads:
                    if num == vn and conf >= 0.5:
                        self.need_check.pop(tid, None)
                        self.contra.pop(tid, None)
                    elif conf >= 0.6 and len(num) == 2 and num in self._roster(vs):
                        self.contra[tid] = self.contra.get(tid, 0) + 1
                        if self.contra[tid] >= 2:
                            self.log(f"[numéros] identité #{vn} retirée (lu #{num} deux fois)")
                            self.validated.pop(tid, None)
                            self.votes.forget(tid)
                            self.contra.pop(tid, None)
            elif tid not in self.validated:
                allowed = set(self._roster(side)) if side else set(self._roster("our")) | set(self._roster("them"))
                self.votes.add(tid, reads, allowed)
                for num, conf in reads:             # joueur revenu dans le champ : une lecture suffit
                    if side and (side, num) in self.recent_lost and conf >= 0.5:
                        self.votes.votes[tid][num] += 1.0
                        self.votes.reads[tid][num] += 2
        want_side = None
        if self.mode == "players":
            missing = [k for k in self.selected if k not in self.locked]
            if not missing:
                return
            want_side = {k[0] for k in missing}
        cands = []
        for d in dets:
            if d.get("key") or d["cluster"] not in (0, 1):
                continue
            h = d["box"][3] - d["box"][1]
            if h < self.a.min_px or now < self.next_ocr.get(d["tid"], 0):
                continue
            if self.mode == "team" and d["tid"] in self.votes.manual:
                continue
            if self.mode == "team" and d["tid"] in self.validated:
                # validé : on ne relit que pour vérifier après un croisement, ou toutes les 10 s
                if d["tid"] not in self.need_check and now < self.next_verify.get(d["tid"], 0):
                    continue
                self.next_verify[d["tid"]] = now + 10.0
            if want_side and d["side"] and d["side"] not in want_side:
                continue
            if d["side"] and d["side"] != self.focus:     # on ne lit que l'équipe analysée
                continue
            cands.append((-h, d))
        # modèle entraîné : reconnaissance du joueur entier (2 fois/s au plus par joueur)
        if self.mode == "team" and self.recog:
            batch = {}
            for d in dets:
                t, side = d["tid"], d["side"]
                if t < 0 or side not in self.recog or t in self.validated or t in self.votes.manual:
                    continue
                if now < self.next_recog.get(t, 0) or (d["box"][3] - d["box"][1]) < 40:
                    continue
                x1, y1, x2, y2 = [int(v) for v in d["box"]]
                crop = frame[max(0, y1):y2, max(0, x1):x2]
                if crop.size:
                    batch.setdefault(side, []).append((d, crop))
                    self.next_recog[t] = now + 0.5
            for side, items in batch.items():
                for (d, _), (num, prob) in zip(items, self.recog[side].predict([c for _, c in items[:8]])):
                    if prob >= 0.85 and num in self._roster(side):
                        self.votes.add(d["tid"], [(num, min(prob, 0.95))], {num})
        if self.paused:
            # pause : chaque joueur non identifié est lu une fois, en plusieurs cadrages (dos décalé
            # à gauche/droite, plus haut, manches) ; 3 lectures concordantes suffisent à le valider
            for _, d in sorted(cands, key=lambda t: t[0]):
                if d["tid"] in self.pause_done or d["tid"] < 0:
                    continue
                x1, y1, x2, y2 = d["box"]
                w, h = x2 - x1, y2 - y1
                variants = [d["box"], [x1 - 0.08 * w, y1, x2 - 0.08 * w, y2], [x1 + 0.08 * w, y1, x2 + 0.08 * w, y2],
                            [x1, y1 - 0.05 * h, x2, y2 - 0.05 * h]]
                jobs = [back_crop(frame, np.array(b), sleeves=False) for b in variants]
                jobs.append(back_crop(frame, d["box"], sleeves=True))
                for crop in jobs:
                    if crop is not None:
                        self.ocr_pause.put(dict(crop=crop, tid=d["tid"], cluster=d["cluster"], side=d["side"],
                                                box=d["box"].copy()))
                self.pause_done.add(d["tid"])
                break                       # un joueur par image : la file reste courte
            return
        def facing(d):
            f, _ = pose_crops(frame, d.get("kp"))
            return f
        # priorité aux joueurs vus de dos (numéro visible), puis profil ; jamais de face
        cands = [c for c in cands if facing(c[1]) != "face"]
        cands.sort(key=lambda t: (facing(t[1]) != "dos", t[0]))
        for _, d in cands[: self.a.ocr_per_frame]:
            f, crops = pose_crops(frame, d.get("kp"))
            if not crops:
                # pas de squelette exploitable : cadrage d'après le rectangle (une fois sur deux les manches)
                sleeves = (d["box"][3] - d["box"][1]) > 2 * self.a.min_px and self.next_ocr.get(("s", d["tid"]), 0) <= now
                crops = [back_crop(frame, d["box"], sleeves=sleeves)]
                if sleeves:
                    self.next_ocr[("s", d["tid"])] = now + 1.2
            ok = False
            for crop in crops:
                if crop is not None and self.ocr.put(dict(crop=crop, tid=d["tid"], cluster=d["cluster"],
                                                          side=d["side"], box=d["box"].copy())):
                    ok = True
            if ok:
                self.next_ocr[d["tid"]] = now + 0.6
        if len(self.kin_tid) > 300:              # ménage des joueurs non identifiés disparus
            self.kin_tid = {t: k for t, k in self.kin_tid.items() if k.last_t and now - k.last_t < 10}

    # -------------------------------------------------------------- sortie
    def state_msg(self, dets, game_t, calibrated):
        W, Hh = self.size
        cur = {s: self.shifts[s].current(game_t) for s in ("our", "them")}
        players = []
        for d in dets:
            if d["cluster"] == REF:                     # arbitre désigné : exclu, rien n'est affiché
                continue
            x1, y1, x2, y2 = d["box"]
            side, num = d["side"], d["num"]
            if side is not None and side != self.focus:
                continue
            if self.mode == "players" and (side, num) not in self.selected:
                continue
            k = d.get("kin")
            p = dict(tid=int(d["tid"]), box=[round(x1 / W, 4), round(y1 / Hh, 4), round(x2 / W, 4), round(y2 / Hh, 4)],
                     side=side, num=num, goalie=bool(d.get("goalie")))
            kp = d.get("kp")
            if kp is not None:
                p["kp"] = [[round(float(x) / W, 4), round(float(y) / Hh, 4)] if c > 0.35 else None for x, y, c in kp]
            hist = self.track_hist.get(d["tid"])
            if hist and len(hist) >= 3:                  # vitesse à l'écran (fraction d'image / s)
                (t0, c0), (t1, c1) = hist[max(0, len(hist) - 3)], hist[-1]
                if t1 - t0 > 0.05:
                    v = (c1 - c0) / (t1 - t0)
                    pv = self.vel_smooth.get(d["tid"])
                    v = v if pv is None else 0.65 * v + 0.35 * pv        # vitesse lissée (réactive)
                    self.vel_smooth[d["tid"]] = v
                    p["v"] = [round(float(v[0]) / W, 4), round(float(v[1]) / Hh, 4)]
            if num and side:
                info = self._roster(side).get(num, {})
                p["name"] = info.get("short", "")
                if side in cur and num in cur[side]:
                    p["shift"] = round(cur[side][num])
            if k is not None and calibrated:
                p["speed"] = round(k.speed, 1)
                p["dist"] = round(k.dist)
            players.append(p)
        msg = dict(type="state", mode=self.mode, focus=self.focus, fps=round(self.fps, 1), paused=self.paused,
                   ts=round(self.cap_ts, 3), cages=self.cages_px(self._last_H),
                   status=dict(calib=self.calib_state if self.cam is None else ("ok" if calibrated else "perdu"),
                               match=(f"{self.match.short['home']} / {self.match.short['away']}" if self.match else None),
                               sides=len(self.cluster_side) == 2, colors=self.clf_ready,
                               our_color=self.our_color),
                   clock=self.sb.state(), players=players)
        f = self.focus
        if self.mode == "team":
            msg["onice"] = [dict(num=n, name=self._roster(f).get(n, {}).get("short", ""), shift=round(s))
                            for n, s in sorted(cur[f].items(), key=lambda t: -t[1])]
            pr = self.pressure.state()
            if f == "them":                       # pression vue du côté de l'équipe analysée
                pr["off"], pr["dfd"] = pr["dfd"], pr["off"]
            pr["fatigue"] = pr["fatigue"].get(f)
            msg["pressure"] = pr
            msg["minimap"] = [[round(d["world"][0], 1), round(d["world"][1], 1), d["side"], d["num"]]
                              for d in dets if d.get("world") and d["side"] == f]
        else:
            sel = []
            for side, num in self.selected:
                k = self.kin.get((side, num))
                info = self._roster(side).get(num, {})
                st = self.shifts[side].stats(num)
                dd = self.detail.get((side, num), {})
                sel.append(dict(side=side, num=num, name=info.get("name", num), pos=info.get("pos", ""),
                                visible=(side, num) in self.locked,
                                speed=round(k.speed, 1) if k else None, top=round(k.top, 1) if k else None,
                                dist=round(k.dist) if k else 0, seen=round(k.seen) if k else 0,
                                shift=round(cur[side].get(num, 0)), on_ice=num in cur[side],
                                shifts=st["count"], toi=round(st["total"] + cur[side].get(num, 0)),
                                avg=round(st["avg"]), speeds=list(dd.get("speeds", []))[-120:],
                                trail=list(dd.get("trail", []))[-120:]))
            msg["selected"] = sel
        gs = self.sb.game_seconds
        if self.match and gs is not None:
            with self.feed.lock:
                pens = self.match.penalties_active(gs)
            msg["pp"] = [dict(side="our" if p["side"] == self.match.our else "them", num=p["num"],
                              remaining=p["remaining"]) for p in pens]
        return msg

    def save(self, force=False):
        try:
            self.memory.save(force)
        except Exception as ex:
            self.log(f"[mémoire] écriture impossible : {ex}")
        players = {}
        for (side, num), k in self.kin.items():
            if not num:
                continue
            info = self._roster(side).get(num, {})
            players[f"{side}:{num}"] = dict(camp=side, num=num, nom=info.get("name", ""),
                                            distance_mesuree_m=round(k.dist), vitesse_max_kmh=round(k.top, 1),
                                            temps_vu_s=round(k.seen), **{f"presences_{a}": b for a, b in
                                                                         self.shifts[side].stats(num).items()})
        payload = dict(match=self.match.summary() if self.match else None, joueurs=players,
                       presences={s: self.shifts[s].done for s in self.shifts},
                       feuille_ffhg=(self.match.events if self.match else []))
        self.rec.save(payload, force)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--equipe", default="BREST", help="ton équipe (pour trouver le match du jour)")
    ap.add_argument("--match", default=None, help="numéro du match FFHG (sinon trouvé tout seul)")
    ap.add_argument("--match-json", default=None, help="feuille de match locale (tests sur replay)")
    ap.add_argument("--source", default=None, help="vidéo à rejouer comme du direct (tests)")
    ap.add_argument("--speed", type=float, default=1.0, help="vitesse de relecture de --source")
    ap.add_argument("--start", type=float, default=0.0, help="début de --source (s)")
    ap.add_argument("--loop", action="store_true", help="rejoue --source en boucle")
    ap.add_argument("--rink", default=None, choices=list(PRESETS), help="dimensions (sinon déduites)")
    ap.add_argument("--patinoire", default=None, help="nom du profil de patinoire si pas de feuille de match")
    ap.add_argument("--recalib", action="store_true", help="refait la calibration de la patinoire")
    ap.add_argument("--our-color", default=None, choices=["sombre", "clair"],
                    help="maillot de ton équipe (défaut : sombre à domicile, clair à l'extérieur)")
    ap.add_argument("--weights", default="yolov8s-pose.pt",
                    help="détecteur (défaut : avec squelette ; yolov8s.pt = sans squelette)")
    ap.add_argument("--imgsz", type=int, default=1280, help="taille d'analyse (plus grand = joueurs du fond mieux vus)")
    ap.add_argument("--conf", type=float, default=0.22)
    ap.add_argument("--device", default=None)
    ap.add_argument("--fps", type=float, default=15, help="images analysées par seconde (mode équipe)")
    ap.add_argument("--fps-players", type=float, default=12, help="idem en mode joueurs choisis")
    ap.add_argument("--ocr-per-frame", type=int, default=2, help="dos lus par image au plus")
    ap.add_argument("--min-px", type=int, default=55, help="taille mini d'un joueur pour lire son numéro")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--dxcam", action="store_true",
                    help="capture par la carte graphique (plus rapide, mais peut se bloquer)")
    ap.add_argument("--collecte", action="store_true",
                    help="garde des vignettes du dos des joueurs identifiés par clic (pour entraîner "
                         "un lecteur de numéros) dans dataset_numeros/")
    ap.add_argument("--apercu", action="store_true",
                    help="diagnostic : écrit apercu_capture.jpg (ce que voit le moteur) toutes les 10 s")
    ap.add_argument("--gt", default=None, help=argparse.SUPPRESS)
    Engine(ap.parse_args()).run()


if __name__ == "__main__":
    main()
