"""Composition de la vidéo de sortie (format vertical 1080x1350, style « broadcast analytics »)."""
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BG = (23, 17, 13)            # BGR
PANEL = (34, 27, 22)
ICE = (40, 33, 27)
GRAY = (150, 140, 128)
DIM = (95, 88, 80)
WHITE = (245, 242, 238)

LABELS = {
    "fr": dict(title="HOCKEY AI ANALYTICS",
               sub="Suivi joueurs · Identification équipes · Calibration caméra · Contrôle d'espace",
               live="VUE TACTIQUE LIVE", live_sub="homographie patinoire · contrôle d'espace Voronoï",
               attacking="EN ATTAQUE", nocal="caméra non calibrée sur ce plan",
               s1="CONTRÔLE D'ESPACE · ZONE OFF.", s2="ESPACEMENT · ENVELOPPE m²",
               s3="DISTANCE PARCOURUE · m", s4="ESPACE LIBRE · ATTAQUE",
               s4sub="écart moy. {t} → défenseur le + proche",
               recap="RÉCAP DE L'ACTION", poss="{n} possessions · {s:.1f} s analysées",
               r1="DISTANCE PARCOURUE", r2="VITESSE MAX", r3="CONTRÔLE ZONE OFF. EN ATTAQUE",
               r4="ESPACE LIBRE MOYEN EN ATTAQUE"),
    "en": dict(title="HOCKEY AI ANALYTICS",
               sub="Player tracking · Team ID · Camera calibration · Space control",
               live="LIVE TACTICAL VIEW", live_sub="rink homography · Voronoi space control",
               attacking="ATTACKING", nocal="camera not calibrated on this shot",
               s1="SPACE CONTROL · OFF. ZONE", s2="TEAM SPACING · HULL m²",
               s3="DISTANCE COVERED · m", s4="OPEN SPACE · OFFENSE",
               s4sub="avg gap {t} → nearest defender",
               recap="POSSESSION RECAP", poss="{n} possessions · {s:.1f} s tracked",
               r1="DISTANCE COVERED", r2="TOP SPEED", r3="OFF. ZONE SPACE ON OFFENSE",
               r4="AVG OPEN SPACE ON OFFENSE"),
}


def _font(name, size):
    for p in (os.path.join(HERE, "fonts", name),):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


class Renderer:
    W, H = 1080, 1350
    Y_VIDEO, H_VIDEO = 92, 608
    Y_TAC_HDR = 700
    Y_RINK, H_RINK = 736, 476
    Y_STATS = 1234

    def __init__(self, rink, team_names, team_colors, lang="fr", src_size=(1920, 1080)):
        self.rink = rink
        self.names = team_names
        self.col = team_colors                       # BGR vifs pour l'UI
        self.fill = [tuple(int(c * 0.42 + b * 0.58) for c, b in zip(tc, ICE)) for tc in team_colors]
        self.T = LABELS[lang]
        self.f = {k: _font(n, s) for k, (n, s) in dict(
            title=("Inter-Bold.otf", 30), sub=("Inter-Medium.otf", 14), leg=("Inter-SemiBold.otf", 14),
            hdr=("Inter-Bold.otf", 14), hdrs=("Inter-Medium.otf", 12), lab=("Inter-SemiBold.otf", 11),
            val=("Inter-Bold.otf", 22), tag=("Inter-Bold.otf", 13), pill=("Inter-Bold.otf", 13),
            rt=("Inter-Bold.otf", 40), rs=("Inter-Medium.otf", 15), rl=("Inter-SemiBold.otf", 12),
            rv=("Inter-Bold.otf", 30)).items()}
        # vidéo : mise à l'échelle dans le panneau
        sw, sh = src_size
        self.vs = min(self.W / sw, self.H_VIDEO / sh)
        self.vw, self.vh = int(sw * self.vs), int(sh * self.vs)
        self.vx = (self.W - self.vw) // 2
        self.vy = self.Y_VIDEO + (self.H_VIDEO - self.vh) // 2
        # patinoire : échelle px/m
        self.ps = min((self.W - 80) / rink.L, (self.H_RINK - 24) / rink.W)
        self.rw, self.rh = int(rink.L * self.ps), int(rink.W * self.ps)
        self.rx = (self.W - self.rw) // 2
        self.ry = self.Y_RINK + (self.H_RINK - self.rh) // 2
        self._build_static()
        self.disp = {}
        self.texts = []

    # ------------------------------------------------------------ utilitaires
    def w2p(self, pts):
        pts = np.asarray(pts, np.float32).reshape(-1, 2)
        x = self.rx + (pts[:, 0] + self.rink.hx) * self.ps
        y = self.ry + (self.rink.hy - pts[:, 1]) * self.ps
        return np.stack([x, y], 1)

    def text(self, xy, s, font, color=WHITE, anchor="la"):
        self.texts.append((xy, s, font, color, anchor))

    def _flush_text(self, img):
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        for xy, s, font, color, anchor in self.texts:
            d.text(xy, s, font=self.f[font], fill=(color[2], color[1], color[0]), anchor=anchor)
        self.texts = []
        return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)

    @staticmethod
    def rrect(img, x1, y1, x2, y2, r, color, alpha=1.0):
        x1, y1, x2, y2, r = int(x1), int(y1), int(x2), int(y2), int(r)
        layer = img.copy() if alpha < 1 else img
        cv2.rectangle(layer, (x1 + r, y1), (x2 - r, y2), color, -1, cv2.LINE_AA)
        cv2.rectangle(layer, (x1, y1 + r), (x2, y2 - r), color, -1, cv2.LINE_AA)
        for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)):
            cv2.circle(layer, (cx, cy), r, color, -1, cv2.LINE_AA)
        if alpha < 1:
            cv2.addWeighted(layer, alpha, img, 1 - alpha, 0, img)

    def _build_static(self):
        """Fond fixe : en-tête, cadre de la patinoire, marquages."""
        img = np.full((self.H, self.W, 3), BG, np.uint8)
        self.rink_mask = np.zeros((self.H, self.W), np.uint8)
        outline = self.w2p(self.rink.outline()).astype(np.int32)
        cv2.fillPoly(self.rink_mask, [outline], 255, cv2.LINE_AA)
        self.base = img
        lines = np.zeros_like(img)
        colors = dict(boards=(120, 112, 104), red=(70, 70, 150), blue=(150, 105, 60),
                      goal=(70, 70, 140), circle=(85, 80, 95), dot=(80, 80, 130),
                      crease=(150, 105, 60), center_circle=(150, 105, 60), center_dot=(150, 105, 60))
        thick = dict(boards=2, red=3, blue=3)
        for poly, role in self.rink.markings():
            p = self.w2p(poly)
            cv2.polylines(lines, [np.int32(p * 4)], False, colors[role], thick.get(role, 1),
                          cv2.LINE_AA, shift=2)
        self.lines = lines
        self.lines_m = (lines.max(2) > 0)

    # -------------------------------------------------------------- panneaux
    def _header(self, img):
        self.text((24, 20), self.T["title"], "title")
        self.text((25, 58), self.T["sub"], "sub", GRAY)
        x = self.W - 24
        for k in (1, 0):
            w = self.f["leg"].getlength(self.names[k])
            self.text((x, 30), self.names[k], "leg", WHITE, "ra")
            cv2.circle(img, (int(x - w - 10), 39), 5, self.col[k], -1, cv2.LINE_AA)
            x -= w + 34

    def _video(self, img, frame, players, show_debug=None):
        v = cv2.resize(frame, (self.vw, self.vh), interpolation=cv2.INTER_AREA)
        s = self.vs
        # teinte segmentée des joueurs
        tint = v.copy()
        any_tint = False
        for p in players:
            if p["team"] < 0 or p.get("poly") is None:
                continue
            poly = np.int32(p["poly"] * s)
            cv2.fillPoly(tint, [poly], self.col[p["team"]], cv2.LINE_AA)
            any_tint = True
        if any_tint:
            cv2.addWeighted(tint, 0.38, v, 0.62, 0, v)
        if show_debug is not None:
            show_debug(v, s)
        for p in players:
            if p["team"] < 0:
                continue
            c = self.col[p["team"]]
            x1, y1, x2, y2 = [q * s for q in p["box"]]
            if p.get("poly") is not None:
                cv2.polylines(v, [np.int32(p["poly"] * s)], True, c, 1, cv2.LINE_AA)
            cx, fy = (x1 + x2) / 2, y2
            ax = max(8, (x2 - x1) * 0.55)
            cv2.ellipse(v, (int(cx), int(fy)), (int(ax), int(ax * 0.32)), 0, 0, 360, c, 2, cv2.LINE_AA)
        img[self.vy:self.vy + self.vh, self.vx:self.vx + self.vw] = v
        # étiquettes vitesse (anti-chevauchement : on remonte l'étiquette si besoin)
        placed = []
        for p in sorted(players, key=lambda q: -q["box"][3]):
            if p["team"] < 0 or p.get("speed") is None:
                continue
            x1, y1, x2, _ = [q * s for q in p["box"]]
            label = f"{p['speed']:.0f} km/h"
            tw = self.f["tag"].getlength(label)
            cx, ty = self.vx + (x1 + x2) / 2, self.vy + y1 - 12
            r = [cx - tw / 2 - 6, ty - 9, cx + tw / 2 + 6, ty + 9]
            for _ in range(6):
                hit = [q for q in placed if r[0] < q[2] and r[2] > q[0] and r[1] < q[3] and r[3] > q[1]]
                if not hit:
                    break
                dy = r[3] - min(q[1] for q in hit) + 2
                r[1] -= dy; r[3] -= dy
            if r[1] < self.vy + 2:
                continue
            placed.append(r)
            self.rrect(img, *r, 6, self.col[p["team"]])
            self.text(((r[0] + r[2]) / 2, (r[1] + r[3]) / 2 + 1), label, "tag", (25, 20, 16), "mm")

    def _tactical(self, img, tracks, voronoi, grid_res, fov, attacking, calibrated):
        self.text((24, self.Y_TAC_HDR + 8), self.T["live"], "hdr")
        w = self.f["hdr"].getlength(self.T["live"])
        self.text((24 + w + 12, self.Y_TAC_HDR + 10), self.T["live_sub"], "hdrs", DIM)
        if attacking is not None:
            arrow = "→" if self.cur_dirs and self.cur_dirs[attacking] > 0 else "←"
            label = f"{self.names[attacking]} {self.T['attacking']} {arrow}"
            tw = self.f["pill"].getlength(label)
            x2 = self.W - 24
            self.rrect(img, x2 - tw - 22, self.Y_TAC_HDR + 4, x2, self.Y_TAC_HDR + 28, 12, self.col[attacking])
            self.text((x2 - tw / 2 - 11, self.Y_TAC_HDR + 17), label, "pill", (25, 20, 16), "mm")

        # glace + Voronoï
        region = np.full((self.H, self.W, 3), ICE, np.uint8)
        if voronoi is not None:
            g0 = (voronoi == 0).astype(np.float32)
            valid = (voronoi >= 0).astype(np.float32)
            up0 = cv2.resize(g0, (self.rw, self.rh), interpolation=cv2.INTER_LINEAR)
            upv = cv2.resize(valid, (self.rw, self.rh), interpolation=cv2.INTER_LINEAR)
            up0 = np.flipud(up0)
            upv = np.flipud(upv)
            sub = region[self.ry:self.ry + self.rh, self.rx:self.rx + self.rw]
            m0 = (up0 > 0.5 * upv) & (upv > 0.2)
            m1 = (up0 <= 0.5 * upv) & (upv > 0.2)
            sub[m0] = self.fill[0]
            sub[m1] = self.fill[1]
        m = self.rink_mask > 0
        img[m] = region[m]
        # champ caméra
        if fov is not None:
            lay = np.zeros((self.H, self.W), np.uint8)
            cv2.polylines(lay, [np.int32(self.w2p(fov) * 4)], True, 255, 1, cv2.LINE_AA, shift=2)
            sel = (lay > 0) & m
            img[sel] = (img[sel] * 0.5 + np.array(WHITE) * 0.5).astype(np.uint8)
        lm = self.lines_m
        img[lm] = np.maximum(img[lm], self.lines[lm])
        # joueurs
        for tr in tracks:
            p = self.w2p(tr.pos)[0]
            if self.rink_mask[int(min(max(p[1], 0), self.H - 1)), int(min(max(p[0], 0), self.W - 1))] == 0:
                continue
            cv2.circle(img, (int(p[0]), int(p[1])), 8, (20, 16, 12), -1, cv2.LINE_AA)
            cv2.circle(img, (int(p[0]), int(p[1])), 6, self.col[tr.team], -1, cv2.LINE_AA)
        if not calibrated:
            self.rrect(img, self.W / 2 - 170, self.ry + self.rh / 2 - 18,
                       self.W / 2 + 170, self.ry + self.rh / 2 + 18, 10, PANEL, 0.9)
            self.text((self.W / 2, self.ry + self.rh / 2), self.T["nocal"], "hdrs", GRAY, "mm")

    def _ema(self, key, val, a=0.15):
        if val is None:
            return self.disp.get(key)
        old = self.disp.get(key)
        self.disp[key] = val if old is None else old + a * (val - old)
        return self.disp[key]

    def _stats(self, img, cur):
        cw = (self.W - 48) / 4
        y = self.Y_STATS
        cv2.line(img, (24, y - 6), (self.W - 24, y - 6), (45, 38, 32), 1)
        zc = cur.get("zone_control") or cur.get("control")
        zc0 = self._ema("zc", zc[0] if zc else None)
        h = cur.get("hull", [0, 0])
        h0, h1 = self._ema("h0", h[0] or None), self._ema("h1", h[1] or None)
        d = cur.get("dist", [0, 0])
        op = self._ema("op", cur.get("open"))
        cols = [
            (self.T["s1"], (f"{zc0 * 100:.0f}%", f"{(1 - zc0) * 100:.0f}%") if zc0 is not None else ("–", "–"),
             zc0 if zc0 is not None else 0.5),
            (self.T["s2"], (f"{h0:.0f}" if h0 else "–", f"{h1:.0f}" if h1 else "–"),
             (h0 or 0) / ((h0 or 0) + (h1 or 0)) if (h0 or h1) else 0.5),
            (self.T["s3"], (f"{d[0]:.0f}", f"{d[1]:.0f}"), d[0] / (d[0] + d[1]) if d[0] + d[1] > 0 else 0.5),
        ]
        for i, (lab, (va, vb), ratio) in enumerate(cols):
            x = 24 + i * cw
            self.text((x, y + 8), lab, "lab", GRAY)
            self.text((x, y + 30), va, "val", self.col[0])
            self.text((x + cw - 22, y + 30), vb, "val", self.col[1], "ra")
            bx1, bx2, by = x, x + cw - 22, y + 66
            split = bx1 + (bx2 - bx1) * float(np.clip(ratio, 0, 1))
            cv2.rectangle(img, (int(bx1), by), (int(split) - 2, by + 4), self.col[0], -1)
            cv2.rectangle(img, (int(split) + 2, by), (int(bx2), by + 4), self.col[1], -1)
        x = 24 + 3 * cw
        att = cur.get("attacking")
        self.text((x, y + 8), self.T["s4"], "lab", GRAY)
        c = self.col[att] if att is not None else WHITE
        self.text((x, y + 30), f"{op:.1f} m" if op is not None else "–", "val", c)
        tname = self.names[att] if att is not None else "ATT"
        self.text((x, y + 64), self.T["s4sub"].format(t=tname), "hdrs", DIM)

    # ----------------------------------------------------------------- frame
    def render(self, frame, players, tracks, analytics, fov, calibrated, debug=None):
        img = self.base.copy()
        cur = analytics.cur or {}
        self.cur_dirs = cur.get("dirs")
        self._header(img)
        self._video(img, frame, players, debug)
        self._tactical(img, tracks, analytics.voronoi, None, fov, cur.get("attacking"), calibrated)
        self._stats(img, cur)
        return self._flush_text(img)

    def render_recap(self, last_frame_img, recap, alpha=1.0):
        img = last_frame_img.copy()
        y0, y1 = self.vy, self.vy + self.vh
        dark = img[y0:y1, self.vx:self.vx + self.vw].astype(np.float32)
        dark = cv2.GaussianBlur(dark, (0, 0), 3) * (1 - 0.68 * alpha)
        img[y0:y1, self.vx:self.vx + self.vw] = dark.astype(np.uint8)
        if alpha < 0.3:
            return img
        cx = self.W // 2
        N = self.names
        y = y0 + 70
        self.text((cx, y), self.T["recap"], "rt", WHITE, "mm")
        self.text((cx, y + 38), self.T["poss"].format(n=max(recap["possessions"], 1), s=recap["seconds"]),
                  "rs", GRAY, "mm")
        rows = [(self.T["r1"], f"{N[0]} {recap['dist'][0]:.0f} m  ·  {N[1]} {recap['dist'][1]:.0f} m")]
        ts, tt = recap["top_speed"]
        rows.append((self.T["r2"], f"{ts:.1f} km/h  ({N[tt]})"))
        zc = recap["zone_ctrl"]
        rows.append((self.T["r3"], "  ·  ".join(
            f"{N[k]} {zc[k] * 100:.0f}%" if zc[k] is not None else f"{N[k]} –" for k in (0, 1))))
        op = recap["open"]
        rows.append((self.T["r4"], "  ·  ".join(
            f"{N[k]} {op[k]:.1f} m" if op[k] is not None else f"{N[k]} –" for k in (0, 1))))
        y += 100
        for lab, val in rows:
            self.text((cx, y), lab, "rl", GRAY, "mm")
            self.text((cx, y + 32), val, "rv", WHITE, "mm")
            y += 98
        return self._flush_text(img)
