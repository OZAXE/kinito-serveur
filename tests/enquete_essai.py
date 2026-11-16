"""
Essais de l'Enquete, tranche 1 (sans reseau) :
- salle d attente : portraits, reglages de l hote, lancement ;
- tirage : solution coherente, tueur parmi les joueurs, de 4 a 12 joueurs ;
- temps : ouverture puis enquete, victoire du tueur a l heure butoir ;
- NON-FUITE : etat public sans solution ni tueur avant la fin, vue privee de
  meme forme pour tous ;
- couche branchee sur le serveur (connexions soiree), sauvegarde.

Usage : python tests/enquete_essai.py
"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import enquete as m
import serveur
from stockage import StockageMemoire

ECHECS = []


def verifier(cond, quoi):
    print(("  ok  " if cond else "  ECHEC ") + quoi)
    if not cond:
        ECHECS.append(quoi)


NOMS = ["Lea", "Max", "Zoe", "Tom", "Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Ida", "Jo"]


def partie(n, graine=1, vitesse=None):
    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    if vitesse:
        m.action(e, "Lea", {"type": "enq_config", "vitesse": vitesse}, 1000.0)
    portraits = [p["id"] for p in m.PACKS[e["univers"]]["portraits"]]
    for i in range(n):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": portraits[i]}, 1000.0)
    return e


def essai_salle_attente():
    print("Salle d'attente")
    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    ev = m.action(e, "Lea", {"type": "enq_portrait", "portrait": "comtesse"}, 1000.0)
    verifier(e["joueurs"] == [{"nom": "Lea", "portrait": "comtesse"}] and not ev, "Léa prend la comtesse")
    ev = m.action(e, "Max", {"type": "enq_portrait", "portrait": "comtesse"}, 1000.0)
    verifier(len(e["joueurs"]) == 1 and ev and ev[0]["msg"]["type"] == "enq_erreur", "  portrait déjà pris : refusé")
    m.action(e, "Max", {"type": "enq_portrait", "portrait": "docteur"}, 1000.0)
    m.action(e, "Max", {"type": "enq_portrait", "portrait": "capitaine"}, 1000.0)
    verifier([j["portrait"] for j in e["joueurs"]] == ["comtesse", "capitaine"], "  on peut changer de portrait")
    v = e["version"]
    ev = m.action(e, "Max", {"type": "enq_config", "vitesse": "flash"}, 1000.0)
    verifier(e["version"] == v and ev[0]["msg"]["type"] == "enq_erreur", "seul l'hôte règle l'enquête")
    m.action(e, "Lea", {"type": "enq_config", "vitesse": "flash"}, 1000.0)
    verifier(e["vitesse"] == "flash" and e["jeux"] == ["kinito", "menteur", "buckshot"], "Flash : jeux rapides seulement")
    m.action(e, "Lea", {"type": "enq_config", "jeux": ["kinito", "casino"]}, 1000.0)
    verifier(e["jeux"] == ["kinito"], "  un jeu non autorisé par la vitesse est écarté")
    ev = m.action(e, "Lea", {"type": "enq_config", "duree": 60}, 1000.0)
    verifier(ev and ev[0]["msg"]["type"] == "enq_erreur", "durée trop courte refusée")
    m.action(e, "Lea", {"type": "enq_config", "unite": "pompe"}, 1000.0)
    verifier(e["unite"] == "pompe", "unité de pénalité réglable (mode sans alcool)")
    ev = m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0)
    verifier(e["phase"] == "lobby" and ev[0]["msg"]["type"] == "enq_erreur", "lancement refusé à 2 joueurs")

    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    for i in range(12):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": m.PACKS["manoir"]["portraits"][i]["id"]}, 1000.0)
    ev = m.action(e, NOMS[12], {"type": "enq_portrait", "portrait": "voyante"}, 1000.0)
    verifier(len(e["joueurs"]) == 12, "12 joueurs au plus")


def essai_tirage():
    print("Tirage")
    bons = True
    tueurs = set()
    for n in range(4, 13):
        for vitesse in ("flash", "express", "soiree", "marathon"):
            for g in range(30):
                e = partie(n, vitesse=vitesse)
                m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(g))
                k = m.CONFIG["vitesses"][vitesse]["lieux_armes"]
                s = e["solution"]
                bons &= (e["phase"] == "ouverture" and len(e["lieux"]) == k and len(e["armes"]) == k
                         and s["lieu"] in e["lieux"] and s["arme"] in e["armes"]
                         and s["tueur"] in [j["nom"] for j in e["joueurs"]] and s["tueur"] == e["tueur"])
                tueurs.add(s["tueur"])
    verifier(bons, "de 4 à 12 joueurs, à chaque vitesse : solution cohérente et lieux/armes retenus selon la vitesse")
    verifier(len(tueurs) == 12, "  chacun peut être tiré comme tueur")


def essai_temps():
    print("Temps")
    e = partie(5)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(3))
    verifier(m.prochaine_echeance(e) == 1000.0 + m.CONFIG["duree_ouverture"], "fin de l'ouverture programmée")
    m.expirer(e, 1000.0 + m.CONFIG["duree_ouverture"])
    verifier(e["phase"] == "enquete" and m.prochaine_echeance(e) == e["fin_a"], "l'enquête commence après l'ouverture")
    m.expirer(e, e["fin_a"])
    verifier(e["phase"] == "fin" and e["vainqueur"] == "tueur", "heure butoir : le tueur gagne")
    v = m.vue_publique(e, e["fin_a"])
    verifier(v["fin"]["solution"] == e["solution"] and e["tueur"] in v["fin"]["recit"], "la fin révèle la solution et le récit")


def essai_non_fuite():
    print("Non-fuite")
    e = partie(8)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(7))
    for phase_t in (1000.0, 1000.0 + m.CONFIG["duree_ouverture"]):
        m.expirer(e, phase_t)
        pub = m.vue_publique(e, phase_t)
        texte = json.dumps(pub)
        verifier(pub["fin"] is None and "tueur" not in pub and '"solution"' not in texte.replace('"fin": null', ''),
                 f"phase {e['phase']} : ni solution ni tueur dans l'état public")
        verifier(e["solution"]["arme"] in pub["armes"], "  (les armes retenues, elles, sont publiques)")
    vues = [m.vue_privee(e, j["nom"]) for j in e["joueurs"]]
    verifier(len({tuple(sorted(v)) for v in vues}) == 1, "vue privée : mêmes clés pour les 8 joueurs")
    verifier(sum(v["role"] == "tueur" for v in vues) == 1 and all(v["solution"] is None for v in vues if v["role"] != "tueur"),
             "  un seul tueur, et lui seul connaît la solution")
    spect = m.vue_privee(e, "Inconnu")
    verifier(sorted(spect) == sorted(vues[0]) and spect["role"] is None, "  même forme pour un spectateur")


class FauxWS:
    def __init__(self):
        self.recus = []

    async def send_text(self, t):
        self.recus.append(json.loads(t))

    async def close(self, code=1000):
        pass

    def de_type(self, t):
        return [x for x in self.recus if x.get("type") == t]


async def essai_couche():
    print("Couche branchée sur le serveur")
    memoire = StockageMemoire()
    await serveur.demarrage(memoire)
    code = (await serveur.creer_salon())["code"]
    ws = {}
    for nom in NOMS[:5]:
        ws[nom] = FauxWS()
        await serveur.abonner(code, nom, ws[nom])
    await serveur.couche_action(code, "Lea", {"type": "enq_creer"})
    verifier("enq" in serveur.couches.get(code, {}), "Léa crée l'enquête de la soirée")
    verifier(all(w.de_type("enq_etat") for w in ws.values()), "  tous les abonnés reçoivent l'état")
    portraits = [p["id"] for p in m.PACKS["manoir"]["portraits"]]
    for i, nom in enumerate(NOMS[:5]):
        await serveur.couche_action(code, nom, {"type": "enq_portrait", "portrait": portraits[i]})
    for w in ws.values():
        w.recus.clear()
    await serveur.couche_action(code, "Lea", {"type": "enq_lancer"})
    moi = {n: w.de_type("enq_moi")[-1] for n, w in ws.items()}
    verifier(sum(x["role"] == "tueur" for x in moi.values()) == 1, "lancée : un seul tueur parmi les 5")
    verifier(len({tuple(sorted(x)) for x in moi.values()}) == 1, "  messages privés de même forme pour tous")
    pub = [w.de_type("enq_etat")[-1] for w in ws.values()]
    verifier(all(p == pub[0] for p in pub) and pub[0]["fin"] is None, "  état public identique pour tous, sans solution")
    tueur = [n for n, x in moi.items() if x["role"] == "tueur"][0]
    verifier(all(tueur not in json.dumps(w.de_type("enq_moi")[-1]) for n, w in ws.items() if n != tueur),
             "  aucun message privé ne nomme le tueur à un autre joueur")
    await serveur.sauvegarde.vider()
    sauvee = await memoire.lire(f"couche:{code}:enq")
    verifier(sauvee and sauvee["donnees"]["solution"]["tueur"] == tueur, "l'enquête est sauvegardée")
    serveur.couches.clear()
    await serveur.demarrage(memoire)
    rechargee = serveur.couches.get(code, {}).get("enq")
    verifier(rechargee is not None and rechargee.etat["tueur"] == tueur, "  et rechargée après redémarrage")
    w = FauxWS()
    await serveur.abonner(code, "Max", w)
    verifier(w.de_type("enq_pack") and w.de_type("enq_etat") and w.de_type("enq_moi"),
             "un joueur qui se reconnecte reçoit le pack, l'état et sa vue")
    await serveur.arret()


essai_salle_attente()
essai_tirage()
essai_temps()
essai_non_fuite()
asyncio.run(essai_couche())
print("OK" if not ECHECS else f"{len(ECHECS)} ECHEC(S)")
sys.exit(1 if ECHECS else 0)
