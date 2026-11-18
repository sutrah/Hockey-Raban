#!/usr/bin/env python3
"""Entraîne la reconnaissance des joueurs à partir des vignettes collectées (option --collecte).

  py train_numeros.py                 (toutes les équipes présentes dans dataset_numeros/)
  py train_numeros.py --equipe BRE    (une seule équipe)

Les vignettes sont celles des joueurs nommés à la main par le coach (étiquettes sûres) :
dataset_numeros/BRE_91/*.jpg, dataset_numeros/BRE_22/*.jpg, ...
Le modèle apprend à reconnaître le JOUEUR entier (numéro, casque, silhouette), pas seulement
les chiffres. Il est enregistré dans modeles/joueurs_<EQUIPE>.pt et utilisé tout seul par
live_engine.py au lancement suivant.

Conseils : au moins 15 vignettes par joueur (idéalement 40+, sur plusieurs matchs) ;
supprime les vignettes fausses avant d'entraîner. Avec une RTX : 1 à 3 minutes.
"""
import argparse
import glob
import os
import random

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = (96, 192)            # largeur, hauteur des vignettes données au modèle


def load_images(folder):
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*.jpg"))):
        im = cv2.imread(p)
        if im is not None and im.shape[0] >= 24 and im.shape[1] >= 12:
            out.append(im)
    return out


def to_tensor(im, torch, train=False):
    im = cv2.resize(im, SIZE, interpolation=cv2.INTER_AREA)
    if train:                                   # augmentations (pas de miroir : le numéro s'inverserait)
        if random.random() < 0.8:
            a = random.uniform(0.7, 1.3)
            b = random.uniform(-25, 25)
            im = np.clip(im.astype(np.float32) * a + b, 0, 255).astype(np.uint8)
        if random.random() < 0.5:
            k = random.choice([3, 5])
            im = cv2.GaussianBlur(im, (k, k), 0)
        if random.random() < 0.5:               # léger recadrage / décalage
            h, w = im.shape[:2]
            dx, dy = random.randint(-6, 6), random.randint(-10, 10)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            im = cv2.warpAffine(im, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
        if random.random() < 0.3:               # quadrillage façon filet de protection
            step = random.randint(5, 9)
            im = im.copy()
            im[::step, :] = (im[::step, :] * 0.5).astype(np.uint8)
            im[:, ::step] = (im[:, ::step] * 0.5).astype(np.uint8)
    x = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    return torch.from_numpy(x.transpose(2, 0, 1).astype(np.float32))


def build_model(n_classes, torch, pretrained=True):
    import torchvision
    try:
        w = torchvision.models.ResNet18_Weights.DEFAULT if pretrained else None
        m = torchvision.models.resnet18(weights=w)
    except Exception:
        m = torchvision.models.resnet18(weights=None)
    m.fc = torch.nn.Linear(m.fc.in_features, n_classes)
    return m


def train_team(team, folders, epochs, device, log=print):
    import torch
    classes, data = [], []
    for f in folders:
        num = os.path.basename(f).split("_", 1)[1]
        ims = load_images(f)
        if len(ims) < 8:
            log(f"  {team} #{num} : seulement {len(ims)} vignettes, ignoré (8 minimum)")
            continue
        classes.append(num)
        data += [(im, len(classes) - 1) for im in ims]
    if len(classes) < 2:
        log(f"[{team}] pas assez de joueurs avec des vignettes (2 minimum) : rien à entraîner")
        return None
    random.shuffle(data)
    n_val = max(len(classes), len(data) // 6)
    val, tr = data[:n_val], data[n_val:]
    log(f"[{team}] {len(classes)} joueurs, {len(tr)} vignettes d'entraînement, {len(val)} de contrôle")
    model = build_model(len(classes), torch).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    lossf = torch.nn.CrossEntropyLoss(label_smoothing=0.1)
    best, best_state = -1.0, None
    for ep in range(epochs):
        model.train()
        random.shuffle(tr)
        for i in range(0, len(tr), 32):
            batch = tr[i:i + 32]
            x = torch.stack([to_tensor(im, torch, True) for im, _ in batch]).to(device)
            y = torch.tensor([c for _, c in batch]).to(device)
            opt.zero_grad()
            loss = lossf(model(x), y)
            loss.backward()
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            x = torch.stack([to_tensor(im, torch) for im, _ in val]).to(device)
            pred = model(x).argmax(1).cpu().numpy()
        acc = float((pred == np.array([c for _, c in val])).mean())
        if acc >= best:
            best, best_state = acc, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if ep % 3 == 0 or ep == epochs - 1:
            log(f"  époque {ep + 1}/{epochs} · justesse sur les vignettes de contrôle : {100 * acc:.0f} %")
    os.makedirs(os.path.join(HERE, "modeles"), exist_ok=True)
    path = os.path.join(HERE, "modeles", f"joueurs_{team}.pt")
    torch.save(dict(classes=classes, state=best_state, size=SIZE, val_acc=best), path)
    log(f"[{team}] modèle enregistré : {path} (justesse {100 * best:.0f} %)")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--equipe", default=None, help="abréviation (ex. BRE) ; défaut : toutes")
    ap.add_argument("--epoques", type=int, default=15)
    args = ap.parse_args()
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    root = os.path.join(HERE, "dataset_numeros")
    folders = [f for f in glob.glob(os.path.join(root, "*_*")) if os.path.isdir(f)]
    teams = sorted({os.path.basename(f).split("_", 1)[0] for f in folders})
    if args.equipe:
        teams = [t for t in teams if t == args.equipe.upper()]
    if not teams:
        raise SystemExit("Aucune vignette dans dataset_numeros/ : lance live_engine.py avec --collecte et "
                         "identifie des joueurs d'un clic.")
    print(f"[ok] calcul sur {device}")
    for t in teams:
        train_team(t, [f for f in folders if os.path.basename(f).startswith(t + "_")], args.epoques, device)


if __name__ == "__main__":
    main()
