"""Télécharge les données D2 depuis hockeyfrance.com (FFHG) -> data/raw/*.json
Usage: python3 scrape.py [--force]
Ne retélécharge pas les matchs terminés déjà présents."""
import html, json, os, re, sys, time, urllib.request

AJAX = "https://www.hockeyfrance.com/competitions/wp-admin/admin-ajax.php"
MATCH = "https://www.hockeyfrance.com/competitions/rencontre/%d/"
COMPETITION_ID, PHASE_ID = 242, 728          # Division 2, saison 2026-2027, saison régulière
RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
UA = {"User-Agent": "Mozilla/5.0 (stats-coach)"}

def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == 3: raise
            time.sleep(2 ** attempt * 2)

def match_data(mid):
    page = get(MATCH % mid)
    m = re.search(r'<live-rencontre-container[^>]*?\s:data="([^"]*)"', page, re.S)
    return json.loads(html.unescape(m.group(1))) if m else None

def main(force=False):
    os.makedirs(RAW, exist_ok=True)
    teams = json.loads(get(f"{AJAX}?action=get_equipes&phase_id={PHASE_ID}"))["data"]["data"]
    json.dump(teams, open(f"{RAW}/equipes.json", "w"), ensure_ascii=False)
    renc = json.loads(get(f"{AJAX}?action=get_rencontres&page=&equipe_id=&competition_id={COMPETITION_ID}"
                          f"&phase_id={PHASE_ID}&date_min=&date_max=&par_page=500&journee=&limite=0"))["data"]["data"]
    json.dump(renc, open(f"{RAW}/rencontres.json", "w"), ensure_ascii=False)
    done = [m for m in renc if m["etat"] == "T"]
    print(f"{len(renc)} matchs au calendrier, {len(done)} terminés")
    for m in done:
        path = f"{RAW}/match_{m['id']}.json"
        if os.path.exists(path) and not force: continue
        d = match_data(m["id"])
        if d is None:
            print("  ! pas de données pour", m["id"], m["rencontre_libelle"]); continue
        json.dump(d, open(path, "w"), ensure_ascii=False)
        print("  +", m["id"], f"J{m['tour']}", m["rencontre_libelle"], len(d["evenements"]), "événements")
        time.sleep(1)

if __name__ == "__main__":
    main("--force" in sys.argv)
