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

N = len(T.PLATEAU)


def verifier_config():
    assert T.PLATEAU[0] == "depart"
    assert set(T.PLATEAU) <= {"depart", "pieces", "piege", "evenement", "tournee"}
    ids = [m["id"] for m in T.TR_MINIJEUX]
    assert len(ids) == len(set(ids)) >= 3
    for m in T.TR_MINIJEUX:
        assert m["sens"] in ("grand", "petit") and m["max"] > 0 and 5 <= m["duree"] <= 60, m
        assert "—" not in m["regle"] + m["nom"]
    for e in T.TR_CONFIG["evenements"]:
        assert "—" not in e["texte"]


def verifier(etat):
    pub = T.tr_etat_public(etat, 0)
    assert all(0 <= p < N for p in etat["positions"])
    assert all(p >= 0 for p in etat["pieces"]) and all(g >= 0 for g in etat["gorgees"])
    assert 0 <= etat["capsule_case"] < N and T.PLATEAU[etat["capsule_case"]] != "depart"
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
    for l in res["lignes"]:
        assert 1 <= l["de"] <= 6 and len(l["chemin"]) == l["de"]
        assert l["chemin"][-1] == (l["depart"] + l["de"]) % N
        raccourci = any(e.get("id") == "raccourci" for e in l["effets"])
        if not raccourci:
            assert l["arrivee"] == l["chemin"][-1]
    achats = sum(1 for l in res["lignes"] for e in l["effets"] if e["type"] == "capsule")
    assert sum(etat["capsules"]) - sum(avant["capsules"]) == achats


def verifier_minijeu(etat):
    r = etat["resultat_minijeu"]
    grand = r["sens"] == "grand"
    notes = [l["score"] for l in r["lignes"] if l["score"] is not None]
    assert notes == sorted(notes, reverse=grand), "classement du mini-jeu"
    for l in r["lignes"]:
        g = T._M["gains"]
        assert l["pieces"] == (g[l["rang"] - 1] if l["score"] is not None and l["rang"] <= len(g) else 0)
    if len({l["rang"] for l in r["lignes"]}) >= 2:
        pire = max(l["rang"] for l in r["lignes"])
        assert all(l["boit"] == (T._M["dernier_boit"] if l["rang"] == pire else 0) for l in r["lignes"])


def score_hasard(m):
    r = random.random()
    if r < 0.05:
        return random.choice(["abc", None, True, float("nan"), "1e999", -50, 10 ** 9, [], {}])
    return random.uniform(0, m["max"] * 1.1)


def partie(n, graine):
    random.seed(graine)
    t = 1000.0
    tours = random.choice([None, 5, 10, 15, 3, "x"])
    etat = T.tr_initialiser([f"J{i}" for i in range(n)], t, tours=tours)
    assert etat["nb_tours"] in (5, 10, 15)
    vus = set()
    for _ in range(20000):
        verifier(etat)
        if etat["phase"] == "fin":
            break
        r = random.random()
        i = random.randrange(n)
        phase = etat["phase"]
        avant = {"capsules": list(etat["capsules"])}
        if r < 0.03:
            if etat["partis"][i]:
                T.tr_joueur_revenu(etat, i, t)
            elif sum(not p for p in etat["partis"]) > 1:
                T.tr_joueur_parti(etat, i, t)
        elif r < 0.12 or phase == "deplacement":
            t = etat["echeance"] + 0.1
            T.tr_expirer(etat, t)
        elif phase == "des":
            T.tr_action(etat, i, {"type": "tr_lancer"}, t)
        elif phase == "minijeu":
            m = T._minijeu(etat["minijeu"]["id"])
            T.tr_action(etat, i, {"type": "tr_score", "score": score_hasard(m)}, t)
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
    return vus


def scenario():
    """Un cas a la main : capsule, depart, cases, mini-jeu."""
    e = T.tr_initialiser(["A", "B", "C"], 0, tours=5)
    rng = random.Random(3)
    e["capsule_case"] = 4
    e["pieces"] = [25, 5, 10]
    e["positions"] = [0, 0, 22]
    e["des"] = {"0": 5, "1": 4, "2": 3}
    ev = []
    T._deplacer(e, ev, 1, rng)
    l = {x["joueur"]: x for x in e["deplacements"]["lignes"]}
    assert [x["joueur"] for x in e["deplacements"]["lignes"]] == [0, 1, 2]
    # A passe sur la capsule (case 4) avec 25 pieces : il l achete.
    assert e["capsules"][0] == 1 and any(x["type"] == "capsule" for x in l[0]["effets"])
    assert e["capsule_case"] != 4
    # C (22 -> 1) passe par le depart : +5.
    assert any(x["type"] == "depart" and x["pieces"] == 5 for x in l[2]["effets"])
    assert e["phase"] == "deplacement" and ev[0]["msg"]["type"] == "tr_deplacements"
    # Mini-jeu : B gagne, A deuxieme, C n envoie rien et boit.
    T.tr_expirer(e, e["echeance"] + 0.1)
    assert e["phase"] == "minijeu"
    grand = e["minijeu"]["sens"] == "grand"
    p = list(e["pieces"])
    T.tr_action(e, 1, {"type": "tr_score", "score": 200 if grand else 100}, 2)
    T.tr_action(e, 0, {"type": "tr_score", "score": 100 if grand else 200}, 2)
    assert T.tr_score(e, 0)["score"] in (100, 200)
    assert T.tr_action(e, 0, {"type": "tr_score", "score": 1}, 2) == [], "un seul score par mini-jeu"
    T.tr_expirer(e, e["echeance"] + 0.1)
    r = {x["joueur"]: x for x in e["resultat_minijeu"]["lignes"]}
    assert r[1]["rang"] == 1 and r[1]["pieces"] == 10 and r[0]["pieces"] == 6
    assert r[2]["score"] is None and r[2]["boit"] == 2
    assert e["pieces"][1] - p[1] == 10
    for i in range(3):
        T.tr_action(e, i, {"type": "tr_suivant"}, 3)
    assert e["phase"] == "des" and e["tour"] == 2


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    verifier_config()
    scenario()
    vus = set()
    for k in range(nb):
        vus |= partie(random.Random(k).randint(T.TR_JOUEURS_MIN, T.TR_JOUEURS_MAX), k)
    assert vus == {m["id"] for m in T.TR_MINIJEUX}, vus
    print(f"La Tournée : {nb} parties au hasard OK, {len(T.TR_MINIJEUX)} mini-jeux.")


if __name__ == "__main__":
    main()
