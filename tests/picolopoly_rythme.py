"""
Rythme des gorgees de Picopoly, par difficulte (picolopoly.py).

Des bots jouent des parties "raisonnables" (achat si 150 EUR restent en
poche, maisons des qu un groupe est complet et qu il reste 400 EUR, cagnotte
toujours raflee, pas de roulette ni d echange). On compte toutes les gorgees
bues par la table.

Le temps d un tour n existe pas dans le moteur : on l etalonne sur la
partie reelle en difficile, ou une heure donne ~100 gorgees. Le tour ou le
difficile atteint 100 represente donc 1 h, et on lit les autres difficultes
a ce meme tour. Cibles : difficile ~100, normal ~40, facile ~30.

Usage : python tests/picolopoly_rythme.py [parties_par_cas]
Sortie en erreur si normal ou facile s ecartent de plus de 20 % de la cible.
"""
import collections
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import picolopoly as pp

CIBLES = {"difficile": 100, "normal": 40, "facile": 30}
TOURS_MAX = 300


def partie(nb, difficulte, graine):
    """Total cumule des gorgees de la table apres chaque tour, et leurs raisons."""
    random.seed(graine)
    e = pp.pp_initialiser(["J%d" % k for k in range(nb)], 0, difficulte)
    e["fin_prevue"] = 10 ** 9
    t, courbe, raisons = 0, [], collections.Counter()
    while e["phase"] != "fin" and len(courbe) < TOURS_MAX:
        t += 1
        a = e["attente"]
        if a is not None:
            i, ty = a["joueur"], a["type"]
            if ty == "achat":
                v = e["argent"][i] >= a["prix"] + 150
            elif ty in ("parc", "prison_carte"):
                v = True
            elif ty == "echange":
                v = False
            else:
                v = pp._valeur_defaut(e, a)
            ev = pp.pp_action(e, i, {"type": "pp_choix", "valeur": v}, t)
        elif e["dette"] is not None:
            ev = pp.pp_expirer(e, e["echeance"] + 1)
        elif e["phase"] == "tour":
            ev = pp.pp_action(e, e["courant"], {"type": "pp_lancer"}, t)
        else:
            i = e["courant"]
            for c, p in enumerate(e["proprietes"]):
                if p and p["proprietaire"] == i and e["argent"][i] > 400:
                    pp.pp_action(e, i, {"type": "pp_construire", "case": c}, t)
            ev = pp.pp_action(e, i, {"type": "pp_terminer"}, t)
            courbe.append(sum(e["gorgees"]))
        for m in ev or []:
            if m["msg"].get("type") == "pp_gorgees":
                for b in m["msg"]["boivent"]:
                    raisons[m["msg"]["raison"]] += b["nombre"]
    courbe += [courbe[-1] if courbe else 0] * (TOURS_MAX - len(courbe))
    return courbe, raisons


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    ecarts = []
    for nb in (3, 4, 6, 8):
        res = {}
        for d in CIBLES:
            courbes, raisons = [], collections.Counter()
            for g in range(n):
                c, r = partie(nb, d, g)
                courbes.append(c)
                raisons += r
            res[d] = ([statistics.mean(c[k] for c in courbes) for k in range(TOURS_MAX)], raisons)
        dur = res["difficile"][0]
        heure = next(k for k in range(TOURS_MAX) if dur[k] >= CIBLES["difficile"]) + 1
        print("%d joueurs : 1 h = %d tours (%.0f s par tour)" % (nb, heure, 3600 / heure))
        for d, (moy, raisons) in res.items():
            total = moy[heure - 1]
            tout = sum(raisons.values()) or 1
            print("  %-9s %5.1f gorgees/h, 1 toutes les %.2f min   (%s)" % (
                d, total, 60 / total, ", ".join("%s %d%%" % (k, 100 * v / tout)
                                                for k, v in raisons.most_common(4))))
            if d != "difficile":
                ecarts.append((abs(total - CIBLES[d]) / CIBLES[d], nb, d, total))
    pire = max(ecarts)
    print("Plus gros ecart a la cible : %d %% (%s a %d joueurs : %.1f)" % (
        100 * pire[0], pire[2], pire[1], pire[3]))
    if pire[0] > 0.20:
        sys.exit("Rythme hors cible")


if __name__ == "__main__":
    main()
