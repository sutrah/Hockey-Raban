#!/usr/bin/env python3
"""Outil de calibration : clique des repères de la patinoire sur 1+ images de la vidéo.

  python calibrate.py --video match.mp4 --rink iihf --out calib.json

Commandes (fenêtre vidéo) :
  clic gauche     place le repère sélectionné
  n / p           repère suivant / précédent        (liste à gauche)
  u               annule le dernier point
  d / a           avance / recule d'1 s       ·   . / ,  ±1 frame
  k               enregistre cette image comme image clé (≥ 4 points)
  c               efface les points de l'image courante
  q / Échap       enregistre le JSON et quitte

Conseil : choisis des points bien répartis (ex. 2 lignes bleues × 2 bandes + points
d'engagement). Dès 4 points, les lignes de la patinoire sont reprojetées en vert :
si elles collent aux vraies lignes, la calibration est bonne. Ajoute une image clé
supplémentaire pour chaque grand plan différent (autre moitié de patinoire, zoom…).
"""
import argparse
import json

import cv2
import numpy as np

from rink import Rink


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--rink", default="iihf")
    ap.add_argument("--out", default="calib.json")
    ap.add_argument("--start", type=float, default=0.0)
    args = ap.parse_args()

    rink = Rink(args.rink)
    names = list(rink.landmarks().keys())
    lm = rink.landmarks()
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    state = dict(idx=int(args.start * fps), sel=0, pts={}, keys=[])

    def read(i):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        return f if ok else None

    frame = read(state["idx"])
    h, w = frame.shape[:2]
    disp_s = min(1.0, 1400 / w)

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            state["pts"][names[state["sel"]]] = (x / disp_s, y / disp_s)
            state["sel"] = (state["sel"] + 1) % len(names)

    win = "Calibration patinoire"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, on_mouse)

    while True:
        v = cv2.resize(frame, None, fx=disp_s, fy=disp_s)
        pts = state["pts"]
        if len(pts) >= 4:
            img = np.float32([pts[k] for k in pts])
            wld = np.float32([lm[k] for k in pts])
            H, _ = cv2.findHomography(wld, img, 0)
            if H is not None:
                for poly, _ in rink.markings():
                    P = np.c_[poly, np.ones(len(poly))] @ H.T
                    if (P[:, 2] <= 0).any():
                        continue
                    q = (P[:, :2] / P[:, 2:3]) * disp_s
                    if np.abs(q).max() > 1e5:
                        continue
                    cv2.polylines(v, [np.int32(q)], False, (80, 255, 80), 1, cv2.LINE_AA)
                # erreur de reprojection
                rp = cv2.perspectiveTransform(wld.reshape(-1, 1, 2), H).reshape(-1, 2)
                err = np.linalg.norm(rp - img, axis=1).mean()
                cv2.putText(v, f"erreur moy. {err:.1f} px", (10, v.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 255, 80), 2)
        for k, (x, y) in pts.items():
            cv2.circle(v, (int(x * disp_s), int(y * disp_s)), 5, (0, 200, 255), -1)
            cv2.putText(v, str(names.index(k) + 1), (int(x * disp_s) + 6, int(y * disp_s) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        # panneau
        panel = np.full((v.shape[0], 330, 3), 28, np.uint8)
        cv2.putText(panel, f"frame {state['idx']}  t={state['idx'] / fps:.1f}s", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(panel, f"images cles : {[k['frame'] for k in state['keys']]}", (10, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        for i, n in enumerate(names):
            col = (0, 200, 255) if i == state["sel"] else ((80, 255, 80) if n in pts else (170, 170, 170))
            cv2.putText(panel, f"{i + 1:2d} {n}", (10, 70 + i * 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1)
        # mini-carte avec le repère sélectionné
        ms = 300 / rink.L
        mh = int(rink.W * ms)
        y0 = min(v.shape[0] - mh - 10, 70 + len(names) * 20 + 10)
        mp = lambda X, Y: (int(15 + (X + rink.hx) * ms), int(y0 + (rink.hy - Y) * ms))
        for poly, _ in rink.markings():
            cv2.polylines(panel, [np.int32([mp(a, b) for a, b in poly])], False, (120, 120, 120), 1, cv2.LINE_AA)
        sx, sy = lm[names[state["sel"]]]
        cv2.circle(panel, mp(sx, sy), 5, (0, 200, 255), -1)
        cv2.imshow(win, np.hstack([v, panel]))

        k = cv2.waitKey(30) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("n"):
            state["sel"] = (state["sel"] + 1) % len(names)
        elif k == ord("p"):
            state["sel"] = (state["sel"] - 1) % len(names)
        elif k == ord("u") and pts:
            pts.pop(list(pts.keys())[-1])
        elif k == ord("c"):
            pts.clear()
        elif k in (ord("d"), ord("a"), ord("."), ord(",")):
            step = {ord("d"): int(fps), ord("a"): -int(fps), ord("."): 1, ord(","): -1}[k]
            state["idx"] = int(np.clip(state["idx"] + step, 0, nfr - 1))
            frame = read(state["idx"])
            pts.clear()
        elif k == ord("k"):
            if len(pts) < 4:
                print("Il faut au moins 4 points.")
                continue
            state["keys"] = [kk for kk in state["keys"] if kk["frame"] != state["idx"]]
            state["keys"].append(dict(frame=state["idx"], names=list(pts.keys()),
                                      image_points=[list(map(float, pts[n])) for n in pts],
                                      rink_points=[list(lm[n]) for n in pts]))
            print(f"Image clé {state['idx']} enregistrée ({len(pts)} points)")

    if state["keys"]:
        with open(args.out, "w") as f:
            json.dump(dict(rink=args.rink, video=args.video, keyframes=state["keys"]), f, indent=1)
        print(f"-> {args.out} ({len(state['keys'])} image(s) clé(s))")
    else:
        print("Aucune image clé enregistrée (touche k).")


if __name__ == "__main__":
    main()
