"""
Mode simule de l'Enquete (tranche 9) : des parties entieres jouees par des
joueurs fictifs, pour regler l equilibre sans reunir tout le monde.

Chaque minute de partie :
- chaque joueur gagne 1 PE avec une probabilite pe_heure / 60 (il gagne des
  parties aux autres jeux de la soiree) ;
- il regarde son telephone une fois sur deux et joue son tour
  (enquete.jouer_bot : indices, lectures, expertises, accusations ; le
  tueur achete des charges et sabote) ;
- avec la probabilite --partage, il a discute avec un autre enqueteur et
  connait ses plis (l entraide a l oral) ;
- le serveur avance (temoins, rumeurs, evenements, heure butoir).

Usage :
  python tests/simulation.py                       (toutes les vitesses, 6 joueurs)
  python tests/simulation.py --parties 300 --joueurs 8 --pe-heure 5 --partage 0.3
"""
import argparse
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import enquete as m

NOMS = ["Lea", "Max", "Zoe", "Tom", "Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Ida"]


def une_partie(graine, n, vitesse, pe_heure, partage, univers="manoir", config=None):
    config = config or m.CONFIG
    rng = random.Random(graine)
    e = m.nouvelle_partie("SIMU", NOMS[0], 0.0, config)
    m.action(e, NOMS[0], {"type": "enq_config", "vitesse": vitesse, "univers": univers}, 0.0, config=config)
    ids = [p["id"] for p in m.PACKS[univers]["portraits"]]
    for i in range(n):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": ids[i]}, 0.0, config=config)
    m.action(e, NOMS[0], {"type": "enq_lancer"}, 0.0, rng=rng, config=config)
    t = float(config["duree_ouverture"])
    m.expirer(e, t, config, rng)
    noms = [j["nom"] for j in e["joueurs"]]
    while e["phase"] == "enquete":
        t += 60
        m.expirer(e, t, config, rng)
        for nom in noms:
            if e["phase"] != "enquete":
                break
            if rng.random() < pe_heure / 60:
                m.crediter_pe(e, nom, 1, "menteur", "victoire", config)
            if rng.random() < 0.5:
                autres = [x for x in noms if x != nom and x != e["tueur"]]
                amis = [x for x in autres if rng.random() < partage]
                m.jouer_bot(e, nom, t, rng, config, partage=amis)
    return e, t


def mesurer(e, t_fin):
    duree = max(1, e["fin_a"] - e["ouverture_fin_a"])
    heures = max(1 / 60, (t_fin - e["ouverture_fin_a"]) / 3600)
    gorgees = [sum(n for _, n, _ in p["gorgees"]) for p in e["perso"].values()]
    archives = [p["archive"] for p in e["perso"].values()]
    return {
        "tueur_gagne": e["vainqueur"] == "tueur",
        "raison": e["raison_fin"],
        "avance": min(1.0, (t_fin - e["ouverture_fin_a"]) / duree),
        "accusations": len(e["accusations"]),
        "indices": statistics.mean(len(a) for a in archives),
        "faux": sum(1 for a in archives for x in a if not x["vrai"]),
        "faux_demasques": sum(1 for a in archives for x in a if not x["vrai"] and x["tampon"] == "falsifie"),
        "sabotages": len(e["sabotages"]),
        "detectes": sum(1 for x in e["sabotages"] if x["detecte"]),
        "gorgees_h": statistics.mean(gorgees) / heures,
        "gorgees_h_max": max(gorgees) / heures,
        "handicaps": sum(1 for p in e["perso"].values() if p.get("handicaps")),
    }


def resume(mesures):
    k = len(mesures)
    moy = lambda c: statistics.mean(x[c] for x in mesures)
    raisons = {}
    for x in mesures:
        raisons[x["raison"]] = raisons.get(x["raison"], 0) + 1
    return {
        "tueur": sum(x["tueur_gagne"] for x in mesures) / k,
        "raisons": {r: n / k for r, n in raisons.items()},
        "avance": moy("avance"), "accusations": moy("accusations"), "indices": moy("indices"),
        "faux": moy("faux"), "faux_demasques": moy("faux_demasques"), "sabotages": moy("sabotages"),
        "detectes": moy("detectes"), "gorgees_h": moy("gorgees_h"), "gorgees_h_max": moy("gorgees_h_max"),
        "handicaps": moy("handicaps"),
    }


def simuler(vitesses, parties, joueurs, pe_heure, partage, univers="manoir", config=None):
    out = {}
    for v in vitesses:
        mesures = []
        for g in range(parties):
            e, t = une_partie(g, joueurs, v, pe_heure, partage, univers, config)
            mesures.append(mesurer(e, t))
        out[v] = resume(mesures)
    return out


def afficher(res, joueurs, pe_heure, partage):
    print(f"Simulation : {joueurs} joueurs, {pe_heure} PE/heure chacun, entraide {partage:.0%}")
    print(f"{'vitesse':<10}{'tueur':>7}{'fin':>7}{'accus.':>8}{'indices':>8}{'faux':>6}{'démasq.':>8}{'sabot.':>7}{'repérés':>8}{'gorg/h':>8}{'max/h':>7}{'plafond':>8}")
    for v, r in res.items():
        print(f"{v:<10}{r['tueur']:>7.0%}{r['avance']:>7.0%}{r['accusations']:>8.1f}{r['indices']:>8.1f}{r['faux']:>6.1f}"
              f"{r['faux_demasques']:>8.1f}{r['sabotages']:>7.1f}{r['detectes']:>8.1f}{r['gorgees_h']:>8.1f}{r['gorgees_h_max']:>7.1f}{r['handicaps']:>8.1f}")
        print("          fins : " + ", ".join(f"{k} {n:.0%}" for k, n in sorted(r["raisons"].items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--parties", type=int, default=150)
    a.add_argument("--joueurs", type=int, default=6)
    a.add_argument("--pe-heure", type=float, default=4)
    a.add_argument("--partage", type=float, default=0.25)
    a.add_argument("--univers", default="manoir")
    a.add_argument("--vitesses", default="flash,express,soiree,marathon")
    o = a.parse_args()
    afficher(simuler(o.vitesses.split(","), o.parties, o.joueurs, o.pe_heure, o.partage, o.univers), o.joueurs, o.pe_heure, o.partage)
