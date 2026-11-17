"""
Essais cibles : retour au Kinito en attente, BeerBattle avec un seul present,
validation des cartes de BeerBattle. Hors reseau (fausses connexions).

Usage : python tests/beerbattle_kinito_essai.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import serveur

echecs = []


def verifier(condition, texte):
    print(("  ok  " if condition else "  ECHEC  ") + texte)
    if not condition:
        echecs.append(texte)


class FauxWS:
    def __init__(self):
        self.query_params = {}
        self.recus = []

    async def send_text(self, t):
        self.recus.append(json.loads(t))

    async def close(self, code=1000):
        pass

    def de_type(self, t):
        return [m for m in self.recus if m.get("type") == t]


async def asseoir(code, noms):
    salon = serveur.salons.setdefault(code, serveur.nouveau_salon())
    ws, joueurs = {}, {}
    for nom in noms:
        ws[nom] = FauxWS()
        joueurs[nom] = await serveur.arrivee(code, salon, ws[nom], nom)
    return salon, ws, joueurs


async def partir(code, salon, joueur):
    async with serveur.verrou(salon):
        await serveur.depart(code, joueur, joueur["ws"])


async def revenir(code, salon, nom):
    w = FauxWS()
    async with serveur.verrou(salon):
        j = await serveur.arrivee(code, salon, w, nom)
    return w, j


async def essai_kinito():
    print("Kinito : retour pendant une partie en attente")
    code = "KINI"
    salon, ws, j = await asseoir(code, ["Lea", "Tom"])
    await serveur.traiter_message(code, j["Lea"], {"type": "demarrer"})
    await partir(code, salon, j["Tom"])
    verifier(salon["etat"]["phase"] == "attente", "Tom part : la partie se met en attente")
    ws["Lea"].recus.clear()
    _, _ = await revenir(code, salon, "Tom")
    etats = ws["Lea"].de_type("etat")
    verifier(salon["etat"]["phase"] == "lancer", "Tom revient : la partie reprend")
    verifier(bool(etats) and etats[-1]["phase"] == "lancer",
             "  Lea (restee a table) recoit l etat de reprise")


def bb_preparer(salon):
    """Partie en phase de jeu, positions fixees, a Lea (0) de jouer."""
    e = salon["etat"]
    e["phase"] = "jeu"
    e["joueur_courant"] = 0
    e["action_faite"] = False
    for i in range(e["nb_joueurs"]):
        e["positions"][i] = {"ligne": 0, "colonne": i}
    return e


def carte(t, v=0):
    return {"type": t, "valeur": v, "rouge": True, "libelle": t, "symbole": "?"}


async def essai_cartes():
    print("BeerBattle : validation des cartes")
    code = "BEER"
    salon, ws, j = await asseoir(code, ["Lea", "Tom", "Zoe"])
    await serveur.traiter_message(code, j["Lea"], {"type": "bb_demarrer"})
    lea = j["Lea"]

    async def essai(message, main, armes=(), attendu_fait=False, texte=""):
        e = bb_preparer(salon)
        e["mains"][0] = list(main)
        e["armes"][0] = list(armes)
        e["verres"] = [0] * e["nb_joueurs"]
        e["elimine"] = [False] * e["nb_joueurs"]
        avant = json.dumps(e["mains"][0])
        await serveur.traiter_message(code, lea, message)
        fait = e["action_faite"]
        verifier(fait == attendu_fait and (fait or json.dumps(e["mains"][0]) == avant), texte)

    dep = carte("deplacement", 3)
    gor = carte("gorgee", 6)
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "cc_gorgee"}, [gor],
                texte="cc_gorgee sans index_carte : refuse, sans exception")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "cc_gorgee", "index_carte": 5}, [gor],
                texte="index_carte hors bornes : refuse")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "cc_gorgee", "index_carte": True}, [gor],
                texte="index_carte booleen : refuse")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "cc_gorgee", "index_carte": 0}, [dep],
                texte="une carte Depl. 3 ne passe pas pour des gorgees")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "cc_gorgee", "index_carte": 0}, [gor],
                attendu_fait=True, texte="  une vraie carte de gorgees passe")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "as", "index_carte": 0}, [carte("roi")],
                texte="un Roi ne passe pas pour un As")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "arme", "index_carte": 0}, [gor],
                texte="arme sans arme posee : refuse")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "arme"}, [gor], [carte("roi")],
                texte="arme sans index_carte : refuse")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "joker", "index_carte": 0}, [carte("joker")],
                texte="Joker sans arme posee : refuse")
    await essai({"type": "bb_attaquer", "cible": 2, "type_attaque": "joker", "index_carte": 0}, [carte("joker")],
                [carte("valet")], attendu_fait=True, texte="  Joker avec un Valet a portee (2 cases) : accepte")
    salon["etat"]["positions"][2] = {"ligne": 5, "colonne": 5}
    e = bb_preparer(salon)
    e["positions"][2] = {"ligne": 5, "colonne": 5}
    e["mains"][0], e["armes"][0] = [carte("joker")], [carte("valet")]
    await serveur.traiter_message(code, lea, {"type": "bb_attaquer", "cible": 2, "type_attaque": "joker",
                                              "index_carte": 0})
    verifier(not e["action_faite"], "  Joker avec une cible hors de portee : refuse")
    await essai({"type": "bb_attaquer", "cible": 1, "type_attaque": "inconnu"}, [gor],
                texte="type d attaque inconnu : rien ne se passe")
    await essai({"type": "bb_dame", "cible": 1, "index_carte": 0}, [gor],
                texte="une carte de gorgees ne passe pas pour une Dame")
    await essai({"type": "bb_dame", "cible": 1}, [carte("dame")], texte="Dame sans index_carte : refuse")
    await essai({"type": "bb_dame", "cible": 1, "index_carte": 0}, [carte("dame")],
                attendu_fait=True, texte="  une vraie Dame passe")
    await essai({"type": "bb_poser_arme", "index_carte": 3}, [carte("roi")], texte="poser une arme hors bornes : refuse")
    await essai({"type": "bb_poser_arme"}, [carte("roi")], texte="poser une arme sans index : refuse")
    await essai({"type": "bb_as_deplacer", "ligne": 0, "colonne": 1, "cible": 1, "index_carte": 0}, [carte("dame")],
                texte="As-deplacement avec une Dame : refuse (et pas de deplacement)")
    verifier(salon["etat"]["positions"][0] == {"ligne": 0, "colonne": 0}, "  la position n a pas bouge")
    await essai({"type": "bb_as_deplacer", "ligne": 0, "colonne": 1, "cible": 1, "index_carte": 0}, [carte("as")],
                attendu_fait=True, texte="  avec un As : accepte")

    # Ramassage : index_echange valide
    e = bb_preparer(salon)
    e["action_faite"] = True
    e["plateau"][0][0] = carte("dame")
    e["mains"][0] = [gor]
    await serveur.traiter_message(code, lea, {"type": "bb_ramasser", "decision": "echanger", "index_echange": 7})
    verifier(e["joueur_courant"] == 0 and e["mains"][0] == [gor], "echange avec un index hors bornes : refuse")
    await serveur.traiter_message(code, lea, {"type": "bb_ramasser", "decision": "echanger"})
    verifier(e["joueur_courant"] == 0, "echange sans index : refuse, sans exception")
    await serveur.traiter_message(code, lea, {"type": "bb_ramasser", "decision": "echanger", "index_echange": 0})
    verifier(e["joueur_courant"] != 0 and e["mains"][0][0]["type"] == "dame", "  echange valide : accepte, tour suivant")
    serveur.arreter_minuteurs(salon)


async def essai_seul():
    print("BeerBattle : dernier joueur present")
    serveur.BB_DELAI_SEUL = 0.2
    code = "SEUL"
    salon, ws, j = await asseoir(code, ["Lea", "Tom", "Zoe"])
    await serveur.traiter_message(code, j["Lea"], {"type": "bb_demarrer"})
    bb_preparer(salon)
    await partir(code, salon, j["Tom"])
    verifier(salon.get("bb_seul") is None, "un depart sur trois : pas de delai")
    await partir(code, salon, j["Zoe"])
    verifier(salon.get("bb_seul") is not None, "Lea reste seule en lice : delai arme")
    verifier(salon["etat"]["phase"] != "fini", "  pas de victoire immediate")
    await revenir(code, salon, "Zoe")
    verifier(salon.get("bb_seul") is None, "Zoe revient : delai annule")
    await asyncio.sleep(0.4)
    verifier(salon["etat"]["phase"] != "fini", "  et pas de victoire a l echeance")
    await partir(code, salon, salon["joueurs"][2])
    await asyncio.sleep(0.4)
    verifier(salon["etat"]["phase"] == "fini" and salon["etat"]["gagnant_nom"] == "Lea",
             "personne n est revenu a l echeance : Lea gagne")
    verifier(bool(ws["Lea"].de_type("bb_victoire")), "  bb_victoire envoye")

    # Un elimine absent ne compte pas ; le dernier present lui-meme part : rien.
    code = "SEUM"
    salon, ws, j = await asseoir(code, ["Lea", "Tom", "Zoe"])
    await serveur.traiter_message(code, j["Lea"], {"type": "bb_demarrer"})
    bb_preparer(salon)
    salon["etat"]["elimine"][1] = True
    await partir(code, salon, j["Tom"])
    verifier(salon.get("bb_seul") is None, "un elimine qui part : pas de delai (deux en lice presents)")
    await partir(code, salon, j["Zoe"])
    await partir(code, salon, j["Lea"])
    await asyncio.sleep(0.4)
    verifier(salon["etat"]["phase"] != "fini", "plus personne a table : pas de vainqueur designe")
    serveur.arreter_minuteurs(salon)


async def principal():
    await serveur.demarrage(__import__("stockage").StockageMemoire())
    await essai_kinito()
    await essai_cartes()
    await essai_seul()


if __name__ == "__main__":
    asyncio.run(principal())
    print("OK" if not echecs else f"ECHEC ({len(echecs)})")
    sys.exit(1 if echecs else 0)
