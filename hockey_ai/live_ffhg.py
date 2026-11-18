"""Feuille de match FFHG : trouve le match du jour, charge les équipes et suit les événements.

Les données viennent de la page publique du match sur hockeyfrance.com (même source que
scrape.py) : joueurs avec numéros et postes, buts, pénalités, chrono officiel.
"""
import datetime as dt
import html
import json
import re
import threading
import time
import unicodedata
import urllib.request

AJAX = "https://www.hockeyfrance.com/competitions/wp-admin/admin-ajax.php"
MATCH = "https://www.hockeyfrance.com/competitions/rencontre/%d/"
TYPE_D2 = 3
UA = {"User-Agent": "Mozilla/5.0 (stats-coach)"}


def _get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _api(action, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return json.loads(_get(f"{AJAX}?action={action}&{q}"))["data"]["data"]


def norm(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def fetch_match(mid):
    page = _get(MATCH % mid)
    m = re.search(r'<live-rencontre-container[^>]*?\s:data="([^"]*)"', page, re.S)
    return json.loads(html.unescape(m.group(1))) if m else None


def find_today_matches(team="BREST", day=None, type_id=TYPE_D2, log=print):
    """Matchs de l'équipe ce jour-là : [(id, libellé, date)]."""
    day = day or dt.date.today()
    season = day.year + 1 if day.month >= 7 else day.year
    out = []
    for c in _api("get_competition_by_type", id_type=type_id):
        if c["saison"] != season:
            continue
        for p in _api("get_phases", competition_id=c["id"]):
            renc = _api("get_rencontres", page="", equipe_id="", competition_id=c["id"], phase_id=p["id"],
                        date_min="", date_max="", par_page=500, journee="", limite=0)
            for r in renc:
                if norm(team) in norm(r["rencontre_libelle"]) and str(r.get("date_rencontre", "")).startswith(day.isoformat()):
                    out.append((r["id"], r["rencontre_libelle"], r["date_rencontre"]))
    return out


class Match:
    """Vue simplifiée d'une feuille de match."""

    def __init__(self, data, our_team="BREST"):
        self.update(data, our_team)

    def update(self, d, our_team=None):
        self.raw = d
        if our_team:
            self.our_team = our_team
        self.home = d["receveur"]
        self.away = d["visiteur"]
        self.ids = {"home": self.home["id"], "away": self.away["id"]}
        t = norm(self.our_team)
        self.our = "home" if t in norm(self.home.get("libelle_court", "") + self.home.get("libelle_complet", "")) else "away"
        self.them = "away" if self.our == "home" else "home"
        self.abbr = {"home": self.home.get("abreviation", ""), "away": self.away.get("abreviation", "")}
        self.short = {"home": self.home.get("libelle_court", ""), "away": self.away.get("libelle_court", "")}
        self.roster = {"home": {}, "away": {}}
        for j in d.get("joueurs", []):
            side = "home" if j["equipe_id"] == self.ids["home"] else "away"
            num = str(j.get("numero") or "").strip()
            if not num:
                continue
            nom = j.get("nom_complet", "")
            parts = nom.split()
            short = parts[0].capitalize() if parts else nom
            if len(parts) > 1:              # « Motreff D. » : deux frères peuvent jouer ensemble
                short += f" {parts[1][0].upper()}."
            self.roster[side][num] = dict(
                num=num, name=nom, short=short,
                pos=(j.get("position") or {}).get("code", ""))
        self.rink_name = d.get("patinoire", "") or ""
        self.events = d.get("evenements", [])
        self.score = {"home": 0, "away": 0}
        for s in d.get("scores", []) or []:
            side = "home" if s.get("equipe_id") == self.ids["home"] else "away"
            self.score[side] = s.get("score", 0)
        self.chrono = d.get("chronometre") or {}
        self.period_len = (d.get("periodes") or {}).get("duree", 20) * 60

    def goalies(self, side):
        return {n for n, p in self.roster[side].items() if p["pos"] == "GB"}

    def penalties_active(self, game_s):
        """Pénalités en cours au temps de jeu game_s (secondes écoulées depuis le début du match)."""
        out = []
        for e in self.events:
            if e.get("type") != "SANCTION":
                continue
            a, b = e.get("temps_debut_sanction"), e.get("temps_fin_sanction")
            if a is None or b is None or not (a <= game_s < b):
                continue
            side = "home" if (e.get("equipe") or {}).get("id") == self.ids["home"] else "away"
            j = e.get("joueur") or e.get("substitution") or {}
            out.append(dict(side=side, num=str(j.get("numero", "")), remaining=int(b - game_s),
                            label=(e.get("sanction") or {}).get("libelle_court", "")))
        return out

    def summary(self):
        return dict(home=self.short["home"], away=self.short["away"], abbr=self.abbr, our=self.our,
                    score=self.score, rink=self.rink_name,
                    roster={s: sorted(self.roster[s].values(), key=lambda p: int(p["num"]) if p["num"].isdigit() else 999)
                            for s in ("home", "away")})


class FFHGFeed(threading.Thread):
    """Relit la feuille de match toutes les `every` secondes pendant le match."""

    def __init__(self, match_id, our_team="BREST", every=25, log=print):
        super().__init__(daemon=True)
        self.mid, self.every, self.log = match_id, every, log
        d = fetch_match(match_id)
        if d is None:
            raise RuntimeError(f"feuille du match {match_id} introuvable")
        self.match = Match(d, our_team)
        self.lock = threading.Lock()
        self.stop_flag = False
        self.last_ok = time.time()

    def run(self):
        while not self.stop_flag:
            time.sleep(self.every)
            try:
                d = fetch_match(self.mid)
                if d:
                    with self.lock:
                        self.match.update(d)
                    self.last_ok = time.time()
            except Exception as e:      # réseau coupé : on garde la dernière version
                self.log(f"[ffhg] lecture impossible ({e}), nouvel essai plus tard")


class OfflineFeed:
    """Feuille de match lue depuis un fichier (ex. data/raw/match_84389.json) : tests sur replay."""

    def __init__(self, path, our_team="BREST"):
        with open(path) as f:
            self.match = Match(json.load(f), our_team)
        self.lock = threading.Lock()

    def start(self):
        pass


def pick_match(team, abbr_seen=None, log=print):
    """Choisit le match du jour ; s'il y en a plusieurs, utilise les abréviations lues sur le bandeau TV."""
    ms = find_today_matches(team, log=log)
    if not ms:
        return None
    if len(ms) == 1 or not abbr_seen:
        return ms[0][0]
    for mid, lib, _ in ms:
        d = fetch_match(mid)
        if d and {d["receveur"].get("abreviation"), d["visiteur"].get("abreviation")} >= set(abbr_seen):
            return mid
    return ms[0][0]
