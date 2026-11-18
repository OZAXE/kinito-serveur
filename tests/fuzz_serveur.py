"""
Fuzz du serveur : des messages de travers envoyes a chaque jeu.

Un vieil onglet, un double appui, un client d une ancienne version ou un
petit malin peuvent envoyer n importe quoi : un champ absent, un texte a la
place d un nombre, une liste a la place d un objet, un index hors bornes,
une action hors de son tour. Le serveur doit refuser poliment, jamais lever
d exception (une exception dans un traitement peut laisser une table figee).

Pour chaque jeu, trois robots s assoient, lancent la partie, puis envoient
pendant quelques secondes un melange de messages valides et absurdes,
y compris les messages des AUTRES jeux et de l Enquete. A la fin, on verifie
qu aucune erreur n a ete loguee par le serveur et que la table repond encore.

Usage : python tests/fuzz_serveur.py [--duree 8] [--graine 1] [--jeu tous]
"""
import argparse
import asyncio
import json
import logging
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import uvicorn
import websockets

import serveur

PORT = int(os.environ.get("FUZZ_PORT", "18766"))

TYPES = [
    # Kinito
    "demarrer", "lancer", "annoncer", "reaction", "abandonner", "nouvelle_manche",
    # BeerBattle
    "bb_demarrer", "bb_placer", "bb_attaquer", "bb_ne_rien_faire", "bb_ramasser", "bb_deplacer",
    "bb_as_deplacer", "bb_dame", "bb_poser_arme",
    # Buckshot
    "bt_demarrer", "bt_composer", "bt_tirer", "bt_jouer_objet",
    # Picopoly
    "pp_demarrer", "pp_lancer", "pp_terminer", "pp_choix", "pp_construire", "pp_vendre",
    "pp_hypothequer", "pp_lever", "pp_prison_payer", "pp_prison_carte", "pp_proposer_echange",
    "pp_faillite", "pp_roulette",
    # Menteur
    "mt_demarrer", "mt_poser", "mt_menteur", "mt_cible",
    # Undercover
    "uc_demarrer", "uc_pret", "uc_fini", "uc_voter", "uc_deviner",
    # Telephone Dessine
    "td_demarrer", "td_contribuer", "td_brouillon", "td_suggestion", "td_reveler", "td_suivant",
    "td_auto", "td_voter", "td_reaction",
    # Casino
    "cs_demarrer", "cs_choisir", "cs_hall", "cs_pret", "cs_rl_miser", "cs_rl_annuler", "cs_bj_miser",
    "cs_bj_annuler", "cs_bj_tirer", "cs_bj_rester", "cs_bj_doubler", "cs_bj_splitter",
    "cs_pk_coucher", "cs_pk_suivre", "cs_pk_relancer",
    # Salon et Enquete
    "resync", "retour_salon", "ping", "enq_creer", "enq_config", "enq_lancer", "enq_annuler",
    "enq_portrait", "enq_consulter", "enq_acheter", "enq_accuser", "enq_bu", "enq_expertise",
    "enq_charge", "enq_sabotage", "enq_lire", "enq_repondu", "enq_refuser", "enq_quitter",
    "enq_bot_ajouter", "enq_bot_retirer", None, 3, "",
]
CHAMPS = ["argent_donne", "argent_recu", "arme", "cartes", "case", "categorie", "choix", "cible",
          "colonne", "decision", "difficulte", "donne", "dosage", "duree", "element",
          "gorgees_publiques", "id", "index_carte", "index_echange", "jeu", "jeux", "lieu", "ligne",
          "mise", "mode", "montant", "nom", "numero", "paiement", "pari", "plafond", "portrait",
          "recoit", "reel", "sabotage", "score", "score_cite", "suspect", "tapis", "total",
          "type_attaque", "unite", "univers", "valeur", "via_carte", "vitesse", "page",
          "famille", "undercover", "white", "mot", "contenu", "auto", "actif", "emoji", "album",
          "source", "etapes"]


def valeur_bizarre(rnd):
    return rnd.choice([
        None, True, False, 0, 1, 2, -1, 3, 7, 39, 40, 99, 10 ** 12, -10 ** 12, 1.5, float("nan") if False else 2.5,
        "", "x", "0", "1", "rouge", "n7", "tous", "prendre", "menteur", "roulette", "poker", "classique",
        "normal", "cc_mains_nues", "pe", "gorgee",
        [], [0], [0, 1, 2], [-1], ["a"], [0, 0], [None], list(range(10)),
        {}, {"0": 3}, {"1": -2}, {"a": "b"}, [[1]], {"0": [1]},
    ])


def message_au_hasard(rnd):
    m = {"type": rnd.choice(TYPES)}
    for c in rnd.sample(CHAMPS, rnd.randint(0, 5)):
        m[c] = valeur_bizarre(rnd)
    return m


class Erreurs(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages = []

    def emit(self, record):
        texte = record.getMessage()
        if record.exc_info:
            texte += "\n" + logging.Formatter().formatException(record.exc_info)
        self.messages.append(texte)


DEMARRER = {
    "kinito": {"type": "demarrer", "difficulte": "normal"},
    "beerbattle": {"type": "bb_demarrer"},
    "buckshot": {"type": "bt_demarrer"},
    "picolopoly": {"type": "pp_demarrer"},
    "menteur": {"type": "mt_demarrer", "mode": "chaos"},
    "casino": {"type": "cs_demarrer"},
    "undercover": {"type": "uc_demarrer", "famille": "melange"},
    "telephone": {"type": "td_demarrer", "vitesse": "flash", "mode": "speedrun"},
}


async def scenario(jeu, duree, graine):
    rnd = random.Random(graine)
    erreurs = Erreurs()
    config = uvicorn.Config(serveur.app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off")
    srv = uvicorn.Server(config)
    t_srv = asyncio.ensure_future(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    for nom in ("uvicorn.error", "uvicorn", "serveur", "asyncio", "fastapi"):
        logging.getLogger(nom).addHandler(erreurs)
    serveur.salons.clear()
    code = serveur.generer_code()
    connexions = []
    recus = {"n": 0}

    async def lire(ws):
        try:
            async for _ in ws:
                recus["n"] += 1
        except Exception:
            pass

    for nom in ("Alice", "Bruno", "Chloe"):
        ws = await websockets.connect(f"ws://127.0.0.1:{PORT}/ws/{code}/{nom}", ping_interval=None)
        connexions.append(ws)
        asyncio.ensure_future(lire(ws))
        await asyncio.sleep(0.05)
    await connexions[0].send(json.dumps(DEMARRER[jeu]))
    await asyncio.sleep(0.5)
    if serveur.salons[code].get("jeu") != jeu:
        erreurs.messages.append("la partie de " + jeu + " n'a pas demarre")

    fin = asyncio.get_event_loop().time() + duree
    envoyes = 0
    while asyncio.get_event_loop().time() < fin:
        ws = rnd.choice(connexions)
        m = message_au_hasard(rnd)
        # De temps en temps, on relance le jeu teste (et seulement lui : les
        # autres remplaceraient la partie et on ne fuzzerait plus ce jeu).
        if m["type"] in [d["type"] for d in DEMARRER.values()] or m["type"] == "retour_salon":
            m = dict(DEMARRER[jeu]) if rnd.random() < 0.3 else {"type": "resync"}
        brut = json.dumps(m) if rnd.random() > 0.02 else rnd.choice(["pas du json", "[1,2]", "42", "null"])
        try:
            await ws.send(brut)
        except Exception:
            pass
        envoyes += 1
        await asyncio.sleep(rnd.uniform(0, 0.01))
    await asyncio.sleep(1)
    # La table repond encore : un resync revient avec quelque chose.
    avant = recus["n"]
    try:
        await connexions[1].send(json.dumps({"type": "resync"}))
    except Exception:
        pass
    await asyncio.sleep(0.8)
    if recus["n"] == avant:
        erreurs.messages.append("la table ne repond plus a un resync")
    for ws in connexions:
        await ws.close()
    srv.should_exit = True
    await t_srv
    return envoyes, erreurs.messages


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--jeu", choices=list(DEMARRER) + ["tous"], default="tous")
    p.add_argument("--duree", type=float, default=8)
    p.add_argument("--graine", type=int, default=1)
    a = p.parse_args()
    jeux = list(DEMARRER) if a.jeu == "tous" else [a.jeu]
    total = 0
    for jeu in jeux:
        envoyes, erreurs = asyncio.run(scenario(jeu, a.duree, a.graine))
        uniques = []
        for e in erreurs:
            cle = e.strip().splitlines()[-1]
            if cle not in [u.strip().splitlines()[-1] for u in uniques]:
                uniques.append(e)
        print(f"== {jeu} : {envoyes} messages envoyes, {len(uniques)} erreur(s) distincte(s)")
        for e in uniques:
            print("   " + e[-1800:].replace("\n", "\n   "))
        total += len(uniques)
    print("OK" if not total else "ECHEC")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
