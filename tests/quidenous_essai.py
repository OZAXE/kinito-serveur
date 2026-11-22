"""
Essai du moteur de Qui de nous ?, hors ligne : des centaines de parties jouees
au hasard (votes valides et absurdes, absents, delais depasses), de 3 a 8
joueurs, avec tous les paquets. On verifie qu aucune ne plante ni ne se fige,
que les votes ne fuient pas avant la revelation, et que les regles tiennent
(plus designes, paris, gorgees, fin).
Usage : python tests/quidenous_essai.py [nombre de parties]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import quidenous as Q
from quidenous_cartes import GENTIL, EPICE, cartes, cle


def verifier_cartes():
    vues = set()
    for t in GENTIL + EPICE:
        assert isinstance(t, str) and 10 < len(t) < 120, t
        assert t == t.strip() and t[0].islower(), "la carte complete « Qui de nous... » : " + t
        assert t not in vues, "carte en double : " + t
        assert "poitiers" not in t.lower(), t
        assert "—" not in t, t
        vues.add(t)
    assert len(GENTIL) >= 60 and len(EPICE) >= 50
    assert len({cle(c) for c in cartes("melange")}) == len(GENTIL) + len(EPICE)


def verifier(etat):
    pub = Q.qn_etat_public(etat, 0)
    texte = repr(pub)
    if etat["phase"] == "vote":
        assert etat["echeance"] is not None and pub["carte"]
        assert pub["resultat"] is None, "les votes fuient"
        assert "'pari'" not in texte and "'cible'" not in texte, "un vote fuit"
    elif etat["phase"] == "revelation":
        assert etat["echeance"] is not None and pub["resultat"] is not None
        if etat["anonyme"]:
            assert "votes" not in pub["resultat"], "vote anonyme : on ne dit pas qui a vote pour qui"
    else:
        assert etat["phase"] == "fin" and etat["echeance"] is None and etat["final"]
        assert pub["carte"] is None
    assert all(p >= 0 for p in etat["points"]) and all(g >= 0 for g in etat["gorgees"])


def verifier_revelation(etat, votes, presents, avant_points, avant_gorgees):
    res = etat["resultat"]
    n = etat["nb_joueurs"]
    compte = [0] * n
    for v in votes.values():
        compte[v["cible"]] += 1
    assert res["compte"] == compte
    haut = max(compte)
    assert res["designes"] == [j for j in range(n) if haut and compte[j] == haut]
    for j in range(n):
        attendu = 0
        if j in res["designes"]:
            attendu += Q._G["designe"]
        v = votes.get(str(j))
        if v is not None:
            if v["pari"] in res["designes"]:
                assert etat["points"][j] - avant_points[j] == 1
            else:
                attendu += Q._G["mauvais_pari"]
                assert etat["points"][j] == avant_points[j]
        elif j in presents:
            attendu += Q._G["sans_vote"]
        assert res["boit"][j] == attendu, (j, res, votes)
        assert etat["gorgees"][j] - avant_gorgees[j] == attendu


def vote_hasard(n, i):
    r = random.random()
    if r < 0.08:
        return {"cible": random.choice([i, -1, n, "x", None, True, 1.5]), "pari": random.randrange(n)}
    if r < 0.12:
        return {"cible": random.randrange(n), "pari": random.choice([None, -3, n, "b"])}
    return {"cible": random.choice([j for j in range(n) if j != i]), "pari": random.randrange(n)}


def partie(n, graine):
    random.seed(graine)
    t = 1000.0
    paquet = random.choice([None, "gentil", "epice", "melange", "inconnu"])
    manches = random.choice([None, 5, 10, 15, 7, "x"])
    anonyme = random.choice([True, False, None])
    etat = Q.qn_initialiser([f"J{i}" for i in range(n)], t, manches=manches, paquet=paquet,
                            anonyme=anonyme)
    assert etat["nb_manches"] in (5, 10, 15)
    assert etat["anonyme"] == (anonyme is not False)
    reveles = 0
    for _ in range(3000):
        verifier(etat)
        if etat["phase"] == "fin":
            break
        r = random.random()
        i = random.randrange(n)
        avant_phase = etat["phase"]
        votes = {k: dict(v) for k, v in etat["votes"].items()}
        presents = [j for j in range(n) if not etat["partis"][j]]
        avant_points, avant_gorgees = list(etat["points"]), list(etat["gorgees"])
        if r < 0.05:
            if etat["partis"][i]:
                Q.qn_joueur_revenu(etat, i, t)
            elif sum(not p for p in etat["partis"]) > 1:
                Q.qn_joueur_parti(etat, i, t)
        elif r < 0.12:
            t = etat["echeance"] + 0.1
            Q.qn_expirer(etat, t)
        elif etat["phase"] == "vote":
            v = vote_hasard(n, i)
            ev = Q.qn_action(etat, i, {"type": "qn_voter", **v}, t)
            if etat["phase"] == "revelation":
                votes = {k: dict(v) for k, v in etat["votes"].items()}
            elif not any(e["msg"]["type"] == "erreur" for e in ev) and not etat["partis"][i]:
                assert etat["votes"][str(i)]["cible"] != i
        else:
            Q.qn_action(etat, i, {"type": random.choice(["qn_suivant", "qn_voter", "qn_rien"])}, t)
        t += random.uniform(0, 3)
        if avant_phase == "vote" and etat["phase"] == "revelation":
            reveles += 1
            verifier_revelation(etat, etat["votes"], presents, avant_points, avant_gorgees)
    else:
        raise AssertionError("partie figee")
    assert reveles == etat["nb_manches"] == len(etat["historique"])
    assert len({h["carte"] for h in etat["historique"]}) == etat["nb_manches"]
    final = etat["final"]
    assert sorted(final["classement"]) == list(range(n))
    meilleur = max(etat["points"])
    assert final["gagnants"] == [j for j in range(n) if etat["points"][j] == meilleur]
    haut = max(etat["designations"])
    assert final["titre"] == [j for j in range(n) if haut and etat["designations"][j] == haut]
    return etat


def soiree():
    """Deux parties de suite dans un salon : pas de carte retiree."""
    e1 = Q.qn_initialiser(["A", "B", "C"], 0, manches=15, paquet="melange")
    e2 = Q.qn_initialiser(["A", "B", "C"], 0, manches=15, paquet="melange", deja=e1["deja"])
    assert not {c[1] for c in e1["tirage"]} & {c[1] for c in e2["tirage"]}, "carte retiree le meme soir"
    e3 = Q.qn_initialiser(["A", "B", "C"], 0, manches=5, paquet="epice")
    assert all(c[0] == "epice" for c in e3["tirage"])


def scenario():
    """Un cas a la main : 4 joueurs, votes a decouvert."""
    e = Q.qn_initialiser(["A", "B", "C", "D"], 0, manches=5, anonyme=False)
    assert Q.qn_action(e, 0, {"type": "qn_voter", "cible": 0, "pari": 1}, 1)[0]["msg"]["type"] == "erreur"
    v0 = e["version"]
    ev = Q.qn_action(e, 0, {"type": "qn_voter", "cible": 1, "pari": 1}, 1)
    assert e["version"] == v0 + 1 and ev[0]["a"] == 0 and ev[0]["msg"]["type"] == "qn_vote"
    assert Q.qn_vote(e, 0) == {"type": "qn_vote", "index": 0, "cible": 1, "pari": 1}
    assert Q.qn_vote(e, 1) is None
    # A change d avis : son vote est remplace.
    Q.qn_action(e, 0, {"type": "qn_voter", "cible": 2, "pari": 1}, 2)
    Q.qn_action(e, 1, {"type": "qn_voter", "cible": 2, "pari": 2}, 2)
    Q.qn_action(e, 2, {"type": "qn_voter", "cible": 1, "pari": 2}, 3)
    assert e["phase"] == "vote"
    Q.qn_action(e, 3, {"type": "qn_voter", "cible": 2, "pari": 3}, 4)
    assert e["phase"] == "revelation", "tout le monde a vote : revelation"
    r = e["resultat"]
    assert r["compte"] == [0, 1, 3, 0] and r["designes"] == [2]
    assert r["boit"] == [1, 0, 2, 1], r["boit"]   # A et D ont mal parie, C est designe
    assert e["points"] == [0, 1, 1, 0]
    assert r["votes"] == [{"joueur": 0, "cible": 2}, {"joueur": 1, "cible": 2},
                          {"joueur": 2, "cible": 1}, {"joueur": 3, "cible": 2}]
    # Suivant de tous : manche 2. D part, ne vote pas, pas de gorgee « sans vote ».
    for i in range(4):
        Q.qn_action(e, i, {"type": "qn_suivant"}, 5)
    assert e["phase"] == "vote" and e["manche"] == 1
    Q.qn_joueur_parti(e, 3, 6)
    assert Q.qn_action(e, 0, {"type": "qn_voter", "cible": 3, "pari": 1}, 6)[0]["msg"]["type"] == "erreur"
    Q.qn_action(e, 0, {"type": "qn_voter", "cible": 1, "pari": 1}, 6)
    Q.qn_action(e, 1, {"type": "qn_voter", "cible": 0, "pari": 0}, 6)
    assert e["phase"] == "vote"
    Q.qn_expirer(e, e["echeance"] + 1)
    r = e["resultat"]
    assert r["designes"] == [0, 1] and r["sans_vote"] == [2]
    assert r["boit"] == [2, 2, 1, 0]
    # On termine au minuteur.
    for _ in range(10):
        if e["phase"] == "fin":
            break
        Q.qn_expirer(e, e["echeance"] + 1)
    assert e["points"] == [1, 2, 1, 0] and e["designations"] == [1, 2, 3, 0]
    assert e["phase"] == "fin" and e["final"]["gagnants"] == [1] and e["final"]["titre"] == [2]
    assert [d["joueur"] for d in e["final"]["derniers"]] == [3]


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    verifier_cartes()
    soiree()
    scenario()
    for k in range(nb):
        partie(random.Random(k).randint(Q.QN_JOUEURS_MIN, Q.QN_JOUEURS_MAX), k)
    print(f"Qui de nous ? : {nb} parties au hasard OK, {len(GENTIL)} cartes gentilles, "
          f"{len(EPICE)} épicées.")


if __name__ == "__main__":
    main()
