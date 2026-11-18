"""Suivi du mouvement de caméra -> homographie image -> patinoire à chaque frame.

Principe :
  * l'utilisateur calibre 1..n images clés (calibrate.py) -> H_key (image -> monde)
  * entre deux frames : flux optique sur le décor (joueurs et incrustations TV
    masqués) -> homographie inter-frame M, H_t = H_{t-1} @ M
  * coupure de plan / dérive : ré-ancrage par appariement ORB sur les images clés
"""
import cv2
import numpy as np


def load_calibration(path):
    import json
    with open(path) as f:
        data = json.load(f)
    keys = []
    for k in data["keyframes"]:
        img = np.array(k["image_points"], np.float32)
        wld = np.array(k["rink_points"], np.float32)
        H, _ = cv2.findHomography(img, wld, cv2.RANSAC if len(img) > 4 else 0, 1.0)
        if H is None:
            raise ValueError(f"Calibration invalide à la frame {k['frame']}")
        keys.append((int(k["frame"]), H))
    return data.get("rink", "iihf"), sorted(keys, key=lambda t: t[0])


class CameraTracker:
    def __init__(self, keyframes, frame_size, scale=0.5, static_mask=None,
                 reanchor_every=45, cut_threshold=0.55, log=print):
        self.keys = dict(keyframes)          # frame_idx -> H (image pleine rés. -> monde)
        self.key_feats = {}                  # frame_idx -> (kp, des) ORB
        self.s = scale
        w, h = frame_size
        self.sw, self.sh = int(w * scale), int(h * scale)
        S = np.diag([scale, scale, 1.0])
        self.S, self.Si = S, np.linalg.inv(S)
        self.static = None
        if static_mask is not None:
            self.static = cv2.resize(static_mask, (self.sw, self.sh), interpolation=cv2.INTER_NEAREST)
        self.prev_gray = None
        self.prev_hist = None
        self.H = None
        self.reanchor_every = reanchor_every
        self.cut_threshold = cut_threshold
        self.since_anchor = 0
        self.orb = cv2.ORB_create(2000)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.log = log
        self.lost_frames = 0

    # ---------------------------------------------------------------- helpers
    def _mask(self, boxes):
        m = np.full((self.sh, self.sw), 255, np.uint8)
        if self.static is not None:
            m[self.static > 0] = 0
        for x1, y1, x2, y2 in boxes:
            pad = 0.15 * (x2 - x1)
            cv2.rectangle(m, (int((x1 - pad) * self.s), int((y1 - pad) * self.s)),
                          (int((x2 + pad) * self.s), int((y2 + pad) * self.s)), 0, -1)
        return m

    def _orb(self, gray, mask):
        kp, des = self.orb.detectAndCompute(gray, mask)
        return kp, des

    def register_keyframe_image(self, idx, frame, boxes=()):
        g = cv2.cvtColor(cv2.resize(frame, (self.sw, self.sh)), cv2.COLOR_BGR2GRAY)
        self.key_feats[idx] = self._orb(g, self._mask(boxes))

    def _match_key(self, gray, mask):
        """Homographie image courante -> monde via la meilleure image clé."""
        kp, des = self._orb(gray, mask)
        if des is None or len(kp) < 30:
            return None, 0
        best, best_n = None, 0
        for idx, (kk, dk) in self.key_feats.items():
            if dk is None:
                continue
            ms = self.bf.knnMatch(des, dk, k=2)
            good = [m[0] for m in ms if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
            if len(good) < 25:
                continue
            src = np.float32([kp[m.queryIdx].pt for m in good])
            dst = np.float32([kk[m.trainIdx].pt for m in good])
            M, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
            if M is None:
                continue
            n = int(inl.sum())
            if n > best_n:
                # M : petite image courante -> petite image clé
                best_n = n
                best = self.keys[idx] @ self.Si @ M @ self.S
        return best, best_n

    # ------------------------------------------------------------------ main
    def update(self, idx, frame, boxes):
        small = cv2.resize(frame, (self.sw, self.sh))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        mask = self._mask(boxes)

        cut = False
        if self.prev_hist is not None:
            corr = cv2.compareHist(self.prev_hist, hist, cv2.HISTCMP_CORREL)
            cut = corr < self.cut_threshold

        if idx in self.keys:
            self.H = self.keys[idx].copy()
            self.since_anchor = 0
            if idx not in self.key_feats:
                self.key_feats[idx] = self._orb(gray, mask)
        elif cut or self.H is None:
            H, n = self._match_key(gray, mask)
            self.H = H if n >= 40 else None
            self.since_anchor = 0
        else:
            M = self._interframe(gray, mask)
            if M is None:
                self.lost_frames += 1
                if self.lost_frames > 5:
                    H, n = self._match_key(gray, mask)
                    self.H = H if n >= 40 else None
            else:
                self.lost_frames = 0
                self.H = self.H @ self.Si @ M @ self.S
                self.since_anchor += 1
                if self.reanchor_every and self.since_anchor >= self.reanchor_every:
                    self.since_anchor = 0
                    H, n = self._match_key(gray, mask)
                    if n >= 120:   # ré-ancrage seulement si très fiable
                        self.H = H

        self.prev_gray, self.prev_hist, self.prev_mask = gray, hist, mask
        return self.H

    def _interframe(self, gray, mask):
        """Homographie petite image courante -> petite image précédente."""
        if self.prev_gray is None:
            return np.eye(3)
        pts = cv2.goodFeaturesToTrack(self.prev_gray, 600, 0.01, 8, mask=self.prev_mask)
        if pts is None or len(pts) < 20:
            return None
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, pts, None,
                                              winSize=(21, 21), maxLevel=3)
        ok = st.ravel() == 1
        if ok.sum() < 20:
            return None
        p0, p1 = pts[ok].reshape(-1, 2), nxt[ok].reshape(-1, 2)
        M, inl = cv2.findHomography(p1, p0, cv2.RANSAC, 2.0)
        if M is None or inl.sum() < 15:
            return None
        return M


def detect_static_overlay(video_path, start_frame=0, samples=60, step=5, thr=2.0):
    """Masque des pixels qui ne bougent pas alors que la caméra bouge (score TV, logos).
    Renvoie None si la caméra semble fixe."""
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frames = []
    i = 0
    while len(frames) < samples:
        ok, f = cap.read()
        if not ok:
            break
        if i % step == 0:
            frames.append(cv2.cvtColor(cv2.resize(f, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.float32))
        i += 1
    cap.release()
    if len(frames) < 10:
        return None
    std = np.stack(frames).std(0)
    m = (std < thr).astype(np.uint8) * 255
    frac = m.mean() / 255
    if frac > 0.4:      # caméra quasi fixe : pas d'incrustation identifiable
        return None
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.dilate(m, np.ones((7, 7), np.uint8))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    return m, frac
