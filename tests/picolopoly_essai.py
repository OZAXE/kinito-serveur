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
        paris = list(pp.PP_ROULETTE_PARIS) + ["n0", "n17", "n36", "n37", "n07", "vert", 3,
                                             "x17-18", "x0-1-2-3", "x31-32-33-34-35-36", "x3-4"]
        if rnd.random() < 0.2:
            # Ancien format a un seul pari (pages en cache).
            m.update(pari=rnd.choice(["rouge", "plein", "douzaine", "vert"]),
                     numero=rnd.randint(-1, 37), mise=rnd.choice([50, 100, 25, 10 ** 6, "x"]))
        else:
            m["mises"] = rnd.choice([[], "x", [{"pari": rnd.choice(paris),
                                                "montant": rnd.choice([50, 100, 150, 25, 10 ** 6, "x"])}
                                               for _ in range(rnd.randint(1, 4))]])
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


def essai_roulette():
    """Gains du tapis, comme au casino, sur un tirage force."""
    def lancer(mises, tirage, argent=1000):
        e = pp.pp_initialiser(["A", "B"], 1000.0, "normal")
        i = e["courant"]
        e["argent"][i] = argent
        cagnotte = e["cagnotte"]
        ancien = pp.random.randint
        pp.random.randint = lambda a, b: tirage
        try:
            ev = pp.pp_action(e, i, {"type": "pp_roulette", "mises": mises}, 1000.0)
        finally:
            pp.random.randint = ancien
        msg = [x["msg"] for x in ev if x["msg"].get("type") == "pp_roulette"]
        return e, i, msg[0] if msg else None, cagnotte

    # 100 sur le 17, 50 sur noir, 50 sur la 2e douzaine, 50 sur rouge : le 17 noir sort.
    e, i, m, c0 = lancer([{"pari": "n17", "montant": 100}, {"pari": "noir", "montant": 50},
                          {"pari": "d2", "montant": 50}, {"pari": "rouge", "montant": 50}], 17)
    verifier(m is not None, "roulette refusee")
    verifier(m["gain"] == 100 * 36 + 50 * 2 + 50 * 3, "gain du tapis faux : " + str(m["gain"]))
    verifier(e["argent"][i] == 1000 - 250 + m["gain"], "argent faux apres la roulette")
    verifier(e["cagnotte"] == c0 + 50, "la mise perdue (rouge) doit aller a la cagnotte")
    verifier(e["roulette_jouee"], "roulette_jouee pas mis")
    # Le zero : tout perd sauf le plein sur 0 ; colonnes et manque perdent aussi.
    e, i, m, c0 = lancer([{"pari": "n0", "montant": 50}, {"pari": "c1", "montant": 50},
                          {"pari": "manque", "montant": 50}, {"pari": "pair", "montant": 50}], 0)
    verifier(m["gain"] == 50 * 36, "zero mal paye")
    verifier(e["cagnotte"] == c0 + 150, "zero : mises perdues pas a la cagnotte")
    # Colonnes : c1 = 1, 4... 34 ; passe = 19-36.
    e, i, m, _ = lancer([{"pari": "c1", "montant": 50}, {"pari": "passe", "montant": 50}], 34)
    verifier(m["gain"] == 150 + 100, "colonne ou passe mal payee")
    # Ancien format (page en cache) : plein sur le 5.
    e = pp.pp_initialiser(["A", "B"], 1000.0, "normal")
    ancien = pp.random.randint
    pp.random.randint = lambda a, b: 5
    try:
        ev = pp.pp_action(e, e["courant"], {"type": "pp_roulette", "pari": "plein", "numero": 5, "mise": 50}, 1000.0)
    finally:
        pp.random.randint = ancien
    verifier(any(x["msg"].get("type") == "pp_roulette" and x["msg"]["gain"] == 1800 for x in ev), "ancien format refuse")
    # Refus : trop cher, mauvaise tranche, pari inconnu, doublon fusionne.
    for mises in ([{"pari": "rouge", "montant": 2000}], [{"pari": "rouge", "montant": 75}],
                  [{"pari": "vert", "montant": 50}], [{"pari": "n37", "montant": 50}], []):
        e, i, m, _ = lancer(mises, 3)
        verifier(m is None and not e["roulette_jouee"], "mise invalide acceptee : " + json.dumps(mises))
    e, i, m, _ = lancer([{"pari": "rouge", "montant": 50}, {"pari": "rouge", "montant": 50}], 3)
    verifier(m["mises"] == [{"pari": "rouge", "montant": 100, "gagne": True}] and m["gain"] == 200, "doublon mal fusionne")
    # Mises a plusieurs numeros : cheval 17, transversale 11, carre 8, sixain 5.
    e, i, m, c0 = lancer([{"pari": "x17-18", "montant": 50}, {"pari": "x16-17-18", "montant": 50},
                          {"pari": "x14-15-17-18", "montant": 50}, {"pari": "x13-14-15-16-17-18", "montant": 50},
                          {"pari": "x19-20", "montant": 50}], 17)
    verifier(m["gain"] == 50 * 18 + 50 * 12 + 50 * 9 + 50 * 6, "combinaisons mal payees : " + str(m["gain"]))
    verifier(e["cagnotte"] == c0 + 50, "cheval perdant pas a la cagnotte")
    # Le zero paie les mises qui le couvrent.
    e, i, m, _ = lancer([{"pari": "x0-1", "montant": 50}, {"pari": "x0-1-2-3", "montant": 50},
                         {"pari": "x0-2-3", "montant": 50}], 0)
    verifier(m["gain"] == 50 * 18 + 50 * 9 + 50 * 12, "combinaisons du zero mal payees")
    # Une gorgee par lancer, quelle que soit la mise.
    for mises in ([{"pari": "rouge", "montant": 50}], [{"pari": "rouge", "montant": 900},
                                                       {"pari": "n3", "montant": 100}]):
        e, i, m, _ = lancer(mises, 3)
        verifier(e["gorgees"][i] == pp.PP_ROULETTE_GORGEES, "la roulette doit couter "
                 + str(pp.PP_ROULETTE_GORGEES) + " gorgee par lancer")
    # Combinaisons impossibles sur le tapis.
    for pari in ("x3-4", "x1-5", "x18-17", "x1-2-3-4", "x34-35-36-37", "x0-4", "x"):
        e, i, m, _ = lancer([{"pari": pari, "montant": 50}], 3)
        verifier(m is None, "combinaison impossible acceptee : " + pari)
    verifier(len(pp.PP_ROULETTE_COMBIS) == 60 + 14 + 23 + 11, "nombre de combinaisons du tapis")
    print("roulette : OK")


def essai_leviers():
    """Gorgees justifiees : double, cremaillere, tour en prison (difficile)."""
    def partie4():
        e = pp.pp_initialiser(["A", "B", "C", "D"], 1000.0, "difficile")
        return e, e["courant"]
    r = pp.PP_DIFFICULTES["difficile"]
    # Double : apres le deplacement, le lanceur repartit double x 3 adversaires.
    e, i = partie4()
    e["positions"][i] = 0
    des = iter([2, 2])
    ancien_ri, ancien_ch = pp.random.randint, pp.random.choice
    pp.random.randint = lambda a, b: next(des)
    try:
        pp.pp_action(e, i, {"type": "pp_lancer"}, 1000.0)
    finally:
        pp.random.randint, pp.random.choice = ancien_ri, ancien_ch
    while e["attente"] and e["attente"]["type"] != "sommelier":
        a = e["attente"]
        pp.pp_action(e, a["joueur"], {"type": "pp_choix", "valeur": pp._valeur_defaut(e, a)}, 1000.0)
    a = e["attente"]
    verifier(a and a.get("raison") == "double" and a["total"] == int(r["double"] * 3 + 0.5),
             "le double doit faire distribuer : " + json.dumps(a))
    avant = sum(e["gorgees"])
    pp.pp_action(e, i, {"type": "pp_choix", "valeur": {str(a["cibles"][0]): a["total"]}}, 1000.0)
    verifier(sum(e["gorgees"]) == avant + a["total"], "gorgees du double pas bues")
    # Cremaillere : une maison fait distribuer maison x adversaires.
    e, i = partie4()
    for c in pp.PP_GROUPES[pp.PP_CASES[1]["groupe"]]:
        e["proprietes"][c]["proprietaire"] = i
    ev = pp.pp_action(e, i, {"type": "pp_construire", "case": 1}, 1000.0)
    a = e["attente"]
    verifier(a and a.get("raison") == "cremaillere" and a["total"] == int(r["maison"] * 3 + 0.5),
             "la maison doit faire distribuer : " + json.dumps(a))
    # Tour en prison sans double : prison_tour gorgees.
    e, i = partie4()
    e["en_prison"][i] = True
    e["positions"][i] = pp.PP_CASE_PRISON
    des = iter([1, 2])
    pp.random.randint = lambda a, b: next(des)
    try:
        pp.pp_action(e, i, {"type": "pp_lancer"}, 1000.0)
    finally:
        pp.random.randint = ancien_ri
    verifier(e["gorgees"][i] == r["prison_tour"], "tour en prison : " + str(e["gorgees"][i]))
    print("leviers : OK")


def main():
    essai_roulette()
    essai_leviers()
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
