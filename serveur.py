"""
Serveur multijoueur pour le Kinito et BeerBattle.
Technologie : FastAPI + WebSockets pour la communication temps reel.

Architecture :
- Un joueur cree un salon (code a 4 lettres)
- Les autres rejoignent avec ce code
- Chaque action est envoyee au serveur via WebSocket
- Le serveur met a jour l etat et le renvoie a tous les joueurs
"""

import asyncio
import json
import random
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
    # On itere sur tous les joueurs connectes et on leur envoie
    deconnectes = []
    for joueur in salon["joueurs"]:
        try:
            await joueur["ws"].send_text(json.dumps(message))
        except Exception:
            deconnectes.append(joueur)
    # On nettoie les connexions mortes
    for j in deconnectes:
        salon["joueurs"].remove(j)


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
    }


def joueur_suivant(etat, index):
    """Retourne l index du joueur suivant selon le sens."""
    nb = etat["nb_joueurs"]
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

    # On ajoute le joueur au salon
    joueur = {"nom": nom_joueur, "ws": ws, "index": len(salon["joueurs"])}
    salon["joueurs"].append(joueur)

    # On informe tout le monde qu un joueur a rejoint
    await diffuser(code_salon, {
        "type": "joueur_rejoint",
        "nom": nom_joueur,
        "joueurs": [j["nom"] for j in salon["joueurs"]],
    })

    try:
        # Boucle principale : on ecoute les messages de ce joueur
        async for message_brut in ws.iter_text():
            message = json.loads(message_brut)
            await traiter_message(code_salon, joueur, message)

    except WebSocketDisconnect:
        # Le joueur s est deconnecte
        salon["joueurs"].remove(joueur)
        await diffuser(code_salon, {
            "type": "joueur_parti",
            "nom": nom_joueur,
            "joueurs": [j["nom"] for j in salon["joueurs"]],
        })
        # Si le salon est vide, on le supprime
        if not salon["joueurs"]:
            del salons[code_salon]


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
    elif type_msg == "bb_poser_arme":
        await bb_poser_arme(code_salon, salons, diffuser, joueur, message.get("index_carte"))
    elif type_msg == "bb_dame":
        await bb_dame(code_salon, salons, diffuser, joueur, message.get("cible"), message.get("index_carte"))
    elif type_msg == "bb_ne_rien_faire":
        await bb_ne_rien_faire(code_salon, salons, diffuser, joueur)
    elif type_msg == "bb_ramasser":
        await bb_ramasser(code_salon, salons, diffuser, joueur, message.get("decision"), message.get("index_echange"))

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
        etat["phase"] = "resultat_special"
        await diffuser(code_salon, {
            "type": "effet_special",
            "effet": "51",
            "message": etat["message"],
        })
        return

    if score == SCORE_CHANGE_SENS:
        etat["score_annonce"] = score
        etat["sens"] = -etat["sens"]
        etat["annonce_precedente"] = SCORE_CHANGE_SENS
        etat["premiere_annonce"] = False
        sens_texte = "horaire" if etat["sens"] == 1 else "anti-horaire"
        etat["message"] = f"31 : changement de sens ({sens_texte}) !"
        etat["joueur_courant"] = joueur_suivant(etat, etat["joueur_courant"])
        etat["phase"] = "resultat_special"
        await diffuser(code_salon, {
            "type": "effet_special",
            "effet": "31",
            "message": etat["message"],
            "sens": etat["sens"],
        })
        return

    # Score normal : on envoie le score UNIQUEMENT au joueur qui a lance
    etat["phase"] = "annonce"
    await joueur["ws"].send_text(json.dumps({
        "type": "ton_score",
        "score": score,
        "scores_possibles": ECHELLE,
        "premiere_annonce": etat["premiere_annonce"],
    }))


async def kinito_annoncer(code_salon, joueur, score_annonce):
    """Le joueur courant annonce un score (vrai ou bluff)."""
    salon = salons[code_salon]
    etat = salon["etat"]

    if joueur["index"] != etat["joueur_courant"]:
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
        "menteur": menteur,
        "message": message,
        "gorgees": gorgees,         # None = cul sec (cas du 21)
        "score_reel": score_reel,   # None si on ne revele pas
        "score_base": score_base,
    })


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
 
 
async def bb_ne_rien_faire(code_salon, salons, diffuser, joueur):
    """Le joueur passe son action."""
    salon = salons[code_salon]
    etat = salon["etat"]
    if etat["phase"] != "jeu" or joueur["index"] != etat["joueur_courant"] or etat["action_faite"]:
        return
    etat["action_faite"] = True
    await diffuser(code_salon, bb_etat_public(salon))
 
 
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
