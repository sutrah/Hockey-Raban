#!/usr/bin/env python3
"""Prépare l'enregistrement d'un passage pour l'envoyer (site GitHub : 25 Mo max par fichier).

  py preparer_video.py                      (dernière vidéo de Vidéos\\Captures)
  py preparer_video.py "C:\\chemin\\video.mp4"

Crée le dossier  a_envoyer\\  avec :
  - partie1.mp4, partie2.mp4, ... : la vidéo en 720p, sans le son, en morceaux de moins de 24 Mo ;
  - annotations.zip : ta dernière séance d'annotation (images + noms) ;
  - memoire_<match>.json : la mémoire du match.
Il ne reste qu'à glisser le contenu de  a_envoyer\\  sur la page GitHub (Add file > Upload files).
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "a_envoyer")
MAX_MB = 24.0


def derniere_capture():
    home = os.path.expanduser("~")
    pats = [os.path.join(home, d, "Captures", "*.mp4") for d in ("Videos", "Vidéos")]
    pats += [os.path.join(home, "OneDrive", d, "Captures", "*.mp4") for d in ("Videos", "Vidéos")]
    vids = [v for p in pats for v in glob.glob(p)]
    return max(vids, key=os.path.getmtime) if vids else None


def mo(p):
    return os.path.getsize(p) / 1e6


def avec_ffmpeg(src):
    """H.264 (petits fichiers, bonne qualité) via imageio-ffmpeg, installé au besoin."""
    try:
        import imageio_ffmpeg
    except ImportError:
        print("[vidéo] installation de l'outil de compression (une seule fois)…")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"])
        if r.returncode != 0:
            return False
        import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    for seg in (50, 30, 15):                       # morceaux plus courts si trop lourds
        for f in glob.glob(os.path.join(OUT, "partie*.mp4")):
            os.remove(f)
        print(f"[vidéo] compression en 720p, morceaux de {seg} s…")
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
               "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
               "-pix_fmt", "yuv420p", "-an", "-force_key_frames", f"expr:gte(t,n_forced*{seg})",
               "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
               "-segment_start_number", "1", os.path.join(OUT, "partie%d.mp4")]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            return False
        parts = glob.glob(os.path.join(OUT, "partie*.mp4"))
        if parts and max(mo(p) for p in parts) <= MAX_MB:
            return True
    return True


def avec_opencv(src):
    """Secours sans ffmpeg : OpenCV, nouveau morceau dès que le fichier approche 24 Mo."""
    import cv2
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"Impossible d'ouvrir la vidéo : {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    part, wr, path, k = 0, None, None, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        if h > 720:
            fr = cv2.resize(fr, (int(round(w * 720 / h / 2)) * 2, 720), interpolation=cv2.INTER_AREA)
        if wr is None or (k % 25 == 0 and mo(path) > MAX_MB - 2):
            if wr is not None:
                wr.release()
            part += 1
            path = os.path.join(OUT, f"partie{part}.mp4")
            wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (fr.shape[1], fr.shape[0]))
        wr.write(fr)
        k += 1
        if k % 300 == 0:
            print(f"[vidéo] {k}/{n} images")
    if wr is not None:
        wr.release()
    return part > 0


def annotations():
    ds = sorted(glob.glob(os.path.join(HERE, "annotations", "*", "*", "frames.json")), key=os.path.getmtime)
    if not ds:
        print("[annotations] aucune séance d'annotation trouvée")
        return
    folder = os.path.dirname(ds[-1])
    z = os.path.join(OUT, "annotations.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for p in glob.glob(os.path.join(folder, "*")):
            if os.path.isfile(p) and not p.endswith(".tmp"):
                f.write(p, os.path.join(os.path.basename(folder), os.path.basename(p)))
    print(f"[annotations] {folder} -> annotations.zip ({mo(z):.1f} Mo)")
    meta = json.load(open(os.path.join(folder, "meta.json")))
    mem = os.path.join(HERE, "matchs", meta.get("memory", ""))
    if meta.get("memory") and os.path.exists(mem):
        shutil.copy(mem, OUT)
        print(f"[mémoire] {os.path.basename(mem)} copié")
    if mo(z) > MAX_MB:
        print("[annotations] attention : annotations.zip dépasse 24 Mo, préviens-moi")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else derniere_capture()
    if not src or not os.path.exists(src):
        raise SystemExit("Vidéo introuvable. Donne son chemin :  py preparer_video.py \"C:\\...\\ma_video.mp4\"")
    print(f"[vidéo] {src} ({mo(src):.0f} Mo)")
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "*")):
        os.remove(f)
    if not avec_ffmpeg(src):
        print("[vidéo] compression H.264 indisponible, méthode de secours (fichiers plus nombreux)")
        avec_opencv(src)
    annotations()
    print("\nÀ envoyer (contenu du dossier a_envoyer) :")
    for p in sorted(glob.glob(os.path.join(OUT, "*"))):
        flag = "  <- TROP LOURD" if mo(p) > 25 else ""
        print(f"  {os.path.basename(p):28s} {mo(p):6.1f} Mo{flag}")
    print(f"\nDossier : {OUT}")
    if os.name == "nt":
        os.startfile(OUT)


if __name__ == "__main__":
    main()
