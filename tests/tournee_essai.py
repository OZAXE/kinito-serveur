"""
Essai du moteur de La Tournee, hors ligne : des centaines de parties jouees
au hasard (des, scores valides et absurdes, absents, delais depasses), de 2
a 8 joueurs. On verifie qu aucune ne plante ni ne se fige, que les scores ne
fuient pas avant les resultats, et que les regles tiennent (deplacements,
capsule d or, cases, gains du mini-jeu, fin).
Usage : python tests/tournee_essai.py [nombre de parties]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tournee as T

TYPES = {"depart", "pieces", "piege", "evenement", "tournee", "chance", "duel", "trappe"}


def cases(etat):
    return T.TR_PLATEAUX[etat["plateau"]]["cases"]


def verifier_config():
    assert set(T.TR_PLATEAUX) == {"terrasse", "pub", "cave"} and T.TR_PLATEAU_DEFAUT in T.TR_PLATEAUX
    for p in T.TR_PLATEAUX.values():
        assert p["cases"][0]["type"] == "depart"
        assert {c["type"] for c in p["cases"]} <= TYPES
        for e in p["evenements"]:
            assert e in T._EVENEMENTS, e
        for e in p.get("chance", []):
            assert e in T._CHANCE, e
        for e in p["evenements_plateau"]["liste"]:
            assert e in T._EV_PLATEAU, e
        assert "—" not in p["description"] + p["nom"]
    # La difficulte : on boit de plus en plus, et les retournements arrivent au Pub et a la Cave.
    b = [T.TR_PLATEAUX[x]["boire"] for x in ("terrasse", "pub", "cave")]
    for cle in ("piege", "minijeu_dernier", "fin_dernier"):
        assert b[0][cle] <= b[1][cle] <= b[2][cle], cle
    assert "vol_capsule" in T.TR_PLATEAUX["cave"]["evenements"] and T.TR_PLATEAUX["cave"]["duel_capsule"]
    assert not any("vol" in T._EVENEMENTS[e] or "vol_capsule" in T._EVENEMENTS[e] for e in T.TR_PLATEAUX["terrasse"]["evenements"])
    for e in list(T.TR_CONFIG["chance"]) + list(T.TR_CONFIG["evenements_plateau"]):
        assert "—" not in e["texte"] and "cul sec" not in e["texte"].lower(), e
    ids = [m["id"] for m in T.TR_MINIJEUX]
    assert len(ids) == len(set(ids)) >= 3
    for m in T.TR_MINIJEUX:
        assert m["sens"] in ("grand", "petit") and m["max"] > 0 and 5 <= m["duree"] <= 60, m
        assert "—" not in m["regle"] + m["nom"]
    for e in T.TR_CONFIG["evenements"]:
        assert "—" not in e["texte"] and "cul sec" not in e["texte"].lower(), e


def verifier(etat):
    pub = T.tr_etat_public(etat, 0)
    cs = cases(etat)
    n = len(cs)
    assert all(0 <= p < n for p in etat["positions"])
    assert all(p >= 0 for p in etat["pieces"]) and all(g >= 0 for g in etat["gorgees"])
    assert all(c >= 0 for c in etat["capsules"])
    assert 0 <= etat["capsule_case"] < n and cs[etat["capsule_case"]]["type"] not in ("depart", "trappe")
    assert len(pub["plateau"]) == n and pub["carte"]["id"] == etat["plateau"]
    if etat["videur"] is not None:
        assert 0 < etat["videur"] < n
    if etat["phase"] == "minijeu":
        assert pub["resultat_minijeu"] is None and "scores" not in pub, "les scores fuient"
        assert pub["minijeu"] and pub["minijeu"]["graine"]
    if etat["phase"] == "fin":
        assert etat["echeance"] is None and etat["final"]
    else:
        assert etat["echeance"] is not None


def verifier_deplacements(etat, avant):
    """Rejoue les deplacements a partir de l etat d avant et compare les positions."""
    res = etat["deplacements"]
    des = [l["de"] for l in res["lignes"]]
    assert des == sorted(des, reverse=True), "ordre des des"
    cs = cases(etat)
    bouge = False
    for l in res["lignes"]:
        assert 1 <= l["de"] <= 6 and len(l["chemin"]) == l["de"]
        prec = l["depart"]
        for c in l["chemin"]:
            assert c in cs[prec]["suivants"], "chemin hors du plateau"
            prec = c
        o = avant["options"].get(str(l["joueur"]))
        deplace = not any(l["chemin"][0] == x["chemin"][0] for x in (o or []))
        if o and not deplace:
            k = etat["choix"][str(l["joueur"])]
            assert l["chemin"] == o[k]["chemin"], "le chemin choisi n est pas suivi"
            if str(l["joueur"]) in avant["choix"]:
                assert k == avant["choix"][str(l["joueur"])]
        if any("vers" in e or "avec" in e for e in l["effets"]):
            bouge = True
    if not bouge:
        for l in res["lignes"]:
            assert l["arrivee"] == l["chemin"][-1] or any(e["type"] == "videur" for e in l["effets"])
    achats = sum(1 for l in res["lignes"] for e in l["effets"] if e["type"] == "capsule")
    assert sum(etat["capsules"]) - sum(avant["capsules"]) == achats


def verifier_minijeu(etat):
    r = etat["resultat_minijeu"]
    grand = r["sens"] == "grand"
    if r["equipes"]:
        a, b = r["equipes"]
        assert a and b and not set(a) & set(b)
        g = r["bilan_equipes"]["gagnante"]
        for l in r["lignes"]:
            if g is None:
                assert l["pieces"] == T._M["egalite_equipes"] and l["boit"] == 0
            elif l["equipe"] == g:
                assert l["pieces"] == T._M["cagnotte_equipe"] // len(r["equipes"][g]) and l["boit"] == 0
            else:
                assert l["pieces"] == 0 and l["boit"] == T.TR_PLATEAUX[etat["plateau"]]["boire"]["minijeu_dernier"]
        return
    notes = [l["score"] for l in r["lignes"] if l["score"] is not None]
    assert notes == sorted(notes, reverse=grand), "classement du mini-jeu"
    for l in r["lignes"]:
        g = T._M["gains"]
        assert l["pieces"] == (g[l["rang"] - 1] if l["score"] is not None and l["rang"] <= len(g) else 0)
    if len({l["rang"] for l in r["lignes"]}) >= 2:
        pire = max(l["rang"] for l in r["lignes"])
        boit = T.TR_PLATEAUX[etat["plateau"]]["boire"]["minijeu_dernier"]
        assert all(l["boit"] == (boit if l["rang"] == pire else 0) for l in r["lignes"])


def score_hasard(m):
    r = random.random()
    if r < 0.05:
        return random.choice(["abc", None, True, float("nan"), "1e999", -50, 10 ** 9, [], {}])
    return random.uniform(0, m["max"] * 1.1)


def partie(n, graine):
    random.seed(graine)
    t = 1000.0
    tours = random.choice([None, 5, 10, 15, 3, "x"])
    plateau = random.choice([None, "terrasse", "pub", "cave", "lune"])
    etat = T.tr_initialiser([f"J{i}" for i in range(n)], t, tours=tours, plateau=plateau)
    assert etat["nb_tours"] in (5, 10, 15)
    assert etat["plateau"] == (plateau if plateau in T.TR_PLATEAUX else T.TR_PLATEAU_DEFAUT)
    vus = set()
    for _ in range(20000):
        verifier(etat)
        if etat["phase"] == "fin":
            break
        r = random.random()
        i = random.randrange(n)
        phase = etat["phase"]
        avant = {"capsules": list(etat["capsules"]), "options": dict(etat["options"]), "choix": dict(etat["choix"])}
        if r < 0.03:
            if etat["partis"][i]:
                T.tr_joueur_revenu(etat, i, t)
            elif sum(not p for p in etat["partis"]) > 1:
                T.tr_joueur_parti(etat, i, t)
        elif r < 0.12 or phase == "deplacement":
            t = etat["echeance"] + 0.1
            T.tr_expirer(etat, t)
        elif phase == "des":
            if etat["options"].get(str(i)) and str(i) not in etat["choix"] and random.random() < 0.8:
                k = random.choice([0, 1, 2, 7, -1, "a", True])
                T.tr_action(etat, i, {"type": "tr_chemin", "choix": k}, t)
            else:
                T.tr_action(etat, i, {"type": "tr_lancer"}, t)
        elif phase == "minijeu":
            m = T._minijeu(etat["minijeu"]["id"])
            T.tr_action(etat, i, {"type": "tr_score", "score": score_hasard(m),
                                  "gorgees": random.choice([None, 0, 1, 3, 99, -2, "x"])}, t)
        else:
            T.tr_action(etat, i, {"type": random.choice(["tr_suivant", "tr_lancer", "tr_rien"])}, t)
        t += random.uniform(0, 2)
        if phase == "des" and etat["phase"] == "deplacement":
            verifier_deplacements(etat, avant)
        if phase == "minijeu" and etat["phase"] == "resultats":
            verifier_minijeu(etat)
            vus.add(etat["resultat_minijeu"]["minijeu"])
    else:
        raise AssertionError("partie figee")
    assert len(etat["historique"]) == etat["nb_tours"]
    h = [x["minijeu"] for x in etat["historique"]]
    assert all(a != b for a, b in zip(h, h[1:])), "deux fois le meme mini-jeu de suite"
    f = etat["final"]
    assert sorted(f["classement"]) == list(range(n))
    top = max((etat["capsules"][j], etat["pieces"][j]) for j in range(n))
    assert f["gagnants"] == [j for j in range(n) if (etat["capsules"][j], etat["pieces"][j]) == top]
    return vus, etat


def scenario():
    """Un cas a la main : capsule, depart, cases, mini-jeu."""
    e = T.tr_initialiser(["A", "B", "C"], 0, tours=5, plateau="terrasse")
    rng = random.Random(3)
    e["capsule_case"] = 4
    e["pieces"] = [25, 5, 10]
    e["positions"] = [0, 0, 21]
    e["des"] = {"0": 5, "1": 4, "2": 3}
    ev = []
    T._deplacer(e, ev, 1, rng)
    l = {x["joueur"]: x for x in e["deplacements"]["lignes"]}
    assert [x["joueur"] for x in e["deplacements"]["lignes"]] == [0, 1, 2]
    # A passe sur la capsule (case 4) avec 25 pieces : il l achete.
    assert e["capsules"][0] == 1 and any(x["type"] == "capsule" for x in l[0]["effets"])
    assert e["capsule_case"] != 4
    # C (21 -> 0) passe par le depart : +5.
    assert any(x["type"] == "depart" and x["pieces"] == 5 for x in l[2]["effets"])
    assert e["phase"] == "deplacement" and ev[0]["msg"]["type"] == "tr_deplacements"
    # Mini-jeu : B gagne, A deuxieme, C n envoie rien et boit.
    T.tr_expirer(e, e["echeance"] + 0.1)
    assert e["phase"] == "minijeu"
    e["minijeu"]["equipes"] = None
    grand = e["minijeu"]["sens"] == "grand"
    p = list(e["pieces"])
    haut = T._minijeu(e["minijeu"]["id"])["max"]
    bas = haut // 2
    T.tr_action(e, 1, {"type": "tr_score", "score": haut if grand else bas}, 2)
    T.tr_action(e, 0, {"type": "tr_score", "score": bas if grand else haut}, 2)
    assert T.tr_score(e, 0)["score"] in (haut, bas)
    assert T.tr_action(e, 0, {"type": "tr_score", "score": 1}, 2) == [], "un seul score par mini-jeu"
    T.tr_expirer(e, e["echeance"] + 0.1)
    r = {x["joueur"]: x for x in e["resultat_minijeu"]["lignes"]}
    assert r[1]["rang"] == 1 and r[1]["pieces"] == 10 and r[0]["pieces"] == 6
    assert r[2]["score"] is None and r[2]["boit"] == T.TR_PLATEAUX["terrasse"]["boire"]["minijeu_dernier"]
    assert e["pieces"][1] - p[1] == 10
    for i in range(3):
        T.tr_action(e, i, {"type": "tr_suivant"}, 3)
    assert e["phase"] == "des" and e["tour"] == 2


def scenario_chemins():
    """Un embranchement : le joueur choisit, le chemin est suivi ; sans choix, le hasard a l echeance."""
    e = T.tr_initialiser(["A", "B"], 0, tours=5, plateau="terrasse")
    e["positions"] = [3, 3]
    random.seed(1)
    while True:
        # On force un de qui passe l embranchement (case 4) : il faut au moins 2 pas.
        e["des"], e["options"], e["choix"] = {}, {}, {}
        T.tr_action(e, 0, {"type": "tr_lancer"}, 1)
        if e["options"].get("0"):
            break
    o = e["options"]["0"]
    assert len(o) == 2 and {x["libelle"] for x in o} == {"tout droit", "par la pergola"}, o
    assert T.tr_action(e, 0, {"type": "tr_chemin", "choix": 5}, 1)[0]["msg"]["type"] == "erreur"
    k = [x["libelle"] for x in o].index("par la pergola")
    T.tr_action(e, 0, {"type": "tr_chemin", "choix": k}, 1)
    assert e["choix"]["0"] == k and e["phase"] == "des"
    assert T.tr_action(e, 0, {"type": "tr_chemin", "choix": 1 - k}, 1) == [], "un seul choix"
    T.tr_action(e, 1, {"type": "tr_lancer"}, 1)
    if e["phase"] == "des":
        assert e["options"].get("1") and 1 in T._attendus(e)
        T.tr_expirer(e, e["echeance"] + 0.1)
    assert e["phase"] == "deplacement"
    l = [x for x in e["deplacements"]["lignes"] if x["joueur"] == 0][0]
    assert l["chemin"] == o[k]["chemin"] and l["chemin"][:2] == [4, 24]


def scenario_variete():
    """10 mini-jeux d affilee : aucune redite tant que le catalogue le permet."""
    rng = random.Random(5)
    e = T.tr_initialiser(["A", "B", "C"], 0, plateau="pub")
    vus = []
    for _ in range(10):
        T._lancer_minijeu(e, [], 0, rng)
        vus.append(e["minijeu"]["id"])
    garde = max(1, len(T.TR_MINIJEUX) // 2)
    for k in range(1, len(vus)):
        assert vus[k] not in vus[max(0, k - garde):k], vus
    for m in T.TR_MINIJEUX:
        assert 0 <= m.get("gorgees_max", 0) <= 2, m["id"]


def scenario_retournements():
    """Cave : vol de capsule, duel pour une capsule, videur, partage, trappe."""
    rng = random.Random(5)
    e = T.tr_initialiser(["A", "B", "C"], 0, tours=5, plateau="cave")
    e["capsules"] = [0, 3, 1]
    e["pieces"] = [2, 40, 9]
    eff = {"type": "evenement"}
    T._appliquer(e, 0, T._EVENEMENTS["vol_capsule"], eff, rng)
    assert e["capsules"] == [1, 2, 1] and eff["victime"] == 1
    # Duel dans la Cave : pour une capsule si le perdant en a une.
    cs = cases(e)
    duel = [c["id"] for c in cs if c["type"] == "duel"][0]
    avant = sum(e["capsules"])
    effets = []
    T._effet_case(e, 0, duel, effets, rng)
    d = effets[0]
    assert d["type"] == "duel" and sum(e["capsules"]) == avant
    assert d.get("capsules") == 1 or "pieces" in d
    # Le videur : s arreter dessus renvoie au depart, passer devant coute des pieces.
    e["videur"] = 5
    e["capsule_case"] = 30
    e["positions"] = [3, 1, 0]
    e["pieces"] = [10, 10, 10]
    e["des"], e["options"], e["choix"] = {"0": 2, "1": 6, "2": 1}, {}, {}
    T._deplacer(e, [], 1, rng)
    l = {x["joueur"]: x for x in e["deplacements"]["lignes"]}
    assert e["positions"][0] == 0 and any(x["type"] == "videur" for x in l[0]["effets"])
    assert any(x["type"] == "fouille" for x in l[1]["effets"])
    # Partage : toutes les pieces mises en commun.
    e["pieces"] = [1, 50, 9]
    e["tour"] = 3
    T._EV_PLATEAU["partage"]  # existe
    vieux = T.TR_PLATEAUX["cave"]["evenements_plateau"]
    T.TR_PLATEAUX["cave"]["evenements_plateau"] = {"tous_les": 1, "liste": ["partage"]}
    try:
        T._evenement_plateau(e, rng)
    finally:
        T.TR_PLATEAUX["cave"]["evenements_plateau"] = vieux
    assert sorted(e["pieces"]) == [20, 20, 20] and e["evenement_plateau"]["id"] == "partage"
    # Trappe : on ressort par l autre.
    t = [c for c in cs if c["type"] == "trappe"][0]
    effets = []
    T._effet_case(e, 2, t["id"], effets, rng)
    assert e["positions"][2] == t["vers"] and effets[0]["type"] == "trappe"


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    verifier_config()
    scenario()
    scenario_chemins()
    scenario_retournements()
    scenario_variete()
    vus = set()
    equipes = 0
    plateaux = set()
    for k in range(nb):
        v, etat = partie(random.Random(k).randint(T.TR_JOUEURS_MIN, T.TR_JOUEURS_MAX), k)
        vus |= v
        plateaux.add(etat["plateau"])
    assert vus == {m["id"] for m in T.TR_MINIJEUX}, vus
    assert plateaux == set(T.TR_PLATEAUX)
    print(f"La Tournée : {nb} parties au hasard OK, {len(T.TR_MINIJEUX)} mini-jeux.")


if __name__ == "__main__":
    main()
