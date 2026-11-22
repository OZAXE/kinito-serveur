"""
Essai du moteur du Loup-Garou, hors ligne : des centaines de parties jouees
au hasard (actions valides et absurdes, absents, delais depasses), de 4 a 15
joueurs, avec toutes sortes de compositions. On verifie qu aucune ne plante
ni ne se fige, que les roles des vivants et les actions de la nuit ne fuient
jamais dans l etat public, et que les regles tiennent (meute, protection,
potions, tir du Braconnier, vote, victoire, gorgees).
Usage : python tests/loupgarou_essai.py [nombre de parties]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import loupgarou as L

ROLES_SECRETS = set(L.LG_ROLES)


def verifier_composition():
    for n in range(L.LG_JOUEURS_MIN, L.LG_JOUEURS_MAX + 1):
        roles = L.lg_composition(n)
        assert len(roles) == n
        loups = roles.count("loup")
        assert 1 <= loups <= L.lg_loups_max(n) and loups < n - loups, (n, roles)
        for r in L.LG_SPECIAUX:
            assert roles.count(r) <= 1
        # Valeurs absurdes : toujours jouable.
        for loups_demandes in (0, -3, 99, "x", None, True, 2.7):
            for speciaux in (None, [], ["herboriste", "inconnu"], L.LG_SPECIAUX * 3, "videur"):
                r2 = L.lg_composition(n, loups_demandes, speciaux)
                assert len(r2) == n and 1 <= r2.count("loup") <= L.lg_loups_max(n), (n, loups_demandes, speciaux, r2)
    assert L.lg_composition(8).count("loup") == 2
    assert L.lg_composition(6).count("loup") == 1
    assert set(L.lg_composition(8)) == {"loup", "villageois", "cartomancienne", "videur", "herboriste", "braconnier"}
    assert L.lg_composition(5, 1, []) == ["loup"] + ["villageois"] * 4


def verifier_public(etat):
    pub = L.lg_etat_public(etat, 0)
    json.dumps(pub)
    n = etat["nb_joueurs"]
    for i in range(n):
        if etat["vivants"][i] and etat["phase"] != "fin":
            assert pub["roles"][i] is None, "le role d un vivant fuit"
        else:
            assert pub["roles"][i] == etat["roles"][i]
    texte = json.dumps(pub)
    for cle in ("meute", "visions", "protege", "potions", "victime", "\"actions\"", "suspects"):
        assert cle not in texte, f"{cle} fuit dans l etat public"
    if etat["phase"] == "debat":
        assert "cible" not in json.dumps(pub["a_vote"])
    if etat["phase"] == "fin":
        assert etat["echeance"] is None and etat["final"]
    else:
        assert etat["echeance"] is not None, f"pas d echeance en phase {etat['phase']}"
    assert all(g >= 0 for g in etat["gorgees"])
    assert len(etat["morts"]) == etat["vivants"].count(False)


def verifier_vues(etat):
    n = etat["nb_joueurs"]
    for i in range(n):
        v = L.lg_vue(etat, i)
        json.dumps(v)
        assert v["role"] == etat["roles"][i]
        fantome = not etat["vivants"][i] or etat["phase"] == "fin"
        if fantome:
            assert v["tous_roles"] == etat["roles"]
        else:
            assert v["tous_roles"] is None, "un vivant voit tous les roles"
            if etat["roles"][i] != "loup":
                assert v["loups"] is None and v["meute"] is None, "un villageois voit la meute"
            if etat["roles"][i] != "cartomancienne":
                assert v["visions"] is None
            if etat["roles"][i] != "herboriste":
                assert v["potions"] is None and v["victime"] is None
    assert L.lg_vue(etat, n) is None


def jouer(rnd, n):
    noms = [f"J{k}" for k in range(n)]
    loups = rnd.choice([None, None, 1, 2, 3, 9])
    speciaux = rnd.choice([None, None, [], list(L.LG_SPECIAUX), rnd.sample(L.LG_SPECIAUX, rnd.randint(0, 4))])
    t = 1000.0
    etat = L.lg_initialiser(noms, t, loups=loups, speciaux=speciaux, alea=rnd)
    roles0 = list(etat["roles"])
    pas = 0
    vus = set()
    while etat["phase"] != "fin":
        pas += 1
        assert pas < 4000, "partie figee"
        verifier_public(etat)
        if pas % 5 == 0:
            verifier_vues(etat)
        vus.add(etat["phase"])
        ph = etat["phase"]
        # Departs et retours
        if rnd.random() < 0.02:
            j = rnd.randrange(n)
            if etat["partis"][j]:
                L.lg_joueur_revenu(etat, j, t)
            else:
                L.lg_joueur_parti(etat, j, t)
        if rnd.random() < 0.08:
            t = max(t, etat["echeance"] or t) + 0.1
            L.lg_expirer(etat, t, rnd)
            continue
        i = rnd.randrange(n)
        vivants = [k for k in range(n) if etat["vivants"][k]]
        r = rnd.random()
        if r < 0.05:
            msg = {"type": rnd.choice(list(L.LG_ACTIONS)), "cible": rnd.choice([None, -1, n, "x", 1.5, True, rnd.randrange(n)]),
                   "soin": rnd.choice([True, False, "oui"]), "poison": rnd.choice([None, -1, n, rnd.randrange(n)])}
            L.lg_action(etat, i, msg, t, rnd)
        elif ph == "role":
            L.lg_action(etat, i, {"type": "lg_pret"}, t, rnd)
        elif ph == "nuit":
            avant = {k: v for k, v in etat["actions"].items()}
            role = etat["roles"][i]
            if role == "loup":
                # La meute finit souvent par s entendre.
                proies = [k for k in vivants if etat["roles"][k] != "loup"]
                choix = etat["meute"].get(str(rnd.choice(L._loups_vivants(etat)))) if rnd.random() < 0.6 else None
                cible = choix if choix is not None else rnd.choice(proies or vivants)
            else:
                cible = rnd.choice(vivants)
            ev = L.lg_action(etat, i, {"type": "lg_nuit", "cible": cible}, t, rnd)
            if role == "cartomancienne" and etat["vivants"][i] and str(i) not in avant and any(e["msg"]["type"] == "lg_vision" for e in ev):
                vision = [e for e in ev if e["msg"]["type"] == "lg_vision"][0]
                assert vision["a"] == i and vision["msg"]["role"] == roles0[cible]
            for e in ev:
                if e["msg"]["type"] in ("lg_meute", "lg_vision"):
                    assert e["a"] is not None, "une action de nuit part a tout le monde"
                    if e["msg"]["type"] == "lg_meute":
                        assert roles0[e["a"]] == "loup", "un non-loup recoit la meute"
        elif ph == "potion":
            h = L._role_vivant(etat, "herboriste")
            if h is not None:
                msg = {"type": "lg_potion", "soin": rnd.random() < 0.5,
                       "poison": rnd.choice([None, None, rnd.choice(vivants)])}
                L.lg_action(etat, h, msg, t, rnd)
            else:
                t = etat["echeance"] + 0.1
                L.lg_expirer(etat, t, rnd)
        elif ph == "tir":
            tireur = etat["tireur"]
            assert roles0[tireur] == "braconnier" and not etat["vivants"][tireur]
            if rnd.random() < 0.8:
                L.lg_action(etat, tireur, {"type": "lg_tir", "cible": rnd.choice(vivants)}, t, rnd)
            else:
                t = etat["echeance"] + 0.1
                L.lg_expirer(etat, t, rnd)
        elif ph == "debat":
            cible = rnd.choice(vivants + [None])
            L.lg_action(etat, i, {"type": "lg_voter", "cible": cible}, t, rnd)
        elif ph in ("aube", "verdict"):
            L.lg_action(etat, i, {"type": "lg_suivant"}, t, rnd)
        t += rnd.random() * 2
    # Fin
    verifier_public(etat)
    verifier_vues(etat)
    f = etat["final"]
    assert etat["roles"] == roles0, "les roles ont change en cours de partie"
    loups_vivants = [k for k in range(n) if etat["vivants"][k] and roles0[k] == "loup"]
    autres = [k for k in range(n) if etat["vivants"][k] and roles0[k] != "loup"]
    if f["vainqueur"] == "village":
        assert not loups_vivants
        assert set(f["gagnants"]) == {k for k in range(n) if roles0[k] != "loup"}
    else:
        assert len(loups_vivants) >= len(autres) and loups_vivants
        assert set(f["gagnants"]) == {k for k in range(n) if roles0[k] == "loup"}
    assert {p["joueur"] for p in f["perdants"]} == set(range(n)) - set(f["gagnants"])
    return f["vainqueur"], vus


def scenario_regles():
    """Une partie dirigee, pour verifier chaque regle une par une."""
    rnd = random.Random(1)
    noms = ["Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Hal"]
    etat = L.lg_initialiser(noms, 0, loups=2, speciaux=list(L.LG_SPECIAUX), alea=rnd)
    # Roles imposes pour le scenario.
    etat["roles"] = ["loup", "loup", "cartomancienne", "videur", "herboriste", "braconnier", "villageois", "villageois"]
    etat["composition"] = L._compte_roles(etat["roles"])
    t = 0
    for i in range(8):
        L.lg_action(etat, i, {"type": "lg_pret"}, t)
    assert etat["phase"] == "nuit" and etat["nuit"] == 1
    # Un loup ne devore pas un loup ; un villageois ne se designe pas.
    assert L.lg_action(etat, 0, {"type": "lg_nuit", "cible": 1}, t)[0]["msg"]["type"] == "erreur"
    assert L.lg_action(etat, 6, {"type": "lg_nuit", "cible": 6}, t)[0]["msg"]["type"] == "erreur"
    # Les loups ne sont pas d accord : la nuit attend.
    L.lg_action(etat, 0, {"type": "lg_nuit", "cible": 6}, t)
    L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 7}, t)
    ev = L.lg_action(etat, 2, {"type": "lg_nuit", "cible": 0}, t)
    assert ev[0]["msg"] == {"type": "lg_vision", "nuit": 1, "cible": 0, "role": "loup"} and ev[0]["a"] == 2
    assert L.lg_action(etat, 2, {"type": "lg_nuit", "cible": 1}, t)[0]["msg"]["type"] == "erreur", "une seule vision"
    L.lg_action(etat, 3, {"type": "lg_nuit", "cible": 6}, t)     # le Videur protege Gus
    for i in (4, 5, 6, 7):
        L.lg_action(etat, i, {"type": "lg_nuit", "cible": 0 if i != 0 else 1}, t)
    assert etat["phase"] == "nuit", "la meute n est pas d accord"
    L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 6}, t)     # d accord sur Gus
    assert etat["phase"] == "potion" and etat["victime"] is None, "Gus protege par le Videur"
    v = L.lg_vue(etat, 4)
    assert v["victime"] is None and v["potions"] == {"soin": True, "poison": True}
    assert L.lg_action(etat, 4, {"type": "lg_potion", "soin": True}, t)[0]["msg"]["type"] == "erreur", "rien a soigner"
    L.lg_action(etat, 4, {"type": "lg_potion", "soin": False, "poison": None}, t)
    assert etat["phase"] == "aube" and etat["annonce"]["morts"] == [] and all(etat["vivants"])
    assert etat["suspects"][4] == 1 and etat["suspects"][6] == 1, "flair compte"
    for i in range(8):
        L.lg_action(etat, i, {"type": "lg_suivant"}, t)
    assert etat["phase"] == "debat"
    # Vote : Hal (villageois) elimine par 4 voix contre 2 et 2 blancs.
    votes = {0: 7, 1: 7, 2: 0, 3: 0, 4: 7, 5: 7, 6: None, 7: None}
    for i, c in votes.items():
        L.lg_action(etat, i, {"type": "lg_voter", "cible": c}, t)
    a = etat["annonce"]
    assert etat["phase"] == "verdict" and a["elimine"] == 7 and not etat["vivants"][7]
    assert etat["gorgees"][7] == L._G["elimine"]
    assert sorted(x["joueur"] for x in a["accusateurs"]) == [0, 1, 4, 5]
    assert L.lg_etat_public(etat, t)["roles"][7] == "villageois"
    for i in range(8):
        L.lg_action(etat, i, {"type": "lg_suivant"}, t)
    assert etat["phase"] == "nuit" and etat["nuit"] == 2
    # Le Videur ne protege pas deux fois le meme.
    assert L.lg_action(etat, 3, {"type": "lg_nuit", "cible": 6}, t)[0]["msg"]["type"] == "erreur"
    L.lg_action(etat, 3, {"type": "lg_nuit", "cible": 3}, t)
    L.lg_action(etat, 0, {"type": "lg_nuit", "cible": 5}, t)
    L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 5}, t)
    for i in (2, 4, 5, 6):
        L.lg_action(etat, i, {"type": "lg_nuit", "cible": 1}, t)
    assert etat["phase"] == "potion" and etat["victime"] == 5
    assert L.lg_vue(etat, 4)["victime"] == 5 and L.lg_vue(etat, 2)["victime"] is None
    # Pas de soin : le Braconnier meurt, et empoisonne Bob (loup).
    L.lg_action(etat, 4, {"type": "lg_potion", "soin": False, "poison": 1}, t)
    assert etat["phase"] == "aube" and not etat["vivants"][5] and not etat["vivants"][1]
    assert etat["potions"] == {"soin": True, "poison": False}
    for i in range(8):
        L.lg_action(etat, i, {"type": "lg_suivant"}, t)
    assert etat["phase"] == "tir" and etat["tireur"] == 5
    assert L.lg_action(etat, 2, {"type": "lg_tir", "cible": 0}, t)[0]["msg"]["type"] == "erreur"
    L.lg_action(etat, 5, {"type": "lg_tir", "cible": 0}, t)       # il abat Ana, dernier loup
    assert etat["phase"] == "aube" and etat["annonce"]["tir"] == {"tireur": 5, "cible": 0}
    for i in range(8):
        L.lg_action(etat, i, {"type": "lg_suivant"}, t)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "village"
    assert etat["gorgees"][0] == L._G["accusateur_innocent"] + L._G["abattu"] + L._G["perdant"]
    assert etat["final"]["meilleur_flair"] == [4, 5, 6]


def scenario_loups_gagnent():
    etat = L.lg_initialiser(["A", "B", "C", "D"], 0, loups=1, speciaux=[], alea=random.Random(3))
    etat["roles"] = ["loup", "villageois", "villageois", "villageois"]
    etat["composition"] = L._compte_roles(etat["roles"])
    L.lg_expirer(etat, 100)
    assert etat["phase"] == "nuit"
    L.lg_action(etat, 0, {"type": "lg_nuit", "cible": 1}, 100)
    for i in (1, 2, 3):
        L.lg_action(etat, i, {"type": "lg_nuit", "cible": 0 if i != 0 else 1}, 100)
    assert etat["phase"] == "aube" and not etat["vivants"][1], "sans Herboriste, l aube suit la nuit"
    L.lg_expirer(etat, 1000)
    assert etat["phase"] == "debat"
    L.lg_action(etat, 0, {"type": "lg_voter", "cible": 2}, 1000)
    L.lg_action(etat, 3, {"type": "lg_voter", "cible": 2}, 1000)
    L.lg_action(etat, 2, {"type": "lg_voter", "cible": 0}, 1000)
    assert etat["phase"] == "verdict" and etat["annonce"]["elimine"] == 2
    L.lg_expirer(etat, 5000)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "loups"
    assert etat["final"]["gagnants"] == [0]


def scenario_egalite_et_absents():
    etat = L.lg_initialiser(["A", "B", "C", "D", "E"], 0, loups=1, speciaux=["herboriste"], alea=random.Random(5))
    etat["roles"] = ["loup", "herboriste", "villageois", "villageois", "villageois"]
    etat["composition"] = L._compte_roles(etat["roles"])
    L.lg_expirer(etat, 100)
    # Herboriste partie : la potion dure quand meme quelques secondes.
    L.lg_joueur_parti(etat, 1, 100)
    for i in (0, 2, 3, 4):
        L.lg_action(etat, i, {"type": "lg_nuit", "cible": 2 if i == 0 else 0}, 100)
    assert etat["phase"] == "potion"
    d = etat["echeance"] - 100
    assert L._D["potion_vide_min"] <= d <= L._D["potion_vide_max"]
    L.lg_expirer(etat, 200)
    assert etat["phase"] == "aube" and not etat["vivants"][2]
    L.lg_expirer(etat, 300)
    assert etat["phase"] == "debat"
    # Egalite 1-1 : personne.
    L.lg_action(etat, 0, {"type": "lg_voter", "cible": 3}, 300)
    L.lg_action(etat, 3, {"type": "lg_voter", "cible": 0}, 300)
    assert etat["phase"] == "debat"
    L.lg_action(etat, 4, {"type": "lg_voter", "cible": None}, 300)
    assert etat["phase"] == "verdict", "l absente ne bloque pas le vote"
    assert etat["annonce"]["elimine"] is None and etat["annonce"]["egalite"]
    assert etat["annonce"]["sans_vote"] == []


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    verifier_composition()
    scenario_regles()
    scenario_loups_gagnent()
    scenario_egalite_et_absents()
    rnd = random.Random(2026)
    bilan = {"village": 0, "loups": 0}
    phases = set()
    for k in range(nb):
        n = rnd.randint(L.LG_JOUEURS_MIN, L.LG_JOUEURS_MAX)
        vainqueur, vus = jouer(rnd, n)
        bilan[vainqueur] += 1
        phases |= vus
    assert phases >= {"role", "nuit", "potion", "aube", "tir", "debat", "verdict"}, phases
    print(f"OK : {nb} parties, village {bilan['village']}, loups {bilan['loups']}, phases {sorted(phases)}")


if __name__ == "__main__":
    main()
