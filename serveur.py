"""
Serveur multijoueur pour le Kinito, BeerBattle, Buckshot, Picopoly, Menteur et le Casino.
Technologie : FastAPI + WebSockets pour la communication temps reel.

Architecture :
- Un joueur cree un salon (code a 4 lettres)
- Les autres rejoignent avec ce code
- Chaque action est envoyee au serveur via WebSocket
- Le serveur met a jour l etat et le renvoie a tous les joueurs
"""
import math
import asyncio
import json
import logging
import random
import secrets
import string
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
# Le moteur de Picolopoly vit dans son propre fichier : c est un Monopoly
# complet, et il est pur (ni reseau ni asyncio), donc testable hors ligne.
from picolopoly import (
    PP_CASES, PP_JOUEURS_MIN, PP_JOUEURS_MAX,
    pp_initialiser, pp_ajouter_joueur, pp_action, pp_expirer, pp_armer,
    pp_etat_public, pp_prochaine_echeance, pp_attendu,
)
# Menteur (bluff aux cartes + roulette) : meme principe, moteur pur a part.
from menteur import (
    MT_JOUEURS_MIN, MT_JOUEURS_MAX,
    mt_initialiser, mt_action, mt_expirer, mt_etat_public, mt_main,
    mt_prochaine_echeance, mt_joueur_parti, mt_joueur_revenu,
)
# Casino (roulette, blackjack, poker) : meme principe, moteur pur a part.
from casino import (
    CS_JOUEURS_MIN, CS_JOUEURS_MAX,
    cs_initialiser, cs_ajouter_joueur, cs_action, cs_expirer, cs_etat_public,
    cs_main_poker, cs_prochaine_echeance, cs_joueur_parti, cs_joueur_revenu,
)

# =============================================================
# CREATION DE L APPLICATION FASTAPI
# =============================================================
app = FastAPI()

# CORS : autorise les pages HTML a se connecter au serveur
# meme si elles viennent d un autre domaine (important pour GitHub Pages).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================
# STOCKAGE DES SALONS EN MEMOIRE
# Un dictionnaire qui associe un code de salon a son etat.
# Exemple : salons["ABCD"] = { "joueurs": [...], "etat": {...} }
# =============================================================
salons = {}

journal = logging.getLogger("serveur")

# =============================================================
# PRESENCE DES JOUEURS
# Un telephone verrouille ne ferme pas toujours sa connexion : elle reste
# "ouverte" sans que plus rien ne passe. Le serveur croyait le joueur la, et
# si c etait son tour la partie restait figee. Les clients envoient donc un
# battement de coeur ({"type": "ping"}) toutes les quelques secondes ; un
# client qui en a deja envoye et qui se tait plus de DELAI_SILENCE secondes
# est traite comme parti (sa place est gardee, il la reprend en revenant).
# Les anciens clients, qui n envoient pas de battement, ne sont pas concernes.
# =============================================================
DELAI_SILENCE = 25
# Un salon dont tout le monde est parti est garde un moment : un groupe dont
# tous les telephones se verrouillent en meme temps retrouve sa partie.
DELAI_SALON_VIDE = 900
# Un envoi vers une connexion bloquee ne doit pas figer tout le salon.
DELAI_ENVOI = 5

_veilleur = None

# Affichee sur la page d accueil du serveur : permet de verifier qu un
# deploiement est bien en ligne.
VERSION = "2026-11-15 casino"


# =============================================================
# UTILITAIRES
# =============================================================

def generer_code():
    """Genere un code de salon unique a 4 lettres."""
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in salons:
            return code


def nouveau_salon():
    return {"joueurs": [], "etat": None, "jeu": None}


def verrou(salon):
    """
    Un verrou par salon : les messages des joueurs, les arrivees et les
    departs sont traites un par un. Sans lui, deux evenements simultanes
    (un retour pendant une action, deux clics...) s entremelaient aux
    "await" et laissaient l etat a moitie modifie.
    """
    v = salon.get("verrou")
    if v is None:
        v = salon["verrou"] = asyncio.Lock()
    return v


def meme_prenom(a, b):
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


async def envoyer(joueur, message):
    """
    Envoie un message a un seul joueur, sans jamais lever d exception.
    Un envoi rate (connexion morte) ne doit pas interrompre l action du
    joueur qui l a declenche : avant, l exception remontait et coupait la
    connexion de CET autre joueur, au milieu de son tour.
    """
    ws = joueur.get("ws")
    if ws is None or joueur.get("parti"):
        return False
    try:
        await asyncio.wait_for(ws.send_text(json.dumps(message)), DELAI_ENVOI)
        return True
    except Exception:
        return False


async def fermer(ws, code=1000):
    try:
        await asyncio.wait_for(ws.close(code), 3)
    except Exception:
        pass


async def diffuser(code_salon, message):
    """
    Envoie un message JSON a TOUS les joueurs d un salon.
    C est la fonction cle du multijoueur : quand quelque chose
    se passe, tout le monde est informe instantanement.
    """
    salon = salons.get(code_salon)
    if not salon:
        return
    # Un envoi rate ne marque plus la place comme vide : c etait fait sans
    # prevenir le jeu, et le depart n etait ensuite plus jamais traite
    # (le joueur restait attendu pour toujours). Le depart est detecte par
    # la fin de sa connexion, ou par le silence de ses battements de coeur.
    for joueur in list(salon["joueurs"]):
        if not joueur.get("parti"):
            await envoyer(joueur, message)


async def envoyer_indices(code_salon):
    """
    Renumerote les joueurs selon l ordre de la liste et envoie a chacun
    son propre index. Sans ca un client doit se reconnaitre a son prenom,
    ce qui casse des que deux joueurs portent le meme.
    """
    salon = salons.get(code_salon)
    if not salon:
        return
    en_partie = salon.get("etat") is not None
    for i, j in enumerate(salon["joueurs"]):
        # En pleine partie on ne renumerote pas : ca deplacerait le tour.
        if not en_partie:
            j["index"] = i
        if j.get("parti"):
            continue
        await envoyer(j, {
            "type": "ton_index",
            "index": j["index"],
            "nom": j["nom"],
            # Jeton de session : permet de retrouver sa place apres une
            # coupure, depuis n importe quel chemin (bouton, lien, code).
            "jeton": j.get("jeton"),
        })


def construire_etat_public(code_salon):
    """
    Construit l etat visible par tous les joueurs :
    - noms, ordre de jeu, sens de rotation
    - annonce actuelle et precedente
    - si c est la premiere annonce
    NE contient pas le vrai score des des (secret).
    """
    salon = salons[code_salon]
    etat = salon["etat"]
    return {
        "type": "etat",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "joueur_courant": etat["joueur_courant"],
        "sens": etat["sens"],
        "annonce_precedente": etat["annonce_precedente"],
        "premiere_annonce": etat["premiere_annonce"],
        "phase": etat["phase"],
        "message": etat.get("message", ""),
        "partis": etat.get("partis", []),
        "difficulte": etat.get("difficulte", DIFFICULTE_DEFAUT),
    }


# =============================================================
# LOGIQUE DU KINITO
# Toutes les regles du jeu sont ici, cote serveur.
# Le serveur est l arbitre unique : on ne peut pas tricher.
# =============================================================

TABLEAU_GORGEES = {
    32: [3, 6],
    41: [4, 8], 42: [4, 8], 43: [4, 8],
    52: [5, 10], 53: [5, 10], 54: [5, 10],
    61: [6, 12], 62: [6, 12], 63: [6, 12], 64: [6, 12], 65: [6, 12],
    11: [7, 14], 22: [8, 16], 33: [9, 18], 44: [10, 20],
    55: [11, 22], 66: [12, 24],
    21: [9, 18],
}

ECHELLE = [
    31,
    32, 41, 42, 43, 52, 53, 54, 61, 62, 63, 64, 65,
    11, 22, 33, 44, 55, 66,
    21,
]

SCORE_CHANGE_SENS = 31
SCORE_ANNULE = 51

# 31 et 51 se resolvent des le lancer : on ne peut pas les annoncer.
# 31 reste dans ECHELLE car il sert de plancher au classement.
SCORES_ANNONCABLES = [s for s in ECHELLE if s != SCORE_CHANGE_SENS]

# Difficulte : multiplicateur applique aux gorgees en fin de manche.
# Par defaut 1.0, pour que les clients qui n envoient pas de difficulte
# (la version 2D) gardent exactement le comportement d avant.
DIFFICULTES = {"facile": 0.5, "normal": 0.75, "difficile": 1.0}
DIFFICULTE_DEFAUT = "difficile"


def appliquer_difficulte(etat, gorgees):
    """Ajuste le nombre de gorgees selon la difficulte du salon."""
    if gorgees is None or gorgees <= 0:
        return gorgees          # None = cul sec, 0 = pas de penalite
    mult = etat.get("multiplicateur", 1.0)
    return max(1, math.floor(gorgees * mult + 0.5))


def rang(score):
    """Rang d un score dans l echelle (plus grand = plus fort)."""
    try:
        return ECHELLE.index(score)
    except ValueError:
        return -1


def lancer_des():
    """Lance deux des a 6 faces et retourne le score Kinito."""
    d1 = random.randint(1, 6)
    d2 = random.randint(1, 6)
    grand = max(d1, d2)
    petit = min(d1, d2)
    return grand * 10 + petit


def initialiser_etat_kinito(nb_joueurs):
    """Cree l etat initial d une partie Kinito."""
    return {
        "phase": "lancer",          # phases : lancer, reaction, resultat
        "joueur_courant": 0,
        "sens": 1,                  # +1 ou -1
        "score_reel": 0,            # secret, seul le lanceur le voit
        "score_annonce": 0,         # public
        "annonce_precedente": 0,
        "premiere_annonce": True,
        "message": "",
        "revelation": False,        # faut-il montrer le vrai score ?
        "partis": [False] * nb_joueurs,   # places laissees vides en cours de partie
    }


def joueur_suivant(etat, index):
    """Retourne l index du joueur suivant, en sautant ceux qui sont partis."""
    nb = etat["nb_joueurs"]
    partis = etat.get("partis") or [False] * nb
    suivant = index
    for _ in range(nb):
        suivant = (suivant + etat["sens"] + nb) % nb
        if not partis[suivant]:
            return suivant
    # Plus personne : on rend le voisin direct pour ne pas boucler.
    return (index + etat["sens"] + nb) % nb


def penalite(score, menteur):
    """Retourne le nombre de gorgees pour un score donne."""
    if score == 21:
        return None  # cas special : cul sec
    entree = TABLEAU_GORGEES.get(score)
    if not entree:
        return 0
    return entree[1] if menteur else entree[0]


def calculer_gorgees_menteur(score_reel, score_annonce):
    """Regle du maximum : on prend la penalite la plus salee."""
    if score_reel == 21 or score_annonce == 21:
        return None  # cul sec
    p_reel = TABLEAU_GORGEES.get(score_reel, [0, 0])[1]
    p_annonce = TABLEAU_GORGEES.get(score_annonce, [0, 0])[1]
    return max(p_reel, p_annonce)


# =============================================================
# ENDPOINT WEBSOCKET PRINCIPAL
# C est la porte d entree de toutes les connexions.
# Chaque joueur se connecte ici et y reste connecte.
# =============================================================

@app.websocket("/ws/{code_salon}/{nom_joueur}")
async def websocket_endpoint(ws: WebSocket, code_salon: str, nom_joueur: str):
    """
    Gere la connexion d un joueur.
    code_salon : le code a 4 lettres du salon
    nom_joueur : le prenom du joueur
    """
    await ws.accept()
    demarrer_veilleur()
    nom_joueur = " ".join(nom_joueur.split())[:24] or "Joueur"

    # On recupere ou cree le salon
    if code_salon not in salons:
        salons[code_salon] = nouveau_salon()
    salon = salons[code_salon]

    async with verrou(salon):
        # Le salon a pu etre libere pendant qu on attendait le verrou.
        if salons.get(code_salon) is not salon:
            salons[code_salon] = salon
        joueur = await arrivee(code_salon, salon, ws, nom_joueur)

    try:
        # Boucle principale : on ecoute les messages de ce joueur
        async for message_brut in ws.iter_text():
            try:
                message = json.loads(message_brut)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            joueur["vu"] = time.time()
            type_msg = message.get("type")
            # Battement de coeur : on repond tout de suite, sans verrou.
            if type_msg == "ping":
                joueur["battement"] = True
                if joueur.get("ws") is ws:
                    try:
                        await asyncio.wait_for(ws.send_text('{"type":"pong"}'), DELAI_ENVOI)
                    except Exception:
                        pass
                continue
            salon = salons.get(code_salon)
            if salon is not None and joueur not in salon["joueurs"]:
                # Retire de la salle d attente pendant son silence : on ferme,
                # son client se reconnecte et reprend une place proprement.
                asyncio.ensure_future(fermer(ws, 4003))
                break
            if salon is None or joueur.get("ws") is not ws:
                # Place reprise par une connexion plus recente : celle-ci
                # n a plus voix au chapitre.
                continue
            async with verrou(salon):
                if joueur.get("parti"):
                    # Declare parti (silence trop long) mais il parle de
                    # nouveau : il reprend sa place avant d agir.
                    await revenir(code_salon, salon, joueur)
                try:
                    if type_msg == "resync":
                        await envoyer_instantane(code_salon, joueur, complet=False)
                    else:
                        await traiter_message(code_salon, joueur, message)
                except Exception:
                    # Une action qui plante ne doit plus couper la connexion du
                    # joueur (il se retrouvait "parti" au milieu de son tour).
                    journal.exception("Erreur sur %r dans le salon %s", type_msg, code_salon)

    except WebSocketDisconnect:
        pass
    except Exception:
        # Connexion deja fermee de notre cote (vieil onglet remplace, joueur
        # muet ecarte) : Starlette leve alors une autre exception. Rien a faire
        # de plus que le nettoyage ci-dessous.
        pass

    finally:
        # ATTENTION : iter_text() de Starlette attrape lui-meme
        # WebSocketDisconnect et se contente de terminer la boucle. Le bloc
        # "except" ci-dessus ne s executait donc jamais, et le nettoyage non
        # plus : joueurs fantomes dans les salons, "joueur_parti" jamais
        # envoye, salons vides jamais liberes. D ou ce finally.
        salon = salons.get(code_salon)
        if salon is not None:
            async with verrou(salon):
                await depart(code_salon, joueur, ws)


async def arrivee(code_salon, salon, ws, nom_joueur):
    """Un joueur se connecte : nouvelle place, ou retour a la sienne."""
    jeton = ws.query_params.get("jeton")

    # Un joueur qui revient reprend SA place (meme index, meme siege) au lieu
    # d en occuper une nouvelle. D abord par son jeton...
    joueur = None
    if jeton:
        for j in salon["joueurs"]:
            if j.get("jeton") == jeton:
                joueur = j
                break
    # ... puis par son prenom. Sans jeton (bouton Quitter, autre navigateur,
    # appli installee au lieu de Safari, lien retape...), il etait AJOUTE
    # comme nouveau joueur : on le retrouvait en double a la table, son
    # ancienne place tenue par un fantome dont le tour bloquait la partie.
    # A une table de soiree, un prenom deja assis, c est la meme personne.
    if joueur is None:
        for j in salon["joueurs"]:
            if meme_prenom(j["nom"], nom_joueur):
                joueur = j
                break

    # Les clients a battement de coeur le disent des la connexion : sans ca,
    # un telephone qui se reconnectait puis se verrouillait avant son premier
    # battement n etait jamais declare parti.
    bat = ws.query_params.get("bat") == "1"

    if joueur is not None:
        ancien_ws = joueur.get("ws")
        joueur["ws"] = ws
        if ancien_ws is not None and ancien_ws is not ws:
            # Un vieil onglet tenait encore la place : on le previent (pour
            # qu il ne se reconnecte pas tout seul et ne reprenne pas la
            # place a son tour), puis on le ferme.
            try:
                await asyncio.wait_for(ancien_ws.send_text('{"type":"remplace"}'), 1)
            except Exception:
                pass
            asyncio.ensure_future(fermer(ancien_ws, 4002))
        await revenir(code_salon, salon, joueur, annoncer=False)
        joueur["battement"] = bat
        retour = True
    else:
        joueur = {
            "nom": nom_joueur,
            "ws": ws,
            "index": len(salon["joueurs"]),
            "jeton": secrets.token_hex(16),
            "parti": False,
            "vu": time.time(),
            "battement": bat,
        }
        salon["joueurs"].append(joueur)
        retour = False
        arrivee_en_partie(salon, joueur)

    annuler_liberation(salon)

    # On informe tout le monde qu un joueur a rejoint (ou est revenu)
    await diffuser(code_salon, {
        "type": "joueur_rejoint",
        "nom": joueur["nom"],
        "retour": retour,
        "joueurs": [j["nom"] for j in salon["joueurs"]],
    })
    await envoyer_indices(code_salon)
    await envoyer_instantane(code_salon, joueur, complet=True, nouveau=not retour)
    return joueur


def arrivee_en_partie(salon, joueur):
    """Un nouveau venu en pleine partie : on lui fait une place si le jeu le permet."""
    etat_courant = salon.get("etat")
    nom_joueur = joueur["nom"]
    # Arrivee en pleine partie : on agrandit le plateau pour que le
    # nouveau venu entre vraiment dans le tour, au lieu de rester
    # spectateur avec un index hors bornes. Ajouter en fin de liste ne
    # decale l index de personne.
    if etat_courant is not None and salon.get("jeu") == "kinito":
        etat_courant["nb_joueurs"] = len(salon["joueurs"])
        etat_courant["partis"].append(False)
        etat_courant["message"] = nom_joueur + " rejoint la partie."
        kinito_reprendre(etat_courant)

    # Buckshot n a pas de fin de partie : un arrivant laisse spectateur
    # le resterait pour toujours. On lui fait donc une place, verre vide
    # (il sera le chargeur de la prochaine manche) et sans objet jusqu a
    # la prochaine distribution. Au-dela de 8 presents, il regarde.
    elif etat_courant is not None and salon.get("jeu") == "buckshot":
        presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
        if presents <= BT_JOUEURS_MAX and joueur["index"] == etat_courant["nb_joueurs"]:
            etat_courant["nb_joueurs"] += 1
            etat_courant["gorgees"].append(0)
            etat_courant["objets"].append(None)
            etat_courant["menottes"].append(False)
            etat_courant["dernier_reel"].append(0)
            etat_courant["message"] = nom_joueur + " rejoint la partie."

    # Picolopoly : une partie dure une heure, on fait une place a
    # l arrivant (argent de depart, case Depart) tant qu on reste a 8.
    elif etat_courant is not None and salon.get("jeu") == "picolopoly":
        presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
        if (presents <= PP_JOUEURS_MAX and etat_courant["phase"] != "fin"
                and joueur["index"] == etat_courant["nb_joueurs"]):
            pp_ajouter_joueur(etat_courant, nom_joueur)

    # Casino : on s assoit a tout moment avec ses jetons de depart, et on
    # joue des le coup suivant.
    elif etat_courant is not None and salon.get("jeu") == "casino":
        presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
        if presents <= CS_JOUEURS_MAX and joueur["index"] == etat_courant["nb_joueurs"]:
            cs_ajouter_joueur(etat_courant, nom_joueur)


async def revenir(code_salon, salon, joueur, annoncer=True):
    """Le joueur reprend sa place (apres une coupure ou un silence)."""
    etait_parti = joueur.get("parti")
    joueur["parti"] = False
    joueur["vu"] = time.time()
    annuler_liberation(salon)
    etat = salon.get("etat")
    if not etait_parti or etat is None:
        return
    idx = joueur["index"]
    jeu = salon.get("jeu")
    if jeu in ("kinito", "picolopoly") and idx < len(etat.get("partis", [])):
        etat["partis"][idx] = False
        if jeu == "kinito":
            kinito_reprendre(etat)
        else:
            etat["version"] += 1
            pp_rearmer_si_attendu(etat, idx)
    elif jeu == "menteur" and idx < etat["nb_joueurs"]:
        mt_joueur_revenu(etat, idx, time.time())
        mt_programmer(code_salon)
    elif jeu == "casino" and idx < etat["nb_joueurs"]:
        cs_joueur_revenu(etat, idx, time.time())
        cs_programmer(code_salon)
    if annoncer:
        await diffuser(code_salon, {
            "type": "joueur_rejoint",
            "nom": joueur["nom"],
            "retour": True,
            "joueurs": [j["nom"] for j in salon["joueurs"]],
        })
        await envoyer_instantane(code_salon, joueur, complet=True)


async def envoyer_instantane(code_salon, joueur, complet=True, nouveau=False):
    """
    Remet un joueur dans la partie en cours : tout ce qu il a pu manquer
    pendant son absence, y compris ce qui n est envoye qu a lui (ses des, sa
    main, la demande de reagir...). Sans ces messages prives, un joueur qui
    revenait au moment ou c etait a lui d agir ne voyait rien a faire, et la
    table l attendait indefiniment.
    complet : renvoie aussi le message de debut de partie (le client
    reconstruit la table) ; inutile pour un simple "resync" au reveil.
    nouveau : la table a pu s agrandir, tout le monde doit le voir.
    """
    salon = salons.get(code_salon)
    if not salon or salon.get("etat") is None:
        return
    etat = salon["etat"]
    jeu = salon.get("jeu")
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]

    if jeu == "kinito":
        if complet:
            await envoyer(joueur, {"type": "partie_demarree", "jeu": "kinito", "joueurs": noms})
        if nouveau:
            await diffuser(code_salon, construire_etat_public(code_salon))
        else:
            await envoyer(joueur, construire_etat_public(code_salon))
        phase = etat["phase"]
        if phase == "annonce" and etat["joueur_courant"] == idx:
            # Il avait lance : on lui redonne son score (secret).
            await envoyer(joueur, {
                "type": "ton_score",
                "score": etat["score_reel"],
                "scores_possibles": SCORES_ANNONCABLES,
                "premiere_annonce": etat["premiere_annonce"],
            })
        elif phase == "reaction" and joueur_suivant(etat, etat["joueur_courant"]) == idx:
            nom_annonceur = noms[etat["joueur_courant"]]
            await envoyer(joueur, {
                "type": "annonce",
                "annonceur": nom_annonceur,
                "reacteur": joueur["nom"],
                "index_reacteur": idx,
                "score_annonce": etat["score_annonce"],
            })
            await envoyer(joueur, {
                "type": "a_toi_de_reagir",
                "annonceur": nom_annonceur,
                "premiere_annonce": etat["premiere_annonce"],
            })
        elif phase == "resultat" and etat.get("dernier_resultat"):
            # Sans le panneau de resultat, il n avait pas de bouton
            # "Nouvelle manche" et ne voyait plus rien se passer.
            await envoyer(joueur, etat["dernier_resultat"])

    elif jeu == "beerbattle":
        # BeerBattle ne renvoyait RIEN a la reconnexion : le client restait
        # sur l etat d avant la coupure. Et si c etait le tour du revenant,
        # personne d autre ne jouait -- donc plus aucune diffusion pour le
        # reveiller : la partie etait figee pour de bon.
        if complet:
            await envoyer(joueur, {"type": "bb_demarree", "joueurs": noms})
        await envoyer(joueur, bb_etat_public(salon))
        # Sa main est secrete : elle part sur sa seule connexion. Un
        # arrivant tardif n a pas de place dans l etat (les cartes sont
        # distribuees au lancement) : il n a donc pas de main.
        if idx < etat["nb_joueurs"]:
            await envoyer(joueur, {"type": "bb_ta_main", "main": etat["mains"][idx], "mon_index": idx})
            # S il s etait coupe en plein ramassage, il lui faut aussi le
            # contenu de sa case pour pouvoir finir son tour.
            if etat["phase"] == "jeu" and etat["joueur_courant"] == idx and etat["action_faite"]:
                pos = etat["positions"][idx]
                if pos:
                    await envoyer(joueur, {
                        "type": "bb_carte_case",
                        "carte": etat["plateau"][pos["ligne"]][pos["colonne"]],
                    })
        if etat["phase"] == "fini" and etat.get("gagnant_nom"):
            await envoyer(joueur, {"type": "bb_victoire", "gagnant": etat["gagnant_nom"]})

    elif jeu == "buckshot":
        if complet:
            await envoyer(joueur, {"type": "bt_demarree", "joueurs": noms})
        await envoyer(joueur, {"type": "bt_mon_index", "index": idx})
        # A tout le monde : un arrivant agrandit la table pour les autres aussi.
        if nouveau:
            await diffuser(code_salon, bt_etat_public(salon))
        else:
            await envoyer(joueur, bt_etat_public(salon))

    elif jeu == "picolopoly":
        # Retour ou arrivee : le plateau a lui seul, l etat a tous (la table a
        # pu s agrandir). Le revenant n est plus absent : on lui rend un
        # delai normal au lieu de celui, tres court, qui joue a sa place.
        if complet:
            await envoyer(joueur, {"type": "pp_demarree", "joueurs": noms, "plateau": PP_CASES})
        if complet or nouveau:
            pp_rearmer_si_attendu(etat, idx)
            await pp_publier(code_salon, [])
        else:
            await envoyer(joueur, pp_etat_public(etat, time.time()))

    elif jeu == "menteur":
        # Sa main est secrete : elle ne part que vers lui. Un arrivant en
        # pleine partie regarde (pas de main) jusqu a la partie suivante.
        if complet:
            await envoyer(joueur, {"type": "mt_demarree", "joueurs": noms})
        await envoyer(joueur, mt_etat_public(etat, time.time()))
        main = mt_main(etat, idx)
        if main is not None:
            await envoyer(joueur, main)

    elif jeu == "casino":
        # Un arrivant agrandit la table : tout le monde doit le voir.
        if complet:
            await envoyer(joueur, {"type": "cs_demarree", "joueurs": noms})
        if nouveau:
            await cs_publier(code_salon, [])
        else:
            await envoyer(joueur, cs_etat_public(etat, time.time()))
            main = cs_main_poker(etat, idx)
            if main is not None:
                await envoyer(joueur, main)


async def depart(code_salon, joueur, ws):
    """
    La connexion ws de ce joueur est terminee (ou muette depuis trop
    longtemps). A appeler sous le verrou du salon.
    """
    salon = salons.get(code_salon)
    # Si la place a deja ete reprise par une connexion plus recente, ou deja
    # traitee, on ne touche a rien.
    if (not salon or joueur not in salon["joueurs"]
            or joueur.get("ws") is not ws or joueur.get("parti")):
        return
    index_parti = joueur["index"]

    if salon.get("etat") is None:
        # --- Salle d attente : on retire vraiment et on renumerote,
        # pour que la liste reste compacte et les index justes.
        salon["joueurs"].remove(joueur)
        joueur["parti"] = True
        await diffuser(code_salon, {
            "type": "joueur_parti",
            "nom": joueur["nom"],
            "joueurs": [j["nom"] for j in salon["joueurs"]],
        })
        if salon["joueurs"]:
            await envoyer_indices(code_salon)
    else:
        # --- En pleine partie : on garde la place dans la liste pour
        # ne decaler l index de personne, on la marque vide, et le
        # tour de jeu la sautera.
        joueur["parti"] = True
        await diffuser(code_salon, {
            "type": "joueur_parti",
            "nom": joueur["nom"],
            "joueurs": [j["nom"] for j in salon["joueurs"]],
        })
        jeu = salon.get("jeu")
        if jeu == "kinito":
            await kinito_joueur_parti(code_salon, index_parti)
        elif jeu == "beerbattle":
            await bb_joueur_parti(code_salon, index_parti)
        elif jeu == "buckshot":
            await bt_joueur_parti(code_salon, index_parti)
        elif jeu == "picolopoly":
            await pp_joueur_parti(code_salon, index_parti)
        elif jeu == "menteur":
            etat = salon["etat"]
            if index_parti < etat["nb_joueurs"]:
                mt_joueur_parti(etat, index_parti, time.time())
                await mt_publier(code_salon, [])
        elif jeu == "casino":
            etat = salon["etat"]
            if index_parti < etat["nb_joueurs"]:
                evenements = cs_joueur_parti(etat, index_parti, time.time())
                await cs_publier(code_salon, evenements)

    # Plus personne de connecte : le salon sera libere s il le reste.
    if all(j.get("parti") for j in salon["joueurs"]):
        programmer_liberation(code_salon)


def annuler_liberation(salon):
    t = salon.pop("liberation", None)
    if t is not None and not t.done():
        t.cancel()


def programmer_liberation(code_salon):
    """
    Libere le salon s il reste sans personne DELAI_SALON_VIDE secondes.
    Avant, il etait supprime des que le dernier joueur se deconnectait : si
    tous les telephones se verrouillaient en meme temps, la partie etait
    perdue et chacun revenait dans un salon vide.
    """
    salon = salons.get(code_salon)
    if salon is None:
        return
    annuler_liberation(salon)

    async def liberer():
        await asyncio.sleep(DELAI_SALON_VIDE)
        if salons.get(code_salon) is salon and all(j.get("parti") for j in salon["joueurs"]):
            for cle in ("pp_minuteur", "mt_minuteur", "cs_minuteur"):
                m = salon.get(cle)
                if m is not None and not m.done():
                    m.cancel()
            salons.pop(code_salon, None)

    salon["liberation"] = asyncio.ensure_future(liberer())


def demarrer_veilleur():
    global _veilleur
    if _veilleur is None or _veilleur.done():
        _veilleur = asyncio.ensure_future(veilleur())


async def veilleur():
    """Repere les joueurs muets (telephone en veille) et les traite comme partis."""
    while True:
        await asyncio.sleep(1)
        try:
            maintenant = time.time()
            for code, salon in list(salons.items()):
                for j in list(salon["joueurs"]):
                    if (j.get("battement") and not j.get("parti")
                            and maintenant - j.get("vu", maintenant) > DELAI_SILENCE):
                        ws = j.get("ws")
                        async with verrou(salon):
                            await depart(code, j, ws)
                        if ws is not None:
                            asyncio.ensure_future(fermer(ws, 4001))
        except Exception:
            journal.exception("Erreur du veilleur")


# =============================================================
# TRAITEMENT DES MESSAGES
# Chaque action d un joueur arrive ici.
# =============================================================

async def traiter_message(code_salon, joueur, message):
    """
    Aiguille chaque message vers la bonne fonction de traitement.
    Le "type" du message indique quelle action le joueur veut faire.
    """
    type_msg = message.get("type")
    salon = salons[code_salon]
    if not isinstance(type_msg, str):
        return
    # Une action d un jeu qui n est pas (ou plus) en cours est ignoree : sans
    # partie, etat vaut None et l action plantait.
    jeu = salon.get("jeu")
    en_cours = salon.get("etat") is not None
    actions_kinito = ("lancer", "annoncer", "reaction", "abandonner", "nouvelle_manche")
    if type_msg in actions_kinito and not (en_cours and jeu == "kinito"):
        return
    if type_msg.startswith("bb_") and type_msg != "bb_demarrer" and not (en_cours and jeu == "beerbattle"):
        return
    if type_msg.startswith("bt_") and type_msg != "bt_demarrer" and not (en_cours and jeu == "buckshot"):
        return
    if type_msg.startswith("mt_") and type_msg != "mt_demarrer" and not (en_cours and jeu == "menteur"):
        return
    if type_msg.startswith("cs_") and type_msg != "cs_demarrer" and not (en_cours and jeu == "casino"):
        return

    # --- Demarrer une partie ---
    if type_msg == "demarrer":
        # Kinito n a pas de fin : un "demarrer" en pleine partie (double clic,
        # vieil onglet) remettait tout a zero sous les pieds des joueurs.
        if salon.get("etat") is None:
            await demarrer_kinito(code_salon, message)

    # --- Actions du Kinito ---
    elif type_msg == "lancer":
        await kinito_lancer(code_salon, joueur)
    elif type_msg == "annoncer":
        await kinito_annoncer(code_salon, joueur, message.get("score"))
    elif type_msg == "reaction":
        await kinito_reaction(code_salon, joueur, message.get("choix"), message.get("score_cite"))
    elif type_msg == "abandonner":
        await kinito_abandonner(code_salon, joueur)
    elif type_msg == "nouvelle_manche":
        await kinito_nouvelle_manche(code_salon, joueur)

# --- Actions de BeerBattle ---
    elif type_msg == "bb_demarrer":
        # Relance seulement depuis la salle d attente ou une partie finie :
        # deux "Relancer" (deux joueurs, double clic) redemarraient la
        # partie qui venait de commencer.
        etat = salon.get("etat")
        if etat is None or jeu != "beerbattle" or etat.get("phase") == "fini":
            await bb_demarrer(code_salon, salons, diffuser)
    elif type_msg == "bb_placer":
        await bb_placer(code_salon, salons, diffuser, joueur, message.get("ligne"), message.get("colonne"))
    elif type_msg == "bb_deplacer":
        await bb_deplacer(code_salon, salons, diffuser, joueur, message.get("ligne"), message.get("colonne"), message.get("via_carte"), message.get("index_carte"))
    elif type_msg == "bb_attaquer":
        await bb_attaquer(code_salon, salons, diffuser, joueur, message.get("cible"), message.get("index_carte"), message.get("type_attaque"))
    elif type_msg == "bb_as_deplacer":
        await bb_as_deplacer_frapper(code_salon, salons, diffuser, joueur, message.get("ligne"), message.get("colonne"), message.get("cible"), message.get("index_carte"))
    elif type_msg == "bb_poser_arme":
        await bb_poser_arme(code_salon, salons, diffuser, joueur, message.get("index_carte"))
    elif type_msg == "bb_dame":
        await bb_dame(code_salon, salons, diffuser, joueur, message.get("cible"), message.get("index_carte"))
    elif type_msg == "bb_ne_rien_faire":
        await bb_ne_rien_faire(code_salon, salons, diffuser, joueur)
    elif type_msg == "bb_ramasser":
        await bb_ramasser(code_salon, salons, diffuser, joueur, message.get("decision"), message.get("index_echange"))

    # --- Actions de Buckshot ---
    elif type_msg == "bt_demarrer":
        if salon.get("etat") is None:
            await bt_demarrer(code_salon, salons, diffuser)
    elif type_msg == "bt_composer":
        await bt_composer(code_salon, salons, diffuser, joueur, message.get("total"), message.get("reel"))
    elif type_msg == "bt_jouer_objet":
        await bt_jouer_objet(code_salon, salons, diffuser, joueur, message.get("cible"))
    elif type_msg == "bt_tirer":
        await bt_tirer(code_salon, salons, diffuser, joueur, message.get("cible"))

    # --- Actions de Picolopoly (le moteur valide tout) ---
    elif type_msg == "pp_demarrer":
        await pp_demarrer(code_salon, message.get("difficulte"))
    elif isinstance(type_msg, str) and type_msg.startswith("pp_"):
        await pp_traiter(code_salon, joueur, message)

    # --- Actions de Menteur (le moteur valide tout) ---
    elif type_msg == "mt_demarrer":
        await mt_demarrer(code_salon, message.get("mode"))
    elif type_msg.startswith("mt_"):
        await mt_traiter(code_salon, joueur, message)

    # --- Casino (le moteur valide tout) ---
    elif type_msg == "cs_demarrer":
        await cs_demarrer(code_salon)
    elif type_msg.startswith("cs_"):
        await cs_traiter(code_salon, joueur, message)

# =============================================================
# ACTIONS DU KINITO
# =============================================================

async def demarrer_kinito(code_salon, message):
    """Demarre une partie Kinito avec les joueurs presents."""
    salon = salons[code_salon]
    nb = len(salon["joueurs"])

    if nb < 2:
        await diffuser(code_salon, {"type": "erreur", "message": "Il faut au moins 2 joueurs."})
        return

    etat = initialiser_etat_kinito(nb)
    etat["nb_joueurs"] = nb

    difficulte = (message or {}).get("difficulte", DIFFICULTE_DEFAUT)
    if difficulte not in DIFFICULTES:
        difficulte = DIFFICULTE_DEFAUT
    etat["difficulte"] = difficulte
    etat["multiplicateur"] = DIFFICULTES[difficulte]
    salon["etat"] = etat
    salon["jeu"] = "kinito"

    await diffuser(code_salon, {
        "type": "partie_demarree",
        "jeu": "kinito",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
    })
    await diffuser(code_salon, construire_etat_public(code_salon))


async def kinito_lancer(code_salon, joueur):
    """Le joueur courant lance les des."""
    salon = salons[code_salon]
    etat = salon["etat"]

    # Securite : seul le joueur courant peut lancer, et une seule fois. Un
    # double appui (ou un message rejoue apres une coupure) relancait les des
    # en pleine annonce.
    if etat["phase"] != "lancer" or joueur["index"] != etat["joueur_courant"]:
        return

    score = lancer_des()
    etat["score_reel"] = score

    # Cas speciaux : 31 et 51 s appliquent immediatement
    if score == SCORE_ANNULE:
        etat["score_annonce"] = score
        etat["premiere_annonce"] = True
        etat["annonce_precedente"] = 0
        etat["joueur_courant"] = joueur_suivant(etat, etat["joueur_courant"])
        etat["message"] = "51 : tout le monde boit 1 gorgee, on repart de zero !"
        etat["phase"] = "lancer"
        await diffuser(code_salon, {
            "type": "effet_special",
            "effet": "51",
            "message": etat["message"],
            # "lanceur" : le tour a deja avance, un client ne peut plus deviner
            # qui vient de lancer. "etat" evite de rediffuser un etat separe,
            # qui ecraserait aussitot ce message chez les clients existants.
            "lanceur": joueur["index"],
            "etat": construire_etat_public(code_salon),
        })
        return

    if score == SCORE_CHANGE_SENS:
        etat["score_annonce"] = score
        etat["sens"] = -etat["sens"]
        # Le 31 relance les annonces : il ne se pose pas comme score a battre.
        # (31 est le plus faible de l ECHELLE et n a pas d entree dans
        # TABLEAU_GORGEES : le garder comme reference rendait gratuits
        # l abandon et les accusations qui en decoulent.)
        etat["annonce_precedente"] = 0
        etat["premiere_annonce"] = True
        sens_texte = "horaire" if etat["sens"] == 1 else "anti-horaire"
        etat["message"] = f"31 : changement de sens ({sens_texte}) !"
        etat["joueur_courant"] = joueur_suivant(etat, etat["joueur_courant"])
        etat["phase"] = "lancer"
        await diffuser(code_salon, {
            "type": "effet_special",
            "effet": "31",
            "message": etat["message"],
            "sens": etat["sens"],
            "lanceur": joueur["index"],
            "etat": construire_etat_public(code_salon),
        })
        return

    # Score normal : on envoie le score UNIQUEMENT au joueur qui a lance
    etat["phase"] = "annonce"
    await envoyer(joueur, {
        "type": "ton_score",
        "score": score,
        "scores_possibles": SCORES_ANNONCABLES,
        "premiere_annonce": etat["premiere_annonce"],
    })


async def kinito_annoncer(code_salon, joueur, score_annonce):
    """Le joueur courant annonce un score (vrai ou bluff)."""
    salon = salons[code_salon]
    etat = salon["etat"]

    if etat["phase"] != "annonce" or joueur["index"] != etat["joueur_courant"]:
        return

    if score_annonce not in SCORES_ANNONCABLES:
        await envoyer(joueur, {
            "type": "erreur",
            "message": "Ce score ne peut pas etre annonce.",
        })
        return

    etat["score_annonce"] = score_annonce
    etat["phase"] = "reaction"

    # Le joueur suivant va reagir
    index_reacteur = joueur_suivant(etat, etat["joueur_courant"])
    nom_annonceur = salon["joueurs"][etat["joueur_courant"]]["nom"]
    nom_reacteur = salon["joueurs"][index_reacteur]["nom"]

    # On informe tout le monde de l annonce
    await diffuser(code_salon, {
        "type": "annonce",
        "annonceur": nom_annonceur,
        "reacteur": nom_reacteur,
        "index_reacteur": index_reacteur,
        # Le score annonce est fourni pour que les clients qui le souhaitent
        # puissent l afficher brievement a tous (version 3D, ou les joueurs
        # peuvent etre a distance et ne pas s entendre). Le client 2D ne lit
        # pas ce champ : il garde sa regle d attention, l annonce y reste
        # purement verbale.
        "score_annonce": score_annonce,
    })

    # On envoie separement au reacteur pour qu il sache que c est son tour.
    # (Avant : si sa connexion etait morte, l exception coupait celle de
    # l ANNONCEUR, qui se retrouvait "parti" au milieu de son tour.)
    await envoyer(salon["joueurs"][index_reacteur], {
        "type": "a_toi_de_reagir",
        "annonceur": nom_annonceur,
        "premiere_annonce": etat["premiere_annonce"],
    })


async def kinito_reaction(code_salon, joueur, choix, score_cite=None):
    """
    Le joueur reacteur fait son choix :
    - prends : on continue
    - menteur : on verifie
    - moins : le joueur doit avoir cite le bon score precedent
    """
    salon = salons[code_salon]
    etat = salon["etat"]
    index_reacteur = joueur_suivant(etat, etat["joueur_courant"])

    # Hors de la phase de reaction (panneau reste ouvert apres une coupure,
    # manche relancee entre-temps), une reaction faisait avancer le tour
    # n importe comment.
    if etat["phase"] != "reaction" or joueur["index"] != index_reacteur:
        return

    score_reel = etat["score_reel"]
    score_annonce = etat["score_annonce"]
    annonce_prec = etat["annonce_precedente"]
    nom_annonceur = salon["joueurs"][etat["joueur_courant"]]["nom"]
    nom_reacteur = salon["joueurs"][index_reacteur]["nom"]

    if choix == "prends":
        # Le jeu continue : l annonce devient la reference
        etat["annonce_precedente"] = score_annonce
        etat["premiere_annonce"] = False
        etat["joueur_courant"] = index_reacteur
        etat["phase"] = "lancer"
        await diffuser(code_salon, {
            "type": "joueur_prend",
            "nom": nom_reacteur,
        })
        await diffuser(code_salon, construire_etat_public(code_salon))

    elif choix == "menteur":
        il_mentait = score_reel != score_annonce
        if il_mentait:
            gorgees = calculer_gorgees_menteur(score_reel, score_annonce)
            await fin_manche(code_salon, etat["joueur_courant"], True,
                             f"{nom_annonceur} a ete demasque !", gorgees, score_reel, score_annonce)
        else:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} a accuse a tort", gorgees, score_reel, score_annonce)

    elif choix == "moins":
        # Le reacteur doit avoir cite le bon score precedent
        if etat["premiere_annonce"]:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} a accuse alors qu il n y avait pas d annonce", gorgees, None, score_annonce)
            return

        if score_cite != annonce_prec:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} n a pas cite le bon score precedent", gorgees, None, score_annonce)
        else:
            annonce_trop_basse = rang(score_annonce) < rang(annonce_prec)
            if annonce_trop_basse:
                gorgees = penalite(annonce_prec, True)
                await fin_manche(code_salon, etat["joueur_courant"], True,
                                 f"{nom_annonceur} a annonce moins que le precedent", gorgees, None, annonce_prec)
            else:
                gorgees = penalite(score_annonce, False)
                await fin_manche(code_salon, index_reacteur, False,
                                 f"{nom_reacteur} a accuse a tort", gorgees, None, score_annonce)


async def kinito_abandonner(code_salon, joueur):
    """Le joueur courant assume avoir fait moins que le score a battre."""
    salon = salons[code_salon]
    etat = salon["etat"]

    # Seul le joueur courant peut abandonner, et seulement s il y a
    # une annonce a battre (pas a la premiere annonce de la manche).
    if etat["phase"] not in ("lancer", "annonce") or joueur["index"] != etat["joueur_courant"]:
        return
    if etat["premiere_annonce"]:
        return

    nom = salon["joueurs"][etat["joueur_courant"]]["nom"]
    # Il boit la colonne simple du score qu il devait battre.
    gorgees = penalite(etat["annonce_precedente"], False)
    await fin_manche(code_salon, etat["joueur_courant"], False,
                     f"{nom} assume avoir fait moins", gorgees, None, etat["annonce_precedente"])

async def fin_manche(code_salon, index_perdant, menteur, message, gorgees, score_reel, score_base):
    """Envoie le resultat d une manche perdue a tous les joueurs."""
    salon = salons[code_salon]
    etat = salon["etat"]
    nom_perdant = salon["joueurs"][index_perdant]["nom"]
    gorgees = appliquer_difficulte(etat, gorgees)

    # Le prochain tour repart du joueur apres le perdant
    etat["joueur_courant"] = joueur_suivant(etat, index_perdant)
    etat["phase"] = "resultat"

    resultat = {
        "type": "fin_manche",
        "perdant": nom_perdant,
        "index_perdant": index_perdant,   # evite aux clients de retrouver le joueur par son nom
        "menteur": menteur,
        "message": message,
        "gorgees": gorgees,         # None = cul sec (cas du 21)
        "score_reel": score_reel,   # None si on ne revele pas
        "score_base": score_base,
    }
    # Garde pour un joueur qui revient pendant l ecran de resultat.
    etat["dernier_resultat"] = resultat
    await diffuser(code_salon, resultat)


def kinito_reprendre(etat):
    """
    Si la partie s etait mise en attente faute de joueurs, elle repart des
    qu il y a de nouveau deux presents (retour ou nouveau venu).
    """
    if etat.get("phase") != "attente":
        return
    presents = [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]
    if len(presents) < 2:
        return
    if etat["partis"][etat["joueur_courant"]]:
        etat["joueur_courant"] = presents[0]
    etat["premiere_annonce"] = True
    etat["annonce_precedente"] = 0
    etat["score_reel"] = 0
    etat["score_annonce"] = 0
    etat["phase"] = "lancer"
    etat["message"] = "La partie reprend."


async def kinito_joueur_parti(code_salon, index):
    """
    Un joueur quitte en pleine partie : on saute sa place.
    Si le jeu l attendait (a lui de lancer, d annoncer ou de reagir), la
    manche en cours ne peut plus se resoudre : elle repart du joueur present
    suivant.
    """
    salon = salons.get(code_salon)
    if not salon or not salon.get("etat") or salon.get("jeu") != "kinito":
        return
    etat = salon["etat"]
    if index >= len(etat.get("partis", [])):
        return

    # On regarde QUI le jeu attendait avant de marquer la place vide.
    attendu = (etat["joueur_courant"] == index)
    if etat["phase"] == "reaction" and joueur_suivant(etat, etat["joueur_courant"]) == index:
        attendu = True

    etat["partis"][index] = True

    presents = [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]
    if len(presents) < 2:
        etat["phase"] = "attente"
        etat["message"] = "Il ne reste plus assez de joueurs."
        await diffuser(code_salon, construire_etat_public(code_salon))
        return

    if attendu:
        etat["joueur_courant"] = joueur_suivant(etat, index)
        etat["premiere_annonce"] = True
        etat["annonce_precedente"] = 0
        etat["score_reel"] = 0
        etat["score_annonce"] = 0
        etat["phase"] = "lancer"
        etat["message"] = "Un joueur est parti : la manche repart."
    elif etat["joueur_courant"] == index:
        etat["joueur_courant"] = joueur_suivant(etat, index)

    await diffuser(code_salon, construire_etat_public(code_salon))


async def kinito_nouvelle_manche(code_salon, joueur):
    """Remet les compteurs a zero et relance un tour."""
    salon = salons[code_salon]
    etat = salon["etat"]

    # Tout le monde a le bouton "Nouvelle manche" : seul le premier appui
    # compte. Un appui tardif (panneau reste ouvert, joueur revenu) remettait
    # a zero la manche suivante en plein milieu d une annonce.
    if etat["phase"] != "resultat":
        return

    etat["premiere_annonce"] = True
    etat["annonce_precedente"] = 0
    etat["score_reel"] = 0
    etat["score_annonce"] = 0
    etat["phase"] = "lancer"
    etat["message"] = ""
    etat["dernier_resultat"] = None

    await diffuser(code_salon, construire_etat_public(code_salon))

# =============================================================
# CONSTANTES DE BEERBATTLE
# =============================================================
BB_TAILLE = 6           # plateau 6x6
BB_VERRE_MAX = 20       # gorgees pour etre elimine
 
 
def bb_creer_paquet():
    """Cree les 54 cartes (52 + 2 jokers)."""
    couleurs = ['coeur', 'carreau', 'trefle', 'pique']
    rouge = ['coeur', 'carreau']
    paquet = []
    for coul in couleurs:
        for v in range(2, 11):
            if 2 <= v <= 5:
                type_carte, libelle = 'deplacement', f'Dépl. {v}'
            else:
                type_carte, libelle = 'gorgee', f'{v} gorgées'
            paquet.append({'type': type_carte, 'valeur': v, 'rouge': coul in rouge,
                           'libelle': libelle, 'symbole': str(v)})
        paquet.append({'type': 'valet', 'valeur': 0, 'rouge': coul in rouge, 'libelle': 'Valet (arme 2)', 'symbole': 'V'})
        paquet.append({'type': 'dame',  'valeur': 0, 'rouge': coul in rouge, 'libelle': 'Dame (vide verre)', 'symbole': 'D'})
        paquet.append({'type': 'roi',   'valeur': 0, 'rouge': coul in rouge, 'libelle': 'Roi (arme 6)', 'symbole': 'R'})
        paquet.append({'type': 'as',    'valeur': 0, 'rouge': coul in rouge, 'libelle': 'As (mi-verre CC)', 'symbole': 'A'})
    paquet.append({'type': 'joker', 'valeur': 0, 'rouge': True,  'libelle': 'Joker (mi-verre)', 'symbole': 'J'})
    paquet.append({'type': 'joker', 'valeur': 0, 'rouge': False, 'libelle': 'Joker (mi-verre)', 'symbole': 'J'})
    return paquet
 
 
def bb_initialiser(nb_joueurs):
    """Cree l etat initial d une partie BeerBattle."""
    paquet = bb_creer_paquet()
    random.shuffle(paquet)
 
    max_cartes = 3 if nb_joueurs == 5 else 4
 
    # Distribution des mains
    mains = []
    index = 0
    for i in range(nb_joueurs):
        main = []
        for _ in range(max_cartes):
            main.append(paquet[index]); index += 1
        mains.append(main)
 
    # Remplissage du plateau 6x6
    plateau = []
    for ligne in range(BB_TAILLE):
        rangee = []
        for colonne in range(BB_TAILLE):
            rangee.append(paquet[index]); index += 1
        plateau.append(rangee)
 
    return {
        "phase": "placement",       # placement, jeu, ramassage, fini
        "nb_joueurs": nb_joueurs,
        "max_cartes": max_cartes,
        "plateau": plateau,
        "mains": mains,
        "armes": [[] for _ in range(nb_joueurs)],
        "verres": [0] * nb_joueurs,
        "elimine": [False] * nb_joueurs,
        "positions": [None] * nb_joueurs,   # rempli au placement
        "joueur_courant": 0,
        "joueur_en_placement": 0,
        "action_faite": False,
        "message": "",
    }
 
 
def bb_distance(pos1, pos2):
    """Distance de Manhattan entre deux cases (sans diagonale)."""
    return abs(pos1["ligne"] - pos2["ligne"]) + abs(pos1["colonne"] - pos2["colonne"])
 
 
def bb_case_occupee(etat, ligne, colonne):
    """Vrai si un joueur non elimine est sur cette case."""
    for i in range(etat["nb_joueurs"]):
        if etat["elimine"][i]:
            continue
        p = etat["positions"][i]
        if p and p["ligne"] == ligne and p["colonne"] == colonne:
            return True
    return False
 
 
def bb_joueurs_sur_case(etat, ligne, colonne):
    """Liste des index des joueurs non elimines sur une case."""
    liste = []
    for i in range(etat["nb_joueurs"]):
        if etat["elimine"][i]:
            continue
        p = etat["positions"][i]
        if p and p["ligne"] == ligne and p["colonne"] == colonne:
            liste.append(i)
    return liste
 
 
def bb_plateau_vide(etat):
    """Vrai si toutes les cases sont vides."""
    for ligne in etat["plateau"]:
        for case in ligne:
            if case is not None:
                return False
    return True
 
 
def bb_appliquer_gorgees(etat, cible, nombre):
    """Ajoute des gorgees a un joueur, l elimine si 20 atteint."""
    etat["verres"][cible] += nombre
    if etat["verres"][cible] >= BB_VERRE_MAX:
        etat["verres"][cible] = BB_VERRE_MAX
        etat["elimine"][cible] = True
 
 
def bb_mi_verre(etat, cible):
    """Mi-verre : moitie de ce qu il reste avant 20, arrondi sup."""
    restant = BB_VERRE_MAX - etat["verres"][cible]
    degats = (restant + 1) // 2     # arrondi superieur
    bb_appliquer_gorgees(etat, cible, degats)
 
 
def bb_etat_public(salon):
    """
    Construit l etat visible par TOUS (sans les mains secretes).
    Le plateau est envoye SANS le contenu des cartes (juste vide ou non).
    """
    etat = salon["etat"]
    # Plateau "masque" : on dit juste si une case a une carte ou non
    plateau_masque = []
    for ligne in etat["plateau"]:
        rangee = []
        for case in ligne:
            rangee.append(case is not None)   # True = carte presente, False = vide
        plateau_masque.append(rangee)
 
    return {
        "type": "bb_etat",
        "phase": etat["phase"],
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "plateau": plateau_masque,
        "positions": etat["positions"],
        "verres": etat["verres"],
        "elimine": etat["elimine"],
        "armes": [[a["type"] for a in armes_j] for armes_j in etat["armes"]],
        "joueur_courant": etat["joueur_courant"],
        "joueur_en_placement": etat["joueur_en_placement"],
        "action_faite": etat["action_faite"],
        "max_cartes": etat["max_cartes"],
        "verre_max": BB_VERRE_MAX,
        "partis": [i in bb_absents(salon) for i in range(etat["nb_joueurs"])],
        "message": etat.get("message", ""),
    }
 
 
# =============================================================
# ACTIONS DE BEERBATTLE
# Chaque fonction recoit (salon, joueur, message) et modifie l etat.
# Le serveur appelant doit ensuite diffuser le nouvel etat.
# =============================================================
 
async def bb_demarrer(code_salon, salons, diffuser):
    """
    Demarre (ou relance) une partie BeerBattle.

    C est ici qu un spectateur entre vraiment dans le jeu : quelqu un arrive
    en cours de partie ne peut pas etre insere (les cartes sont distribuees
    au lancement), mais il est dans salon["joueurs"], donc la manche suivante
    le prend en compte comme tout le monde.
    """
    salon = salons[code_salon]

    # Les places laissees vides en cours de partie ne doivent pas etre
    # redistribuees : on herisserait la nouvelle partie d un siege que
    # personne ne joue, et le tour s y bloquerait. Renumeroter est sans
    # danger ICI, et seulement ici : une nouvelle partie commence, il n y a
    # plus aucun index a preserver -- contrairement a un depart en plein jeu.
    if any(j.get("parti") for j in salon["joueurs"]):
        salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
        for i, j in enumerate(salon["joueurs"]):
            j["index"] = i
        await envoyer_indices(code_salon)

    nb = len(salon["joueurs"])
    if nb < 3:
        await diffuser(code_salon, {"type": "erreur", "message": "BeerBattle nécessite au moins 3 joueurs."})
        return
    # 54 cartes : 36 au sol plus les mains. A six, la distribution deborde du
    # paquet et bb_initialiser leve une IndexError. On refuse proprement.
    if nb > 5:
        await diffuser(code_salon, {
            "type": "erreur",
            "message": f"BeerBattle se joue à 5 au maximum ({nb} connectés) : il n'y a pas assez de cartes au-delà.",
        })
        return
    salon["etat"] = bb_initialiser(nb)
    salon["jeu"] = "beerbattle"
    await diffuser(code_salon, {"type": "bb_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
 
 
async def bb_envoyer_mains(code_salon, salons):
    """Envoie a chaque joueur SA main secrete, individuellement."""
    salon = salons[code_salon]
    etat = salon["etat"]
    import json
    for i, joueur in enumerate(salon["joueurs"]):
        if i < etat["nb_joueurs"]:
            await envoyer(joueur, {
                "type": "bb_ta_main",
                "main": etat["mains"][i],
                "mon_index": i,
            })
 

async def bb_envoyer_carte_case(code_salon, salons):
    """Envoie au joueur courant le contenu de sa case (a lui seul)."""
    salon = salons[code_salon]
    etat = salon["etat"]
    import json
    idx = etat["joueur_courant"]
    pos = etat["positions"][idx]
    carte_sol = etat["plateau"][pos["ligne"]][pos["colonne"]]
    # On retrouve le joueur courant dans la liste des connectes
    for joueur in salon["joueurs"]:
        if joueur["index"] == idx:
            await envoyer(joueur, {
                "type": "bb_carte_case",
                "carte": carte_sol,   # None s il n y a pas de carte
            })
            break

 
async def bb_placer(code_salon, salons, diffuser, joueur, ligne, colonne):
    """Place un joueur sur sa case de depart."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "placement":
        return
    if joueur["index"] != etat["joueur_en_placement"]:
        return
    if bb_case_occupee(etat, ligne, colonne):
        return
 
    if not bb_case_valide(ligne, colonne):
        return
 
    etat["positions"][joueur["index"]] = {"ligne": ligne, "colonne": colonne}
    etat["joueur_en_placement"] += 1
    bb_avancer_placement(salon)
    await diffuser(code_salon, bb_etat_public(salon))
 
 
async def bb_deplacer(code_salon, salons, diffuser, joueur, ligne, colonne, via_carte, index_carte):
    """Deplace le joueur courant."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "jeu" or joueur["index"] != etat["joueur_courant"]:
        return
    if etat["action_faite"] or not bb_case_valide(ligne, colonne):
        return
 
    etat["positions"][joueur["index"]] = {"ligne": ligne, "colonne": colonne}
    # Si deplacement via carte, on la retire de la main
    if via_carte and index_carte is not None:
        if 0 <= index_carte < len(etat["mains"][joueur["index"]]):
            etat["mains"][joueur["index"]].pop(index_carte)
    etat["action_faite"] = True
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
    await bb_envoyer_carte_case(code_salon, salons)
 

async def bb_as_deplacer_frapper(code_salon, salons, diffuser, joueur, ligne, colonne, cible, index_carte):
    """
    L As : se deplacer d une case vers une cible, puis la frapper en mi-verre.
    ligne/colonne = destination ; cible = joueur a frapper ; index_carte = l As.
    """
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    if etat["phase"] != "jeu" or idx != etat["joueur_courant"] or etat["action_faite"]:
        return
    if not bb_case_valide(ligne, colonne):
        return
    if not isinstance(cible, int) or not 0 <= cible < etat["nb_joueurs"]:
        return

    # On verifie que la destination est bien a 1 case (deplacement As)
    moi = etat["positions"][idx]
    dist = abs(ligne - moi["ligne"]) + abs(colonne - moi["colonne"])
    if dist > 1:
        return

    # On se deplace sur la case
    etat["positions"][idx] = {"ligne": ligne, "colonne": colonne}

    # On verifie que la cible est bien sur cette case
    pos_cible = etat["positions"][cible]
    if not pos_cible or pos_cible["ligne"] != ligne or pos_cible["colonne"] != colonne:
        # Pas de cible valide ici : on annule en remettant la position ? Non,
        # le deplacement reste valable, on ne frappe juste pas.
        etat["action_faite"] = True
        await diffuser(code_salon, bb_etat_public(salon))
        await bb_envoyer_mains(code_salon, salons)
        await bb_envoyer_carte_case(code_salon, salons)
        return

    # Mi-verre sur la cible
    bb_mi_verre(etat, cible)
    etat["mains"][idx].pop(index_carte)
    etat["action_faite"] = True
    etat["message"] = f"{salon['joueurs'][idx]['nom']} fonce à l'As sur {salon['joueurs'][cible]['nom']}"

    if await bb_verifier_victoire(code_salon, salons, diffuser):
        return
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
    await bb_envoyer_carte_case(code_salon, salons)

 
async def bb_attaquer(code_salon, salons, diffuser, joueur, cible, index_carte, type_attaque):
    """
    Gere une attaque.
    type_attaque : 'cc_gorgee', 'cc_mains_nues', 'arme', 'as', 'joker'
    cible : index du joueur vise
    index_carte : carte gorgee/as/joker utilisee (peut etre None pour mains nues)
    """
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    if etat["phase"] != "jeu" or idx != etat["joueur_courant"] or etat["action_faite"]:
        return
    if not isinstance(cible, int) or not 0 <= cible < etat["nb_joueurs"]:
        return
 
    main = etat["mains"][idx]
 
    if type_attaque == "cc_mains_nues":
        degats = 5 if bb_plateau_vide(etat) else 1
        bb_appliquer_gorgees(etat, cible, degats)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} frappe à mains nues ({degats})"
 
    elif type_attaque == "cc_gorgee":
        carte = main[index_carte]
        bb_appliquer_gorgees(etat, cible, carte["valeur"])
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} attaque ({carte['valeur']} gorgées)"
 
    elif type_attaque == "arme":
        # index_carte = carte gorgee ; le serveur verifie la portee via l arme la plus adaptee
        carte = main[index_carte]
        # On determine quelle arme peut atteindre la cible
        dist = bb_distance(etat["positions"][idx], etat["positions"][cible])
        arme_utilisee = None
        for arme in etat["armes"][idx]:
            portee = 6 if arme["type"] == "roi" else 2
            if dist <= portee:
                arme_utilisee = arme
                break
        if not arme_utilisee:
            return  # pas d arme a portee
        if arme_utilisee["type"] == "roi":
            degats = (carte["valeur"] + 1) // 2   # divise par 2 arrondi sup
        else:
            degats = carte["valeur"]
        bb_appliquer_gorgees(etat, cible, degats)
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} tire ({degats} gorgées)"
 
    elif type_attaque == "as":
        bb_mi_verre(etat, cible)
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} frappe à l'As (mi-verre)"
 
    elif type_attaque == "joker":
        bb_mi_verre(etat, cible)
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} tire au Joker (mi-verre)"
 
    etat["action_faite"] = True
 
    # Verifie la victoire
    if await bb_verifier_victoire(code_salon, salons, diffuser):
        return
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
    await bb_envoyer_carte_case(code_salon, salons)
 
 
async def bb_poser_arme(code_salon, salons, diffuser, joueur, index_carte):
    """Pose une arme (Roi ou Valet) devant soi. Consomme le tour."""
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    if etat["phase"] != "jeu" or idx != etat["joueur_courant"] or etat["action_faite"]:
        return
    carte = etat["mains"][idx][index_carte]
    if carte["type"] not in ("roi", "valet"):
        return
    etat["armes"][idx].append(carte)
    etat["mains"][idx].pop(index_carte)
    etat["action_faite"] = True
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
    await bb_envoyer_carte_case(code_salon, salons)
 
 
async def bb_dame(code_salon, salons, diffuser, joueur, cible, index_carte):
    """Joue une Dame : remet le verre d une cible a zero."""
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    if etat["phase"] != "jeu" or idx != etat["joueur_courant"] or etat["action_faite"]:
        return
    if not isinstance(cible, int) or not 0 <= cible < etat["nb_joueurs"]:
        return
    etat["verres"][cible] = 0
    etat["mains"][idx].pop(index_carte)
    etat["action_faite"] = True
    etat["message"] = f"{salon['joueurs'][idx]['nom']} remet à zéro le verre de {salon['joueurs'][cible]['nom']}"
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
    await bb_envoyer_carte_case(code_salon, salons)
 
async def bb_ne_rien_faire(code_salon, salons, diffuser, joueur):
    """Le joueur passe son action."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "jeu" or joueur["index"] != etat["joueur_courant"] or etat["action_faite"]:
        return
    etat["action_faite"] = True
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_carte_case(code_salon, salons)
 
 
async def bb_ramasser(code_salon, salons, diffuser, joueur, decision, index_echange):
    """
    Phase de ramassage apres l action.
    decision : 'prendre', 'echanger', 'rien'
    index_echange : carte a echanger si main pleine
    """
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    if etat["phase"] != "jeu" or joueur["index"] != etat["joueur_courant"] or not etat["action_faite"]:
        return
    pos = etat["positions"][idx]
    carte_sol = etat["plateau"][pos["ligne"]][pos["colonne"]]
 
    if decision == "prendre" and carte_sol is not None:
        if len(etat["mains"][idx]) < etat["max_cartes"]:
            etat["mains"][idx].append(carte_sol)
            etat["plateau"][pos["ligne"]][pos["colonne"]] = None
    elif decision == "echanger" and carte_sol is not None and index_echange is not None:
        ma_carte = etat["mains"][idx][index_echange]
        etat["plateau"][pos["ligne"]][pos["colonne"]] = ma_carte
        etat["mains"][idx][index_echange] = carte_sol
 
    # Fin du tour : joueur suivant non elimine (et present)
    bb_joueur_suivant(etat, bb_absents(salon))
    etat["action_faite"] = False
    etat["message"] = ""
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
 
 
def bb_joueur_suivant(etat, absents=()):
    """
    Passe au joueur suivant non elimine, en sautant les places vides :
    donner la main a un absent figeait la partie jusqu a son retour.
    """
    nb = etat["nb_joueurs"]
    suivant = etat["joueur_courant"]
    for _ in range(nb):
        suivant = (suivant + 1) % nb
        if not etat["elimine"][suivant] and suivant not in absents:
            break
    else:
        # Personne de disponible : on prend le prochain non elimine.
        suivant = (etat["joueur_courant"] + 1) % nb
        for _ in range(nb):
            if not etat["elimine"][suivant]:
                break
            suivant = (suivant + 1) % nb
    etat["joueur_courant"] = suivant


def bb_absents(salon):
    """Index des places laissees vides en cours de partie."""
    return {j["index"] for j in salon["joueurs"] if j.get("parti")}


def bb_case_valide(ligne, colonne):
    return (isinstance(ligne, int) and isinstance(colonne, int)
            and 0 <= ligne < BB_TAILLE and 0 <= colonne < BB_TAILLE)


def bb_avancer_placement(salon):
    """
    Au placement, un absent ne choisira jamais sa case : on le place d office
    sur une case libre au hasard, pour que la partie puisse commencer.
    """
    etat = salon["etat"]
    absents = bb_absents(salon)
    while etat["joueur_en_placement"] < etat["nb_joueurs"] and etat["joueur_en_placement"] in absents:
        i = etat["joueur_en_placement"]
        libres = [(l, c) for l in range(BB_TAILLE) for c in range(BB_TAILLE)
                  if not bb_case_occupee(etat, l, c)]
        l, c = random.choice(libres)
        etat["positions"][i] = {"ligne": l, "colonne": c}
        etat["joueur_en_placement"] += 1
    if etat["phase"] == "placement" and etat["joueur_en_placement"] >= etat["nb_joueurs"]:
        etat["phase"] = "jeu"
        etat["joueur_courant"] = etat["nb_joueurs"] - 1
        etat["action_faite"] = False
        # Le premier a jouer est le joueur 0, ou le premier present apres lui.
        bb_joueur_suivant(etat, absents)


async def bb_joueur_parti(code_salon, index):
    """
    Un joueur quitte BeerBattle en pleine partie. Sa place reste (il la
    reprend en revenant), mais si la table l attendait -- pour se placer, ou
    pour jouer son tour -- elle passe au suivant au lieu de l attendre.
    """
    salon = salons.get(code_salon)
    if not salon or not salon.get("etat") or salon.get("jeu") != "beerbattle":
        return
    etat = salon["etat"]
    if index >= etat["nb_joueurs"]:
        return      # un spectateur ne tenait aucune place
    absents = bb_absents(salon)
    if len(absents) >= etat["nb_joueurs"]:
        return      # plus personne : on attend un retour
    nom = salon["joueurs"][index]["nom"]
    if etat["phase"] == "placement" and etat["joueur_en_placement"] == index:
        bb_avancer_placement(salon)
        etat["message"] = nom + " est parti : il est placé d'office."
    elif etat["phase"] == "jeu" and etat["joueur_courant"] == index:
        bb_joueur_suivant(etat, absents)
        etat["action_faite"] = False
        etat["message"] = nom + " est parti : son tour passe."
    else:
        await diffuser(code_salon, bb_etat_public(salon))
        return
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
 
 
async def bb_verifier_victoire(code_salon, salons, diffuser):
    """Verifie s il ne reste qu un joueur."""
    salon = salons[code_salon]
    etat = salon["etat"]
    survivants = [i for i in range(etat["nb_joueurs"]) if not etat["elimine"][i]]
    if len(survivants) <= 1:
        gagnant = survivants[0] if survivants else None
        nom = salon["joueurs"][gagnant]["nom"] if gagnant is not None else "Personne"
        etat["phase"] = "fini"
        etat["gagnant_nom"] = nom
        await diffuser(code_salon, {"type": "bb_victoire", "gagnant": nom})
        return True
    return False
 



# =============================================================
# ACTIONS DE BUCKSHOT (version soiree)
# Roulette russe adaptee : deux paquets (cartouches reelles / a blanc)
# et des objets. Le serveur est l arbitre : la composition de la pile
# reste secrete, seuls le contenu des regards (Loupe / Telephone) sont
# envoyes en prive au joueur concerne.
# Chaque fonction recoit (code_salon, salons, diffuser, ...).
# =============================================================

# Les 6 objets possibles (memes que la version locale).
BT_OBJETS = [
    {"id": "loupe",     "nom": "Loupe",     "icone": "\U0001F50D"},
    {"id": "telephone", "nom": "Telephone", "icone": "\U0001F4F1"},
    {"id": "inverseur", "nom": "Inverseur", "icone": "\U0001F504"},
    {"id": "biere",     "nom": "Biere",     "icone": "\U0001F37A"},
    {"id": "menottes",  "nom": "Menottes",  "icone": "\U0001F512"},
    {"id": "scie",      "nom": "Scie",      "icone": "\U0001FA9A"},
]


BT_JOUEURS_MAX = 8


def bt_bornes_cartouches(etat):
    """
    Nombre de cartouches que le chargeur peut mettre : au moins une par
    joueur, au plus deux. Jusqu a 4 joueurs on garde 4 a 8 ; a 8 joueurs,
    8 a 16, pour que tout le monde tire au moins une fois par manche.
    """
    n = etat["nb_joueurs"]
    return max(4, n), max(8, 2 * n)


def bt_initialiser(nb_joueurs):
    """Initialise l etat d une partie de Buckshot."""
    return {
        "nb_joueurs": nb_joueurs,
        "phase": "composition",     # composition | objets | manche_fin
        "gorgees": [0] * nb_joueurs,
        "objets": [None] * nb_joueurs,   # {"id","nom","icone","utilise"} par manche
        "menottes": [False] * nb_joueurs,
        "courant": 0,               # joueur actif
        "chargeur": 0,              # joueur qui compose (verre le plus vide)
        "pile": [],                 # 'reel'/'blanc', index 0 = dessus (SECRET)
        "scie_active": False,
        "compteur_reel": 0,         # pour departager les egalites de verre vide
        "dernier_reel": [0] * nb_joueurs,
        # Composition annoncee a voix haute par le chargeur : publique. Seul
        # l ORDRE de la pile est secret.
        "annonce_reel": 0,
        "annonce_total": 0,
        "message": "",
    }


def bt_absents(salon):
    """Index des places laissees vides en cours de partie (joueur parti)."""
    return {j["index"] for j in salon["joueurs"] if j.get("parti")}


def bt_le_plus_vide(etat, absents=()):
    """
    Index du joueur au verre le plus vide (egalite : dernier a avoir bu du reel).
    Un absent ne peut pas charger : la manche l attendrait pour toujours.
    """
    idx, mini, dernier = 0, None, -1
    for i in range(etat["nb_joueurs"]):
        if i in absents:
            continue
        g = etat["gorgees"][i]
        if mini is None or g < mini or (g == mini and etat["dernier_reel"][i] > dernier):
            mini, idx, dernier = g, i, etat["dernier_reel"][i]
    return idx


def bt_etat_public(salon):
    """Etat visible par tous. Ne contient JAMAIS le contenu de la pile."""
    etat = salon["etat"]
    absents = bt_absents(salon)
    return {
        "type": "bt_etat",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "phase": etat["phase"],
        "gorgees": etat["gorgees"],
        "objets": etat["objets"],        # objets face visible : publics
        "menottes": etat["menottes"],
        "courant": etat["courant"],
        "chargeur": etat["chargeur"],
        "pile_reste": len(etat["pile"]),
        "scie_active": etat["scie_active"],
        "annonce_reel": etat.get("annonce_reel", 0),
        "annonce_total": etat.get("annonce_total", 0),
        "cartouches_min": bt_bornes_cartouches(etat)[0],
        "cartouches_max": bt_bornes_cartouches(etat)[1],
        "partis": [i in absents for i in range(etat["nb_joueurs"])],
        "message": etat.get("message", ""),
    }


async def bt_envoyer_index(code_salon, salons):
    """Envoie a chaque joueur son propre index (pour savoir qui il est)."""
    salon = salons[code_salon]
    for joueur in salon["joueurs"]:
        await envoyer(joueur, {"type": "bt_mon_index", "index": joueur["index"]})


async def bt_envoyer_prive(salon, index, message):
    """Envoie un message a un seul joueur (par son index)."""
    for joueur in salon["joueurs"]:
        if joueur["index"] == index:
            await envoyer(joueur, message)
            break


async def bt_demarrer(code_salon, salons, diffuser):
    """Demarre une partie Buckshot (le createur du salon lance)."""
    salon = salons[code_salon]
    nb = len(salon["joueurs"])
    if nb < 2:
        await diffuser(code_salon, {"type": "erreur", "message": "Buckshot demande au moins 2 joueurs."})
        return
    if nb > BT_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": "Buckshot se joue a 8 joueurs maximum."})
        return
    salon["etat"] = bt_initialiser(nb)
    salon["jeu"] = "buckshot"
    salon["etat"]["chargeur"] = bt_le_plus_vide(salon["etat"])
    await diffuser(code_salon, {"type": "bt_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await bt_envoyer_index(code_salon, salons)
    await diffuser(code_salon, bt_etat_public(salon))


async def bt_composer(code_salon, salons, diffuser, joueur, total, reel):
    """Le chargeur compose la pile puis on distribue les objets."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "composition":
        return
    if joueur["index"] != etat["chargeur"]:
        return
    # Bornes de securite
    try:
        total = int(total)
        reel = int(reel)
    except (TypeError, ValueError):
        return
    mini, maxi = bt_bornes_cartouches(etat)
    total = max(mini, min(maxi, total))
    reel = max(0, min(total, reel))

    # Construction et melange de la pile
    pile = ["reel"] * reel + ["blanc"] * (total - reel)
    random.shuffle(pile)
    etat["pile"] = pile

    # Distribution des objets : un par joueur, tire des 6 disponibles
    paquet = BT_OBJETS[:]
    random.shuffle(paquet)
    for i in range(etat["nb_joueurs"]):
        o = paquet[i % len(paquet)]
        etat["objets"][i] = {"id": o["id"], "nom": o["nom"], "icone": o["icone"], "utilise": False}

    etat["menottes"] = [False] * etat["nb_joueurs"]
    etat["scie_active"] = False
    etat["annonce_reel"] = reel
    etat["annonce_total"] = total
    etat["courant"] = etat["chargeur"]     # le chargeur ouvre la manche
    etat["phase"] = "objets"
    etat["message"] = "Nouvelle manche : " + str(reel) + " reelle(s) sur " + str(total) + "."
    await diffuser(code_salon, bt_etat_public(salon))


def bt_prochain(etat, absents=()):
    """
    Fait passer la main au joueur suivant, en sautant les menottes et les
    places vides : donner la main a un absent figerait la table.
    """
    n = etat["nb_joueurs"]
    for _ in range(n + 1):
        etat["courant"] = (etat["courant"] + 1) % n
        if etat["courant"] in absents:
            continue
        if etat["menottes"][etat["courant"]]:
            etat["menottes"][etat["courant"]] = False
            etat["message"] = "Menotte : le joueur saute son tour."
            continue
        return


async def bt_jouer_objet(code_salon, salons, diffuser, joueur, cible):
    """Un joueur joue son objet pendant la fenetre d objets (avant le tir)."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "objets":
        return
    idx = joueur["index"]
    # Un spectateur (arrive a table pleine) n a pas de place dans l etat :
    # sans ce garde-fou, objets[idx] levait une IndexError dans la boucle de
    # la WebSocket, et coupait sa connexion.
    if idx >= etat["nb_joueurs"]:
        return
    o = etat["objets"][idx]
    if not o or o["utilise"]:
        return
    oid = o["id"]

    if oid == "loupe":
        o["utilise"] = True
        etat["message"] = joueur["nom"] + " utilise la Loupe."
        if etat["pile"]:
            await bt_envoyer_prive(salon, idx, {
                "type": "bt_regard", "position": 0, "nature": etat["pile"][0],
            })
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "telephone":
        o["utilise"] = True
        etat["message"] = joueur["nom"] + " utilise le Telephone."
        if etat["pile"]:
            pos = 0
            if len(etat["pile"]) > 1:
                pos = random.randint(1, len(etat["pile"]) - 1)
            await bt_envoyer_prive(salon, idx, {
                "type": "bt_regard", "position": pos, "nature": etat["pile"][pos],
            })
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "inverseur":
        o["utilise"] = True
        if etat["pile"]:
            etat["pile"][0] = "blanc" if etat["pile"][0] == "reel" else "reel"
        etat["message"] = joueur["nom"] + " inverse la carte du dessus."
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "biere":
        o["utilise"] = True
        if etat["pile"]:
            etat["pile"].pop(0)
        etat["message"] = joueur["nom"] + " defausse la carte du dessus."
        if not etat["pile"]:
            await bt_manche_fin(code_salon, salons, diffuser)
            return
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "scie":
        o["utilise"] = True
        etat["scie_active"] = True
        etat["message"] = joueur["nom"] + " arme la Scie : prochaine reelle doublee."
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "menottes":
        if cible is None:
            return
        try:
            cible = int(cible)
        except (TypeError, ValueError):
            return
        if cible < 0 or cible >= etat["nb_joueurs"]:
            return
        o["utilise"] = True
        etat["menottes"][cible] = True
        etat["message"] = joueur["nom"] + " menotte " + salon["joueurs"][cible]["nom"] + "."
        await diffuser(code_salon, bt_etat_public(salon))


async def bt_tirer(code_salon, salons, diffuser, joueur, cible):
    """Le joueur actif se sert lui-meme ou sert un adversaire, puis tire."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "objets":
        return
    if joueur["index"] != etat["courant"]:
        return
    try:
        cible = int(cible)
    except (TypeError, ValueError):
        return
    if cible < 0 or cible >= etat["nb_joueurs"]:
        return
    if not etat["pile"]:
        await bt_manche_fin(code_salon, salons, diffuser)
        return

    nature = etat["pile"].pop(0)
    soi = (cible == etat["courant"])
    gorgees = 0
    if nature == "reel":
        gorgees = 1
        if etat["scie_active"]:
            gorgees = 2
            etat["scie_active"] = False
        etat["gorgees"][cible] += gorgees
        etat["compteur_reel"] += 1
        etat["dernier_reel"][cible] = etat["compteur_reel"]

    # On envoie l evenement de tir pour l animation cote client
    await diffuser(code_salon, {
        "type": "bt_tir",
        "tireur": etat["courant"],
        "cible": cible,
        "nature": nature,
        "gorgees": gorgees,
        "soi": soi,
    })

    # Resolution du tour : se viser soi + blanc = on rejoue
    rejoue = (soi and nature == "blanc")
    if not etat["pile"]:
        await bt_manche_fin(code_salon, salons, diffuser)
        return
    if not rejoue:
        bt_prochain(etat, bt_absents(salon))
    await diffuser(code_salon, bt_etat_public(salon))


async def bt_manche_fin(code_salon, salons, diffuser):
    """Fin de manche : bilan des gorgees, le plus vide rechargera."""
    salon = salons[code_salon]
    etat = salon["etat"]
    etat["chargeur"] = bt_le_plus_vide(etat, bt_absents(salon))
    etat["phase"] = "composition"
    etat["message"] = "Manche terminee."
    await diffuser(code_salon, {
        "type": "bt_manche_fin",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "gorgees": etat["gorgees"],
        "chargeur": etat["chargeur"],
    })
    await diffuser(code_salon, bt_etat_public(salon))


async def bt_joueur_parti(code_salon, index):
    """
    Un joueur quitte en pleine partie. Si la table l attendait -- pour tirer,
    ou pour charger le paquet -- la main passe au present suivant. Sans ca
    la partie restait figee jusqu a son retour, et pour toujours s il ne
    revenait pas. Sa place reste dans l etat : il la reprend avec son jeton.
    """
    salon = salons.get(code_salon)
    if not salon or not salon.get("etat") or salon.get("jeu") != "buckshot":
        return
    etat = salon["etat"]
    if index >= etat["nb_joueurs"]:
        return      # un spectateur ne tenait aucune place
    absents = bt_absents(salon)
    if len(absents) >= etat["nb_joueurs"]:
        return      # plus personne : le salon va etre libere

    nom = salon["joueurs"][index]["nom"]
    if etat["phase"] == "composition" and etat["chargeur"] == index:
        etat["chargeur"] = bt_le_plus_vide(etat, absents)
        etat["message"] = nom + " est parti : " + salon["joueurs"][etat["chargeur"]]["nom"] + " charge a sa place."
    elif etat["phase"] == "objets" and etat["courant"] == index:
        bt_prochain(etat, absents)
        etat["message"] = nom + " est parti : la main passe."
    else:
        return
    await diffuser(code_salon, bt_etat_public(salon))


# =============================================================
# PICOPOLY (Monopoly a boire)
# Les regles sont dans picolopoly.py. Ici : le reseau, et le minuteur qui
# joue le choix par defaut d un joueur qui ne repond pas (absent, ou parti)
# et arrete la partie au bout de l heure. Sans lui, un seul joueur qui
# s absente figerait toute la table.
# =============================================================

async def pp_demarrer(code_salon, difficulte=None):
    salon = salons[code_salon]
    if salon.get("jeu") == "picolopoly" and salon.get("etat") and salon["etat"]["phase"] != "fin":
        return      # deja en cours : un double clic ne relance pas tout
    # Comme bb_demarrer : on repart des seuls presents, renumerotes. Sur la
    # relance d une partie finie, une place vide recevrait des tours.
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < PP_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur", "message": "Picopoly demande au moins 2 joueurs."})
        return
    if nb > PP_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": "Picopoly se joue a 8 joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = pp_initialiser([j["nom"] for j in salon["joueurs"]], time.time(), difficulte)
    salon["jeu"] = "picolopoly"
    await diffuser(code_salon, {
        "type": "pp_demarree",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "plateau": PP_CASES,
    })
    await envoyer_indices(code_salon)
    await pp_publier(code_salon, [])


async def pp_publier(code_salon, evenements):
    """Envoie les evenements du moteur, puis l etat, puis rearme le minuteur."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "picolopoly" or not salon.get("etat"):
        return
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, pp_etat_public(salon["etat"], time.time()))
    pp_programmer(code_salon)


def pp_programmer(code_salon):
    """Un seul minuteur par salon, cale sur la prochaine echeance."""
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("pp_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    echeance = pp_prochaine_echeance(salon["etat"])
    salon["pp_minuteur"] = None if echeance is None else asyncio.create_task(
        pp_minuteur(code_salon, salon, echeance))


async def pp_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    # Le salon a pu etre libere, ou recree sous le meme code.
    if salons.get(code_salon) is not salon or salon.get("jeu") != "picolopoly":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        # Une action arrivee pendant qu on attendait le verrou a rearme un
        # autre minuteur : celui-ci n a plus rien a faire.
        if salon.get("pp_minuteur") is not moi:
            return
        # On se retire AVANT d envoyer quoi que ce soit : une action qui arrive
        # pendant la diffusion rearme le minuteur, et ne doit pas nous annuler
        # au milieu d un envoi.
        salon["pp_minuteur"] = None
        etat = salon["etat"]
        # Plus personne a table : on ne joue pas une heure de partie a la place
        # de tout le monde. Le retour du premier joueur relance le minuteur.
        if all(etat["partis"]):
            return
        version = etat["version"]
        evenements = pp_expirer(etat, time.time())
        if etat["version"] != version:
            await pp_publier(code_salon, evenements)
        else:
            pp_programmer(code_salon)


async def pp_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "picolopoly" or etat is None:
        return
    version = etat["version"]
    evenements = pp_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await pp_publier(code_salon, evenements)
    else:
        # Refus : seule l erreur privee part, l etat n a pas bouge.
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


async def pp_joueur_parti(code_salon, index):
    """
    La place reste (il la reprend avec son jeton) ; ses tours sont sautes, et
    si la table l attendait, son choix par defaut part apres quelques
    secondes au lieu du delai normal.
    """
    salon = salons.get(code_salon)
    if not salon or not salon.get("etat") or salon.get("jeu") != "picolopoly":
        return
    etat = salon["etat"]
    if index >= etat["nb_joueurs"]:
        return      # un spectateur ne tenait aucune place
    etat["partis"][index] = True
    etat["version"] += 1
    pp_rearmer_si_attendu(etat, index)
    await pp_publier(code_salon, [])


def pp_rearmer_si_attendu(etat, index):
    """
    Recalcule le delai seulement si c est CE joueur que la table attend :
    delai court s il vient de partir, normal s il revient. Avant, chaque
    arrivee ou depart de n importe qui remettait a zero le delai du joueur
    attendu : avec un telephone qui se reconnecte souvent, le choix par
    defaut d un absent ne partait jamais et la partie restait figee.
    """
    att = pp_attendu(etat)
    if att is not None and att[0] == index:
        pp_armer(etat)


# =============================================================
# MENTEUR (bluff aux cartes + roulette des shots)
# Les regles sont dans menteur.py. Ici : le reseau, les mains privees, et le
# minuteur qui joue a la place d un joueur absent ou trop lent, et enchaine
# les manches apres chaque retournement.
# =============================================================

async def mt_demarrer(code_salon, mode=None):
    salon = salons[code_salon]
    etat = salon.get("etat")
    # Lancement depuis la salle d attente, ou relance d une partie finie
    # (meme depuis un autre jeu du salon fini) : un double clic ne relance pas.
    if etat is not None and not (salon.get("jeu") == "menteur" and etat.get("phase") == "fin"):
        return
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < MT_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur", "message": "Menteur demande au moins 2 joueurs."})
        return
    if nb > MT_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": "Menteur se joue a 8 joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = mt_initialiser([j["nom"] for j in salon["joueurs"]], time.time(), mode)
    salon["jeu"] = "menteur"
    await diffuser(code_salon, {"type": "mt_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await mt_publier(code_salon, [])


async def mt_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous et sa main a chacun."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "menteur" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, mt_etat_public(etat, time.time()))
    for joueur in salon["joueurs"]:
        if not joueur.get("parti"):
            main = mt_main(etat, joueur["index"])
            if main is not None:
                await envoyer(joueur, main)
    mt_programmer(code_salon)


def mt_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("mt_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = mt_prochaine_echeance(etat) if etat and salon.get("jeu") == "menteur" else None
    salon["mt_minuteur"] = None if echeance is None else asyncio.create_task(
        mt_minuteur(code_salon, salon, echeance))


async def mt_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "menteur":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("mt_minuteur") is not moi:
            return
        salon["mt_minuteur"] = None
        etat = salon["etat"]
        # Plus personne a table : on ne joue pas toute la partie a leur place.
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = mt_expirer(etat, time.time())
        if etat["version"] != version:
            await mt_publier(code_salon, evenements)
        else:
            mt_programmer(code_salon)


async def mt_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "menteur" or etat is None:
        return
    version = etat["version"]
    evenements = mt_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await mt_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# CASINO (roulette, blackjack, poker)
# Les regles sont dans casino.py. Ici : le reseau, les cartes privees du
# poker, et le minuteur qui lance la roue, joue a la place d un joueur absent
# ou trop lent, et enchaine les coups.
# =============================================================

async def cs_demarrer(code_salon):
    salon = salons[code_salon]
    # Le casino n a pas de fin : on l ouvre depuis la salle d attente, ou a
    # la place d un autre jeu fini du salon. Un double clic ne rouvre rien.
    etat = salon.get("etat")
    if etat is not None:
        jeu = salon.get("jeu")
        fini = (jeu == "menteur" and etat.get("phase") == "fin") or \
               (jeu == "beerbattle" and etat.get("phase") == "fini")
        if not fini:
            return
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < CS_JOUEURS_MIN:
        return
    if nb > CS_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": "Le casino accueille 8 joueurs au plus."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = cs_initialiser([j["nom"] for j in salon["joueurs"]], time.time())
    salon["jeu"] = "casino"
    await diffuser(code_salon, {"type": "cs_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await cs_publier(code_salon, [])


async def cs_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous et ses cartes a chacun."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "casino" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, cs_etat_public(etat, time.time()))
    for joueur in salon["joueurs"]:
        if not joueur.get("parti"):
            main = cs_main_poker(etat, joueur["index"])
            if main is not None:
                await envoyer(joueur, main)
    cs_programmer(code_salon)


def cs_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("cs_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = cs_prochaine_echeance(etat) if etat and salon.get("jeu") == "casino" else None
    salon["cs_minuteur"] = None if echeance is None else asyncio.create_task(
        cs_minuteur(code_salon, salon, echeance))


async def cs_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "casino":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("cs_minuteur") is not moi:
            return
        salon["cs_minuteur"] = None
        etat = salon["etat"]
        # Plus personne a table : la roue ne tourne pas dans le vide.
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = cs_expirer(etat, time.time())
        if etat["version"] != version:
            await cs_publier(code_salon, evenements)
        else:
            cs_programmer(code_salon)


async def cs_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "casino" or etat is None:
        return
    version = etat["version"]
    evenements = cs_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await cs_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# ENDPOINT HTTP : creer un salon
# Les joueurs font une requete HTTP pour obtenir un code de salon.
# =============================================================

@app.get("/creer-salon")
async def creer_salon():
    """Cree un nouveau salon vide et retourne son code."""
    code = generer_code()
    salons[code] = {"joueurs": [], "etat": None, "jeu": None}
    return {"code": code}


@app.get("/salon/{code}")
async def info_salon(code: str):
    """Retourne les infos d un salon (pour verifier qu il existe)."""
    if code not in salons:
        return {"existe": False}
    salon = salons[code]
    return {
        "existe": True,
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "jeu": salon["jeu"],
    }


@app.get("/")
async def racine():
    """Page d accueil du serveur (pour verifier qu il tourne)."""
    return {"status": "Le serveur tourne", "salons_actifs": len(salons), "version": VERSION}
