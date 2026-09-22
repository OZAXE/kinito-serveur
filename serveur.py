"""
Serveur multijoueur pour le Kinito, BeerBattle et Buckshot.
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
import random
import secrets
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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


# =============================================================
# UTILITAIRES
# =============================================================

def generer_code():
    """Genere un code de salon unique a 4 lettres."""
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in salons:
            return code


async def diffuser(code_salon, message):
    """
    Envoie un message JSON a TOUS les joueurs d un salon.
    C est la fonction cle du multijoueur : quand quelque chose
    se passe, tout le monde est informe instantanement.
    """
    salon = salons.get(code_salon)
    if not salon:
        return
    # On itere sur tous les joueurs encore presents et on leur envoie.
    for joueur in salon["joueurs"]:
        if joueur.get("parti"):
            continue
        try:
            await joueur["ws"].send_text(json.dumps(message))
        except Exception:
            # On marque la place comme vide au lieu de retirer l element :
            # retirer decalerait l index de tous les joueurs suivants, et
            # donc le tour de jeu. Le nettoyage reel se fait a la
            # deconnexion (voir le finally de websocket_endpoint).
            joueur["parti"] = True


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
        try:
            await j["ws"].send_text(json.dumps({
                "type": "ton_index",
                "index": j["index"],
                "nom": j["nom"],
                # Jeton de session : permet de retrouver sa place apres une
                # coupure, meme si un autre joueur porte le meme prenom.
                "jeton": j.get("jeton"),
            }))
        except Exception:
            pass


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

    # On recupere ou cree le salon
    if code_salon not in salons:
        # Nouveau salon : on l initialise
        salons[code_salon] = {
            "joueurs": [],
            "etat": None,   # l etat sera initialise quand la partie commence
            "jeu": None,    # "kinito" ou "beerbattle"
        }

    salon = salons[code_salon]
    jeton = ws.query_params.get("jeton")

    # Un joueur qui revient reprend SA place (meme index, meme siege) au lieu
    # d en occuper une nouvelle. On l identifie par son jeton et non par son
    # prenom, pour que deux homonymes ne puissent pas se voler leur place.
    joueur = None
    if jeton:
        for j in salon["joueurs"]:
            if j.get("jeton") == jeton:
                joueur = j
                break

    retour = joueur is not None
    if retour:
        ancien_ws = joueur.get("ws")
        joueur["ws"] = ws
        joueur["parti"] = False
        etat_courant = salon.get("etat")
        if etat_courant and joueur["index"] < len(etat_courant.get("partis", [])):
            etat_courant["partis"][joueur["index"]] = False
            kinito_reprendre(etat_courant)
        # Si un vieil onglet tenait encore la place, on le libere.
        if ancien_ws is not None and ancien_ws is not ws:
            try:
                await ancien_ws.close()
            except Exception:
                pass
    else:
        joueur = {
            "nom": nom_joueur,
            "ws": ws,
            "index": len(salon["joueurs"]),
            "jeton": secrets.token_hex(16),
            "parti": False,
        }
        salon["joueurs"].append(joueur)

        # Arrivee en pleine partie : on agrandit le plateau pour que le
        # nouveau venu entre vraiment dans le tour, au lieu de rester
        # spectateur avec un index hors bornes. Ajouter en fin de liste ne
        # decale l index de personne.
        etat_courant = salon.get("etat")
        if etat_courant is not None and salon.get("jeu") == "kinito":
            etat_courant["nb_joueurs"] = len(salon["joueurs"])
            etat_courant["partis"].append(False)
            etat_courant["message"] = nom_joueur + " rejoint la partie."
            kinito_reprendre(etat_courant)

    # On informe tout le monde qu un joueur a rejoint (ou est revenu)
    await diffuser(code_salon, {
        "type": "joueur_rejoint",
        "nom": joueur["nom"],
        "retour": retour,
        "joueurs": [j["nom"] for j in salon["joueurs"]],
    })
    await envoyer_indices(code_salon)

    # Qu il revienne ou qu il arrive, celui qui se connecte en pleine partie
    # a manque le "partie_demarree" initial : sans lui son client ne peut pas
    # construire la table.
    if salon.get("etat") is not None and salon.get("jeu") == "kinito":
        try:
            await ws.send_text(json.dumps({
                "type": "partie_demarree",
                "jeu": "kinito",
                "joueurs": [j["nom"] for j in salon["joueurs"]],
            }))
        except Exception:
            pass
        await diffuser(code_salon, construire_etat_public(code_salon))

    try:
        # Boucle principale : on ecoute les messages de ce joueur
        async for message_brut in ws.iter_text():
            message = json.loads(message_brut)
            await traiter_message(code_salon, joueur, message)

    except WebSocketDisconnect:
        pass

    finally:
        # ATTENTION : iter_text() de Starlette attrape lui-meme
        # WebSocketDisconnect et se contente de terminer la boucle. Le bloc
        # "except" ci-dessus ne s executait donc jamais, et le nettoyage non
        # plus : joueurs fantomes dans les salons, "joueur_parti" jamais
        # envoye, salons vides jamais liberes. D ou ce finally.
        salon = salons.get(code_salon)
        # Si la place a deja ete reprise par une connexion plus recente,
        # ce bloc ne doit surtout pas la marquer comme vide.
        if (salon and joueur in salon["joueurs"]
                and joueur.get("ws") is ws and not joueur.get("parti")):
            index_parti = joueur["index"]

            if salon.get("etat") is None:
                # --- Salle d attente : on retire vraiment et on renumerote,
                # pour que la liste reste compacte et les index justes.
                salon["joueurs"].remove(joueur)
                await diffuser(code_salon, {
                    "type": "joueur_parti",
                    "nom": joueur["nom"],
                    "joueurs": [j["nom"] for j in salon["joueurs"]],
                })
                if salon["joueurs"]:
                    await envoyer_indices(code_salon)
                else:
                    salons.pop(code_salon, None)
            else:
                # --- En pleine partie : on garde la place dans la liste pour
                # ne decaler l index de personne, on la marque vide, et le
                # tour de jeu la sautera.
                joueur["parti"] = True
                await diffuser(code_salon, {
                    "type": "joueur_parti",
                    "nom": nom_joueur,
                    "joueurs": [j["nom"] for j in salon["joueurs"]],
                })
                if salon.get("jeu") == "kinito":
                    await kinito_joueur_parti(code_salon, index_parti)

                # Plus personne de connecte : on libere le salon.
                if all(j.get("parti") for j in salon["joueurs"]):
                    salons.pop(code_salon, None)


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

    # --- Demarrer une partie ---
    if type_msg == "demarrer":
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
        await bt_demarrer(code_salon, salons, diffuser)
    elif type_msg == "bt_composer":
        await bt_composer(code_salon, salons, diffuser, joueur, message.get("total"), message.get("reel"))
    elif type_msg == "bt_jouer_objet":
        await bt_jouer_objet(code_salon, salons, diffuser, joueur, message.get("cible"))
    elif type_msg == "bt_tirer":
        await bt_tirer(code_salon, salons, diffuser, joueur, message.get("cible"))

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

    # Securite : seul le joueur courant peut lancer
    if joueur["index"] != etat["joueur_courant"]:
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
    await joueur["ws"].send_text(json.dumps({
        "type": "ton_score",
        "score": score,
        "scores_possibles": SCORES_ANNONCABLES,
        "premiere_annonce": etat["premiere_annonce"],
    }))


async def kinito_annoncer(code_salon, joueur, score_annonce):
    """Le joueur courant annonce un score (vrai ou bluff)."""
    salon = salons[code_salon]
    etat = salon["etat"]

    if joueur["index"] != etat["joueur_courant"]:
        return

    if score_annonce not in SCORES_ANNONCABLES:
        await joueur["ws"].send_text(json.dumps({
            "type": "erreur",
            "message": "Ce score ne peut pas etre annonce.",
        }))
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
        # On ne donne PAS le score annonce aux autres (regle d attention)
    })

    # On envoie separement au reacteur pour qu il sache que c est son tour
    await salon["joueurs"][index_reacteur]["ws"].send_text(json.dumps({
        "type": "a_toi_de_reagir",
        "annonceur": nom_annonceur,
        "premiere_annonce": etat["premiere_annonce"],
    }))


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

    if joueur["index"] != index_reacteur:
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
    if joueur["index"] != etat["joueur_courant"]:
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

    # Le prochain tour repart du joueur apres le perdant
    etat["joueur_courant"] = joueur_suivant(etat, index_perdant)
    etat["phase"] = "resultat"

    await diffuser(code_salon, {
        "type": "fin_manche",
        "perdant": nom_perdant,
        "index_perdant": index_perdant,   # evite aux clients de retrouver le joueur par son nom
        "menteur": menteur,
        "message": message,
        "gorgees": gorgees,         # None = cul sec (cas du 21)
        "score_reel": score_reel,   # None si on ne revele pas
        "score_base": score_base,
    })


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

    etat["premiere_annonce"] = True
    etat["annonce_precedente"] = 0
    etat["score_reel"] = 0
    etat["score_annonce"] = 0
    etat["phase"] = "lancer"

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
        "message": etat.get("message", ""),
    }
 
 
# =============================================================
# ACTIONS DE BEERBATTLE
# Chaque fonction recoit (salon, joueur, message) et modifie l etat.
# Le serveur appelant doit ensuite diffuser le nouvel etat.
# =============================================================
 
async def bb_demarrer(code_salon, salons, diffuser):
    """Demarre une partie BeerBattle."""
    salon = salons[code_salon]
    nb = len(salon["joueurs"])
    if nb < 3:
        await diffuser(code_salon, {"type": "erreur", "message": "BeerBattle nécessite au moins 3 joueurs."})
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
            try:
                await joueur["ws"].send_text(json.dumps({
                    "type": "bb_ta_main",
                    "main": etat["mains"][i],
                    "mon_index": i,
                }))
            except Exception:
                pass
 

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
            try:
                await joueur["ws"].send_text(json.dumps({
                    "type": "bb_carte_case",
                    "carte": carte_sol,   # None s il n y a pas de carte
                }))
            except Exception:
                pass
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
 
    etat["positions"][joueur["index"]] = {"ligne": ligne, "colonne": colonne}
    etat["joueur_en_placement"] += 1
 
    if etat["joueur_en_placement"] >= etat["nb_joueurs"]:
        etat["phase"] = "jeu"
        etat["joueur_courant"] = 0
        etat["action_faite"] = False
    await diffuser(code_salon, bb_etat_public(salon))
 
 
async def bb_deplacer(code_salon, salons, diffuser, joueur, ligne, colonne, via_carte, index_carte):
    """Deplace le joueur courant."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "jeu" or joueur["index"] != etat["joueur_courant"]:
        return
    if etat["action_faite"]:
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
    if joueur["index"] != etat["joueur_courant"]:
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
 
    # Fin du tour : joueur suivant non elimine
    bb_joueur_suivant(etat)
    etat["action_faite"] = False
    etat["message"] = ""
    await diffuser(code_salon, bb_etat_public(salon))
    await bb_envoyer_mains(code_salon, salons)
 
 
def bb_joueur_suivant(etat):
    """Passe au joueur suivant non elimine."""
    nb = etat["nb_joueurs"]
    suivant = (etat["joueur_courant"] + 1) % nb
    # On saute les elimines
    tours = 0
    while etat["elimine"][suivant] and tours < nb:
        suivant = (suivant + 1) % nb
        tours += 1
    etat["joueur_courant"] = suivant
 
 
async def bb_verifier_victoire(code_salon, salons, diffuser):
    """Verifie s il ne reste qu un joueur."""
    salon = salons[code_salon]
    etat = salon["etat"]
    survivants = [i for i in range(etat["nb_joueurs"]) if not etat["elimine"][i]]
    if len(survivants) <= 1:
        gagnant = survivants[0] if survivants else None
        nom = salon["joueurs"][gagnant]["nom"] if gagnant is not None else "Personne"
        etat["phase"] = "fini"
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
        "message": "",
    }


def bt_le_plus_vide(etat):
    """Index du joueur au verre le plus vide (egalite : dernier a avoir bu du reel)."""
    idx, mini, dernier = 0, None, -1
    for i in range(etat["nb_joueurs"]):
        g = etat["gorgees"][i]
        if mini is None or g < mini or (g == mini and etat["dernier_reel"][i] > dernier):
            mini, idx, dernier = g, i, etat["dernier_reel"][i]
    return idx


def bt_etat_public(salon):
    """Etat visible par tous. Ne contient JAMAIS le contenu de la pile."""
    etat = salon["etat"]
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
        "message": etat.get("message", ""),
    }


async def bt_envoyer_index(code_salon, salons):
    """Envoie a chaque joueur son propre index (pour savoir qui il est)."""
    salon = salons[code_salon]
    for joueur in salon["joueurs"]:
        try:
            await joueur["ws"].send_text(json.dumps({
                "type": "bt_mon_index", "index": joueur["index"],
            }))
        except Exception:
            pass


async def bt_envoyer_prive(salon, index, message):
    """Envoie un message a un seul joueur (par son index)."""
    for joueur in salon["joueurs"]:
        if joueur["index"] == index:
            try:
                await joueur["ws"].send_text(json.dumps(message))
            except Exception:
                pass
            break


async def bt_demarrer(code_salon, salons, diffuser):
    """Demarre une partie Buckshot (le createur du salon lance)."""
    salon = salons[code_salon]
    nb = len(salon["joueurs"])
    if nb < 2:
        await diffuser(code_salon, {"type": "erreur", "message": "Buckshot demande au moins 2 joueurs."})
        return
    if nb > 6:
        await diffuser(code_salon, {"type": "erreur", "message": "Buckshot se joue a 6 joueurs maximum."})
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
    total = max(4, min(8, total))
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
    etat["courant"] = etat["chargeur"]     # le chargeur ouvre la manche
    etat["phase"] = "objets"
    etat["message"] = "Nouvelle manche : " + str(reel) + " reelle(s) sur " + str(total) + "."
    await diffuser(code_salon, bt_etat_public(salon))


def bt_prochain(etat):
    """Fait passer la main au joueur suivant (en sautant les menottes)."""
    n = etat["nb_joueurs"]
    for _ in range(n + 1):
        etat["courant"] = (etat["courant"] + 1) % n
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
        bt_prochain(etat)
    await diffuser(code_salon, bt_etat_public(salon))


async def bt_manche_fin(code_salon, salons, diffuser):
    """Fin de manche : bilan des gorgees, le plus vide rechargera."""
    salon = salons[code_salon]
    etat = salon["etat"]
    etat["chargeur"] = bt_le_plus_vide(etat)
    etat["phase"] = "composition"
    etat["message"] = "Manche terminee."
    await diffuser(code_salon, {
        "type": "bt_manche_fin",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "gorgees": etat["gorgees"],
        "chargeur": etat["chargeur"],
    })
    await diffuser(code_salon, bt_etat_public(salon))


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
    return {"status": "Le serveur tourne", "salons_actifs": len(salons)}
