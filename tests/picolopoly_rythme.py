"""
Rythme des gorgees de Picopoly, par difficulte et PAR PERSONNE (picolopoly.py).

Des bots jouent des parties "raisonnables" : achat si 150 EUR restent en
poche, maisons des qu un groupe est complet et qu il reste 400 EUR,
cagnotte toujours raflee, roulette un tour sur deux (50 EUR sur rouge),
pas d echange. Les gorgees a distribuer vont a un adversaire au hasard.
On compte toutes les gorgees bues, divisees par le nombre de joueurs.

Le moteur ne connait pas l heure : on prend SECONDES_PAR_TOUR secondes par
tour joue (lancer, deplacement, choix, animations). Une heure = 3600 /
SECONDES_PAR_TOUR tours, partages entre les joueurs.
Cibles par personne et par heure : difficile ~100, normal ~40, facile ~30.

Usage : python tests/picolopoly_rythme.py [parties_par_cas]
Sortie en erreur si une difficulte s ecarte de plus de 25 % de sa cible
en moyenne sur 3 a 8 joueurs.
"""
import collections
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import picolopoly as pp

CIBLES = {"difficile": 100, "normal": 40, "facile": 30}
SECONDES_PAR_TOUR = 60
TOURS_HEURE = 3600 // SECONDES_PAR_TOUR
JOUEURS = (3, 4, 6, 8)


def partie(nb, difficulte, graine, tours=TOURS_HEURE):
    """(gorgees par personne apres `tours` tours, gorgees par raison)."""
    random.seed(graine)
    e = pp.pp_initialiser(["J%d" % k for k in range(nb)], 0, difficulte)
    e["fin_prevue"] = 10 ** 9
    t, faits, raisons = 0, 0, collections.Counter()
    while e["phase"] != "fin" and faits < tours:
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
            i = e["courant"]
            ev = []
            if not e["roulette_jouee"] and e["argent"][i] >= 300 and random.random() < 0.5:
                ev = pp.pp_action(e, i, {"type": "pp_roulette",
                                         "mises": [{"pari": "rouge", "montant": 50}]}, t)
                e["roulette_jouee"] = True
            if e["attente"] is None:
                ev += pp.pp_action(e, i, {"type": "pp_lancer"}, t)
        else:
            i = e["courant"]
            ev = None
            if e["argent"][i] > 400:
                for c, p in enumerate(e["proprietes"]):
                    if p and p["proprietaire"] == i:
                        r = pp.pp_action(e, i, {"type": "pp_construire", "case": c}, t)
                        if not (len(r) == 1 and r[0]["msg"].get("type") == "erreur"):
                            ev = r
                            break
            if ev is None:
                ev = pp.pp_action(e, i, {"type": "pp_terminer"}, t)
                faits += 1
        for m in ev or []:
            if m["msg"].get("type") == "pp_gorgees":
                for b in m["msg"]["boivent"]:
                    raisons[m["msg"]["raison"]] += b["nombre"]
    return sum(e["gorgees"]) / nb, raisons


def mesurer(n, difficultes=CIBLES, joueurs=JOUEURS):
    """{difficulte: {nb: (par personne, raisons)}}"""
    res = {}
    for d in difficultes:
        res[d] = {}
        for nb in joueurs:
            tot, raisons = [], collections.Counter()
            for g in range(n):
                v, r = partie(nb, d, g)
                tot.append(v)
                raisons += r
            res[d][nb] = (statistics.mean(tot), raisons)
    return res


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    res = mesurer(n)
    print("1 h = %d tours (%d s par tour). Gorgees par personne et par heure :" % (TOURS_HEURE, SECONDES_PAR_TOUR))
    pire = (0, "")
    for d, parnb in res.items():
        valeurs = [parnb[nb][0] for nb in JOUEURS]
        moy = statistics.mean(valeurs)
        raisons = sum((parnb[nb][1] for nb in JOUEURS), collections.Counter())
        tout = sum(raisons.values()) or 1
        print("  %-9s %s  -> moyenne %.0f (cible %d)" % (
            d, "  ".join("%dj %.0f" % (nb, v) for nb, v in zip(JOUEURS, valeurs)), moy, CIBLES[d]))
        print("            " + ", ".join("%s %d%%" % (k, 100 * v / tout) for k, v in raisons.most_common(7)))
        ecart = abs(moy - CIBLES[d]) / CIBLES[d]
        pire = max(pire, (ecart, d))
    print("Plus gros ecart a la cible (moyenne 3-8 joueurs) : %d %% (%s)" % (100 * pire[0], pire[1]))
    if pire[0] > 0.25:
        sys.exit("Rythme hors cible")


if __name__ == "__main__":
    main()
