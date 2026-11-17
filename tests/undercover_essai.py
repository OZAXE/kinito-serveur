"""
Essai du moteur d Undercover, hors ligne : des centaines de parties jouees au
hasard (actions valides et invalides, absents, delais depasses), de 3 a 15
joueurs, avec toutes les familles de mots. On verifie qu aucune ne plante ni
ne se fige, que les mots et les roles ne fuient pas dans l etat public, et
que les regles tiennent (egalites, Mr White, victoires, gorgees, points).
Usage : python tests/undercover_essai.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import undercover as U
from undercover_mots import MOTS, paires


def verifier(etat):
    n = etat["nb_joueurs"]
    vivants = [i for i in range(n) if etat["vivants"][i]]
    civils = [i for i in vivants if etat["roles"][i] == "civil"]
    infiltres = [i for i in vivants if etat["roles"][i] != "civil"]
    if etat["phase"] == "fin":
        assert etat["echeance"] is None and etat["resultat"] is not None
        v = etat["resultat"]["vainqueur"]
        if v == "civils":
            assert not infiltres
        elif v == "infiltres":
            assert infiltres and len(civils) <= 1
        else:
            assert etat["devinette"] and etat["devinette"]["correct"]
    else:
        assert etat["echeance"] is not None, "la table attend toujours quelque chose"
        if etat["phase"] != "mrwhite":
            assert infiltres and len(civils) >= 2, "la partie aurait du finir"
    if etat["phase"] == "indices":
        assert etat["ordre"] and etat["parole"] < len(etat["ordre"])
        assert etat["vivants"][etat["ordre"][etat["parole"]]]
    if etat["departage"]:
        assert all(etat["vivants"][j] for j in etat["departage"])
    # Rien de secret dans l etat public avant la fin.
    pub = U.uc_etat_public(etat, 0)
    if etat["phase"] != "fin":
        assert pub["mots"] is None
        texte = repr({k: v for k, v in pub.items() if k not in ("devinette", "message")})
        for mot in etat["mots"].values():
            assert "'" + mot + "'" not in texte, "un mot secret fuit : " + mot
        morts = {e["joueur"] for e in etat["eliminations"]}
        for i, r in enumerate(pub["roles"]):
            assert (r is not None) == (i in morts)
        assert "votes" not in pub or not pub.get("votes")
    else:
        assert pub["roles"] == etat["roles"] and pub["mots"]["civil"] == etat["mots"]["civil"]


def partie(n, famille, graine, config=None):
    random.seed(graine)
    t = 1000.0
    nb_u, nb_w = config or (None, None)
    etat = U.uc_initialiser(["J%d" % k for k in range(n)], t, famille, nb_u, nb_w)
    r = etat["repartition"]
    assert r["undercover"] + r["white"] + r["civil"] == n
    assert U.uc_repartition_valide(n, r["undercover"], r["white"])
    assert etat["roles"].count("undercover") == r["undercover"]
    assert etat["roles"].count("white") == r["white"]
    for i in range(n):
        m = U.uc_mot(etat, i)
        if etat["roles"][i] == "white":
            assert m["mot"] is None and m["white"]
        else:
            assert m["mot"] == etat["mots"][etat["roles"][i]] and not m["white"]
    assert etat["mots"]["civil"] != etat["mots"]["undercover"]
    for _ in range(4000):
        verifier(etat)
        if etat["phase"] == "fin":
            return etat
        x = random.random()
        if x < 0.04:
            i = random.randrange(n)
            if etat["partis"][i]:
                U.uc_joueur_revenu(etat, i, t)
            else:
                U.uc_joueur_parti(etat, i, t)
            continue
        if x < 0.10 or etat["phase"] == "revelation":
            t = etat["echeance"] + 0.1
            U.uc_expirer(etat, t)
            continue
        i = random.randrange(n)
        ph = etat["phase"]
        if ph == "lecture":
            msg = {"type": "uc_pret"}
        elif ph == "indices":
            if random.random() < 0.8:
                i = etat["ordre"][etat["parole"]]
            msg = {"type": "uc_fini"}
        elif ph == "vote":
            vivants = [j for j in range(n) if etat["vivants"][j]]
            if random.random() < 0.85:
                i = random.choice(vivants)
            cands = etat["departage"] or vivants
            msg = {"type": "uc_voter", "cible": random.choice(cands + [None, -1, "x", 99])}
        elif ph == "mrwhite":
            if random.random() < 0.8:
                i = etat["elimination"]["joueur"]
            msg = {"type": "uc_deviner", "mot": random.choice(
                [etat["mots"]["civil"], etat["mots"]["undercover"], "n'importe quoi", "", None, 3])}
        else:
            msg = {"type": random.choice(["uc_pret", "uc_fini", "uc_voter", "uc_deviner", "zz"])}
        if random.random() < 0.05:
            msg = {"type": random.choice(list(U.UC_ACTIONS)), "cible": "?", "mot": ["x"]}
        version = etat["version"]
        ev = U.uc_action(etat, i, msg, t)
        if etat["version"] == version:
            assert all(e["a"] is not None for e in ev), "un refus ne part qu au fautif"
    raise AssertionError("partie figee")


def tests_cibles():
    # Normalisation et tolerance du mot de Mr White.
    assert U.normaliser("L'Œuf au plat !") == "oeufauplat"
    assert U.mot_trouve("cafe", "Café", "Thé")
    assert U.mot_trouve("le café", "Café", "Thé")
    assert U.mot_trouve("CAFÉS", "Café", "Thé")
    assert U.mot_trouve("croisant", "Croissant", "Pain au chocolat")
    assert U.mot_trouve("pain o chocolat", "Pain au chocolat", "Croissant")
    assert not U.mot_trouve("thé", "Café", "Thé")
    assert not U.mot_trouve("cat", "Chat", "Tigre")      # mot court : exact seulement
    assert not U.mot_trouve("vin rose", "Vin rouge", "Vin rosé")
    assert U.mot_trouve("vin rouge", "Vin rouge", "Vin rosé")
    assert not U.mot_trouve("", "Café", "Thé") and not U.mot_trouve(None, "Café", "Thé")

    # Repartition conseillee, toujours valide ; une repartition invalide est corrigee.
    for n in range(U.UC_JOUEURS_MIN, U.UC_JOUEURS_MAX + 1):
        assert U.uc_repartition_valide(n, *U.uc_repartition(n)), n
    e = U.uc_initialiser(["A", "B", "C", "D"], 0, "classique", 2, 1)
    assert (e["repartition"]["undercover"], e["repartition"]["white"]) == U.uc_repartition(4)
    e = U.uc_initialiser(["A", "B", "C", "D", "E", "F"], 0, "classique", 0, 2)
    assert e["repartition"] == {"undercover": 0, "white": 2, "civil": 4}
    assert not e["message"].startswith("Réglage ajusté")

    # « Rejouer » apres des departs : le reglage du createur (2 + 1 pour 7)
    # n est plus valide a 4 presents. Repli sur le conseille, annonce a la table.
    e = U.uc_initialiser(["A", "B", "C", "D"], 0, "classique", 2, 1)
    nb_u, nb_w = U.uc_repartition(4)
    assert e["message"].startswith(f"Réglage ajusté pour 4 joueurs : {nb_u} Undercover, {nb_w} Mr White."), e["message"]
    # Sans reglage envoye (conseille) : pas d annonce.
    e = U.uc_initialiser(["A", "B", "C", "D"], 0, "classique")
    assert e["message"] == "Découvrez votre mot en secret.", e["message"]

    # Mots : chaque paire a deux mots differents, pas de doublon dans une famille.
    for fam in MOTS:
        vues = set()
        for cat, a, b in paires(fam):
            assert a != b and U.normaliser(a) != U.normaliser(b), (a, b)
            assert (a, b) not in vues
            vues.add((a, b))

    # Scenario dirige : 5 joueurs, egalite, departage, Mr White qui devine.
    random.seed(7)
    e = U.uc_initialiser(["A", "B", "C", "D", "E"], 0, "classique", 1, 1)
    e["roles"] = ["civil", "undercover", "civil", "white", "civil"]
    for i in range(5):
        U.uc_action(e, i, {"type": "uc_pret"}, 1)
    assert e["phase"] == "indices" and e["tour"] == 1
    assert e["ordre"][0] != 3, "Mr White ne commence jamais"
    for _ in range(5):
        U.uc_action(e, e["ordre"][e["parole"]], {"type": "uc_fini"}, 2)
    assert e["phase"] == "vote"
    # A et C votent B, B et D votent C, E vote A : egalite B / C.
    for v, c in ((0, 1), (2, 1), (1, 2), (3, 2), (4, 0)):
        U.uc_action(e, v, {"type": "uc_voter", "cible": c}, 3)
    assert e["phase"] == "indices" and e["departage"] == [1, 2] and e["ordre"] == [1, 2]
    U.uc_action(e, 1, {"type": "uc_fini"}, 4)
    U.uc_action(e, 2, {"type": "uc_fini"}, 4)
    assert e["phase"] == "vote"
    ev = U.uc_action(e, 0, {"type": "uc_voter", "cible": 4}, 5)
    assert ev and ev[0]["a"] == 0, "pendant un departage on vote entre les ex aequo"
    for v in range(5):
        U.uc_action(e, v, {"type": "uc_voter", "cible": 2 if v != 2 else 1}, 5)
    assert not e["vivants"][2] and e["elimination"]["role"] == "civil"
    g = {b["joueur"]: b["gorgees"] for b in e["elimination"]["gorgees"]}
    assert g == {2: U.UC_GORGEES_CIVIL, 0: U.UC_GORGEES_ACCUSATEUR, 1: U.UC_GORGEES_ACCUSATEUR,
                 3: U.UC_GORGEES_ACCUSATEUR, 4: U.UC_GORGEES_ACCUSATEUR}, g
    assert e["phase"] == "revelation"
    U.uc_expirer(e, e["echeance"] + 0.1)
    assert e["phase"] == "indices" and e["tour"] == 2
    while e["phase"] == "indices":
        U.uc_action(e, e["ordre"][e["parole"]], {"type": "uc_fini"}, 20)
    for v in (0, 1, 3, 4):
        U.uc_action(e, v, {"type": "uc_voter", "cible": 3 if v != 3 else 0}, 21)
    assert e["phase"] == "mrwhite" and e["elimination"]["role"] == "white"
    assert U.uc_action(e, 0, {"type": "uc_deviner", "mot": "x"}, 22)[0]["a"] == 0
    U.uc_action(e, 3, {"type": "uc_deviner", "mot": e["mots"]["civil"].upper()}, 22)
    assert e["phase"] == "fin" and e["resultat"]["vainqueur"] == "white"
    assert e["resultat"]["gagnants"] == [3] and e["points"]["D"] == U.UC_POINTS["white"]

    # Points cumules d une partie a l autre, paires pas rejouees.
    e2 = U.uc_initialiser(e["noms"], 0, "classique", None, None, e["points"], e["deja"])
    assert e2["points"]["D"] == U.UC_POINTS["white"]
    assert e2["deja"][-1] not in e["deja"] and len(e2["deja"]) == len(e["deja"]) + 1

    # Victoire des infiltres a 3 : on elimine un Civil, il en reste un.
    e = U.uc_initialiser(["A", "B", "C"], 0)
    e["roles"] = ["civil", "civil", "undercover"]
    U.uc_expirer(e, e["echeance"] + 1)
    while e["phase"] == "indices":
        U.uc_action(e, e["ordre"][e["parole"]], {"type": "uc_fini"}, 50)
    for v, c in ((0, 1), (1, 2), (2, 1)):
        U.uc_action(e, v, {"type": "uc_voter", "cible": c}, 51)
    assert e["phase"] == "fin" and e["resultat"]["vainqueur"] == "infiltres"
    assert e["resultat"]["gagnants"] == [2] and e["points"]["C"] == U.UC_POINTS["undercover"]
    print("scenarios diriges : ok")


def main():
    tests_cibles()
    nb = fins = 0
    vainqueurs = {}
    for g in range(700):
        n = random.Random(g).randint(U.UC_JOUEURS_MIN, U.UC_JOUEURS_MAX)
        famille = ("classique", "trash", "melange", "???")[g % 4]
        config = None
        if g % 3 == 0:
            r = random.Random(g + 1)
            config = (r.randint(0, 4), r.randint(0, 3))
        e = partie(n, famille, g, config)
        nb += 1
        fins += e["phase"] == "fin"
        v = e["resultat"]["vainqueur"]
        vainqueurs[v] = vainqueurs.get(v, 0) + 1
    assert fins == nb
    print("%d parties au hasard, toutes finies : %s" % (nb, vainqueurs))
    print("OK")


if __name__ == "__main__":
    main()
