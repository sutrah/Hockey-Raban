"""Construit docs/data/d2.sqlite à partir de data/raw/*.json (schéma portable vers Postgres/MySQL)."""
import glob, json, os, re, sqlite3
HERE = os.path.dirname(os.path.abspath(__file__))
RAW, DB = f"{HERE}/data/raw", f"{HERE}/docs/data/d2.sqlite"

SCHEMA = """
CREATE TABLE equipes(id INTEGER PRIMARY KEY, abreviation TEXT, nom_court TEXT, nom TEXT,
  club TEXT);          -- code fédéral du club : un même club change parfois d'identifiant d'équipe d'une saison à l'autre
CREATE TABLE joueurs(id INTEGER PRIMARY KEY, nom TEXT, licence TEXT);
CREATE TABLE matchs(id INTEGER PRIMARY KEY,
  saison INTEGER,      -- 2027 = saison 2026-2027
  phase TEXT,          -- P = saison régulière, T = séries pour le titre, C = séries pour le maintien
  tour TEXT,           -- libellé du tour en séries (Quarts de finale...)
  journee INTEGER, date TEXT, etat TEXT,
  dom_id INTEGER, ext_id INTEGER, buts_dom INTEGER, buts_ext INTEGER,
  fin TEXT,            -- S = temps réglementaire, PRL = prolongation, TAB = tirs au but
  patinoire TEXT, spectateurs INTEGER,
  detail_tirs INTEGER  -- 1 si tirs + engagements saisis sur ce match
);
CREATE TABLE compositions(match_id INTEGER, joueur_id INTEGER, equipe_id INTEGER, numero TEXT, poste TEXT,
  PRIMARY KEY(match_id, joueur_id));
CREATE TABLE buts(id TEXT PRIMARY KEY, match_id INTEGER, temps INTEGER, periode INTEGER, equipe_id INTEGER,
  buteur_id INTEGER, assist1_id INTEGER, assist2_id INTEGER, gardien_id INTEGER,
  situation TEXT,      -- EG, SUP1, SUP2, INF1...
  cage_vide INTEGER, score_pour INTEGER, score_contre INTEGER);  -- score après le but
CREATE TABLE buts_sur_glace(but_id TEXT, joueur_id INTEGER, equipe_id INTEGER, signe INTEGER); -- +1 / -1
CREATE TABLE penalites(id TEXT PRIMARY KEY, match_id INTEGER, temps INTEGER, periode INTEGER, equipe_id INTEGER,
  joueur_id INTEGER, minutes INTEGER, code TEXT, libelle TEXT, debut INTEGER, fin INTEGER);
CREATE TABLE tirs(id TEXT PRIMARY KEY, match_id INTEGER, temps INTEGER, periode INTEGER, equipe_id INTEGER,
  tireur_id INTEGER, gardien_id INTEGER);          -- tirs cadrés arrêtés (hors buts)
CREATE TABLE engagements(id TEXT PRIMARY KEY, match_id INTEGER, temps INTEGER, periode INTEGER,
  joueur_dom_id INTEGER, joueur_ext_id INTEGER, gagnant_id INTEGER, equipe_gagnante_id INTEGER);
CREATE TABLE gardiens_presence(match_id INTEGER, joueur_id INTEGER, equipe_id INTEGER, entree INTEGER, sortie INTEGER);
CREATE TABLE temps_morts(match_id INTEGER, temps INTEGER, equipe_id INTEGER);
CREATE TABLE staff(match_id INTEGER, equipe_id INTEGER, nom TEXT, role TEXT);
"""

def periode(t, duree=1200):
    return min((max(t - 1, 0)) // duree + 1, 4) if t else 1

def main():
    if os.path.exists(DB): os.remove(DB)
    db = sqlite3.connect(DB); db.executescript(SCHEMA)
    def pid(p):   # identifiant stable de la personne
        if not p: return None
        db.execute("INSERT OR IGNORE INTO joueurs VALUES(?,?,?)", (p["id"], p["nom_complet"], p.get("code_adherent")))
        return p["id"]
    clubs = {}
    for ph in sorted(json.load(open(f"{RAW}/phases.json")), key=lambda p: p["saison"]):
        fichier = f"{RAW}/rencontres_{ph['phase_id']}.json"
        if not os.path.exists(fichier): continue      # phase pas encore téléchargée
        for m in json.load(open(fichier)):
            for t in (m["receveur"], m["visiteur"]):
                club = re.match(r"\s*(\d+)", t["libelle"]).group(1)
                clubs[club] = t["id"]                    # l'identifiant le plus récent du club l'emporte
                db.execute("INSERT OR REPLACE INTO equipes VALUES(?,?,?,?,?)", (t["id"], t["abreviation"], t["libelle_court"], t["libelle_complet"], club))
            sc = {s["equipe_id"]: s["score"] for s in m["score"]}
            dom, ext = m["receveur"]["id"], m["visiteur"]["id"]
            played = m["etat"] == "T"
            db.execute("INSERT OR REPLACE INTO matchs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (m["id"], ph["saison"], ph["type"], m.get("tour_libelle"),
                m["tour"], m["date_rencontre"], m["etat"],
                dom, ext, sc.get(dom) if played else None, sc.get(ext) if played else None,
                m["victoire_par"] if played else None, (m.get("lieu_de_pratique") or {}).get("nom"), None, 0))
    for f in sorted(glob.glob(f"{RAW}/match_*.json")):
        d = json.load(open(f)); mid = int(f.split("_")[-1][:-5])
        if not db.execute("SELECT 1 FROM matchs WHERE id=?", (mid,)).fetchone(): continue
        dom, ext = d["receveur"]["id"], d["visiteur"]["id"]
        part = {}
        for j in d["joueurs"]:
            part[j["participant_id"]] = (pid(j), j["equipe_id"])
            db.execute("INSERT OR IGNORE INTO compositions VALUES(?,?,?,?,?)",
                       (mid, j["id"], j["equipe_id"], j["numero"], (j.get("position") or {}).get("code")))
        score = {dom: 0, ext: 0}; ntir = 0
        for e in sorted(d["evenements"], key=lambda e: (e["temps"], e["id"])):
            t, typ, eq = e["temps"], e["type"], (e.get("equipe") or {}).get("id")
            if typ == "BUT":
                score[eq] += 1; adv = ext if eq == dom else dom
                a = [pid(x) for x in e.get("assistants") or []] + [None, None]
                cv = 1 if (e.get("surnombre_details") or {}).get("code") == "CV" else 0
                db.execute("INSERT INTO buts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (e["id"], mid, t, periode(t), eq, pid(e.get("buteur")),
                    a[0], a[1], pid(e.get("gardien")), (e.get("surnombre") or {}).get("code"), cv, score[eq], score[adv]))
                for sign, key in ((1, "joueurs_plus_ids"), (-1, "joueurs_moins_ids")):
                    for p in e.get(key) or []:
                        if p in part: db.execute("INSERT INTO buts_sur_glace VALUES(?,?,?,?)", (e["id"], part[p][0], part[p][1], sign))
            elif typ == "SANCTION":
                s = e.get("sanction") or {}
                db.execute("INSERT INTO penalites VALUES(?,?,?,?,?,?,?,?,?,?,?)", (e["id"], mid, t, periode(t), eq,
                    pid(e.get("joueur") or e.get("substitution")), e.get("temps_penalite"), s.get("code"), s.get("libelle"),
                    e.get("temps_debut_sanction"), e.get("temps_fin_sanction")))
            elif typ == "TIR":
                ntir += 1
                db.execute("INSERT INTO tirs VALUES(?,?,?,?,?,?,?)", (e["id"], mid, t, periode(t), eq, pid(e.get("tireur")), pid(e.get("gardien"))))
            elif typ == "ENGA":
                db.execute("INSERT INTO engagements VALUES(?,?,?,?,?,?,?,?)", (e["id"], mid, t, periode(t + 1),
                    pid(e.get("receveur")), pid(e.get("visiteur")), pid(e.get("gagnant")), e.get("equipe_gagante_id")))
            elif typ == "TEMPS_MORT":
                db.execute("INSERT INTO temps_morts VALUES(?,?,?)", (mid, t, eq))
        for mv in d.get("mouvements") or []:
            db.execute("INSERT INTO gardiens_presence VALUES(?,?,?,?,?)", (mid, pid(mv["personne"]), mv["equipe"]["id"],
                       mv.get("temps_rentree_secondes"), mv.get("temps_sortie_secondes")))
        for s in d.get("staffs") or []:
            db.execute("INSERT INTO staff VALUES(?,?,?,?)", (mid, s["equipe_id"], f'{s["nom"]} {s["prenom"]}', (s.get("attributs") or {}).get("libelle")))
        db.execute("UPDATE matchs SET spectateurs=?, detail_tirs=? WHERE id=?", (d.get("nombre_spectateurs"), 1 if ntir > 10 else 0, mid))
    # un seul identifiant par club sur toutes les saisons (Reims, Évry-Viry, Français Volants... en ont changé)
    for old, club in db.execute("SELECT id, club FROM equipes").fetchall():
        new = clubs[club]
        if new == old: continue
        for tbl, col in (("matchs", "dom_id"), ("matchs", "ext_id"), ("compositions", "equipe_id"), ("buts", "equipe_id"), ("buts_sur_glace", "equipe_id"),
                         ("penalites", "equipe_id"), ("tirs", "equipe_id"), ("engagements", "equipe_gagnante_id"), ("gardiens_presence", "equipe_id"),
                         ("temps_morts", "equipe_id"), ("staff", "equipe_id")):
            db.execute(f"UPDATE {tbl} SET {col}=? WHERE {col}=?", (new, old))
        db.execute("DELETE FROM equipes WHERE id=?", (old,))
    db.commit()
    for t in ("equipes","joueurs","matchs","compositions","buts","buts_sur_glace","penalites","tirs","engagements","gardiens_presence","staff"):
        print(f"{t:20}", db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])

if __name__ == "__main__": main()
