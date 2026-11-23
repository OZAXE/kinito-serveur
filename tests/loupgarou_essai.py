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


def nb_loups(roles):
    return sum(1 for r in roles if r in L.LG_ROLES_LOUPS)


def verifier_composition():
    for n in range(L.LG_JOUEURS_MIN, L.LG_JOUEURS_MAX + 1):
        roles = L.lg_composition(n)
        assert len(roles) == n
        loups = nb_loups(roles)
        assert 1 <= loups <= L.lg_loups_max(n) and loups < n - loups, (n, roles)
        for r in L.LG_SPECIAUX:
            assert roles.count(r) <= L.LG_GROUPES.get(r, 1)
        # Valeurs absurdes : toujours jouable.
        for loups_demandes in (0, -3, 99, "x", None, True, 2.7):
            for speciaux in (None, [], ["herboriste", "inconnu"], L.LG_SPECIAUX * 3, "videur"):
                r2 = L.lg_composition(n, loups_demandes, speciaux)
                assert len(r2) == n and 1 <= nb_loups(r2) <= L.lg_loups_max(n), (n, loups_demandes, speciaux, r2)
                for r in L.LG_GROUPES:
                    assert r2.count(r) in (0, L.LG_GROUPES[r])
                if "loup_blanc" in r2:
                    assert nb_loups(r2) >= 2
    assert L.lg_composition(8).count("loup") == 2
    assert L.lg_composition(6).count("loup") == 1
    assert set(L.lg_composition(8)) == {"loup", "villageois", "cartomancienne", "videur", "herboriste", "braconnier"}
    assert L.lg_composition(5, 1, []) == ["loup"] + ["villageois"] * 4
    assert len(L.lg_composition(20)) == 20 and nb_loups(L.lg_composition(20)) == 5
    assert "loup_blanc" not in L.lg_composition(5, 1, ["loup_blanc"])
    assert L.lg_composition(6, 1, ["soeur", "frere"]).count("frere") == 3
    assert L.lg_composition(5, 1, ["frere", "soeur"]).count("soeur") == 2 and "frere" not in L.lg_composition(5, 1, ["frere", "soeur"])
    assert L.LG_NOMS_ROLES["cartomancienne"] == "Voyante" and L.LG_NOMS_ROLES["herboriste"] == "Sorcière"


def verifier_public(etat):
    pub = L.lg_etat_public(etat, 0)
    json.dumps(pub)
    n = etat["nb_joueurs"]
    for i in range(n):
        if etat["vivants"][i] and etat["phase"] != "fin" and etat["idiot_revele"] != i:
            assert pub["roles"][i] is None, "le role d un vivant fuit"
        else:
            assert pub["roles"][i] == etat["roles"][i]
    texte = json.dumps(pub)
    for cle in ("meute", "visions", "protege", "potions", "victime", '"actions"', "suspects", '"convertis"',
                "amoureux", "flairs", "couple", "modele", "solo"):
        if cle in ("amoureux", "\"convertis\"") and etat["phase"] == "fin":
            continue
        assert cle not in texte, f"{cle} fuit dans l etat public"
    if etat["phase"] in ("debat", "election"):
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
        role = etat["roles"][i]
        fantome = not etat["vivants"][i] or etat["phase"] == "fin"
        if fantome:
            assert v["tous_roles"] == etat["roles"]
        else:
            assert v["tous_roles"] is None, "un vivant voit tous les roles"
            if not L._est_loup(etat, i):
                assert v["loups"] is None and v["meute"] is None, "un villageois voit la meute"
            if role != "cartomancienne":
                assert v["visions"] is None
            if role != "herboriste":
                assert v["potions"] is None and v["victime"] is None
            if role != "renard":
                assert v["flairs"] is None
            if v["amoureux"] is not None:
                assert i in etat["amoureux"] or role == "cupidon", "un tiers voit les amoureux"
            if role not in L.LG_GROUPES:
                assert v["famille"] is None
    assert L.lg_vue(etat, n) is None


def jouer(rnd, n):
    noms = [f"J{k}" for k in range(n)]
    loups = rnd.choice([None, None, 1, 2, 3, 9])
    speciaux = rnd.choice([None, None, None, [], list(L.LG_SPECIAUX), rnd.sample(L.LG_SPECIAUX, rnd.randint(0, 10))])
    t = 1000.0
    etat = L.lg_initialiser(noms, t, loups=loups, speciaux=speciaux, alea=rnd, capitaine=rnd.choice([None, True, False]))
    roles0 = list(etat["roles"])
    pas = 0
    vus = set()
    while etat["phase"] != "fin":
        pas += 1
        assert pas < 6000, "partie figee"
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
                   "cible2": rnd.choice([None, -1, rnd.randrange(n)]), "solo": rnd.choice([None, -1, rnd.randrange(n)]),
                   "infecter": rnd.choice([True, False, "oui"]), "camp": rnd.choice(["loups", "village", 3]),
                   "soin": rnd.choice([True, False, "oui"]), "poison": rnd.choice([None, -1, n, rnd.randrange(n)])}
            L.lg_action(etat, i, msg, t, rnd)
        elif ph == "role":
            L.lg_action(etat, i, {"type": "lg_pret", "camp": rnd.choice(["loups", "village"])}, t, rnd)
        elif ph == "nuit":
            avant = {k: v for k, v in etat["actions"].items()}
            genre = L._type_nuit(etat, i) if etat["vivants"][i] else None
            msg = {"type": "lg_nuit"}
            if genre == "meute":
                # La meute finit souvent par s entendre.
                proies = [k for k in vivants if not L._est_loup(etat, k)]
                choix = etat["meute"].get(str(rnd.choice(L._loups_vivants(etat)))) if rnd.random() < 0.6 else None
                msg["cible"] = choix if choix is not None else rnd.choice(proies or vivants)
                if etat["roles"][i] == "loup_blanc" and rnd.random() < 0.5:
                    msg["solo"] = rnd.choice([None] + L._loups_vivants(etat))
                if etat["roles"][i] == "infect":
                    msg["infecter"] = rnd.random() < 0.4
            else:
                msg["cible"] = rnd.choice(vivants)
                msg["cible2"] = rnd.choice(vivants)
                if genre == "pyromane" and rnd.random() < 0.3:
                    msg["allumer"] = True
            ev = L.lg_action(etat, i, msg, t, rnd)
            if genre == "vision" and str(i) not in avant and any(e["msg"]["type"] == "lg_vision" for e in ev):
                vision = [e for e in ev if e["msg"]["type"] == "lg_vision"][0]
                assert vision["a"] == i and vision["msg"]["role"] == roles0[msg["cible"]]
            for e in ev:
                if e["msg"]["type"] in ("lg_meute", "lg_vision", "lg_renard", "lg_amoureux", "lg_converti", "lg_petite_fille"):
                    assert e["a"] is not None, "une action de nuit part a tout le monde"
                    if e["msg"]["type"] in ("lg_meute", "lg_petite_fille"):
                        assert L._est_loup(etat, e["a"]), "un non-loup recoit la meute"
                if e["msg"]["type"] == "lg_morsure":
                    assert e["a"] is not None and etat["vampires"][e["a"]], "un non-vampire voit la morsure"
        elif ph == "potion":
            h = L._role_vivant(etat, "herboriste")
            if h is not None:
                msg = {"type": "lg_potion", "soin": rnd.random() < 0.5,
                       "poison": rnd.choice([None, None, rnd.choice(vivants)])}
                L.lg_action(etat, h, msg, t, rnd)
            else:
                t = etat["echeance"] + 0.1
                L.lg_expirer(etat, t, rnd)
        elif ph in ("tir", "succession"):
            tireur = etat["tireur"]
            assert not etat["vivants"][tireur]
            if ph == "tir":
                assert roles0[tireur] == "braconnier"
            if rnd.random() < 0.8:
                L.lg_action(etat, tireur, {"type": "lg_tir", "cible": rnd.choice(vivants)}, t, rnd)
            else:
                t = etat["echeance"] + 0.1
                L.lg_expirer(etat, t, rnd)
        elif ph in ("debat", "election"):
            cible = rnd.choice(vivants + [None])
            L.lg_action(etat, i, {"type": "lg_voter", "cible": cible}, t, rnd)
        elif ph in ("aube", "verdict"):
            L.lg_action(etat, i, {"type": "lg_suivant"}, t, rnd)
        t += rnd.random() * 2
    # Fin
    verifier_public(etat)
    verifier_vues(etat)
    f = etat["final"]
    assert etat["roles"] == roles0, "les cartes ont change en cours de partie"
    gagnants = set(f["gagnants"])
    vivants = [k for k in range(n) if etat["vivants"][k]]
    loups_v = [k for k in vivants if L._est_loup(etat, k)]
    v = f["vainqueur"]
    camps = {L._camp(etat, k) for k in vivants}
    if v == "village":
        assert camps == {"village"} and gagnants and all(not L._est_loup(etat, g) for g in gagnants)
        assert all(not etat["vampires"][g] and roles0[g] not in ("pyromane", "assassin") for g in gagnants)
    elif v == "vampires":
        vamp = [k for k in vivants if etat["vampires"][k]]
        assert camps <= {"vampires", "village"} and vamp and len(vamp) >= len(vivants) - len(vamp)
        assert all(etat["vampires"][g] for g in gagnants)
    elif v in ("pyromane", "assassin"):
        assert camps == {v} and gagnants == {roles0.index(v)}
    elif v == "loups":
        assert loups_v and len(loups_v) >= len(vivants) - len(loups_v)
        assert all(L._est_loup(etat, g) and roles0[g] != "loup_blanc" for g in gagnants)
    elif v == "loup_blanc":
        assert vivants == [roles0.index("loup_blanc")] and gagnants == set(vivants)
    elif v == "amoureux":
        assert set(vivants) <= set(etat["amoureux"]) and gagnants == set(etat["amoureux"])
    elif v == "ange":
        assert gagnants == {roles0.index("ange")} and not etat["vivants"][roles0.index("ange")]
    else:
        assert v == "personne" and not vivants and not gagnants
    assert {p["joueur"] for p in f["perdants"]} == set(range(n)) - gagnants
    return v, vus


def scenario_regles():
    """Une partie dirigee, pour verifier chaque regle une par une."""
    rnd = random.Random(1)
    noms = ["Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Hal"]
    etat = L.lg_initialiser(noms, 0, loups=2, speciaux=list(L.LG_SPECIAUX), alea=rnd)
    # Roles imposes pour le scenario.
    etat["roles"] = ["loup", "loup", "cartomancienne", "videur", "herboriste", "braconnier", "villageois", "villageois"]
    etat["vampires"] = [r == "vampire" for r in etat["roles"]]
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
    etat["vampires"] = [r == "vampire" for r in etat["roles"]]
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
    etat["vampires"] = [r == "vampire" for r in etat["roles"]]
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


# =============================================================
# ROLES AJOUTES : un petit scenario dirige par role
# =============================================================
def partie(roles, capitaine=False, graine=7):
    noms = [f"P{k}" for k in range(len(roles))]
    etat = L.lg_initialiser(noms, 0, loups=1, speciaux=[], alea=random.Random(graine), capitaine=capitaine)
    etat["roles"] = list(roles)
    etat["vampires"] = [r == "vampire" for r in etat["roles"]]
    etat["composition"] = L._compte_roles(etat["roles"])
    etat["infection"] = "infect" in roles
    return etat


def tous_prets(etat, camps=None):
    for i in range(etat["nb_joueurs"]):
        msg = {"type": "lg_pret"}
        if camps and i in camps:
            msg["camp"] = camps[i]
        L.lg_action(etat, i, msg, 0)
    assert etat["phase"] == "nuit"


def faire_nuit(etat, choix, rnd=None):
    """choix : index -> cible ou message lg_nuit. Les autres vivants designent un suspect quelconque."""
    rnd = rnd or random.Random(0)
    n = etat["nb_joueurs"]
    for i in range(n):
        if not etat["vivants"][i] or etat["phase"] != "nuit":
            continue
        c = choix.get(i)
        if c is None:
            assert L._type_nuit(etat, i) not in ("meute",), f"loup {i} sans consigne"
            autres = [k for k in range(n) if etat["vivants"][k] and k != i]
            c = autres[0]
        msg = dict(c) if isinstance(c, dict) else {"cible": c}
        msg["type"] = "lg_nuit"
        ev = L.lg_action(etat, i, msg, 0, rnd)
        assert not (ev and ev[0]["msg"]["type"] == "erreur"), (i, msg, ev)
    if etat["phase"] == "potion":
        h = L._role_vivant(etat, "herboriste")
        if h is not None:
            L.lg_action(etat, h, {"type": "lg_potion", "soin": False, "poison": None}, 0)
        else:
            L.lg_expirer(etat, etat["echeance"] + 1, rnd)
    assert etat["phase"] in ("aube", "fin"), etat["phase"]


def suite(etat):
    for i in range(etat["nb_joueurs"]):
        L.lg_action(etat, i, {"type": "lg_suivant"}, 0)


def voter(etat, votes):
    for i in range(etat["nb_joueurs"]):
        if etat["vivants"][i] and etat["phase"] in ("debat", "election") and etat["idiot_revele"] != i:
            L.lg_action(etat, i, {"type": "lg_voter", "cible": votes.get(i)}, 0)


def scenario_cupidon_et_couple_mixte():
    # 0 loup, 1 cupidon, 2..5 villageois
    etat = partie(["loup", "cupidon", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    assert L.lg_vue(etat, 1)["type_nuit"] == "cupidon"
    assert L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 0, "cible2": 0}, 0)[0]["msg"]["type"] == "erreur"
    faire_nuit(etat, {0: 4, 1: {"cible": 0, "cible2": 2}})
    assert etat["amoureux"] == [0, 2]
    assert L.lg_vue(etat, 2)["amoureux"] == [0, 2] and L.lg_vue(etat, 3)["amoureux"] is None
    assert L._camp(etat, 0) == "amoureux" and L._camp(etat, 2) == "amoureux"
    assert not etat["vivants"][4]
    suite(etat)
    # Le village elimine l amoureux villageois : le loup meurt de chagrin, le village gagne.
    voter(etat, {0: 3, 1: 2, 3: 2, 5: 2, 2: 3})
    a = etat["annonce"]
    assert a["elimine"] == 2 and not etat["vivants"][0]
    assert any(m["joueur"] == 0 and m["cause"] == "chagrin" for m in a["morts"])
    suite(etat)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "village"
    # Couple mixte seul en vie : il gagne.
    etat = partie(["loup", "cupidon", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    faire_nuit(etat, {0: 3, 1: {"cible": 0, "cible2": 2}})
    for k in (1, 4):
        etat["vivants"][k] = False
        etat["morts"].append({"joueur": k, "role": etat["roles"][k], "cause": "devore", "boit": 0})
    suite(etat)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "amoureux" and etat["final"]["gagnants"] == [0, 2]


def scenario_ancien_et_pouvoirs():
    # 0 loup, 1 ancien, 2 voyante, 3 chasseur, 4..6 villageois
    etat = partie(["loup", "ancien", "cartomancienne", "braconnier", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    faire_nuit(etat, {0: 1, 2: 0})
    assert etat["vivants"][1] and not etat["ancien_vie"], "l Ancien resiste a la premiere attaque"
    assert etat["annonce"]["morts"] == []
    suite(etat)
    voter(etat, {0: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 1: 0})
    assert not etat["vivants"][1] and etat["pouvoirs_perdus"]
    suite(etat)
    assert etat["phase"] == "nuit" and L._type_nuit(etat, 2) == "suspect", "la Voyante a perdu son don"
    faire_nuit(etat, {0: 3})
    assert not etat["vivants"][3]
    suite(etat)
    assert etat["phase"] == "debat", "le Chasseur sans pouvoir ne tire pas"


def scenario_idiot_bouc_corbeau():
    # 0 loup, 1 idiot, 2 bouc, 3 corbeau, 4..6 villageois
    etat = partie(["loup", "idiot", "bouc", "corbeau", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    assert L._type_nuit(etat, 3) == "corbeau"
    faire_nuit(etat, {0: 6, 3: 5})
    assert etat["annonce"]["corbeau"] == 5 and L.lg_etat_public(etat, 0)["corbeau"] == 5
    suite(etat)
    # Une seule voix contre P5, plus les 2 du Corbeau : 3, contre 2 pour P0.
    voter(etat, {0: 1, 1: 0, 2: 4, 3: 0, 4: 5, 5: 2})
    a = etat["annonce"]
    assert a["compte"][5] == 3 and a["compte"][0] == 2 and a["elimine"] == 5 and not etat["vivants"][5]
    suite(etat)
    faire_nuit(etat, {0: 4, 3: 4})        # le Corbeau designe la victime : sans effet
    assert etat["corbeau_jour"] is None
    suite(etat)
    # L Idiot elimine : epargne, devoile, il ne vote plus.
    voter(etat, {0: 1, 2: 1, 3: 1, 1: 0})
    a = etat["annonce"]
    assert a["idiot"] == 1 and etat["vivants"][1] and etat["idiot_revele"] == 1
    assert L.lg_etat_public(etat, 0)["roles"][1] == "idiot"
    suite(etat)
    faire_nuit(etat, {0: 3, 3: 1})
    etat["corbeau_jour"] = None           # on isole la regle du Bouc
    suite(etat)
    assert L.lg_action(etat, 1, {"type": "lg_voter", "cible": 0}, 0)[0]["msg"]["type"] == "erreur"
    # Egalite 1-1 (P0 et P2 se visent) : le Bouc Emissaire part.
    voter(etat, {0: 2, 2: 0})
    a = etat["annonce"]
    assert a["egalite"] and a["bouc"] == 2 and not etat["vivants"][2]


def scenario_renard_montreur_chevalier():
    # Sieges : 0 loup, 1 montreur, 2 renard, 3 chevalier, 4 loup, 5..8 villageois
    etat = partie(["loup", "montreur", "renard", "chevalier", "loup", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    ev = []
    faire_nuit(etat, {0: 3, 4: 3, 2: 6})
    # Le renard a flaire P6 et ses voisins (5 et 7) : pas de loup, il perd son flair.
    assert etat["flairs"][-1]["groupe"] == [6, 7, 5] and etat["flairs"][-1]["loup"] is False
    assert not etat["renard_actif"]
    # Le Chevalier devore : le premier loup a sa gauche (P4) mourra a l aube suivante.
    assert not etat["vivants"][3] and etat["rouille"] == 4
    assert etat["annonce"]["grogne"] is True, "le montreur est assis a cote de P0"
    suite(etat)
    voter(etat, {})
    suite(etat)
    assert L._type_nuit(etat, 2) == "suspect"
    faire_nuit(etat, {0: 8, 4: 8})
    assert not etat["vivants"][4] and any(m["cause"] == "rouille" for m in etat["annonce"]["morts"])


def scenario_infect_enfant_loup_blanc():
    # 0 infect, 1 loup blanc, 2 enfant sauvage, 3 voyante, 4..8 villageois
    etat = partie(["infect", "loup_blanc", "enfant_sauvage", "cartomancienne", "villageois", "villageois", "villageois",
                   "villageois", "villageois"])
    tous_prets(etat)
    assert L._type_nuit(etat, 2) == "modele"
    assert L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 4, "solo": 0}, 0)[0]["msg"]["type"] == "erreur", "nuit impaire"
    faire_nuit(etat, {0: {"cible": 4, "infecter": True}, 1: 4, 2: 5, 3: 0})
    assert etat["vivants"][4] and etat["convertis"][4] and not etat["infection"]
    assert L.lg_vue(etat, 4)["loups"] == [0, 1, 4] and L._type_nuit(etat, 4) == "meute"
    assert etat["modele"] == 5
    suite(etat)
    voter(etat, {0: 5, 1: 5, 4: 5, 2: 5, 3: 5})
    assert not etat["vivants"][5] and etat["convertis"][2], "l Enfant Sauvage devient loup"
    suite(etat)
    assert L.lg_vue(etat, 1)["solo_possible"] is True
    faire_nuit(etat, {0: 6, 1: {"cible": 6, "solo": 0}, 2: 6, 4: 6})
    morts = {m["joueur"] for m in etat["annonce"]["morts"]}
    assert morts == {0, 6}, morts
    # Le Loup Blanc empeche la victoire anticipee de la meute.
    assert L._camp(etat, 1) == "loup_blanc"


def scenario_ange_chien_capitaine():
    # Ange elimine au premier vote : il gagne seul.
    etat = partie(["loup", "ange", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    faire_nuit(etat, {0: 4})
    suite(etat)
    voter(etat, {0: 1, 2: 1, 3: 1})
    suite(etat)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "ange" and etat["final"]["gagnants"] == [1]
    # Chien-Loup qui choisit les loups.
    etat = partie(["loup", "chien_loup", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat, camps={1: "loups"})
    assert etat["convertis"][1] and L._type_nuit(etat, 1) == "meute" and L.lg_vue(etat, 0)["loups"] == [0, 1]
    # Capitaine : election au premier jour, voix double, succession.
    etat = partie(["loup", "cartomancienne", "villageois", "villageois", "villageois", "villageois", "villageois"], capitaine=True)
    tous_prets(etat)
    faire_nuit(etat, {0: 6})
    suite(etat)
    assert etat["phase"] == "election"
    voter(etat, {0: 2, 1: 2, 2: 2, 3: 1, 4: 1, 5: 3})
    assert etat["capitaine"] == 2 and etat["phase"] == "debat"
    # P2 (capitaine, 2 voix) + P1 contre P3 ; P0, P4, P5 contre P5... 3 voix contre P3, 2 contre P1.
    voter(etat, {2: 3, 1: 3, 0: 1, 4: 1, 5: 0, 3: 0})
    a = etat["annonce"]
    assert a["compte"][3] == 3 and a["compte"][1] == 2 and a["elimine"] == 3
    suite(etat)
    faire_nuit(etat, {0: 2})
    suite(etat)
    assert etat["phase"] == "succession" and etat["tireur"] == 2
    L.lg_action(etat, 2, {"type": "lg_tir", "cible": 4}, 0)
    assert etat["capitaine"] == 4 and etat["phase"] == "aube"


def scenario_petite_fille_et_famille():
    etat = partie(["loup", "loup", "petite_fille", "soeur", "soeur", "villageois", "villageois"])
    tous_prets(etat)
    L.lg_action(etat, 0, {"type": "lg_nuit", "cible": 5}, 0)
    v = L.lg_vue(etat, 2)
    assert v["espion"] == {"5": 1} and v["loups"] is None
    assert L.lg_vue(etat, 3)["famille"] == [3, 4] and L.lg_vue(etat, 5)["famille"] is None
    etat["pf_reperee"] = True
    assert L.lg_vue(etat, 0)["petite_fille"] == 2 and L.lg_vue(etat, 5)["petite_fille"] is None


def morts_aube(etat):
    return {m["joueur"]: m["cause"] for m in etat["annonce"]["morts"]}


def scenario_vampire():
    # 0 loup, 1 vampire, 2 salvateur, 3..5 villageois
    etat = partie(["loup", "vampire", "videur", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    assert L.lg_vue(etat, 1)["type_nuit"] == "vampire" and L.lg_vue(etat, 1)["vampires"] == [1]
    assert L.lg_vue(etat, 3)["vampires"] is None
    assert L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 1}, 0)[0]["msg"]["type"] == "erreur"   # pas un vampire
    assert not [e for e in L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 0}, 0) if e["msg"]["type"] == "erreur"]
    faire_nuit(etat, {0: 5, 1: 3, 2: 4})
    assert morts_aube(etat) == {5: "devore"} and etat["vampires"][3]
    assert "vampires" not in json.dumps(L.lg_etat_public(etat, 0))
    assert L.lg_vue(etat, 3)["vampires"] == [1, 3] and L._camp(etat, 3) == "vampires"
    suite(etat)
    voter(etat, {k: 0 for k in range(6)} | {0: 2})
    suite(etat)
    # Restent 1, 3 (vampires) contre 2, 4 : les vampires gagnent.
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "vampires", etat["phase"]
    assert set(etat["final"]["gagnants"]) == {1, 3}
    # Mordre un loup echoue sans rien dire.
    etat = partie(["loup", "vampire", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    faire_nuit(etat, {0: 5, 1: 0})
    assert not etat["vampires"][0] and morts_aube(etat) == {5: "devore"}
    # Le Salvateur protege de la morsure ; nuit paire : pas de morsure.
    etat = partie(["loup", "vampire", "videur", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    faire_nuit(etat, {0: 6, 1: 3, 2: 3})
    assert not etat["vampires"][3]
    suite(etat)
    voter(etat, {})
    suite(etat)
    assert etat["phase"] == "nuit" and etat["nuit"] == 2 and L.lg_vue(etat, 1)["type_nuit"] == "suspect"


def scenario_pyromane():
    # 0 loup, 1 pyromane, 2..5 villageois
    etat = partie(["loup", "pyromane", "villageois", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    assert L.lg_vue(etat, 1)["type_nuit"] == "pyromane"
    assert L.lg_action(etat, 1, {"type": "lg_nuit", "allumer": True}, 0)[0]["msg"]["type"] == "erreur"
    faire_nuit(etat, {0: 5, 1: 2})
    assert morts_aube(etat) == {5: "devore"} and L.lg_vue(etat, 1)["arroses"] == [2]
    assert L.lg_vue(etat, 2)["arroses"] is None and "arroses" not in json.dumps(L.lg_etat_public(etat, 0))
    suite(etat)
    voter(etat, {})
    suite(etat)
    assert L.lg_action(etat, 1, {"type": "lg_nuit", "cible": 2}, 0)[0]["msg"]["type"] == "erreur"   # deja arrose
    faire_nuit(etat, {0: 4, 1: 3})
    assert morts_aube(etat) == {4: "devore"} and sorted(L.lg_vue(etat, 1)["arroses"]) == [2, 3]
    suite(etat)
    voter(etat, {})
    suite(etat)
    faire_nuit(etat, {0: 1, 1: {"allumer": True}})
    assert morts_aube(etat) == {1: "devore", 2: "brule", 3: "brule"}
    suite(etat)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "loups"


def scenario_assassin():
    # 0 loup, 1 assassin, 2 salvateur, 3..5 villageois
    etat = partie(["loup", "assassin", "videur", "villageois", "villageois", "villageois"])
    tous_prets(etat)
    assert L.lg_vue(etat, 1)["type_nuit"] == "assassin"
    faire_nuit(etat, {0: 1, 1: 3, 2: 4})
    assert morts_aube(etat) == {3: "assassine"}          # les loups ne peuvent rien contre lui
    suite(etat)
    voter(etat, {})
    suite(etat)
    faire_nuit(etat, {0: 5, 1: 2, 2: 2})
    assert morts_aube(etat) == {5: "devore"}             # protege par le Salvateur
    suite(etat)
    voter(etat, {1: 0, 2: 0, 4: 0, 0: 4})
    suite(etat)
    # Restent l Assassin, le Salvateur et un villageois : la partie continue.
    assert etat["phase"] == "nuit"
    faire_nuit(etat, {1: 2, 2: 4})
    assert morts_aube(etat) == {2: "assassine"}
    suite(etat)
    voter(etat, {})
    suite(etat)
    faire_nuit(etat, {1: 4})
    suite(etat)
    assert etat["phase"] == "fin" and etat["final"]["vainqueur"] == "assassin", etat["phase"]
    assert etat["final"]["gagnants"] == [1]


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    verifier_composition()
    scenario_regles()
    scenario_loups_gagnent()
    scenario_egalite_et_absents()
    scenario_cupidon_et_couple_mixte()
    scenario_ancien_et_pouvoirs()
    scenario_idiot_bouc_corbeau()
    scenario_renard_montreur_chevalier()
    scenario_infect_enfant_loup_blanc()
    scenario_ange_chien_capitaine()
    scenario_petite_fille_et_famille()
    scenario_vampire()
    scenario_pyromane()
    scenario_assassin()
    rnd = random.Random(2026)
    bilan = {}
    phases = set()
    for k in range(nb):
        n = rnd.randint(L.LG_JOUEURS_MIN, L.LG_JOUEURS_MAX)
        vainqueur, vus = jouer(rnd, n)
        bilan[vainqueur] = bilan.get(vainqueur, 0) + 1
        phases |= vus
    assert phases >= {"role", "nuit", "potion", "aube", "tir", "debat", "verdict", "election", "succession"}, phases
    print(f"OK : {nb} parties, vainqueurs {bilan}, phases {sorted(phases)}")


if __name__ == "__main__":
    main()
