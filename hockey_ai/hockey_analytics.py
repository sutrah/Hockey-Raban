#!/usr/bin/env python3
"""HOCKEY AI ANALYTICS — analyse tactique d'une vidéo de match de hockey sur glace.

  python hockey_analytics.py match.mp4                       (tout automatique)
  python hockey_analytics.py match.mp4 --teams BREST ANGERS  (noms affichés)
  python hockey_analytics.py                                 (ouvre une fenêtre pour choisir la vidéo)

La caméra est calibrée automatiquement à partir des lignes de la patinoire.
Si ça échoue, calibration manuelle : python calibrate.py --video match.mp4 --out calib.json
puis ajoute --calib calib.json.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import time

import cv2
import numpy as np

from analytics import Analytics
from autocalib import AutoCameraTracker, auto_calibrate
from camera import CameraTracker, detect_static_overlay, load_calibration
from render import Renderer
from rink import Rink
from teams import REF, UNK, TeamClassifier, hex_to_bgr, jersey_feature

DEFAULT_COLORS = ["#4a9eff", "#f5b928"]   # bleu / ambre (UI)


# ---------------------------------------------------------------- détecteurs
class YoloDetector:
    def __init__(self, weights, imgsz=1280, conf=0.25, seg=True, device=None):
        from ultralytics import YOLO
        if seg and "-seg" not in weights:
            weights = weights.replace(".pt", "-seg.pt")
        self.model = YOLO(weights)
        self.seg = seg
        self.kw = dict(classes=[0], imgsz=imgsz, conf=conf, iou=0.5, verbose=False)
        if device:
            self.kw["device"] = device

    def _parse(self, r, tracked):
        out = []
        if r.boxes is None or len(r.boxes) == 0:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy()
        ids = r.boxes.id.cpu().numpy().astype(int) if (tracked and r.boxes.id is not None) else None
        confs = r.boxes.conf.cpu().numpy()
        polys = r.masks.xy if (self.seg and r.masks is not None) else None
        for i, b in enumerate(xyxy):
            if tracked and ids is None:
                continue
            out.append(dict(tid=int(ids[i]) if ids is not None else -1, box=b, conf=float(confs[i]),
                            poly=np.asarray(polys[i], np.float32) if polys is not None and len(polys[i]) >= 3 else None))
        return out

    def detect(self, frame):
        return self._parse(self.model.predict(frame, **self.kw)[0], False)

    def track(self, frame):
        return self._parse(self.model.track(frame, persist=True, tracker="bytetrack.yaml", **self.kw)[0], True)


class GTDetector:
    """Détections « vérité terrain » (tests sur vidéo synthétique)."""
    def __init__(self, path):
        with open(path) as f:
            self.data = json.load(f)["frames"]
        self.i = 0

    def _get(self, idx):
        fr = self.data[idx] if idx < len(self.data) else {"players": []}
        return [dict(tid=p["id"], box=np.array(p["box"], float), conf=1.0,
                     poly=np.array(p["poly"], np.float32) if p.get("poly") else None) for p in fr["players"]]

    def detect(self, frame):
        r = self._get(self.i); self.i += 1; return r

    def track(self, frame):
        r = self._get(self.i); self.i += 1; return r

    def seek(self, idx):
        self.i = idx


# ------------------------------------------------------------------ helpers
def pick_video():
    """Fenêtre de choix de fichier quand aucune vidéo n'est passée en argument."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(title="Choisis la vidéo du match",
                                          filetypes=[("Vidéos", "*.mp4 *.mov *.mkv *.avi *.m4v"), ("Tous", "*.*")])
        root.destroy()
        return path or None
    except Exception:
        return None


def auto_device():
    """GPU NVIDIA, puis Apple Silicon (mps), sinon CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def camera_center(args, rink, f0, f1, fps, size, static):
    """Position de la caméra : relue depuis le cache à côté de la vidéo, sinon estimée."""
    cache = os.path.splitext(args.video)[0] + ".autocalib.json"
    key = dict(rink=[rink.L, rink.W], size=list(size))
    if os.path.exists(cache) and not args.recalib:
        try:
            with open(cache) as f:
                d = json.load(f)
            if d.get("key") == key:
                print(f"[auto] calibration relue depuis {cache} (--recalib pour la refaire)")
                return np.array(d["camera"], float)
        except Exception:
            pass
    c = auto_calibrate(args.video, rink, f0, f1, fps, size, static)
    try:
        with open(cache, "w") as f:
            json.dump(dict(key=key, camera=[float(v) for v in c]), f)
    except OSError:
        pass
    return c


def occluded_flags(dets, thr=0.3):
    """True si le torse d'un joueur est caché par un joueur plus proche de la caméra."""
    out = []
    for i, a in enumerate(dets):
        x1, y1, x2, y2 = a["box"]
        tx1, tx2, ty1, ty2 = x1 + 0.25 * (x2 - x1), x1 + 0.75 * (x2 - x1), y1 + 0.15 * (y2 - y1), y1 + 0.5 * (y2 - y1)
        area = max((tx2 - tx1) * (ty2 - ty1), 1)
        occ = False
        for j, b in enumerate(dets):
            if i == j or b["box"][3] <= y2:
                continue
            ix = max(0, min(tx2, b["box"][2]) - max(tx1, b["box"][0]))
            iy = max(0, min(ty2, b["box"][3]) - max(ty1, b["box"][1]))
            if ix * iy / area > thr:
                occ = True
                break
        out.append(occ)
    return out


def to_world(H, pts):
    p = cv2.perspectiveTransform(np.float32(pts).reshape(-1, 1, 2), H)
    return p.reshape(-1, 2)


def oriented(H, w, h):
    """Normalise le signe de H pour que l'image (bas-centre) ait w>0."""
    v = H @ np.array([w / 2, h * 0.95, 1.0])
    return H * np.sign(v[2]) if v[2] != 0 else H


def camera_fov(H, w, h):
    H = oriented(H, w, h)
    border = []
    n = 12
    for t in np.linspace(0, 1, n):
        border.append((t * w, h))
    for t in np.linspace(1, 0, n):
        border.append((w, t * h))
    for t in np.linspace(1, 0, n):
        border.append((t * w, 0))
    for t in np.linspace(0, 1, n):
        border.append((0, t * h))
    out = []
    for u, v in border:
        p = H @ np.array([u, v, 1.0])
        if p[2] <= 1e-9:
            continue
        out.append(np.clip(p[:2] / p[2], -120, 120))
    return np.array(out, np.float32) if len(out) >= 3 else None


def fix_hi_sign(H, rink):
    """Signe de l'inverse : le centre de la glace doit se projeter avec w>0."""
    Hi = np.linalg.inv(H)
    if (Hi @ np.array([0, 0, 1.0]))[2] < 0:
        Hi = -Hi
    return Hi


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_pos", nargs="?", metavar="VIDEO", help="vidéo du match")
    ap.add_argument("--video", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--calib", default=None,
                    help="JSON produit par calibrate.py (facultatif : sinon calibration automatique)")
    ap.add_argument("--recalib", action="store_true", help="refait la calibration automatique")
    ap.add_argument("--out", default=None, help="vidéo produite (défaut : <vidéo>_analyse.mp4)")
    ap.add_argument("--rink", default=None,
                    help="iihf | iihf_26 | nhl | fichier.json (défaut : iihf, ou celui de la calibration)")
    ap.add_argument("--teams", nargs=2, default=["ÉQUIPE A", "ÉQUIPE B"], metavar=("A", "B"))
    ap.add_argument("--jerseys", nargs=2, default=None, metavar=("HEX_A", "HEX_B"),
                    help="couleurs réelles des maillots (fixe quelle équipe est A/B)")
    ap.add_argument("--colors", nargs=2, default=DEFAULT_COLORS, metavar=("HEX_A", "HEX_B"),
                    help="couleurs d'affichage dans l'interface")
    ap.add_argument("--attack", default=None,
                    help="ex. 'A:droite' si l'équipe A attaque vers la droite (sinon déduit automatiquement)")
    ap.add_argument("--lang", default="fr", choices=["fr", "en"])
    ap.add_argument("--weights", default="yolov8s.pt", help="modèle YOLO (yolov8s/m/l, yolo11s…)")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=None, help="cpu | mps (Mac Apple Silicon) | 0 (GPU NVIDIA) · défaut : auto")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--no-seg", action="store_true", help="sans segmentation (plus rapide, pas de teinte joueurs)")
    ap.add_argument("--no-refs", action="store_true", help="ne pas chercher les arbitres rayés")
    ap.add_argument("--start", type=float, default=0.0, help="début (s)")
    ap.add_argument("--end", type=float, default=None, help="fin (s)")
    ap.add_argument("--fit-seconds", type=float, default=4.0, help="durée utilisée pour apprendre les couleurs")
    ap.add_argument("--recap-seconds", type=float, default=4.0)
    ap.add_argument("--debug-lines", action="store_true", help="affiche les lignes projetées sur la vidéo")
    ap.add_argument("--export", default=None, help="CSV des positions/vitesses par joueur et par frame")
    ap.add_argument("--gt", default=None, help=argparse.SUPPRESS)   # tests : détections vérité terrain
    args = ap.parse_args()
    args.video = args.video or args.video_pos or pick_video()
    if not args.video:
        ap.error("donne la vidéo : python hockey_analytics.py match.mp4")
    if not args.out:
        args.out = os.path.splitext(args.video)[0] + "_analyse.mp4"
    if not args.gt and not args.device:
        args.device = auto_device()
        print(f"[info] calcul sur : {args.device}")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"Impossible d'ouvrir {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, Hh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f0 = int(args.start * fps)
    f1 = min(nfr, int(args.end * fps)) if args.end else nfr

    if args.calib:
        rink_spec, keys = load_calibration(args.calib)
    else:
        rink_spec, keys = "iihf", None
    rink = Rink(args.rink or rink_spec)
    print(f"[info] {W}x{Hh} @ {fps:.1f} fps · frames {f0}->{f1} · patinoire {rink.L}x{rink.W} m · "
          + (f"{len(keys)} image(s) clé(s)" if keys else "calibration automatique"))

    st = detect_static_overlay(args.video, f0)
    static = st[0] if st else None
    if st:
        print(f"[info] incrustations TV fixes masquées ({st[1] * 100:.1f} % de l'image)")

    if keys:
        make_cam = lambda: CameraTracker(keys, (W, Hh), static_mask=static)
    else:
        center = camera_center(args, rink, f0, f1, fps, (W, Hh), static)
        make_cam = lambda: AutoCameraTracker(rink, (W, Hh), center, static_mask=static)

    det = GTDetector(args.gt) if args.gt else YoloDetector(args.weights, args.imgsz, args.conf, not args.no_seg, args.device)

    # ---------------------------------------------- passe 1 : couleurs d'équipe
    clf = TeamClassifier(team_jerseys=args.jerseys, detect_refs=not args.no_refs)
    cam = make_cam()
    fit_end = min(f1, f0 + int(args.fit_seconds * fps))
    step = max(1, int(fps / 6))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    if args.gt:
        det.seek(f0)
    print("[1/2] apprentissage des couleurs d'équipe…")
    for idx in range(f0, fit_end):
        ok, frame = cap.read()
        if not ok:
            break
        need = (idx - f0) % step == 0
        dets = det.detect(frame) if (need or args.gt) else []
        Hc = cam.update(idx, frame, [d["box"] for d in dets])
        if not need:
            continue
        for d, oc in zip(dets, occluded_flags(dets)):
            if oc:
                continue
            x1, y1, x2, y2 = d["box"]
            if Hc is not None:
                wx, wy = to_world(Hc, [((x1 + x2) / 2, y2)])[0]
                if not rink.inside(wx, wy, 1.0):
                    continue
            clf.add_sample(jersey_feature(frame, d["box"], d["poly"]))
    clf.fit()
    jer = ["#%02x%02x%02x" % tuple(reversed(c)) for c in clf.jersey_bgr]
    print(f"[info] maillots détectés : {args.teams[0]} ≈ {jer[0]} · {args.teams[1]} ≈ {jer[1]}"
          "  (si c'est inversé, passe --jerseys)")

    # ------------------------------------------------------- passe 2 : analyse
    attack_dirs = None
    if args.attack:
        team, side = args.attack.split(":")
        s = +1 if side.lower().startswith(("d", "r")) else -1
        a = 0 if team.strip().upper() in ("A", args.teams[0].upper()) else 1
        attack_dirs = {a: s, 1 - a: -s}
    an = Analytics(rink, fps, args.teams, attack_dirs, grid_res=0.25)
    colors = [hex_to_bgr(c) for c in args.colors]
    ren = Renderer(rink, args.teams, colors, args.lang, (W, Hh))
    cam = make_cam()
    if not args.gt:
        det = YoloDetector(args.weights, args.imgsz, args.conf, not args.no_seg, args.device)   # tracker neuf
    else:
        det.seek(f0)

    tmp = args.out + ".tmp.mp4"
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (Renderer.W, Renderer.H))
    csvw = None
    if args.export:
        fcsv = open(args.export, "w", newline="")
        csvw = csv.writer(fcsv)
        csvw.writerow(["frame", "t_s", "track_id", "team", "x_m", "y_m", "speed_kmh", "dist_m"])

    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    print("[2/2] analyse…")
    t0 = time.time()
    last = None
    for idx in range(f0, f1):
        ok, frame = cap.read()
        if not ok:
            break
        dets = det.track(frame)
        H = cam.update(idx, frame, [d["box"] for d in dets])
        players, feed = [], []
        occ = occluded_flags(dets)
        for d, oc in zip(dets, occ):
            x1, y1, x2, y2 = d["box"]
            wpos = None
            if H is not None:
                wx, wy = to_world(H, [((x1 + x2) / 2, y2)])[0]
                if not rink.inside(wx, wy, 1.5):
                    continue          # public, banc, hors glace
                wpos = (float(np.clip(wx, -rink.hx, rink.hx)), float(np.clip(wy, -rink.hy, rink.hy)))
            lab = UNK if oc else clf.predict(jersey_feature(frame, d["box"], d["poly"]))
            team = clf.vote(d["tid"], lab)
            p = dict(d, team=team if team != UNK else REF, world=wpos)
            players.append(p)
            if wpos is not None and team in (0, 1):
                feed.append((d["tid"], team, wpos[0], wpos[1]))
        tracks = an.update(idx, feed) if H is not None else []
        tmap = {t.tid: t for t in tracks}
        for p in players:
            tr = tmap.get(p["tid"])
            p["speed"] = tr.speed if tr is not None and len(tr.hist) > fps * 0.5 else None
            if csvw and tr is not None:
                csvw.writerow([idx, f"{idx / fps:.3f}", tr.tid, args.teams[tr.team],
                               f"{tr.pos[0]:.2f}", f"{tr.pos[1]:.2f}", f"{tr.speed:.1f}", f"{tr.dist:.1f}"])
        fov = camera_fov(H, W, Hh) if H is not None else None
        dbg = None
        if args.debug_lines and H is not None:
            Hi = fix_hi_sign(H, rink)

            def dbg(v, s, Hi=Hi):
                for poly, _ in rink.markings():
                    P = np.c_[poly, np.ones(len(poly))] @ Hi.T
                    okp = P[:, 2] > 1e-9
                    q = np.where(okp[:, None], P[:, :2] / np.where(okp, P[:, 2], 1)[:, None], 0) * s
                    for i in range(len(q) - 1):
                        if okp[i] and okp[i + 1] and np.abs(q[i:i + 2]).max() < 1e5:
                            cv2.line(v, tuple(np.int32(q[i])), tuple(np.int32(q[i + 1])),
                                     (255, 255, 255), 1, cv2.LINE_AA)
        out = ren.render(frame, players, tracks, an, fov, H is not None, dbg)
        vw.write(out)
        last = out
        n = idx - f0 + 1
        if n % 30 == 0:
            el = time.time() - t0
            print(f"  frame {n}/{f1 - f0} · {n / el:.1f} fps · {len(feed)} joueurs sur la glace")

    # -------------------------------------------------------------- récap final
    if last is not None and args.recap_seconds > 0:
        rc = an.recap()
        nrec = int(args.recap_seconds * fps)
        for i in range(nrec):
            vw.write(ren.render_recap(last, rc, min(1.0, i / (0.5 * fps))))
        print("[récap]", json.dumps({k: (v if not isinstance(v, np.floating) else float(v))
                                      for k, v in rc.items()}, default=float))
    vw.release()
    if hasattr(cam, "stats") and cam.stats["frames"]:
        print(f"[auto] patinoire repérée sur {100 * cam.stats['located'] / cam.stats['frames']:.0f} % des images")
    if csvw:
        fcsv.close()

    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.out], check=True)
        os.remove(tmp)
    else:
        os.replace(tmp, args.out)
    print(f"[ok] {args.out}")


if __name__ == "__main__":
    main()
