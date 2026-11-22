"""
Fabrique tournee_plateaux.json : les trois plateaux de La Tournee.

Usage : python tournee_plateaux.py   (reecrit tournee_plateaux.json)

Un plateau = une boucle de cases (le chemin principal, en ellipse) plus des
branches : une branche part d une case de la boucle (l embranchement, ou le
joueur choisit son chemin) et rejoint la boucle plus loin. Les coordonnees
(x, z) sont dans [-1.2, 1.2] : le site les pose telles quelles sur la table.

Chaque case : {id, type, x, z, suivants, zone?, vers?}
  - type : depart, pieces, piege, evenement, tournee, chance, duel, trappe
  - suivants : les cases ou l on peut aller ensuite (2 = embranchement)
  - zone : nom de la branche (affiche au choix du chemin)
  - vers : pour une trappe, la trappe ou elle mene
"""
import json
import math
import os

_ICI = os.path.dirname(os.path.abspath(__file__))


def _boucle(types, rx, rz, ondes=()):
    """Boucle principale. ondes : (amplitude, frequence, phase) qui font serpenter
    le chemin dans le decor (le site pose le monde 3D autour)."""
    n = len(types)
    cases = []
    for k, t in enumerate(types):
        # Le depart en bas (cote joueur), on tourne dans le sens des aiguilles.
        a = math.pi / 2 + 2 * math.pi * k / n
        r = 1 + sum(amp * math.sin(f * a + ph) for amp, f, ph in ondes)
        cases.append({"id": k, "type": t, "x": round(rx * r * math.cos(a), 3), "z": round(rz * r * math.sin(a), 3),
                      "suivants": [(k + 1) % n]})
    return cases


def _branche(cases, depuis, vers, types, courbe, zone):
    """Ajoute une branche de depuis a vers ; courbe < 1 tire vers le centre, > 1 vers l exterieur."""
    p0, p2 = cases[depuis], cases[vers]
    cx, cz = (p0["x"] + p2["x"]) / 2 * courbe, (p0["z"] + p2["z"]) / 2 * courbe
    n = len(types)
    premier = len(cases)
    for i, t in enumerate(types):
        u = (i + 1) / (n + 1)
        x = (1 - u) ** 2 * p0["x"] + 2 * (1 - u) * u * cx + u ** 2 * p2["x"]
        z = (1 - u) ** 2 * p0["z"] + 2 * (1 - u) * u * cz + u ** 2 * p2["z"]
        cases.append({"id": premier + i, "type": t, "x": round(x, 3), "z": round(z, 3),
                      "suivants": [premier + i + 1 if i < n - 1 else vers], "zone": zone})
    cases[depuis]["suivants"].append(premier)


def _trappes(cases, a, b):
    for x, y in ((a, b), (b, a)):
        cases[x]["type"] = "trappe"
        cases[x]["vers"] = y


def terrasse():
    types = ["depart", "pieces", "pieces", "evenement", "pieces", "chance", "pieces", "piege",
             "pieces", "tournee", "pieces", "evenement", "pieces", "pieces", "chance", "pieces",
             "piege", "pieces", "evenement", "pieces", "pieces", "tournee", "pieces", "pieces"]
    cases = _boucle(types, 1.02, 0.84, [(0.07, 3, 0.4), (0.04, 5, 1.3)])
    _branche(cases, 4, 14, ["pieces", "piege", "pieces", "evenement"], 0.0, "la pergola")
    return {
        "id": "terrasse", "nom": "La Terrasse", "difficulte": "facile", "icone": "🌞",
        "description": "Un port au soleil, un raccourci par la pergola. Peu de pièges, peu de surprises, on boit peu.",
        "cases": cases,
        "videur": None,
        "prix_capsule": 20,
        "pieces": {"pieces": 3, "piege": -2, "depart": 5},
        "boire": {"piege": 1, "tournee": 1, "duel": 1, "minijeu_dernier": 1, "fin_dernier": 2, "videur": 0},
        # Facile : pas de vol, pas d echange, peu de retournements.
        "evenements": ["pourboire", "raccourci", "addition", "chanson"],
        "chance": ["jackpot", "poche_trouee", "elan"],
        "duel_capsule": False,
        "evenements_plateau": {"tous_les": 4, "liste": ["soleil", "apero", "promo_capsule"]},
    }


def pub():
    types = ["depart", "pieces", "pieces", "pieces", "evenement", "piege", "pieces", "tournee",
             "pieces", "chance", "pieces", "pieces", "evenement", "piege", "pieces", "duel",
             "pieces", "pieces", "evenement", "pieces", "pieces", "piege", "tournee", "pieces",
             "pieces", "chance", "pieces", "evenement"]
    cases = _boucle(types, 1.1, 0.88, [(0.08, 4, 0.2), (0.05, 3, 2.1)])
    _branche(cases, 3, 11, ["evenement", "chance", "pieces", "duel", "pieces"], 0.3, "l'arrière-salle")
    _branche(cases, 17, 24, ["piege", "pieces", "piege"], 0.4, "le fumoir")
    return {
        "id": "pub", "nom": "Le Pub", "difficulte": "moyen", "icone": "🍺",
        "description": "La ville de nuit : deux embranchements, des duels et des vols de pièces. Une surprise du patron tous les 3 tours.",
        "cases": cases,
        "videur": None,
        "prix_capsule": 20,
        "pieces": {"pieces": 3, "piege": -3, "depart": 5},
        "boire": {"piege": 1, "tournee": 2, "duel": 2, "minijeu_dernier": 2, "fin_dernier": 3, "videur": 0},
        # Moyen : vols de pieces, duels, echanges de place, coup de pouce au dernier.
        "evenements": ["pourboire", "tournee_generale", "pickpocket", "raccourci", "addition", "courage", "chanson"],
        "chance": ["jackpot", "poche_trouee", "echange", "elan"],
        "duel_capsule": False,
        "evenements_plateau": {"tous_les": 3, "liste": ["happy_hour", "tournee_patron", "capsule_bouge", "addition_salee", "coup_de_pouce"]},
    }


def cave():
    types = ["depart", "pieces", "pieces", "piege", "pieces", "evenement", "pieces", "pieces",
             "duel", "pieces", "piege", "pieces", "pieces", "evenement", "piege", "pieces",
             "tournee", "pieces", "chance", "pieces", "piege", "pieces", "pieces", "evenement",
             "pieces", "duel", "pieces", "piege", "pieces", "tournee"]
    cases = _boucle(types, 1.14, 0.9, [(0.09, 5, 0.9), (0.05, 2, 0.3)])
    _trappes(cases, 7, 22)
    _branche(cases, 2, 9, ["pieces", "evenement", "pieces", "chance"], 0.35, "le couloir des fûts")
    _branche(cases, 12, 21, ["piege", "chance", "duel", "pieces", "piege"], 0.25, "les catacombes")
    _branche(cases, 24, 28, ["pieces", "pieces"], 1.75, "l'escalier")
    return {
        "id": "cave", "nom": "La Cave", "difficulte": "difficile", "icone": "💀",
        "description": "Des caves volcaniques : trois chemins, deux trappes, un videur qui rôde, des capsules qui se volent : tout peut basculer.",
        "cases": cases,
        "videur": {"depart": 16, "pas_min": 1, "pas_max": 3, "fouille": -2},
        "prix_capsule": 20,
        "pieces": {"pieces": 3, "piege": -4, "depart": 5},
        "boire": {"piege": 2, "tournee": 3, "duel": 3, "minijeu_dernier": 3, "fin_dernier": 5, "videur": 2},
        # Difficile : on peut voler une capsule, les duels se jouent pour une capsule,
        # et le patron peut tout redistribuer. Rien n est joue avant le dernier tour.
        "evenements": ["pourboire", "tournee_generale", "pickpocket", "raccourci", "addition", "courage", "double_peine", "vol_capsule"],
        "chance": ["jackpot", "poche_trouee", "echange", "elan", "vol_capsule"],
        "duel_capsule": True,
        "evenements_plateau": {"tous_les": 2, "liste": ["tournee_patron", "capsule_bouge", "videur_enerve", "melange", "grosse_tournee", "partage", "coup_de_pouce"]},
    }


def verifier(p):
    ids = {c["id"] for c in p["cases"]}
    assert [c["id"] for c in p["cases"]] == list(range(len(p["cases"]))), p["id"]
    assert p["cases"][0]["type"] == "depart"
    for c in p["cases"]:
        assert c["suivants"] and all(s in ids for s in c["suivants"]), (p["id"], c)
        if c["type"] == "trappe":
            assert p["cases"][c["vers"]]["type"] == "trappe"
    # Toute case est atteignable depuis le depart, et le depart depuis toute case.
    vus, pile = set(), [0]
    while pile:
        c = pile.pop()
        if c not in vus:
            vus.add(c)
            pile.extend(p["cases"][c]["suivants"])
    assert vus == ids, p["id"]


def fabriquer():
    plateaux = [terrasse(), pub(), cave()]
    for p in plateaux:
        verifier(p)
    donnees = {
        "_note": "Plateaux de La Tournée, fabriqués par tournee_plateaux.py (ne pas modifier à la main).",
        "defaut": "pub",
        "plateaux": plateaux,
    }
    with open(os.path.join(_ICI, "tournee_plateaux.json"), "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return donnees


if __name__ == "__main__":
    d = fabriquer()
    for p in d["plateaux"]:
        print(p["id"], len(p["cases"]), "cases,", sum(len(c["suivants"]) > 1 for c in p["cases"]), "embranchements")
