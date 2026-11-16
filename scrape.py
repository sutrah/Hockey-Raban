"""Télécharge les données D2 depuis hockeyfrance.com (FFHG) -> data/raw/
Usage: python3 scrape.py [--force]
Les matchs terminés déjà présents ne sont pas retéléchargés : après une journée, seuls les nouveaux matchs sont lus."""
import html, json, os, re, sys, time, urllib.request

AJAX = "https://www.hockeyfrance.com/competitions/wp-admin/admin-ajax.php"
MATCH = "https://www.hockeyfrance.com/competitions/rencontre/%d/"
TYPE_D2 = 3
SAISONS = [2025, 2026, 2027]          # 2027 = saison 2026-2027
RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
UA = {"User-Agent": "Mozilla/5.0 (stats-coach)"}
INUTILE = {"structure", "logo", "photo_url", "couleur", "created_at", "updated_at", "id_compet", "types_evenements", "parametrage"}

def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == 3: raise
            time.sleep(2 ** attempt * 2)

def api(action, **params):
    time.sleep(1)
    return json.loads(get(f"{AJAX}?action={action}&" + "&".join(f"{k}={v}" for k, v in params.items())))["data"]["data"]

def alleger(o):
    """Retire les champs répétés et inutiles (logos, photos, structures) pour garder des fichiers légers."""
    if isinstance(o, list): return [alleger(x) for x in o]
    if isinstance(o, dict):
        return {k: alleger(v) for k, v in o.items()
                if k not in INUTILE and not (k == "personne" and "nom_complet" in o)}
    return o

def match_data(mid):
    page = get(MATCH % mid)
    m = re.search(r'<live-rencontre-container[^>]*?\s:data="([^"]*)"', page, re.S)
    return alleger(json.loads(html.unescape(m.group(1)))) if m else None

def main(force=False):
    os.makedirs(RAW, exist_ok=True)
    phases = []
    for c in api("get_competition_by_type", id_type=TYPE_D2):
        if c["saison"] not in SAISONS: continue
        for p in api("get_phases", competition_id=c["id"]):
            phases.append({"saison": c["saison"], "competition_id": c["id"], "phase_id": p["id"], "libelle": p["libelle"], "type": p["type_phase"]})
    json.dump(phases, open(f"{RAW}/phases.json", "w"), ensure_ascii=False, indent=1)
    for p in phases:
        renc = alleger(api("get_rencontres", page="", equipe_id="", competition_id=p["competition_id"], phase_id=p["phase_id"],
                           date_min="", date_max="", par_page=500, journee="", limite=0))
        json.dump(renc, open(f"{RAW}/rencontres_{p['phase_id']}.json", "w"), ensure_ascii=False)
        done = [m for m in renc if m["etat"] == "T"]
        print(f"{p['saison'] - 1}-{p['saison']} {p['libelle']} : {len(renc)} matchs, {len(done)} terminés", flush=True)
        for m in done:
            path = f"{RAW}/match_{m['id']}.json"
            if os.path.exists(path) and not force: continue
            d = match_data(m["id"])
            if d is None:
                print("  ! pas de données pour", m["id"], m["rencontre_libelle"], flush=True); continue
            json.dump(d, open(path, "w"), ensure_ascii=False)
            print("  +", m["id"], m["rencontre_libelle"], len(d["evenements"]), "événements", flush=True)
            time.sleep(1.2)

if __name__ == "__main__":
    main("--force" in sys.argv)
