"""
Essai du moteur du Casino, hors ligne : des centaines de soirees jouees au
hasard (roulette, blackjack, poker, actions valides et invalides, absents,
arrivants, delais depasses), de 1 a 8 joueurs. On verifie qu aucune ne plante
ni ne se fige, que les jetons ne se creent ni ne disparaissent, et que les
regles tiennent (evaluation des mains, pots secondaires).
Usage : python tests/casino_essai.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import casino as C


# -------------------------------------------------------------
# Regles pures
# -------------------------------------------------------------
def essais_mains():
    m = lambda txt: txt.split()
    v = lambda txt: C.cs_meilleure_main(m(txt))[0]
    assert v("AS KS QS JS TS 2D 3C")[0] == 8
    assert v("AS 2S 3S 4S 5S 9D 9C")[:2] == (8, 3)          # quinte flush au 5
    assert v("9S 9H 9D 9C 2S 3D 4C")[0] == 7
    assert v("9S 9H 9D 2C 2S 3D 4C")[0] == 6
    assert v("2S 7S 9S JS KS 3D 4C")[0] == 5
    assert v("AS 2D 3C 4H 5S 9D KC")[:2] == (4, 3)          # quinte blanche au 5
    assert v("TS JD QC KH AS 2D 3C")[:2] == (4, 12)
    assert v("9S 9H 9D 2C 5S KD 4C")[0] == 3
    assert v("9S 9H 2D 2C 5S KD 4C")[0] == 2
    assert v("9S 9H 3D 2C 5S KD 4C")[0] == 1
    assert v("9S TH 3D 2C 5S KD 4C")[0] == 0
    # Departages
    assert v("AS AH KD 2C 5S 7D 8C") > v("AS AH QD 2C 5S 7D 8C")
    assert v("KS KH 2D 2C 5S 7D 8C") > v("QS QH JD JC 5S 7D 8C")
    assert v("AS 2S 3S 4S 5S 9D 9C") < v("2S 3S 4S 5S 6S 9D 9C")
    assert v("AS AH AD KC KS 2D 2C") == v("AS AH AD KC KD 3S 3C")[:1] + v("AS AH AD KC KS 2D 2C")[1:]
    assert C.cs_valeur_bj(["AS", "KD"]) == (21, True)
    assert C.cs_valeur_bj(["AS", "AD", "9C"])[0] == 21
    assert C.cs_valeur_bj(["KS", "QD", "5C"])[0] == 25
    # Pots : A 100 a tapis, B et C 300, D couche apres 50.
    pots = C.cs_pots([100, 300, 300, 50], [0, 1, 2])
    assert pots[0] == {"montant": 350, "eligibles": [0, 1, 2]}, pots
    assert pots[1] == {"montant": 400, "eligibles": [1, 2]}, pots
    assert sum(p["montant"] for p in pots) == 750
    # Roulette
    assert C.cs_gain_pari("n17", 17) == 35 and C.cs_gain_pari("n17", 18) is None
    assert C.cs_gain_pari("rouge", 0) is None and C.cs_gain_pari("pair", 0) is None
    assert C.cs_gain_pari("c3", 36) == 2 and C.cs_gain_pari("d2", 13) == 2
    assert C.cs_pari_valide("n36") and not C.cs_pari_valide("n37")
    assert not C.cs_pari_valide("n017") and not C.cs_pari_valide("bleu")
    assert sorted(C.CS_ORDRE_ROUE) == list(range(37))
    essai_split()
    print("regles : ok")


def essai_split():
    """Une paire de 8 separee : deux mains, deux mises, jouees l une apres l autre."""
    etat = C.cs_initialiser(["A"], 0)
    C.cs_action(etat, 0, {"type": "cs_choisir", "jeu": "blackjack"}, 0)
    C.cs_action(etat, 0, {"type": "cs_bj_miser", "montant": 100}, 0)
    C.cs_action(etat, 0, {"type": "cs_pret"}, 0)
    t = etat["t"]
    # On truque la donne : 8-8 pour le joueur, 10-7 pour le croupier.
    t["mains"][0] = [C._bj_main(["8S", "8H"], 100)]
    t["croupier"] = ["TS", "7D"]
    t["paquet"] = ["2C", "5C", "3D", "TC"]   # tirees par la fin : TC, 3D, 5C, 2C
    t["courant"], t["main_courante"] = 0, 0
    etat["phase"] = "jeu"
    C.cs_action(etat, 0, {"type": "cs_bj_splitter"}, 0)
    assert [m["cartes"] for m in t["mains"][0]] == [["8S", "TC"], ["8H", "3D"]], t["mains"][0]
    assert etat["jetons"][0] == 800
    C.cs_action(etat, 0, {"type": "cs_bj_rester"}, 0)          # 18 sur la main 1
    assert t["main_courante"] == 1
    C.cs_action(etat, 0, {"type": "cs_bj_doubler"}, 0)         # 11 + 5 = 16, double
    assert etat["phase"] == "resultat"
    # Croupier 17 : main 1 (18) gagne 200, main 2 (16, doublee a 200) perd.
    assert [m["resultat"] for m in t["mains"][0]] == ["gagné", "perdu"]
    assert etat["jetons"][0] == 1000 - 100 + 0, etat["jetons"]
    assert etat["bilan"]["lignes"][0]["delta"] == -100


# -------------------------------------------------------------
# Invariants
# -------------------------------------------------------------
def jetons_en_jeu(etat):
    """Jetons poses sur la table (pas encore rendus ni gagnes)."""
    t = etat["t"]
    if t is None:
        return 0
    if etat["jeu"] == "roulette":
        if etat["phase"] == "resultat":
            return 0
        return sum(m["montant"] for ms in t["mises"] for m in ms)
    if etat["jeu"] == "blackjack":
        if etat["phase"] == "resultat":
            return 0
        if etat["phase"] == "mises":
            return sum(t["mises"])
        return sum(m["mise"] for mains in t["mains"] for m in mains)
    if etat["jeu"] == "poker":
        return 0 if etat["phase"] == "resultat" else sum(t["engage"])
    return 0


def verifier(etat, banque):
    n = etat["nb_joueurs"]
    for k in ("noms", "partis", "jetons", "gorgees", "recharges", "distribuees"):
        assert len(etat[k]) == n, k
    assert all(j >= 0 for j in etat["jetons"]), etat["jetons"]
    assert etat["phase"] == "hall" or etat["echeance"] is not None, "la table attend toujours quelque chose"
    if etat["phase"] == "hall":
        assert etat["jeu"] is None and etat["t"] is None
    t = etat["t"]
    if etat["jeu"] == "poker" and etat["phase"] == "jeu":
        c = t["courant"]
        assert c is not None and t["dans_coup"][c] and not t["couches"][c] and not t["tapis"][c]
        assert len(t["tableau"]) in (0, 3, 4, 5)
        assert len(set(t["tableau"] + sum(t["cartes"], []))) == len(t["tableau"]) + len(sum(t["cartes"], []))
        for j in range(n):
            assert len(t[ "cartes"][j]) == (2 if t["dans_coup"][j] else 0)
    if etat["jeu"] == "blackjack" and etat["phase"] == "jeu":
        c, h = t["courant"], t["main_courante"]
        m = t["mains"][c][h]
        assert m["statut"] == "joue" and C.cs_valeur_bj(m["cartes"])[0] < 21 and not m["double"]
        assert len(t["mains"][c]) <= C.CS_BJ_MAINS_MAX
        for mains in t["mains"]:
            for mm in mains:
                assert len(mm["cartes"]) >= 2
    pub = C.cs_etat_public(etat, 0)
    texte = repr(pub)
    assert "paquet" not in texte
    if etat["jeu"] == "poker" and etat["phase"] == "jeu":
        assert "'cartes'" not in texte
    if etat["jeu"] == "blackjack" and etat["phase"] == "jeu":
        assert pub["table"]["croupier"][1] == "??"
    # Conservation : tout ce qui manque a ete gagne ou perdu contre la banque.
    total = sum(etat["jetons"]) + jetons_en_jeu(etat)
    assert total == banque["total"], (total, banque["total"], etat["jeu"], etat["phase"])


def recompter(etat, banque, bilan_avant):
    """Apres un coup solde, la banque a gagne ou perdu le bilan, et recharge."""
    b = etat["bilan"]
    if b is None or b["num"] == bilan_avant:
        return
    for ligne in b["lignes"]:
        if etat["jeu"] == "poker" or b["jeu"] == "poker":
            pass   # le poker est a somme nulle entre joueurs
        else:
            banque["total"] += ligne["delta"]
        if ligne["cul_sec"]:
            banque["total"] += C.CS_RECHARGE
        assert ligne["gorgees"] == 0 or ligne["delta"] < 0
        assert ligne["distribue"] == 0 or ligne["delta"] >= C.CS_JETONS_PAR_DISTRIB
    if b["jeu"] == "poker":
        assert sum(l["delta"] for l in b["lignes"]) == 0, b


# -------------------------------------------------------------
# Actions au hasard
# -------------------------------------------------------------
PARIS = list(C.CS_PARIS_SIMPLES) + ["n%d" % k for k in range(37)] + ["n37", "bleu", None, 5]


def action_au_hasard(etat, i):
    jeu, phase, t = etat["jeu"], etat["phase"], etat["t"]
    r = random.random()
    if r < 0.03:
        return {"type": random.choice(list(C.CS_ACTIONS)), "montant": random.choice([None, -5, 0, 10**9, "x", 30])}
    if phase == "hall":
        return {"type": "cs_choisir", "jeu": random.choice(list(C.CS_JEUX) + ["belote"])}
    if r < 0.05:
        return {"type": "cs_hall"}
    if phase == "mises":
        if r < 0.45:
            m = random.choice([10, 20, 50, 100, 500, 5, etat["jetons"][i]])
            if jeu == "roulette":
                return {"type": "cs_rl_miser", "pari": random.choice(PARIS), "montant": m}
            if r < 0.07:
                return {"type": "cs_bj_miser" if jeu == "blackjack" else "cs_rl_miser",
                        "pari": random.choice(PARIS), "tapis": True}
            return {"type": "cs_bj_miser", "montant": m}
        if r < 0.5:
            return {"type": "cs_rl_annuler" if jeu == "roulette" else "cs_bj_annuler"}
        return {"type": "cs_pret"}
    if phase == "jeu" and jeu == "blackjack":
        return {"type": random.choice(["cs_bj_tirer", "cs_bj_rester", "cs_bj_doubler",
                                       "cs_bj_splitter", "cs_bj_splitter"])}
    if phase == "jeu" and jeu == "poker":
        k = random.random()
        if k < 0.15:
            return {"type": "cs_pk_coucher"}
        if k < 0.6:
            return {"type": "cs_pk_suivre"}
        if k < 0.7:
            return {"type": "cs_pk_relancer", "tapis": True}
        base = t["mise_max"] + t["relance_min"]
        return {"type": "cs_pk_relancer", "montant": random.choice([base, base * 2, base - 1, 15, 10**6])}
    return {"type": "cs_pret"}


def soiree(n, graine):
    random.seed(graine)
    t = 1000.0
    etat = C.cs_initialiser(["J%d" % k for k in range(n)], t)
    banque = {"total": sum(etat["jetons"])}
    coups = {"roulette": 0, "blackjack": 0, "poker": 0}
    for pas in range(4000):
        verifier(etat, banque)
        avant = etat["bilan"]["num"] if etat["bilan"] else 0
        r = random.random()
        if r < 0.02 and etat["nb_joueurs"] < C.CS_JOUEURS_MAX:
            C.cs_ajouter_joueur(etat, "N%d" % etat["nb_joueurs"])
            banque["total"] += C.CS_JETONS_DEPART
        elif r < 0.05:
            j = random.randrange(etat["nb_joueurs"])
            if etat["partis"][j]:
                C.cs_joueur_revenu(etat, j, t)
            elif sum(not p for p in etat["partis"]) > 1:
                C.cs_joueur_parti(etat, j, t)
        elif r < 0.25:
            if etat["echeance"] is not None:
                t = max(t, etat["echeance"]) + 0.01
                C.cs_expirer(etat, t)
        else:
            # Le joueur attendu agit plus souvent que les autres.
            att = C.cs_attendu(etat)
            i = att if att is not None and random.random() < 0.7 else random.randrange(etat["nb_joueurs"])
            if etat["partis"][i]:
                continue
            msg = action_au_hasard(etat, i)
            res = C.cs_action(etat, i, msg, t)
            assert isinstance(res, list)
            t += random.random() * 3
        if etat["bilan"] and etat["bilan"]["num"] != avant:
            coups[etat["bilan"]["jeu"]] += 1
        recompter(etat, banque, avant)
    return coups


def main():
    essais_mains()
    totaux = {"roulette": 0, "blackjack": 0, "poker": 0}
    nb = 0
    for n in range(1, 9):
        for graine in range(25):
            c = soiree(n, graine * 100 + n)
            for k in totaux:
                totaux[k] += c[k]
            nb += 1
    print("soirees : %d, coups joues : %s" % (nb, totaux))
    assert all(v > 100 for v in totaux.values()), totaux
    print("ok")


if __name__ == "__main__":
    main()
