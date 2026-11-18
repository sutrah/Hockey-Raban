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
from collections import deque

import cv2
import numpy as np

from autocalib import AutoCameraTracker, estimate_camera_center
from live_capture import FileSource, ScreenSource, static_mask_from_frames
from live_ocr import NumberVotes, Scoreboard, Worker, back_crop, read_number
from live_state import Kin, Pressure, Recorder, Shifts
from rink import PRESETS, Rink
from teams import REF, UNK, TeamClassifier, jersey_feature

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
COLORS = {"our": "#4a9eff", "them": "#f5b928"}


def slug(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s or "patinoire").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "patinoire"


# ------------------------------------------------------------------ détection
class Detector:
    """YOLO (personnes). track() : suivi plein écran ; detect() : image ou zone, sans suivi."""

    def __init__(self, weights="yolov8s.pt", imgsz=960, conf=0.3, device=None):
        from ultralytics import YOLO
        self.track_model = YOLO(weights)
        self.det_model = YOLO(weights)
        half = device not in (None, "cpu", "mps")
        self.kw = dict(classes=[0], conf=conf, iou=0.5, verbose=False, half=half)
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
        for i, b in enumerate(xyxy):
            if tracked and ids is None:
                continue
            out.append(dict(tid=int(ids[i]) if ids is not None else -1,
                            box=np.array([b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy]), poly=None))
        return out

    def track(self, frame, idx=None):
        r = self.track_model.track(frame, persist=True, tracker=os.path.join(HERE, "bytetrack_live.yaml"), imgsz=self.imgsz, **self.kw)[0]
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
        return [dict(tid=p["id"], box=np.array(p["box"], float), poly=None) for p in fr["players"]]

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
            self.src = ScreenSource()
        # feuille de match
        self.feed = None
        self.match = None
        self._load_match()
        # détection
        if args.gt:
            self.det = GTDetector(args.gt)
        else:
            self.det = Detector(args.weights, args.imgsz, args.conf, args.device or auto_device())
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
        self.last_score = None
        self.calib_t = 0.0
        self.last_cluster = {}
        self.manual_side = {}
        self.known = {}          # tid -> dict(key, cluster, t, world, center, h) : joueurs identifiés
        # lecture du bandeau (1 fois/s) et des numéros : en arrière-plan
        self.latest = None
        if not os.environ.get("HOCKEY_NO_SB"):
            threading.Thread(target=self._scoreboard_loop, daemon=True).start()
        self.ocr = Worker(lambda job: read_number(job["crop"]), maxsize=4)

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
        if os.path.exists(path) and not self.a.recalib:
            with open(path) as f:
                p = json.load(f)
            self.rink = Rink(self.a.rink or p["rink"])
            self.pressure.rink = self.rink
            self._start_tracker(np.array(p["camera"]))
            self.log(f"[calib] profil {os.path.basename(path)} : patinoire {self.rink.L:.0f}×{self.rink.W:.0f} m")
            return
        self.calib_state = "calcul"
        frames = list(self.calib_frames)

        def work():
            presets = [self.a.rink] if self.a.rink else ["iihf", "iihf_26"]
            best = (1e9, None, None)
            for pr in presets:
                rk = Rink(pr)
                c, cost = estimate_camera_center(None, rk, None, self.static, log=self.log,
                                                 frames=list(enumerate(frames)))
                self.log(f"[calib] {pr} ({rk.L:.0f}×{rk.W:.0f}) : score {cost if cost else float('nan'):.3f}")
                if c is not None and cost < best[0]:
                    best = (cost, c, pr)
            if best[1] is None:
                self.calib_state = "échec"
                self.log("[calib] patinoire introuvable : vérifie que la vidéo montre bien la glace")
                return
            self.rink = Rink(best[2])
            self.pressure.rink = self.rink
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(dict(rink=best[2], camera=[float(v) for v in best[1]], score=float(best[0]),
                               date=dt.date.today().isoformat()), f, indent=1)
            self.log(f"[calib] patinoire {self.rink.L:.0f}×{self.rink.W:.0f} m, caméra en "
                     f"({best[1][0]:.1f}, {best[1][1]:.1f}, {best[1][2]:.1f}) m -> {os.path.basename(path)}")
            self._start_tracker(best[1])

        threading.Thread(target=work, daemon=True).start()

    def _start_tracker(self, center):
        self.cam = AutoCameraTracker(self.rink, self.size, center, static_mask=self._mask(), log=self.log,
                                     background=True)
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
        elif t == "assign":                    # le coach a cliqué un joueur et choisi son nom
            tid, side, num = msg.get("tid"), msg.get("side"), str(msg.get("num"))
            if tid is not None:
                self.votes.manual[int(tid)] = num
                self.manual_side[int(tid)] = side
                cl = self.last_cluster.get(int(tid))
                if cl in (0, 1):
                    self.side_votes[cl][0 if side == "our" else 1] += 10
            if self.mode == "players" and msg.get("box") and self.size:
                W, Hh = self.size
                b = np.array(msg["box"], float) * [W, Hh, W, Hh]
                self.locked[(side, num)] = dict(box=b, t=time.time(), vel=np.zeros(2))
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
            time.sleep(0.5)

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
        self.latest = frame
        game_t = self.game_time(now, dtw)
        if self.sb.period and self.sb.period != self.last_period:
            if self.last_period is not None:       # changement de côté à chaque période
                self.pressure.reset_side()
                self.rec.event(game_t, "periode", periode=self.sb.period)
            self.last_period = self.sb.period
        if self.sb.score and self.sb.score != self.last_score:
            if self.last_score is not None:
                self.rec.event(game_t, "score", score=list(self.sb.score))
            self.last_score = self.sb.score

        # --- détection
        dets = self._detect(idx, frame, now)
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

        # --- équipes (couleurs apprises sur les premières secondes)
        if not self.clf_ready:
            for d in dets:
                self.clf.add_sample(jersey_feature(frame, d["box"]))
            if len(self.clf.samples) >= 60:
                try:
                    self.clf.fit()
                    self.clf_ready = True
                    self.log("[équipes] couleurs des maillots apprises")
                except Exception:
                    pass
        self.last_cluster = {}
        for d in dets:
            lab = self.clf.predict(jersey_feature(frame, d["box"])) if self.clf_ready else UNK
            if d["tid"] >= 0:
                lab = self.clf.vote(d["tid"], lab)
            d["cluster"] = lab
            self.last_cluster[d["tid"]] = lab
            d["side"] = self.cluster_side.get(lab) if lab in (0, 1) else None
            if d["tid"] in self.manual_side:
                d["side"] = self.manual_side[d["tid"]]
        self._resolve_sides()

        # --- numéros
        self._read_numbers(frame, dets, now)
        for d in dets:
            d["num"] = None
            if d.get("key"):
                d["side"], d["num"] = d["key"]
            elif d["tid"] >= 0:
                num, _ = self.votes.get(d["tid"])
                if num and d["side"] and num in self._roster(d["side"]):
                    d["num"] = num

        # --- positions sur la glace, vitesses, présences
        world = []
        if H is not None and boxes:
            feet = np.float32([[(b[0] + b[2]) / 2, b[3]] for b in boxes]).reshape(-1, 1, 2)
            wp = cv2.perspectiveTransform(feet, H).reshape(-1, 2)
            for d, (x, y) in zip(dets, wp):
                d["world"] = (float(x), float(y)) if self.rink.inside(x, y, 1.5) else None
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
            ident = sum(1 for d in dets if d["num"])
            self.log(f"[direct] {self.fps:.1f} im/s · patinoire repérée {loc} · {len(dets)} joueurs vus, "
                     f"{ident} identifiés · bandeau {'lu' if self.sb.box else 'non trouvé'}")
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
                if gap > 6.0:
                    continue
                if d.get("world") and m["world"]:
                    dist = np.hypot(d["world"][0] - m["world"][0], d["world"][1] - m["world"][1])
                    ok = dist < 2.0 + 9.0 * gap              # un joueur fait au plus ~9 m/s
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
                d["side"], d["num"] = side, num
                visible.add(best["key"])
                self.known = {t: m for t, m in self.known.items() if m is not best}
        # 3) mémorise les joueurs identifiés de cette image
        for d in dets:
            if d["num"] and d["side"] and d["tid"] >= 0:
                self.known[d["tid"]] = dict(key=(d["side"], d["num"]), cluster=d["cluster"], t=now,
                                            world=d.get("world"),
                                            center=np.array([(d["box"][0] + d["box"][2]) / 2,
                                                             (d["box"][1] + d["box"][3]) / 2]),
                                            h=d["box"][3] - d["box"][1])
        self.known = {t: m for t, m in self.known.items() if now - m["t"] < 30}

    def _resolve_sides(self):
        """Quelle couleur de maillot = quelle équipe (d'après les numéros lus et les clics du coach)."""
        if len(self.cluster_side) == 2:
            return
        v = self.side_votes
        d0 = v[0][0] - v[0][1] + v[1][1] - v[1][0]      # cluster 0 = nous, cluster 1 = eux
        if abs(d0) >= 4:
            self.cluster_side = {0: "our", 1: "them"} if d0 > 0 else {0: "them", 1: "our"}
            self.log(f"[équipes] maillots identifiés (score {d0:+.1f})")
        if self.a.our_color in ("sombre", "clair") and self.clf_ready and not self.cluster_side:
            L = [c[0] for c in self.clf.centers]
            dark = int(np.argmin(L))
            mine = dark if self.a.our_color == "sombre" else 1 - dark
            self.cluster_side = {mine: "our", 1 - mine: "them"}

    def _read_numbers(self, frame, dets, now):
        """Envoie quelques dos à lire en arrière-plan et applique les résultats déjà prêts."""
        if not self.match:
            return
        by_tid = {d["tid"]: d for d in dets}
        for job, reads in self.ocr.results():
            if not reads:
                continue
            cl, side, tid = job["cluster"], job["side"], job["tid"]
            for num, conf in reads:              # quelle couleur de maillot = quelle équipe
                ino, inth = num in self._roster("our"), num in self._roster("them")
                if ino != inth and cl in (0, 1):
                    self.side_votes[cl][0 if ino else 1] += conf
            if self.mode == "players":
                d = by_tid.get(tid)
                for num, conf in sorted(reads, key=lambda r: -r[1]):
                    key = (side, num)
                    if side and key in self.selected and key not in self.locked and conf > 0.6 and len(num) == 2:
                        self.locked[key] = dict(box=job["box"], t=now, vel=np.zeros(2))
                        if d is not None:
                            d["key"] = key
                        break
            else:
                allowed = set(self._roster(side)) if side else set(self._roster("our")) | set(self._roster("them"))
                self.votes.add(tid, reads, allowed)
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
            if self.mode == "team" and (self.votes.get(d["tid"])[0] or d["tid"] in self.votes.manual):
                continue
            if want_side and d["side"] and d["side"] not in want_side:
                continue
            if d["side"] and d["side"] != self.focus:     # on ne lit que l'équipe analysée
                continue
            cands.append((-h, d))
        for _, d in sorted(cands, key=lambda t: t[0])[: self.a.ocr_per_frame]:
            # grands joueurs : une lecture sur deux inclut les manches
            sleeves = (d["box"][3] - d["box"][1]) > 2 * self.a.min_px and self.next_ocr.get(("s", d["tid"]), 0) <= now
            crop = back_crop(frame, d["box"], sleeves=sleeves)
            if sleeves:
                self.next_ocr[("s", d["tid"])] = now + 1.2
            if crop is None:
                continue
            if self.ocr.put(dict(crop=crop, tid=d["tid"], cluster=d["cluster"], side=d["side"], box=d["box"].copy())):
                self.next_ocr[d["tid"]] = now + 0.6
        if len(self.kin_tid) > 300:              # ménage des joueurs non identifiés disparus
            self.kin_tid = {t: k for t, k in self.kin_tid.items() if k.last_t and now - k.last_t < 10}

    # -------------------------------------------------------------- sortie
    def state_msg(self, dets, game_t, calibrated):
        W, Hh = self.size
        cur = {s: self.shifts[s].current(game_t) for s in ("our", "them")}
        players = []
        for d in dets:
            if d["cluster"] == REF:
                continue
            x1, y1, x2, y2 = d["box"]
            side, num = d["side"], d["num"]
            if side is not None and side != self.focus:
                continue
            if self.mode == "players" and (side, num) not in self.selected:
                continue
            k = d.get("kin")
            p = dict(tid=int(d["tid"]), box=[round(x1 / W, 4), round(y1 / Hh, 4), round(x2 / W, 4), round(y2 / Hh, 4)],
                     side=side, num=num)
            if num and side:
                info = self._roster(side).get(num, {})
                p["name"] = info.get("short", "")
                if side in cur and num in cur[side]:
                    p["shift"] = round(cur[side][num])
            if k is not None and calibrated:
                p["speed"] = round(k.speed, 1)
                p["dist"] = round(k.dist)
            players.append(p)
        msg = dict(type="state", mode=self.mode, focus=self.focus, fps=round(self.fps, 1),
                   status=dict(calib=self.calib_state if self.cam is None else ("ok" if calibrated else "perdu"),
                               match=(f"{self.match.short['home']} / {self.match.short['away']}" if self.match else None),
                               sides=len(self.cluster_side) == 2, colors=self.clf_ready),
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
                    help="maillot de ton équipe si les numéros ne suffisent pas à le deviner")
    ap.add_argument("--weights", default="yolov8s.pt")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--device", default=None)
    ap.add_argument("--fps", type=float, default=10, help="images analysées par seconde (mode équipe)")
    ap.add_argument("--fps-players", type=float, default=12, help="idem en mode joueurs choisis")
    ap.add_argument("--ocr-per-frame", type=int, default=2, help="dos lus par image au plus")
    ap.add_argument("--min-px", type=int, default=55, help="taille mini d'un joueur pour lire son numéro")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--gt", default=None, help=argparse.SUPPRESS)
    Engine(ap.parse_args()).run()


if __name__ == "__main__":
    main()
