"""
Tests hors ligne du moteur de Picopoly (picolopoly.py).

Des parties entieres jouees au hasard, avec des coups valides ET absurdes
(mauvais joueur, mauvaise case, types de travers), des absents, des
arrivants et des minuteurs qui expirent. A chaque etape on verifie les
invariants du jeu :
  - aucune exception, et une action refusee ne touche pas l etat ;
  - l etat (et sa version publique) passe en JSON, sans l ordre des paquets ;
  - argent jamais negatif, maisons de 0 a 5, seulement sur un groupe complet
    et sans hypotheque dans le groupe ;
  - aucun titre ni choix ni dette chez un joueur en faillite ;
  - les deux cartes « sortie de prison » existent toujours exactement une fois ;
  - la partie finit toujours (faillites ou chrono).

Usage : python tests/picolopoly_essai.py [nombre_de_parties]
"""
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import picolopoly as pp

ECHECS = []
PAS_MAX = 20000


def verifier(ok, quoi):
    if not ok:
        ECHECS.append(quoi)
        raise AssertionError(quoi)


def invariants(e):
    json.dumps(e)
    pub = pp.pp_etat_public(e, e["debut"])
    json.dumps(pub)
    verifier("paquets" not in pub, "l'ordre des paquets fuit dans l'etat public")
    n = e["nb_joueurs"]
    for k in ("noms", "argent", "positions", "en_prison", "faillite", "partis", "cartes_sortie"):
        verifier(len(e[k]) == n, "liste " + k + " de mauvaise longueur")
    for i in range(n):
        verifier(e["argent"][i] >= 0, "argent negatif pour " + str(i) + " : " + str(e["argent"][i]))
        verifier(0 <= e["positions"][i] < 40, "position hors plateau")
    for c, p in enumerate(e["proprietes"]):
        if p is None:
            continue
        o = p["proprietaire"]
        verifier(0 <= p["maisons"] <= 5, "maisons hors bornes")
        if o is not None:
            verifier(not e["faillite"][o], "un joueur en faillite possede encore " + pp.PP_CASES[c]["nom"])
        else:
            verifier(p["maisons"] == 0 and not p["hypotheque"], "case libre construite ou hypothequee")
        if p["maisons"]:
            g = pp.PP_CASES[c]["groupe"]
            verifier(pp._groupe_complet(e, o, g), "maisons sur un groupe incomplet")
            verifier(not any(e["proprietes"][k]["hypotheque"] for k in pp.PP_GROUPES[g]),
                     "maisons et hypotheque dans le meme groupe")
    for paquet, cid in (("chance", "ch_sortie"), ("caisse", "ca_sortie")):
        en_main = sum(c.count(paquet) for c in e["cartes_sortie"])
        verifier(e["paquets"][paquet].count(cid) + en_main == 1, "carte de sortie " + paquet + " perdue ou dupliquee")
    if e["phase"] != "fin":
        verifier(not e["faillite"][e["courant"]], "la main est a un joueur en faillite")
        if e["attente"]:
            verifier(not e["faillite"][e["attente"]["joueur"]], "choix demande a un joueur en faillite")
        if e["dette"]:
            verifier(not e["faillite"][e["dette"]["joueur"]], "dette d'un joueur en faillite")
        verifier(len(pp._vivants(e)) >= 2, "partie non finie avec moins de deux joueurs en jeu")
        verifier(e["phase"] in ("tour", "resolution", "fin_tour"), "phase inconnue " + str(e["phase"]))
        if e["phase"] == "resolution":
            verifier(e["attente"] is not None or e["dette"] is not None,
                     "resolution figee sans choix ni dette")


def coup_au_hasard(e, rnd):
    """Un message plausible (souvent valide, parfois absurde)."""
    n = e["nb_joueurs"]
    att = pp.pp_attendu(e)
    i = att[0] if att and rnd.random() < 0.8 else rnd.randrange(n + 1)   # n : spectateur
    a = e["attente"]
    mes = [c for c, p in enumerate(e["proprietes"]) if p and p["proprietaire"] == i]
    case = rnd.choice(mes) if mes and rnd.random() < 0.9 else rnd.choice([rnd.randrange(-2, 42), "x", None, True])
    t = rnd.choice(["pp_lancer"] * 6 + ["pp_terminer"] * 4 + ["pp_choix"] * 4 + [
        "pp_construire"] * 3 + ["pp_vendre", "pp_hypothequer", "pp_lever", "pp_prison_payer",
        "pp_prison_carte", "pp_proposer_echange", "pp_faillite", "pp_roulette", "inconnu"])
    m = {"type": t, "case": case}
    if t == "pp_choix":
        if a and a["joueur"] == i and rnd.random() < 0.85:
            if a["type"] in ("achat", "prison_carte", "echange", "parc"):
                m["valeur"] = rnd.random() < 0.7
            elif a["type"] == "cible":
                m["valeur"] = rnd.choice(a["cibles"])
            elif a["type"] == "deplacement":
                m["valeur"] = rnd.choice(a["options"])
            elif a["type"] == "sommelier":
                m["valeur"] = pp._valeur_defaut(e, a)
        else:
            m["valeur"] = rnd.choice([True, 3, "a", None, {"0": 99}, [1]])
    elif t == "pp_proposer_echange":
        cible = rnd.randrange(-1, n + 1)
        siens = [c for c, p in enumerate(e["proprietes"]) if p and p["proprietaire"] == cible]
        m.update(cible=cible, donne=rnd.sample(mes, min(len(mes), rnd.randint(0, 2))),
                 recoit=rnd.sample(siens, min(len(siens), rnd.randint(0, 2))),
                 argent_donne=rnd.choice([0, 0, 50, 300, -5, "x"]),
                 argent_recu=rnd.choice([0, 0, 100, 5000]))
    elif t == "pp_roulette":
        m.update(pari=rnd.choice(list(pp.PP_ROULETTE_GAINS) + ["vert"]),
                 numero=rnd.randint(-1, 37), mise=rnd.choice([50, 100, 25, 10 ** 6, "x"]))
    return i, m


def une_partie(graine, nb, difficulte):
    rnd = random.Random(graine)
    random.seed(graine)
    t = 1000.0
    e = pp.pp_initialiser(["J" + str(k) for k in range(nb)], t, difficulte)
    if graine % 2:
        e["fin_prevue"] = t + 10 ** 7      # sans chrono : on va jusqu aux faillites
    invariants(e)
    for pas in range(PAS_MAX):
        if e["phase"] == "fin":
            return pas
        r = rnd.random()
        if r < 0.02:                                  # un depart / retour
            j = rnd.randrange(e["nb_joueurs"])
            e["partis"][j] = not e["partis"][j]
            pp.pp_armer(e, t)
        elif r < 0.025 and e["nb_joueurs"] < pp.PP_JOUEURS_MAX:
            pp.pp_ajouter_joueur(e, "N" + str(e["nb_joueurs"]))
        elif r < 0.12:                                # personne ne repond
            t = max(t, (e["echeance"] or t)) + 1
            pp.pp_expirer(e, t)
        else:
            i, m = coup_au_hasard(e, rnd)
            avant = copy.deepcopy(e)
            ev = pp.pp_action(e, i, m, t)
            json.dumps(ev)
            if ev and len(ev) == 1 and ev[0]["msg"].get("type") == "erreur":
                verifier(e == avant, "une action refusee a modifie l'etat : " + json.dumps(m))
        invariants(e)
        t += rnd.choice([0.5, 2, 5])
    # Trop de coups : on laisse le chrono finir la partie.
    pp.pp_expirer(e, e["fin_prevue"] + 1)
    invariants(e)
    verifier(e["phase"] == "fin", "la partie ne finit pas au chrono")
    return PAS_MAX


def main():
    nb_parties = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    fins = {"faillites": 0, "chrono": 0}
    for g in range(nb_parties):
        nb = 2 + g % 7
        diff = ["facile", "normal", "difficile"][g % 3]
        try:
            une_partie(g, nb, diff)
        except AssertionError as err:
            print("ECHEC partie", g, "(", nb, "joueurs,", diff, ") :", err)
            sys.exit(1)
        except Exception:
            print("EXCEPTION partie", g, "(", nb, "joueurs,", diff, ")")
            raise
    print("parties jouees :", nb_parties)
    print("OK")


if __name__ == "__main__":
    main()
