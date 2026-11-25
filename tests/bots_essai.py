"""
Essais des bots du mode test (pseudo Admin01), sans reseau ni navigateur.

Pour chaque jeu qui a un cerveau (bots.CERVEAUX) : Admin01 s assoit, ajoute
des bots, lance la partie, et joue lui aussi avec le cerveau du jeu (comme
s il etait un bot, mais par ce script). On verifie que la partie AVANCE
vraiment (manches jouees, fin atteinte), sans erreur de cerveau ni action
refusee en boucle. Temps de reflexion et animations raccourcis.

Verifie aussi : un autre pseudo ne peut pas ajouter de bot ; les bots ne
retiennent pas un salon que tous les humains ont quitte ; retirer un bot ;
l Enquete ne compte pas les bots parmi les presents.

Usage : python tests/bots_essai.py [jeu ...]
"""
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import serveur
import bots
import menteur
import justepinte
import quidenous
import loupgarou
import pichenette
import casino
import tournee
import undercover

bots.VITESSE = 0.01
serveur.BOT_TICK = 0.05
serveur.BOT_PAUSE_ERREUR = 0.3
serveur.DELAI_SALON_VIDE = 0.3
menteur.MT_DELAI_REVELATION = 0.05
menteur.MT_DELAI_PAR_TIR = 0.02
pichenette._D.update(apres_tir=0.05, apres_but=0.05)
tournee._D.update(deplacement_base=0.05, deplacement_par_joueur=0.01, deplacement_par_case=0.0,
                  deplacement_par_effet=0.0)
undercover.UC_DELAI_REVELATION = 0.05
casino.CS_DELAI_ROULETTE = 0.1
casino.CS_DELAI_BJ_FIN = 0.1
casino.CS_DELAI_PAR_CARTE = 0.01
casino.CS_DELAI_POKER_FIN = 0.1
casino.CS_DELAI_POKER_CARTE = 0.01

ECHECS = []
ERREURS_CERVEAU = []


class Attrape(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            ERREURS_CERVEAU.append(record.getMessage())


serveur.journal.addHandler(Attrape())


def verifier(cond, quoi):
    print(("ok   " if cond else "ECHEC") + " " + quoi)
    if not cond:
        ECHECS.append(quoi)


class FauxWS:
    def __init__(self):
        self.query_params = {}
        self.recus = []

    async def send_text(self, t):
        self.recus.append(json.loads(t))

    async def close(self, code=1000):
        pass

    def types(self):
        return [m.get("type") for m in self.recus]


async def table(code, nb_bots, nom="Admin01"):
    serveur.salons[code] = serveur.nouveau_salon()
    salon = serveur.salons[code]
    ws = FauxWS()
    async with serveur.verrou(salon):
        humain = await serveur.arrivee(code, salon, ws, nom)
    for _ in range(nb_bots):
        rep = await serveur.route_ajouter_bot(code, par=nom)
        assert isinstance(rep, dict), rep
    return salon, humain, ws


async def jouer_humain(code, salon, humain, mem, extra=None):
    """Admin01 joue avec le cerveau du jeu (et appuie sur « continuer » via extra)."""
    etat, jeu = salon.get("etat"), salon.get("jeu")
    if etat is None:
        return
    action = bots.CERVEAUX[jeu](etat, humain["index"], mem)
    if action is None and extra is not None:
        action = extra(etat)
    if action:
        action = {k: v for k, v in action.items() if not k.startswith("_")}
        async with serveur.verrou(salon):
            await serveur.traiter_message(code, humain, action)


async def partie(code, jeu, demarrage, nb_bots, fini, extra=None, duree=40, apres=None):
    erreurs_avant = len(ERREURS_CERVEAU)
    salon, humain, ws = await table(code, nb_bots)
    async with serveur.verrou(salon):
        await serveur.traiter_message(code, humain, demarrage)
        if apres:
            await serveur.traiter_message(code, humain, apres)
    verifier(salon.get("jeu") == jeu, f"{jeu} : la partie demarre avec {nb_bots} bots")
    mem = {"vus": []}
    fin = time.time() + duree
    while time.time() < fin and not fini(salon, ws):
        await jouer_humain(code, salon, humain, mem, extra)
        await asyncio.sleep(0.02)
    verifier(fini(salon, ws), f"{jeu} : la partie avance jusqu au bout avec les bots")
    les_bots = [j for j in salon["joueurs"] if j.get("bot")]
    verifier(all(j.get("actions", 0) > 0 for j in les_bots),
             f"{jeu} : chaque bot a vraiment joue ({[j.get('actions', 0) for j in les_bots]} actions)")
    nouvelles = ERREURS_CERVEAU[erreurs_avant:]
    verifier(not nouvelles, f"{jeu} : aucune erreur de bot" + (f" ({nouvelles[:2]})" if nouvelles else ""))
    refus = [m for m in ws.recus if m.get("type") == "erreur"]
    verifier(len(refus) < 5, f"{jeu} : pas de refus en boucle chez Admin01 ({len(refus)})")
    # Fin : on part, les bots s arretent avec le salon.
    async with serveur.verrou(salon):
        await serveur.depart(code, humain, ws)
    await asyncio.sleep(0.6)
    verifier(code not in serveur.salons, f"{jeu} : le salon se libere sans humain malgre les bots")
    await asyncio.sleep(0.1)
    taches = [j.get("tache") for j in salon["joueurs"] if j.get("bot")]
    verifier(all(t is None or t.done() for t in taches), f"{jeu} : les bots s arretent avec le salon")


def nb(ws, t):
    return sum(1 for m in ws.recus if m.get("type") == t)


PARTIES = {
    "kinito": lambda: partie(
        "KINI", "kinito", {"type": "demarrer"}, 3,
        lambda s, ws: nb(ws, "fin_manche") >= 6,
        extra=lambda e: {"type": "nouvelle_manche"} if e.get("phase") == "resultat" else None),
    "menteur": lambda: partie(
        "MENT", "menteur", {"type": "mt_demarrer", "mode": "classique"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=90),
    "menteur_chaos": lambda: partie(
        "CHAO", "menteur", {"type": "mt_demarrer", "mode": "chaos"}, 4,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=90),
    "buckshot": lambda: partie(
        "BUCK", "buckshot", {"type": "bt_demarrer"}, 3,
        lambda s, ws: nb(ws, "bt_manche_fin") >= 3),
    "justepinte": lambda: partie(
        "JUST", "justepinte", {"type": "jp_demarrer", "manches": 5}, 4,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin"),
    "quidenous": lambda: partie(
        "QUID", "quidenous", {"type": "qn_demarrer", "manches": 5}, 4,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin"),
    "loupgarou": lambda: partie(
        "LOUP", "loupgarou", {"type": "lg_demarrer"}, 7,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=120),
    "loupgarou_roles": lambda: partie(
        "LOUR", "loupgarou", {"type": "lg_demarrer", "loups": 3, "capitaine": True,
                              "speciaux": list(loupgarou.LG_SPECIAUX)}, 15,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=180),
    "picolopoly": lambda: partie(
        "PICO", "picolopoly", {"type": "pp_demarrer", "difficulte": "normal"}, 3,
        lambda s, ws: nb(ws, "pp_tour") >= 30, duree=120),
    "casino_roulette": lambda: partie(
        "CSRL", "casino", {"type": "cs_demarrer"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("manche", 0) >= 4, apres={"type": "cs_choisir", "jeu": "roulette"},
        extra=casino_humain),
    "casino_blackjack": lambda: partie(
        "CSBJ", "casino", {"type": "cs_demarrer"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("manche", 0) >= 4, apres={"type": "cs_choisir", "jeu": "blackjack"},
        extra=casino_humain),
    "casino_poker": lambda: partie(
        "CSPK", "casino", {"type": "cs_demarrer"}, 4,
        lambda s, ws: (s.get("etat") or {}).get("manche", 0) >= 6, apres={"type": "cs_choisir", "jeu": "poker"},
        duree=90),
    "pichenette": lambda: partie(
        "PICH", "pichenette", {"type": "pf_demarrer", "buts": 3}, 1,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=240),
    "pichenette_equipes": lambda: partie(
        "PICE", "pichenette", {"type": "pf_demarrer", "buts": 3}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=240),
    "pichenette_objets": lambda: partie(
        "PICO", "pichenette", {"type": "pf_demarrer", "buts": 3, "mode": "objets"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=240),
    "tournee": lambda: partie(
        "TOUR", "tournee", {"type": "tr_demarrer", "tours": 5}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=120),
    "conquete": lambda: partie(
        "ROYA", "conquete", {"type": "sw_demarrer"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=240),
    "beerbattle": lambda: partie(
        "BEER", "beerbattle", {"type": "bb_demarrer"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fini", duree=240),
    "undercover": lambda: partie(
        "UNDE", "undercover", {"type": "uc_demarrer"}, 5,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=120),
    "telephone": lambda: partie(
        "TELE", "telephone", {"type": "td_demarrer", "vitesse": "rapide"}, 3,
        lambda s, ws: (s.get("etat") or {}).get("phase") == "fin", duree=240, extra=hote_telephone),
}


def hote_telephone(etat):
    """Admin01 est l hote du Gribouillophone : il lance et fait defiler la revelation."""
    if etat["phase"] == "pret":
        return {"type": "td_reveler"}
    if etat["phase"] in ("revelation", "resultat_album"):
        return {"type": "td_suivant"}
    return None


def casino_humain(etat):
    return None


async def essai_acces():
    salon, humain, ws = await table("ACCE", 0, nom="Lea")
    rep = await serveur.route_ajouter_bot("ACCE", par="Lea")
    verifier(not isinstance(rep, dict), "un pseudo ordinaire ne peut pas ajouter de bot")
    rep = await serveur.route_ajouter_bot("ACCE", par="Admin01")
    verifier(not isinstance(rep, dict), "Admin01 doit etre assis a la table pour ajouter un bot")
    salon, humain, ws = await table("ACCF", 2)
    info = await serveur.info_salon("ACCF")
    verifier(len(info["bots"]) == 2 and all(n.startswith("Bot ") for n in info["bots"]),
             "/salon donne la liste des bots")
    verifier("Admin01" in serveur._noms_presents("ACCF") and len(serveur._noms_presents("ACCF")) == 1,
             "les bots ne comptent pas parmi les presents de la soiree (Enquete)")
    rep = await serveur.route_retirer_bot("ACCF", par="admin01", nom=info["bots"][0])
    verifier(rep["bots"] == info["bots"][1:], "retirer un bot")
    verifier(len(salon["joueurs"]) == 2, "en salle d attente, le bot retire libere sa place")
    for code in ("ACCE", "ACCF"):
        serveur.arreter_minuteurs(serveur.salons[code])
        for j in serveur.salons[code]["joueurs"]:
            if j.get("tache"):
                j["tache"].cancel()
        serveur.salons.pop(code, None)


async def principal():
    demandes = sys.argv[1:] or list(PARTIES)
    await essai_acces()
    for nom in demandes:
        await PARTIES[nom]()
    if ECHECS:
        print(f"\n{len(ECHECS)} echec(s)")
        sys.exit(1)
    print("\nTout est bon.")


asyncio.run(principal())
