"""Génère une fausse vidéo broadcast de hockey (caméra qui panoramique, joueurs,
incrustation TV) + vérité terrain + calibration, pour tester le pipeline sans YOLO."""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rink import Rink  # noqa: E402

rng = np.random.default_rng(3)
OUT = sys.argv[1] if len(sys.argv) > 1 else "synthetic"
os.makedirs(OUT, exist_ok=True)
rink = Rink("iihf")
W, H, FPS, SECS = 1280, 720, 30, 10
S = 30                     # px/m de la texture
XR, YR = 42, 26            # étendue de la texture (m)

# ------------------------------------------------------------ texture du sol
tw, th = 2 * XR * S, 2 * YR * S
tex = np.zeros((th, tw, 3), np.uint8)
tex[:] = rng.integers(20, 90, (th, tw, 3), dtype=np.uint8)            # « public »
tex = cv2.GaussianBlur(tex, (0, 0), 2)
A = np.array([[1 / S, 0, -XR], [0, -1 / S, YR], [0, 0, 1]])            # tex px -> monde
Ai = np.linalg.inv(A)


def w2t(p):
    p = np.c_[p, np.ones(len(p))] @ Ai.T
    return p[:, :2]


# panneaux publicitaires autour de la bande
big = rink.outline(60)
grown = big * np.array([(rink.hx + 1.2) / rink.hx, (rink.hy + 1.2) / rink.hy])
cv2.fillPoly(tex, [np.int32(w2t(grown))], (200, 200, 200))
for i in range(40):
    a0 = i / 40 * 2 * np.pi
    pts = []
    for a in np.linspace(a0, a0 + 2 * np.pi / 40, 6):
        k = int(a / (2 * np.pi) * len(grown)) % len(grown)
        pts.append(grown[k])
    for a in np.linspace(a0 + 2 * np.pi / 40, a0, 6):
        k = int(a / (2 * np.pi) * len(big)) % len(big)
        pts.append(big[k])
    col = tuple(int(c) for c in rng.integers(30, 255, 3))
    cv2.fillPoly(tex, [np.int32(w2t(np.array(pts)))], col)
# glace
cv2.fillPoly(tex, [np.int32(w2t(rink.outline()))], (232, 228, 222))
ice = np.zeros(tex.shape[:2], np.uint8)
cv2.fillPoly(ice, [np.int32(w2t(rink.outline()))], 255)
noise = rng.normal(0, 6, tex.shape[:2])
for _ in range(900):     # rayures de patins
    x, y = rng.uniform(-rink.hx, rink.hx), rng.uniform(-rink.hy, rink.hy)
    a = rng.uniform(0, np.pi)
    l = rng.uniform(0.5, 3)
    p = w2t(np.array([[x, y], [x + l * np.cos(a), y + l * np.sin(a)]]))
    cv2.line(tex, tuple(np.int32(p[0])), tuple(np.int32(p[1])), (205, 200, 196), 1, cv2.LINE_AA)
t = tex.astype(np.float32)
t[ice > 0] += noise[ice > 0, None]
tex = np.clip(t, 0, 255).astype(np.uint8)
colmap = dict(boards=(60, 60, 60), red=(40, 40, 200), blue=(160, 80, 20), goal=(40, 40, 200),
              circle=(40, 40, 200), dot=(40, 40, 200), crease=(40, 40, 200),
              center_circle=(160, 80, 20), center_dot=(160, 80, 20))
thick = dict(red=0.3, blue=0.3, boards=0.25)
for poly, role in rink.markings():
    cv2.polylines(tex, [np.int32(w2t(poly) * 8)], False, colmap[role],
                  max(1, int(thick.get(role, 0.06) * S)), cv2.LINE_AA, shift=3)

# ------------------------------------------------------------------ joueurs
N = SECS * FPS
players = []
pid = 1
for team in (0, 1):
    for k in range(5):
        players.append(dict(id=pid, team=team, kind="skater", pos=np.array([rng.uniform(-10, 10), rng.uniform(-10, 10)]),
                            vel=np.zeros(2), off=np.array([rng.uniform(-6, 6), rng.uniform(-9, 9)])))
        pid += 1
    gx = -rink.goal_x + 1.0 if team == 0 else rink.goal_x - 1.0
    players.append(dict(id=pid, team=team, kind="goalie", pos=np.array([gx, 0.0]), vel=np.zeros(2), off=np.zeros(2)))
    pid += 1
for k in range(2):
    players.append(dict(id=pid, team=-1, kind="ref", pos=np.array([rng.uniform(-5, 5), 11.0 * (1 if k else -1)]),
                        vel=np.zeros(2), off=np.array([0, 11.0 * (1 if k else -1)])))
    pid += 1

JERSEY = {0: (40, 40, 200), 1: (238, 238, 238), -1: None}
PANTS = {0: (30, 30, 30), 1: (90, 40, 20), -1: (20, 20, 20)}


def zone_center(f):
    # A attaque à droite pendant 5 s, puis B attaque à gauche
    return (1, 15.0) if f < N // 2 else (-1, -15.0)


def step_players(f):
    side, zx = zone_center(f)
    att_team = 0 if side == 1 else 1
    for p in players:
        if p["kind"] == "goalie":
            gx = -rink.goal_x + 1.0 if p["team"] == 0 else rink.goal_x - 1.0
            tgt = np.array([gx, np.clip(np.mean([q["pos"][1] for q in players if q["kind"] == "skater"]) * 0.2, -1.2, 1.2)])
        elif p["kind"] == "ref":
            tgt = np.array([zx * 0.6, p["off"][1]])
        elif p["team"] == att_team:
            tgt = np.array([zx, 0]) + p["off"] * [1.2, 1.0] + 3 * np.sin(f / 25 + p["id"]) * np.array([1, 0.6])
        else:
            tgt = np.array([zx + side * 5, 0]) + p["off"] * [0.5, 0.6]
        d = tgt - p["pos"]
        vmax = 7.5 if p["kind"] == "skater" else 2.5
        desired = d / (np.linalg.norm(d) + 1e-6) * min(vmax, np.linalg.norm(d) * 1.2)
        p["vel"] += (desired - p["vel"]) * 0.06
        p["pos"] = p["pos"] + p["vel"] / FPS
        p["pos"][0] = np.clip(p["pos"][0], -rink.hx + 1, rink.hx - 1)
        p["pos"][1] = np.clip(p["pos"][1], -rink.hy + 1, rink.hy - 1)


# ------------------------------------------------------------------ caméra
C = np.array([0.0, -rink.hy - 14.0, 11.0])
K = np.array([[1150, 0, W / 2], [0, 1150, H / 2], [0, 0, 1.0]])


def camera(tx, zoom):
    T = np.array([tx, -2.0, 0.0])
    z = T - C
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])
    t = -R @ C
    Kz = K.copy()
    Kz[0, 0] = Kz[1, 1] = K[0, 0] * zoom
    P = Kz @ np.c_[R, t]
    Hw = Kz @ np.c_[R[:, 0], R[:, 1], t]
    return P, Hw, x


def proj(P, X):
    X = np.c_[X, np.ones(len(X))] @ P.T
    return X[:, :2] / X[:, 2:3]


BOARD = rink.outline(30)
BOARD = np.vstack([BOARD, BOARD[:1]])
AD_COLS = [tuple(int(c) for c in rng.integers(0, 255, 3)) for _ in range(7)]
vw = cv2.VideoWriter(os.path.join(OUT, "synthetic.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
gt = []
camx = 0.0
for f in range(N):
    step_players(f)
    cx = np.mean([p["pos"][0] for p in players if p["kind"] == "skater"])
    camx += (cx * 0.85 - camx) * 0.05
    zoom = 1.0 + 0.15 * np.sin(f / 80)
    P, Hw, right = camera(camx, zoom)
    img = cv2.warpPerspective(tex, Hw @ A, (W, H), flags=cv2.INTER_LINEAR)
    # bandes verticales blanches (1,07 m) avec panneaux publicitaires, côté fond et extrémités
    for k, (a, b) in enumerate(zip(BOARD[:-1], BOARD[1:])):
        if (a[1] + b[1]) / 2 < -rink.hy + rink.r:
            continue
        q = proj(P, np.array([[a[0], a[1], 0], [b[0], b[1], 0], [b[0], b[1], 1.07], [a[0], a[1], 1.07]]))
        cv2.fillConvexPoly(img, np.int32(q), (236, 236, 236), cv2.LINE_AA)
        if k % 5 in (1, 2):
            q = proj(P, np.array([[a[0], a[1], 0.2], [b[0], b[1], 0.2], [b[0], b[1], 0.85], [a[0], a[1], 0.85]]))
            cv2.fillConvexPoly(img, np.int32(q), AD_COLS[k % len(AD_COLS)], cv2.LINE_AA)
    # joueurs (du plus loin au plus proche)
    order = sorted(players, key=lambda p: -p["pos"][1])
    fr = []
    for p in order:
        F = np.array([p["pos"][0], p["pos"][1], 0.0])
        wide = 1.5 if p["kind"] == "goalie" else 1.0

        def pt(u, z):
            return F + u * wide * right + np.array([0, 0, z])
        legs = proj(P, np.array([pt(-0.22, 0), pt(0.22, 0), pt(0.25, 0.9), pt(-0.25, 0.9)]))
        torso = proj(P, np.array([pt(-0.33, 0.85), pt(0.33, 0.85), pt(0.30, 1.48), pt(-0.30, 1.48)]))
        head = proj(P, np.array([pt(0, 1.62)]))[0]
        hr = abs(proj(P, np.array([pt(0.13, 1.62)]))[0][0] - head[0])
        cv2.fillPoly(img, [np.int32(legs)], PANTS[p["team"]], cv2.LINE_AA)
        if p["team"] >= 0:
            cv2.fillPoly(img, [np.int32(torso)], JERSEY[p["team"]], cv2.LINE_AA)
        else:
            cv2.fillPoly(img, [np.int32(torso)], (240, 240, 240), cv2.LINE_AA)
            for s in np.linspace(-0.25, 0.2, 4):
                st = proj(P, np.array([pt(s, 0.85), pt(s + 0.08, 0.85), pt(s + 0.08, 1.48), pt(s, 1.48)]))
                cv2.fillPoly(img, [np.int32(st)], (15, 15, 15), cv2.LINE_AA)
        cv2.circle(img, (int(head[0]), int(head[1])), max(2, int(hr)), (25, 25, 25), -1, cv2.LINE_AA)
        allp = np.vstack([legs, torso, [head + [0, -hr]]])
        x1, y1 = allp.min(0)
        x2, y2 = allp.max(0)
        if x2 < 0 or x1 > W or y2 < 0 or y1 > H:
            continue
        hull = cv2.convexHull(np.float32(allp)).reshape(-1, 2)
        fr.append(dict(id=p["id"], team=p["team"], world=p["pos"].tolist(),
                       box=[float(x1), float(y1), float(x2), float(y2)], poly=hull.tolist()))
    # incrustation TV fixe
    cv2.rectangle(img, (40, 640), (330, 690), (30, 20, 15), -1)
    cv2.putText(img, "BRS 2  -  1 ANG   2nd 12:34", (52, 672), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    vw.write(img)
    gt.append(dict(players=fr, H_world2img=Hw.tolist()))
    if f == 0:
        Hw0 = Hw
vw.release()

# calibration simulée sur la frame 0 (points cliqués avec ~1,5 px d'erreur)
lm = rink.landmarks()
names, ip, rp = [], [], []
for n, (x, y) in lm.items():
    q = Hw0 @ np.array([x, y, 1.0])
    u, v = q[:2] / q[2]
    if 20 < u < W - 20 and 20 < v < H - 20:
        names.append(n)
        ip.append([float(u + rng.normal(0, 1.5)), float(v + rng.normal(0, 1.5))])
        rp.append([x, y])
json.dump(dict(rink="iihf", keyframes=[dict(frame=0, names=names, image_points=ip, rink_points=rp)]),
          open(os.path.join(OUT, "calib.json"), "w"), indent=1)
json.dump(dict(frames=gt), open(os.path.join(OUT, "gt.json"), "w"))
print("ok", len(names), "repères visibles frame 0")
