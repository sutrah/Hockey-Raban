#!/usr/bin/env python3
"""Outil d'annotation des images capturées avec `live_engine.py --annoter 180`.

  py annoter.py                      (dernière séance capturée)
  py annoter.py --dossier annotations/84389/20261118_201500

Ouvre une page dans le navigateur : chaque image avec les joueurs détectés. Clique un joueur
et choisis son nom (ou « Arbitre ») ; trace un cadre autour d'un joueur oublié. Un nom donné
une fois se propage aux autres images du même joueur (même piste de suivi), en pointillé ;
il suffit de corriger les erreurs.

« Exporter » écrit :
  - la mémoire du match (identités par instant de jeu) : les noms « collent » ensuite à la
    vidéo à chaque relecture (priorité : identification à la main la plus récente) ;
  - dataset_numeros/<EQUIPE>_<num>/ et dataset_equipes/<EQUIPE|ARB>/ : vignettes pour
    entraîner la reconnaissance des joueurs et des équipes (train_numeros.py) ;
  - dataset_detection/ : images + cadres au format YOLO, pour entraîner un détecteur
    propre à la patinoire (joueurs flous au fond, mêlées à la bande).
"""
import argparse
import glob
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8766


def bandeau(game_s, period_len=1200):
    """Temps de jeu écoulé -> chrono du bandeau, ex. 'P1 17:51' (le chrono décompte)."""
    p = int(game_s // period_len) + 1
    rem = max(0, period_len - (game_s - (p - 1) * period_len))
    return f"P{p} {int(rem // 60)}:{int(rem % 60):02d}"


def latest_session():
    ds = sorted(glob.glob(os.path.join(HERE, "annotations", "*", "*")), key=os.path.getmtime)
    ds = [d for d in ds if os.path.exists(os.path.join(d, "frames.json"))]
    return ds[-1] if ds else None


class Session:
    def __init__(self, folder):
        self.folder = folder
        with open(os.path.join(folder, "frames.json")) as f:
            self.frames = json.load(f)["frames"]
        with open(os.path.join(folder, "meta.json")) as f:
            self.meta = json.load(f)
        self.labels_path = os.path.join(folder, "labels.json")
        self.labels = {"explicit": {}, "extra": {}}      # explicit[file][tid] = {side,num,ref} ; extra[file] = [ {box,...} ]
        if os.path.exists(self.labels_path):
            with open(self.labels_path) as f:
                self.labels = json.load(f)

    def save(self, labels):
        self.labels = labels
        tmp = self.labels_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(labels, f, ensure_ascii=False)
        os.replace(tmp, self.labels_path)

    # -------------------------------------------------------------- propagation
    def resolved(self):
        """Identité de chaque (image, piste) : étiquette explicite, sinon la plus proche dans
        le temps pour la même piste (précédente d'abord). -> {file: {tid: (lab, explicite?)}}"""
        order = [fr["file"] for fr in self.frames]
        exp = self.labels.get("explicit", {})
        per_tid = {}
        for i, fr in enumerate(self.frames):
            for d in fr["dets"]:
                lab = exp.get(fr["file"], {}).get(str(d["tid"]))
                if lab:
                    per_tid.setdefault(d["tid"], []).append((i, lab))
        out = {}
        for i, fr in enumerate(self.frames):
            res = {}
            for d in fr["dets"]:
                t = d["tid"]
                lab = exp.get(fr["file"], {}).get(str(t))
                if lab:
                    res[t] = (lab, True)
                    continue
                cands = per_tid.get(t, [])
                prev = [c for c in cands if c[0] <= i]
                nxt = [c for c in cands if c[0] > i]
                pick = prev[-1] if prev else (nxt[0] if nxt else None)
                if pick:
                    res[t] = (pick[1], False)
            out[order[i]] = res
        return out

    # ------------------------------------------------------------------ export
    def export(self, log=lambda m: print(m, flush=True)):
        res = self.resolved()
        abbr = {}
        m = self.meta.get("match")
        if m:
            our = self.meta.get("our") or "home"
            them = "away" if our == "home" else "home"
            abbr = {"our": m["abbr"][our], "them": m["abbr"][them]}
        n_mem = n_num = n_team = n_det = 0
        # 1) mémoire du match
        mem_path = os.path.join(HERE, "matchs", self.meta.get("memory", "memoire_inconnu.json"))
        mem = {"version": 1, "frames": {}}
        if os.path.exists(mem_path):
            with open(mem_path) as f:
                mem = json.load(f)
        session = "annot_" + os.path.basename(self.folder)
        ts = round(time.time(), 1)
        no_tc = n_extra = 0
        for fr in self.frames:
            if not fr.get("key"):
                no_tc += 1
                continue
            W, H = fr["size"]
            entries = [e for e in mem["frames"].get(fr["key"], []) if e[7] != session]
            items = []
            for d in fr["dets"]:
                r = res[fr["file"]].get(d["tid"])
                if r:
                    items.append((d["box"], r[0]))
            for ex in self.labels.get("extra", {}).get(fr["file"], []):
                items.append((ex["box"], ex))
                n_extra += 1 if ex.get("num") and not ex.get("ref") else 0
            for box, lab in items:
                if lab.get("ref") or lab.get("off") or not lab.get("num"):
                    continue
                x1, y1, x2, y2 = box
                entries.append([lab["side"], lab["num"], round((x1 + x2) / 2 / W, 4), round(y2 / H, 4),
                                round((y2 - y1) / H, 4), "main", ts, session, -1])
                n_mem += 1
            mem["frames"][fr["key"]] = entries
        os.makedirs(os.path.dirname(mem_path), exist_ok=True)
        with open(mem_path, "w") as f:
            json.dump(mem, f, separators=(",", ":"))
        # 2) vignettes et 3) détection
        det_img = os.path.join(HERE, "dataset_detection", "images")
        det_lab = os.path.join(HERE, "dataset_detection", "labels")
        os.makedirs(det_img, exist_ok=True)
        os.makedirs(det_lab, exist_ok=True)
        tag = os.path.basename(os.path.dirname(self.folder)) + "_" + os.path.basename(self.folder)
        log(f"[export] mémoire du match : {n_mem} identités ; écriture des vignettes…")
        for k, fr in enumerate(self.frames):
            if k % 20 == 0:
                log(f"[export] image {k + 1}/{len(self.frames)}")
            img = cv2.imread(os.path.join(self.folder, fr["file"]))
            if img is None:
                continue
            H, W = img.shape[:2]
            boxes = []
            items = []
            for d in fr["dets"]:
                r = res[fr["file"]].get(d["tid"])
                items.append((d["box"], r[0] if r else None))
            for ex in self.labels.get("extra", {}).get(fr["file"], []):
                items.append((ex["box"], ex))
            for box, lab in items:
                x1, y1, x2, y2 = [int(v) for v in box]
                if lab is None:
                    continue                      # personne non annotée (public, doute) : ignorée
                if lab.get("off"):
                    continue
                boxes.append((x1, y1, x2, y2))
                crop = img[max(0, y1):y2, max(0, x1):x2]
                if crop.size == 0 or (y2 - y1) < 30:
                    continue
                if not lab.get("ref") and lab.get("side") not in ("our", "them"):
                    continue                      # cadre tracé sans nom : sert à la détection seulement
                team = "ARB" if lab.get("ref") else abbr.get(lab["side"], "EQUIPE_" + lab["side"])
                team = "".join(ch for ch in team if ch.isalnum() or ch in "-_") or "EQUIPE"
                name = f"{tag}_{fr['file'][:-4]}_{x1}_{y1}.jpg"
                dteam = os.path.join(HERE, "dataset_equipes", team)
                os.makedirs(dteam, exist_ok=True)
                cv2.imwrite(os.path.join(dteam, name), crop)
                n_team += 1
                if lab.get("num") and not lab.get("ref"):
                    dnum = os.path.join(HERE, "dataset_numeros", f"{team}_{lab['num']}")
                    os.makedirs(dnum, exist_ok=True)
                    cv2.imwrite(os.path.join(dnum, name), crop)
                    n_num += 1
            if boxes:
                base = f"{tag}_{fr['file'][:-4]}"
                cv2.imwrite(os.path.join(det_img, base + ".jpg"), img)
                with open(os.path.join(det_lab, base + ".txt"), "w") as f:
                    for x1, y1, x2, y2 in boxes:
                        f.write(f"0 {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} {(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}\n")
                n_det += 1
        msg = (f"Export : {n_mem} identités dans la mémoire du match (dont {n_extra} cadres tracés à la main), "
               f"{n_num} vignettes de numéros, {n_team} vignettes d'équipe, {n_det} images de détection")
        if no_tc:
            msg += f" - {no_tc} image(s) sans chrono lu : absentes de la mémoire"
        log(msg)
        return msg


def make_handler(sess):
    page = open(os.path.join(HERE, "annoter.html"), encoding="utf-8").read()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if self.path == "/api/data":
                data = dict(frames=sess.frames, meta=sess.meta, labels=sess.labels)
                return self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
            if self.path.startswith("/img/"):
                name = os.path.basename(self.path[5:])
                p = os.path.join(sess.folder, name)
                if os.path.exists(p):
                    return self._send(200, open(p, "rb").read(), "image/jpeg")
            return self._send(404, b"introuvable", "text/plain")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/labels":
                sess.save(body)
                return self._send(200, b'{"ok":true}', "application/json")
            if self.path == "/api/export":
                try:
                    sess.save(body)
                    msg = sess.export()
                    out = {"ok": True, "msg": msg}
                except Exception as ex:                  # l'erreur s'affiche sur la page (pas de blocage)
                    import traceback
                    traceback.print_exc()
                    out = {"ok": False, "msg": f"Erreur pendant l'export : {ex!r} (détail dans le terminal)"}
                return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json")
            return self._send(404, b"{}", "application/json")
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier", default=None, help="séance capturée (défaut : la plus récente)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--exporter", action="store_true", help="exporte sans ouvrir la page")
    args = ap.parse_args()
    folder = args.dossier or latest_session()
    if not folder:
        raise SystemExit("Aucune capture : lance d'abord  py live_engine.py --match <n°> --annoter 180")
    sess = Session(folder)
    print(f"[annoter] {len(sess.frames)} images dans {folder}")
    tcs = [fr["tc"][0] for fr in sess.frames if fr.get("tc")]
    if tcs:
        print(f"[annoter] extrait du match (chrono du bandeau) : de {bandeau(min(tcs))} à {bandeau(max(tcs))} "
              f"- c'est ce passage qu'il faut revoir pour retrouver tes noms")
    else:
        print("[annoter] pas de chrono lu pendant la capture : la mémoire du match ne pourra pas être recalée")
    if args.exporter:
        sess.export()
        return
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sess))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[annoter] ouvre {url}  (Ctrl+C pour quitter ; pense à « Exporter » avant)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
