"""
Essai du moteur de Royaumes (conquete.py, prefixe sw_), hors ligne :
- les 3 cartes respectent les regles d equilibrage (conquete_cartes.py) ;
- scenarios diriges : couts, pertes, file de choix, declin, Immortel, de,
  gorgees plafonnees par attaque, pouvoirs qui changent les regles, fin ;
- des centaines de parties jouees au hasard (coups valides et invalides,
  mauvais joueur, absents, minuteurs depasses), de 2 a 8 joueurs, avec
  toutes les vitesses et le mode aleatoire : aucune ne plante ni ne se fige,
  et les invariants tiennent apres chaque coup.
Usage : python tests/conquete_essai.py [nombre de parties]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import conquete as C
import conquete_cartes as CC

CFG = C.SW_CONFIG


# =============================================================
# INVARIANTS
# =============================================================
def verifier(etat):
    n = etat["nb_joueurs"]
    for r, reg in enumerate(etat["regions"]):
        assert (reg["j"] is None) == (reg["p"] is None), f"region {r} : proprietaire incoherent"
        assert reg["pions"] >= 0
        if reg["p"] is not None:
            p = etat["peuples"][str(reg["p"])]
            assert p["etat"] in ("actif", "declin"), f"region {r} d un peuple disparu"
            assert p["j"] == reg["j"]
            if p["etat"] == "declin":
                assert reg["pions"] == 1 and not reg["fortin"] and not reg["camp"] and not reg["gardien"]
            elif reg["pions"] == 0:
                assert reg["camp"] or p["race"] == "nomades", f"region {r} vide sans camp"
        else:
            assert reg["pions"] == 0 and not reg["fortin"] and not reg["gardien"]
    for i, j in enumerate(etat["j"]):
        assert j["pieces"] >= 0, "pieces negatives"
        if j["actif"] is not None:
            p = etat["peuples"][str(j["actif"])]
            assert p["etat"] == "actif" and p["j"] == i and p["main"] >= 0
            assert sum(1 for reg in etat["regions"] if reg["p"] == p["id"] and reg["fortin"]) <= CFG["limites"]["fortins"]
            assert sum(1 for reg in etat["regions"] if reg["p"] == p["id"] and reg["camp"]) <= CFG["limites"]["camps"]
            assert sum(1 for reg in etat["regions"] if reg["p"] == p["id"] and reg["gardien"]) <= CFG["limites"]["gardiens"]
        mortels = [pid for pid in j["declins"] if etat["peuples"][str(pid)]["pouvoir"] != "immortel"]
        assert len(mortels) <= 1, "deux peuples en declin non immortels"
        for pid in j["declins"]:
            assert etat["peuples"][str(pid)]["etat"] == "declin"
    assert len(etat["file"]) == CFG["file_visible"]
    for c in etat["file"]:
        assert not C._banni(etat, c["race"], c["pouvoir"]), "combinaison bannie dans la file"
        assert c["pions"] >= CFG["pions_min"]
    if etat["phase"] == "jeu":
        assert etat["echeance"] is not None, "la table attend toujours quelque chose"
        assert 1 <= etat["manche"] <= etat["tours_max"]
        assert etat["joueur"] == etat["ordre"][etat["pos"]]
        if etat["etape"] == "choix":
            assert etat["j"][etat["joueur"]]["actif"] is None
        else:
            assert etat["etape"] in ("action", "conquete", "redeploiement")
            assert etat["j"][etat["joueur"]]["actif"] is not None
    else:
        assert etat["echeance"] is None and etat["resultat"] is not None
        cl = etat["resultat"]["classement"]
        assert sorted(c["joueur"] for c in cl) == list(range(n))
        assert all(cl[k]["pieces"] >= cl[k + 1]["pieces"] for k in range(n - 1))
    pub = C.sw_etat_public(etat, 0)
    json.dumps(pub)
    json.dumps(C.sw_catalogue(etat))


# =============================================================
# OUTILS DES SCENARIOS
# =============================================================
def nouvelle(n=3, **options):
    random.seed(1)
    e = C.sw_initialiser(["J%d" % k for k in range(n)], 0, options)
    return e


def donner_peuple(e, i, race, pouvoir, main=10):
    pid = e["prochain_peuple"]
    e["prochain_peuple"] += 1
    p = {"id": pid, "j": i, "race": race, "pouvoir": pouvoir, "etat": "actif",
         "main": main, "pions": main, "tours": 0, "dragon": False}
    e["peuples"][str(pid)] = p
    e["j"][i]["actif"] = pid
    return p


def poser(e, r, p, pions):
    e["regions"][r].update({"j": p["j"], "p": p["id"], "pions": pions})


def tour_de(e, i, etape="action"):
    e["joueur"] = i
    e["pos"] = e["ordre"].index(i)
    C._debut_tour(e, 0)
    if etape != "action":
        e["etape"] = etape


def act(e, i, **m):
    return C.sw_action(e, i, m, 1)


def erreur(ev):
    return next((x["msg"]["message"] for x in ev if x["msg"]["type"] == "erreur"), None)


def bord_non_montagne(e):
    return next(r["id"] for r in e["carte"]["regions"] if r["bord"] and r["terrain"] != "montagne")


def interieure(e):
    return next(r["id"] for r in e["carte"]["regions"] if not r["bord"])


# =============================================================
# SCENARIOS DIRIGES
# =============================================================
def tests_cartes():
    for nom, carte in C.SW_CARTES.items():
        CC.verifier(carte)
        assert len(carte["regions"]) == CFG["cartes"][nom], nom
        assert carte["joueurs"] == [int(nom), int(nom)]
    for n in range(C.SW_JOUEURS_MIN, C.SW_JOUEURS_MAX + 1):
        assert len(C._carte_pour(n)["regions"]) == CFG["cartes"][str(n)], "une carte par nombre de joueurs"
    # Les cartes du JSON sont bien celles que fabrique le script (rien d edite a la main)
    refaites = CC.toutes()
    for nom in C.SW_CARTES:
        assert refaites[nom]["regions"] == C.SW_CARTES[nom]["regions"], f"carte {nom} differente du script"
    print("cartes : ok")


def tests_couts():
    e = nouvelle(3)
    a = donner_peuple(e, 0, "hordes", "negociant")
    b = donner_peuple(e, 1, "titans", "alpin")
    tour_de(e, 0, "conquete")
    carte = e["carte"]["regions"]
    # premiere conquete : un bord obligatoire
    r_int = interieure(e)
    assert "bord" in erreur(act(e, 0, type="sw_conquerir", region=r_int))
    r = bord_non_montagne(e)
    ev = act(e, 0, type="sw_conquerir", region=r)
    assert erreur(ev) is None and e["regions"][r]["pions"] == 2 and a["main"] == 8
    # une voisine tenue par des Titans actifs (2 pions) : 2 + 2 + 1 (titans) [+1 montagne]
    v = carte[r]["voisins"][0]
    poser(e, v, b, 2)
    attendu = 2 + 2 + 1 + (1 if carte[v]["terrain"] == "montagne" else 0)
    assert C._cout(e, 0, v) == attendu, (C._cout(e, 0, v), attendu)
    # Eclaireur : 1 de moins, jamais sous 1
    a["pouvoir"] = "eclaireur"
    assert C._cout(e, 0, v) == attendu - 1
    e["regions"][v].update({"j": None, "p": None, "pions": 0})
    if carte[v]["terrain"] != "montagne":
        assert C._cout(e, 0, v) == 1
    # Region a soi : refusee
    assert "déjà à toi" in erreur(act(e, 0, type="sw_conquerir", region=r))
    # Pas son tour
    assert "pas ton tour" in erreur(act(e, 1, type="sw_conquerir", region=v))
    print("couts et voisinage : ok")


def tests_pertes():
    e = nouvelle(3)
    a = donner_peuple(e, 0, "hordes", "negociant", main=20)
    b = donner_peuple(e, 1, "cultivateurs", "alpin", main=0)
    tour_de(e, 0, "conquete")
    r = bord_non_montagne(e)
    poser(e, r, a, 1)
    v = next(x for x in e["carte"]["regions"][r]["voisins"])
    poser(e, v, b, 3)
    act(e, 0, type="sw_conquerir", region=v)
    assert e["regions"][v]["j"] == 0 and b["main"] == 2, "defenseur actif : perd 1, garde le reste en main"
    # gorgee : l ancien occupant boit
    assert e["j"][1]["bu"] == 1
    # Intouchables : ne perdent rien
    e2 = nouvelle(3)
    a2 = donner_peuple(e2, 0, "hordes", "negociant", main=20)
    b2 = donner_peuple(e2, 1, "intouchables", "alpin", main=0)
    tour_de(e2, 0, "conquete")
    poser(e2, r, a2, 1)
    poser(e2, v, b2, 3)
    act(e2, 0, type="sw_conquerir", region=v)
    assert b2["main"] == 3
    # Peuple en declin : perd tout
    e3 = nouvelle(3)
    a3 = donner_peuple(e3, 0, "hordes", "negociant", main=20)
    b3 = donner_peuple(e3, 1, "cultivateurs", "alpin", main=0)
    b3["etat"] = "declin"
    e3["j"][1]["actif"] = None
    e3["j"][1]["declins"] = [b3["id"]]
    tour_de(e3, 0, "conquete")
    poser(e3, r, a3, 1)
    poser(e3, v, b3, 1)
    act(e3, 0, type="sw_conquerir", region=v)
    assert b3["main"] == 0 and e3["regions"][v]["j"] == 0
    print("pertes : ok")


def tests_gorgees_plafond():
    e = nouvelle(2)
    a = donner_peuple(e, 0, "hordes", "aerien", main=60)
    b = donner_peuple(e, 1, "cultivateurs", "alpin", main=0)
    for r in range(6):
        poser(e, r, b, 1)
    tour_de(e, 0, "conquete")
    for r in range(6):
        assert erreur(act(e, 0, type="sw_conquerir", region=r)) is None
    assert e["j"][1]["bu"] == CFG["gorgees"]["plafond_par_attaque"], e["j"][1]["bu"]
    print("gorgees plafonnees par attaque : ok")


def tests_file():
    e = nouvelle(3)
    i = e["joueur"]
    e["j"][i]["pieces"] = 5
    deux = e["file"][2]
    act(e, i, type="sw_choisir", rang=2)
    assert e["j"][i]["pieces"] == 3 + deux["pieces"]
    assert e["file"][0]["pieces"] >= 1 and e["file"][1]["pieces"] >= 1
    p = C._actif(e, i)
    assert (p["race"], p["pouvoir"]) == (deux["race"], deux["pouvoir"])
    assert e["etape"] == "conquete" and len(e["file"]) == CFG["file_visible"]
    # Pas assez de pieces
    e2 = nouvelle(3)
    i2 = e2["joueur"]
    assert "pièce" in erreur(act(e2, i2, type="sw_choisir", rang=4))
    # Le minimum de pions est applique
    assert C._pions("titans", "negociant") == CFG["pions_min"]
    print("file de choix : ok")


def tests_declin():
    e = nouvelle(3)
    a = donner_peuple(e, 0, "hordes", "negociant")
    poser(e, 0, a, 4)
    poser(e, 1, a, 3)
    tour_de(e, 0)
    ev = act(e, 0, type="sw_declin")
    assert erreur(ev) is None
    assert a["etat"] == "declin" and e["regions"][0]["pions"] == 1 and e["regions"][1]["pions"] == 1
    assert e["j"][0]["distribue"] == C._gorgees(e, CFG["gorgees"]["declin_distribue"])
    assert e["joueur"] != 0, "le declin termine le tour"
    # 2 regions en declin = 2 pieces
    assert e["j"][0]["pieces"] == 2
    # Nouveau peuple puis nouveau declin : l ancien disparait
    b = donner_peuple(e, 0, "cultivateurs", "immortel")
    poser(e, 5, b, 2)
    tour_de(e, 0)
    act(e, 0, type="sw_declin")
    assert a["etat"] == "disparu" and e["regions"][0]["j"] is None
    # ... sauf Immortel : il reste
    c = donner_peuple(e, 0, "hordes", "alpin")
    poser(e, 7, c, 2)
    tour_de(e, 0)
    act(e, 0, type="sw_declin")
    assert b["etat"] == "declin" and e["regions"][5]["j"] == 0, "un peuple Immortel ne disparait pas"
    assert c["etat"] == "declin"
    # Declin interdit apres une conquete (sauf Endurant)
    e2 = nouvelle(3)
    d = donner_peuple(e2, 0, "hordes", "negociant", main=10)
    tour_de(e2, 0, "conquete")
    act(e2, 0, type="sw_conquerir", region=bord_non_montagne(e2))
    assert erreur(act(e2, 0, type="sw_declin"))
    d["pouvoir"] = "endurant"
    assert erreur(act(e2, 0, type="sw_declin")) is None and d["etat"] == "declin"
    print("declin, Immortel, Endurant : ok")


def tests_de():
    e = nouvelle(3)
    a = donner_peuple(e, 0, "hordes", "negociant", main=1)
    tour_de(e, 0, "conquete")
    r = bord_non_montagne(e)
    random.seed(3)
    ev = act(e, 0, type="sw_tenter", region=r)
    assert erreur(ev) is None
    de = next(x["msg"] for x in ev if x["msg"]["type"] == "sw_de")
    assert de["reussi"] == (1 + de["faces"][-1] >= 2)
    assert e["etape"] == "redeploiement", "sans Furie, le de termine les conquetes"
    assert "déjà servi" in erreur(act(e, 0, type="sw_tenter", region=r)) or e["etape"] == "redeploiement"
    # Assez de pions : pas de de
    e2 = nouvelle(3)
    donner_peuple(e2, 0, "hordes", "negociant", main=5)
    tour_de(e2, 0, "conquete")
    assert "directement" in erreur(act(e2, 0, type="sw_tenter", region=r))
    # Furie : le tour continue
    e3 = nouvelle(3)
    donner_peuple(e3, 0, "hordes", "furie", main=1)
    tour_de(e3, 0, "conquete")
    act(e3, 0, type="sw_tenter", region=r)
    assert e3["etape"] == "conquete"
    # Les faces sont celles de la config
    for _ in range(200):
        e4 = nouvelle(2)
        donner_peuple(e4, 0, "mages", "negociant", main=1)
        tour_de(e4, 0, "conquete")
        random.seed(_)
        ev = act(e4, 0, type="sw_tenter", region=r)
        de = next(x["msg"] for x in ev if x["msg"]["type"] == "sw_de")
        assert all(f in CFG["de_renfort"] for f in de["faces"])
        assert len(de["faces"]) == 1 or 1 + de["faces"][0] < 2, "les Mages ne relancent qu un echec"
    print("de de renfort, Furie, Mages : ok")


def tests_redeploiement():
    e = nouvelle(3)
    a = donner_peuple(e, 0, "hordes", "batisseur", main=4)
    poser(e, 0, a, 1)
    poser(e, 1, a, 1)
    tour_de(e, 0, "redeploiement")
    e["tour"]["rassemble"] = True
    assert "tous tes pions" in erreur(act(e, 0, type="sw_redeployer", pions={"0": 1, "1": 1}))
    assert "au moins 1" in erreur(act(e, 0, type="sw_redeployer", pions={"0": 6, "1": 0}))
    assert erreur(act(e, 0, type="sw_redeployer", pions={"0": 2, "1": 2}, fortin=5))
    ev = act(e, 0, type="sw_redeployer", pions={"0": 5, "1": 1}, fortin=0)
    assert erreur(ev) is None and e["regions"][0]["pions"] == 5 and e["regions"][0]["fortin"]
    # 2 regions + negociant? non : batisseur = +1 par fortin
    assert e["j"][0]["pieces"] == 3
    # Nomades : 2 regions vides permises
    e2 = nouvelle(3)
    n = donner_peuple(e2, 0, "nomades", "alpin", main=0)
    for r in (0, 1, 2):
        poser(e2, r, n, 2)
    tour_de(e2, 0, "redeploiement")
    e2["tour"]["rassemble"] = True
    assert erreur(act(e2, 0, type="sw_redeployer", pions={"0": 6, "1": 0, "2": 0})) is None
    # Gardien : region gardee imprenable
    e3 = nouvelle(3)
    g = donner_peuple(e3, 1, "hordes", "gardien", main=0)
    x = donner_peuple(e3, 0, "hordes", "aerien", main=20)
    poser(e3, 3, g, 1)
    e3["regions"][3]["gardien"] = True
    tour_de(e3, 0, "conquete")
    assert "gardien" in erreur(act(e3, 0, type="sw_conquerir", region=3))
    print("redeploiement, fortin, Nomades, Gardien : ok")


def tests_pouvoirs_speciaux():
    # Dragon : n importe ou pour 1 pion, une fois
    e = nouvelle(3)
    d = donner_peuple(e, 0, "hordes", "dompteur", main=5)
    b = donner_peuple(e, 1, "hordes", "alpin", main=0)
    r = interieure(e)
    poser(e, r, b, 5)
    tour_de(e, 0, "conquete")
    assert erreur(act(e, 0, type="sw_dragon", region=r)) is None
    assert e["regions"][r]["j"] == 0 and e["regions"][r]["pions"] == 1 and d["dragon"]
    assert "déjà servi" in erreur(act(e, 0, type="sw_dragon", region=bord_non_montagne(e)))
    # Emissaire : l adversaire designe ne peut pas attaquer
    e2 = nouvelle(3)
    m = donner_peuple(e2, 0, "hordes", "emissaire", main=0)
    poser(e2, 0, m, 2)
    tour_de(e2, 0, "redeploiement")
    e2["tour"]["rassemble"] = True
    assert erreur(act(e2, 0, type="sw_redeployer", pions={"0": 2}, emissaire=1)) is None
    assert 0 in e2["j"][1]["interdits"]
    y = donner_peuple(e2, 1, "hordes", "aerien", main=20)
    tour_de(e2, 1, "conquete")
    assert "émissaire" in erreur(act(e2, 1, type="sw_conquerir", region=0))
    # Saboteur : -1 pion sur une voisine adverse
    e3 = nouvelle(3)
    s = donner_peuple(e3, 0, "hordes", "saboteur", main=0)
    v = donner_peuple(e3, 1, "hordes", "alpin", main=0)
    poser(e3, 0, s, 2)
    voisine = e3["carte"]["regions"][0]["voisins"][0]
    poser(e3, voisine, v, 2)
    tour_de(e3, 0, "redeploiement")
    e3["tour"]["rassemble"] = True
    assert erreur(act(e3, 0, type="sw_redeployer", pions={"0": 2}, sabotage=voisine)) is None
    assert e3["regions"][voisine]["pions"] == 1
    # Mecene : +6 au premier tour seulement
    e4 = nouvelle(3)
    mc = donner_peuple(e4, 0, "hordes", "mecene", main=0)
    poser(e4, 0, mc, 1)
    tour_de(e4, 0, "redeploiement")
    e4["tour"]["rassemble"] = True
    act(e4, 0, type="sw_redeployer", pions={"0": 1})
    assert e4["j"][0]["pieces"] == 1 + CFG["bonus"]["mecene"]
    # Souterrain : de montagne en montagne
    e5 = nouvelle(8)
    st = donner_peuple(e5, 0, "hordes", "souterrain", main=20)
    monts = [r["id"] for r in e5["carte"]["regions"] if r["terrain"] == "montagne"]
    loin = next((m2 for m2 in monts[1:] if m2 not in e5["carte"]["regions"][monts[0]]["voisins"]), None)
    poser(e5, monts[0], st, 2)
    tour_de(e5, 0, "conquete")
    if loin is not None:
        assert C._verifier_cible(e5, 0, loin)[1] == "tunnel"
    # Marins : un bord eloigne, une fois par tour
    e6 = nouvelle(8)
    mr = donner_peuple(e6, 0, "marins", "alpin", main=30)
    poser(e6, 0, mr, 1)
    bords = [r["id"] for r in e6["carte"]["regions"]
             if r["bord"] and r["id"] not in e6["carte"]["regions"][0]["voisins"] and r["id"] != 0]
    tour_de(e6, 0, "conquete")
    assert erreur(act(e6, 0, type="sw_conquerir", region=bords[-1])) is None and e6["tour"]["marin"]
    print("dragon, emissaire, saboteur, mecene, souterrain, marins : ok")


def tests_minuteur_et_fin():
    # Tours conseilles : base selon le nombre de joueurs (Realms), x facteur de la vitesse
    assert [C.sw_tours_conseilles(n, "soiree") for n in range(2, 9)] == [10, 10, 9, 8, 8, 7, 7]
    assert [C.sw_tours_conseilles(5, v) for v in ("flash", "express", "soiree", "marathon")] == [4, 6, 8, 10]
    assert C.sw_tours_conseilles(2, "flash") == 5 and C.sw_tours_conseilles(2, "marathon") == 13
    assert nouvelle(4)["tours_max"] == 9
    # L hote choisit ; un choix hors bornes est remplace par le conseille
    assert nouvelle(4, tours=12)["tours_max"] == 12
    assert nouvelle(4, tours=99)["tours_max"] == 9 and nouvelle(4, tours="x")["tours_max"] == 9
    e = nouvelle(2, vitesse="flash")
    assert e["tours_max"] == 5 and e["minuteur"] == 40
    t = 0
    tours = 0
    while e["phase"] == "jeu":
        t = e["echeance"] + 0.1
        C.sw_expirer(e, t)
        verifier(e)
        tours += 1
        assert tours < 100
    assert tours == 2 * 5
    res = e["resultat"]
    assert e["j"][res["dernier"]]["bu"] >= C._gorgees(e, CFG["gorgees"]["dernier_boit"])
    assert e["j"][res["gagnant"]]["distribue"] >= C._gorgees(e, CFG["gorgees"]["vainqueur_distribue"])
    assert C.sw_action(e, 0, {"type": "sw_fin_conquete"}, t)[0]["msg"]["type"] == "erreur"
    # Absent : 3 s au lieu du minuteur
    e2 = nouvelle(3)
    i = e2["joueur"]
    C.sw_joueur_parti(e2, i, 0)
    assert e2["echeance"] <= CFG["delai_absent"]
    C.sw_joueur_revenu(e2, i, 1)
    assert e2["echeance"] >= 1 + min(C.SW_DELAI_RETOUR, e2["minuteur"]) - 0.01
    # Vitesse conseillee a 7 joueurs
    assert nouvelle(7)["vitesse"] == CFG["vitesse_defaut_nombreux"]
    assert nouvelle(5)["vitesse"] == CFG["vitesse_defaut"]
    # Mode aleatoire
    e3 = nouvelle(4, aleatoire=True)
    assert len(e3["races"]) == CFG["aleatoire"]["races"] and len(e3["pouvoirs"]) == CFG["aleatoire"]["pouvoirs"]
    assert all(c["race"] in e3["races"] and c["pouvoir"] in e3["pouvoirs"] for c in e3["file"])
    print("minuteur, absents, fin, vitesses, mode aleatoire : ok")


def tests_vengeance():
    e = nouvelle(4, vengeance=True)
    for k in range(4):
        e["j"][k]["suite_pertes"] = 0
    e["j"][3]["suite_pertes"] = 2
    i = e["joueur"] if e["joueur"] != 3 else e["ordre"][1]
    tour_de(e, i, "redeploiement")
    donner_peuple(e, i, "hordes", "alpin", main=0)
    e["tour"]["victimes"] = {"3": 1}
    avant = e["j"][3]["distribue"]
    C._fin_tour(e, [], 1)
    assert e["j"][3]["distribue"] == avant + C._gorgees(e, CFG["gorgees"]["vengeance_distribue"])
    print("vengeance : ok")


# =============================================================
# PARTIES AU HASARD
# =============================================================
def coup_au_hasard(e, rng):
    i = e["joueur"]
    if rng.random() < 0.04:   # un autre joueur tente sa chance (refuse)
        return rng.randrange(e["nb_joueurs"]), {"type": rng.choice(list(C.SW_ACTIONS)), "region": rng.randrange(30)}
    if rng.random() < 0.02:
        return i, {"type": rng.choice(list(C.SW_ACTIONS)), "region": rng.choice([-1, "x", None, 999, 2.5]),
                   "rang": rng.choice([-1, 9, "a"]), "pions": rng.choice([None, [], {"99": 1}])}
    if rng.random() < 0.03:
        return rng.randrange(e["nb_joueurs"]), {"type": "sw_reaction", "emoji": rng.choice(C.SW_REACTIONS + ["x"])}
    etape = e["etape"]
    pub = C.sw_etat_public(e, 0)
    if etape == "choix":
        rangs = [k for k in range(len(e["file"])) if k <= e["j"][i]["pieces"]]
        return i, {"type": "sw_choisir", "rang": rng.choice(rangs) if rng.random() < 0.4 else 0}
    p = C._actif(e, i)
    if etape == "action" and rng.random() < 0.12:
        return i, {"type": "sw_declin"}
    if etape in ("action", "conquete"):
        poss = pub["possibles"]
        main = pub["main_dispo"]
        abordables = [r for r, x in poss.items() if x["cout"] <= main]
        trop_cheres = [r for r, x in poss.items() if x["cout"] > main]
        x = rng.random()
        if p["pouvoir"] == "dompteur" and not p["dragon"] and x < 0.2:
            return i, {"type": "sw_dragon", "region": rng.randrange(len(e["regions"]))}
        if etape == "action" and x < 0.05:
            miennes = C._regions_de(e, p["id"])
            if miennes:
                return i, {"type": "sw_abandonner", "region": rng.choice(miennes)}
        if p["pouvoir"] == "endurant" and e["tour"]["conquetes"] and x < 0.1:
            return i, {"type": "sw_declin"}
        if abordables and x < 0.8:
            return i, {"type": "sw_conquerir", "region": int(rng.choice(abordables))}
        if trop_cheres and not e["tour"]["de_utilise"] and x < 0.92:
            return i, {"type": "sw_tenter", "region": int(rng.choice(trop_cheres))}
        return i, {"type": "sw_fin_conquete"}
    # redeploiement
    miennes = C._regions_de(e, p["id"])
    total = sum(e["regions"][r]["pions"] for r in miennes) + p["main"]
    rep = {}
    if miennes:
        base = {r: 1 for r in miennes}
        reste = total - len(miennes)
        while reste > 0:
            base[rng.choice(miennes)] += 1
            reste -= 1
        if p["race"] == "nomades" and len(miennes) > 1 and rng.random() < 0.5:
            r0 = miennes[0]
            base[miennes[1]] += base[r0]
            base[r0] = 0
        rep = {str(r): v for r, v in base.items()}
        if rng.random() < 0.05:
            rep[str(miennes[0])] = rep[str(miennes[0])] + 1   # total faux : refuse
    m = {"type": "sw_redeployer", "pions": rep}
    if miennes and p["pouvoir"] == "batisseur":
        m["fortin"] = rng.choice(miennes)
    if miennes and p["pouvoir"] == "campeur":
        m["camp"] = rng.choice(miennes)
    if miennes and p["pouvoir"] == "gardien":
        m["gardiens"] = rng.sample(miennes, min(2, len(miennes)))
    if p["pouvoir"] == "saboteur":
        cibles = [r for r, reg in enumerate(e["regions"]) if reg["j"] not in (None, i) and reg["pions"] > 0
                  and any(r in e["carte"]["regions"][mm]["voisins"] for mm in miennes)]
        if cibles:
            m["sabotage"] = rng.choice(cibles)
    if p["pouvoir"] == "emissaire":
        autres = [k for k in range(e["nb_joueurs"]) if k != i and str(k) not in e["tour"]["victimes"]]
        if autres:
            m["emissaire"] = rng.choice(autres)
    if p["pouvoir"] == "endurant" and rng.random() < 0.3:
        m["declin"] = True
    return i, m


def partie(n, graine, options):
    rng = random.Random(graine)
    random.seed(graine)
    e = C.sw_initialiser(["J%d" % k for k in range(n)], 0, options)
    verifier(e)
    t = 0.0
    coups = 0
    while e["phase"] == "jeu":
        coups += 1
        assert coups < 6000, "partie figee"
        t += rng.random() * 3
        x = rng.random()
        if x < 0.01:
            k = rng.randrange(n)
            (C.sw_joueur_revenu if e["partis"][k] else C.sw_joueur_parti)(e, k, t)
        elif x < 0.04 or e["partis"][e["joueur"]]:
            t = max(t, e["echeance"] + 0.01)
            C.sw_expirer(e, t)
        else:
            i, m = coup_au_hasard(e, rng)
            ev = C.sw_action(e, i, m, t)
            for x2 in ev:
                json.dumps(x2)
        verifier(e)
    return e, coups


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    tests_cartes()
    tests_couts()
    tests_pertes()
    tests_gorgees_plafond()
    tests_file()
    tests_declin()
    tests_de()
    tests_redeploiement()
    tests_pouvoirs_speciaux()
    tests_minuteur_et_fin()
    tests_vengeance()
    vitesses = list(CFG["vitesses"]) + [None]
    total_coups = 0
    victoires = {}
    for g in range(nb):
        r = random.Random(g)
        n = r.randint(C.SW_JOUEURS_MIN, C.SW_JOUEURS_MAX)
        options = {"vitesse": vitesses[g % len(vitesses)], "aleatoire": g % 3 == 0, "vengeance": g % 2 == 0}
        e, coups = partie(n, g, options)
        total_coups += coups
        cl = e["resultat"]["classement"][0]
        p = C._peuple(e, e["j"][cl["joueur"]]["actif"]) or {}
        victoires[p.get("race", "-")] = victoires.get(p.get("race", "-"), 0) + 1
    print("%d parties au hasard, toutes finies (%d coups)" % (nb, total_coups))
    print("OK")


if __name__ == "__main__":
    main()
