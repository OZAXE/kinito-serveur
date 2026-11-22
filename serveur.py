"""
Serveur multijoueur pour le Kinito, BeerBattle, Buckshot, Picopoly, Menteur, le Casino
et Undercover.
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
import os
import random
import re
import secrets
import string
import time
import unicodedata
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
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
# Undercover (deduction sociale, indices a l oral) : meme principe.
from undercover import (
    UC_JOUEURS_MIN, UC_JOUEURS_MAX,
    uc_initialiser, uc_action, uc_expirer, uc_etat_public, uc_mot,
    uc_prochaine_echeance, uc_joueur_parti, uc_joueur_revenu,
)
# Gribouillophone (on ecrit, on dessine, on devine) : meme principe.
from telephone import (
    TD_JOUEURS_MIN, TD_JOUEURS_MAX,
    td_initialiser, td_action, td_expirer, td_etat_public, td_tache, td_galerie,
    td_prochaine_echeance, td_joueur_parti, td_joueur_revenu,
)
# Royaumes (conquete de regions, race + pouvoir) : meme principe.
from conquete import (
    SW_JOUEURS_MIN, SW_JOUEURS_MAX,
    sw_initialiser, sw_action, sw_expirer, sw_etat_public, sw_catalogue,
    sw_prochaine_echeance, sw_joueur_parti, sw_joueur_revenu,
)
# Le Juste Pinte (estimation, une question chiffree par manche) : meme principe.
from justepinte import (
    JP_JOUEURS_MIN, JP_JOUEURS_MAX,
    jp_initialiser, jp_action, jp_expirer, jp_etat_public, jp_reponse,
    jp_prochaine_echeance, jp_joueur_parti, jp_joueur_revenu,
)
# Qui de nous ? (vote social) : meme principe.
from quidenous import (
    QN_JOUEURS_MIN, QN_JOUEURS_MAX,
    qn_initialiser, qn_action, qn_expirer, qn_etat_public, qn_vote,
    qn_prochaine_echeance, qn_joueur_parti, qn_joueur_revenu,
)
# La Tournee (party game plateau + mini-jeux) : meme principe.
from tournee import (
    TR_JOUEURS_MIN, TR_JOUEURS_MAX,
    tr_initialiser, tr_action, tr_expirer, tr_etat_public, tr_score,
    tr_prochaine_echeance, tr_joueur_parti, tr_joueur_revenu,
)
# Casino (roulette, blackjack, poker) : meme principe, moteur pur a part.
from casino import (
    CS_JOUEURS_MIN, CS_JOUEURS_MAX,
    cs_initialiser, cs_ajouter_joueur, cs_action, cs_expirer, cs_etat_public,
    cs_main_poker, cs_prochaine_echeance, cs_joueur_parti, cs_joueur_revenu,
)

# Stockage durable (base Neon si DATABASE_URL est defini) et couches
# paralleles d une soiree (l Enquete).
from stockage import creer_stockage, Sauvegarde
from couches import Contexte, TYPES_COUCHES
from protection import Debit, Fenetre, Connexions, ip_client
import enquete_couche  # noqa: F401  (enregistre la couche « enq » : l Enquete)
import bingo_couche  # noqa: F401  (enregistre la couche « bng » : le Bingo de soiree)

# =============================================================
# CREATION DE L APPLICATION FASTAPI
# =============================================================


@asynccontextmanager
async def cycle_de_vie(_app):
    await demarrage()
    demarrer_veilleur()     # nettoie aussi les salons crees mais jamais rejoints
    yield
    await arret()


app = FastAPI(lifespan=cycle_de_vie)

# =============================================================
# LIMITES PAR ADRESSE IP
# Une soiree, c est souvent 10 a 20 telephones sur le meme wifi (donc la meme
# adresse) : chacun a une connexion de table, parfois une connexion soiree,
# se reconnecte apres une coupure et l accueil interroge le serveur toutes
# les 8 s. Les limites laissent cette marge, mais bloquent un script qui
# ouvre des milliers de connexions ou essaie les codes un par un.
# =============================================================
CONNEXIONS_PAR_IP = 40          # WebSockets ouvertes en meme temps
OUVERTURES_PAR_IP = 120         # nouvelles WebSockets par minute
REQUETES_PAR_IP = 600           # requetes HTTP par minute
CREATIONS_PAR_IP = 30           # soirees / tables creees ou rouvertes par minute
RATES_PAR_IP = 30               # codes inexistants essayes par minute

connexions_ip = Connexions(CONNEXIONS_PAR_IP)
ouvertures_ip = Fenetre(OUVERTURES_PAR_IP)
requetes_ip = Fenetre(REQUETES_PAR_IP)
creations_ip = Fenetre(CREATIONS_PAR_IP)
rates_ip = Fenetre(RATES_PAR_IP)


def _ip_requete(request):
    return ip_client(request.headers, request.client) if request is not None else None


@app.middleware("http")
async def limiter_requetes(request: Request, call_next):
    if not requetes_ip.autoriser(_ip_requete(request)):
        return JSONResponse({"detail": "Trop de requêtes, réessaie dans une minute."}, status_code=429)
    return await call_next(request)


def deviner_bloque(ip, code):
    """
    Anti-devinette : chaque essai d un code de soiree inexistant compte. Au-dela
    de RATES_PAR_IP par minute, cette adresse ne recoit plus que « n existe pas »
    (y compris pour un code qui existe) : parcourir les 456 976 codes a 4 lettres
    prendrait des semaines au lieu de quelques heures.
    """
    if rates_ip.plein(ip):
        return True
    if not soiree_existe(soiree_de(code)):
        rates_ip.autoriser(ip)
    return False


def admettre_connexion(ws, code):
    """Controle a l ouverture d une WebSocket. Rend (admise, ip)."""
    ip = ip_client(ws.headers, ws.client)
    if not origine_autorisee(ws.headers.get("origin")):
        journal.warning("WebSocket refusee : origine %r", ws.headers.get("origin"))
        return False, ip
    if not ouvertures_ip.autoriser(ip) or deviner_bloque(ip, code):
        return False, ip
    if not connexions_ip.entrer(ip):
        return False, ip
    return True, ip

# =============================================================
# ORIGINES AUTORISEES
# Seules les pages du site (GitHub Pages) et un poste de dev (localhost,
# fichier ouvert en local : origine "null") peuvent appeler le serveur depuis
# un navigateur. Sans ca, n importe quel site pouvait faire ouvrir des
# connexions a ses visiteurs. Un client hors navigateur n envoie pas d Origin
# (ou en invente une) : ce controle ne remplace pas les limites ci-dessus.
# Autre domaine : variable d environnement ORIGINES_AUTORISEES (separees par
# des virgules) sur Render.
# =============================================================
ORIGINES_AUTORISEES = [o.strip().rstrip("/") for o in os.environ.get(
    "ORIGINES_AUTORISEES", "https://ozaxe.github.io,null").split(",") if o.strip()]
ORIGINES_LOCALES = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


def origine_autorisee(origine):
    """Une connexion sans en-tete Origin (client hors navigateur) est acceptee."""
    if origine is None:
        return True
    origine = origine.rstrip("/")
    return origine in ORIGINES_AUTORISEES or re.fullmatch(ORIGINES_LOCALES, origine) is not None


app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_AUTORISEES,
    allow_origin_regex=ORIGINES_LOCALES,
    allow_methods=["GET"],
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
# Un salon cree (/creer-salon, /rouvrir, /nouvelle-table) ou personne ne s est
# jamais assis n avait aucune liberation programmee : il restait en memoire
# jusqu au redemarrage. Le veilleur le retire apres ce delai, sauf si la
# soiree est encore ouverte quelque part (accueil, Enquete).
DELAI_SALON_ABANDONNE = 900
NETTOYAGE_TOUTES_LES = 60       # secondes entre deux passages du nettoyage
# Plafond global : le serveur gratuit de Render a peu de memoire. Au-dela,
# plus aucun salon n est cree (les tables existantes continuent).
SALONS_MAX = 2000
# Un envoi vers une connexion bloquee ne doit pas figer tout le salon.
DELAI_ENVOI = 5
# Taille maximale d un message recu (en caracteres). Les clients n envoient
# que de petites actions (quelques centaines d octets au plus) : au-dela,
# c est un client fou ou malveillant, sa connexion est fermee (code 1009).
# Uvicorn coupe deja au niveau du protocole avec --ws-max-size (render.yaml).
TAILLE_MAX_MESSAGE = 16 * 1024
# Debit maximal par connexion : au-dela, les messages sont ignores ; une
# inondation continue (REFUS_AVANT_COUPURE refus d affilee) ferme la connexion.
DEBIT_MESSAGES = 20         # par seconde, en continu
RAFALE_MESSAGES = 40
REFUS_AVANT_COUPURE = 200

_veilleur = None

# Affichee sur la page d accueil du serveur : permet de verifier qu un
# deploiement est bien en ligne.
VERSION = "2026-11-18 royaumes"


# =============================================================
# UTILITAIRES
# =============================================================

# =============================================================
# SOIREES ET TABLES
# Une soiree a un code de 4 lettres ("ABCD"), qui est aussi celui de sa
# table 1. Ses autres tables sont "ABCD-2" ... "ABCD-9" : chacune est un
# salon a part entiere (son jeu, ses joueurs, ses reconnexions). Les couches
# (l Enquete) sont rattachees a la SOIREE et couvrent toutes ses tables.
# Un prenom n est qu a une table a la fois.
# =============================================================
TABLES_MAX = 9
# Places au plus a une table (joueurs et spectateurs, places gardees comprises).
# Chaque jeu verifie en plus son propre maximum au lancement ; ce plafond
# empeche d entasser des connexions sans fin dans une salle d attente.
PLACES_MAX = 20


# Forme d un code de table : 4 lettres majuscules, suivies de -2 a -9 pour
# les tables supplementaires. Tout le reste est refuse a l entree : avant,
# n importe quelle chaine (longue, minuscules...) creait un salon en memoire.
CODE_TABLE = re.compile(r"[A-Z]{4}(-[2-9])?")


def normaliser_code(code):
    """Le code en majuscules s il a la bonne forme, sinon None."""
    code = (code or "").strip().upper()
    return code if CODE_TABLE.fullmatch(code) else None


def nettoyer_prenom(nom):
    """Prenom affichable : sans caracteres de controle, espaces reduits, 24 caracteres."""
    nom = "".join(c if unicodedata.category(c) != "Cc" else (" " if c.isspace() else "")
                  for c in (nom or ""))
    return " ".join(nom.split())[:24]


def soiree_de(code):
    return code.split("-", 1)[0]


def numero_table(code):
    _, _, n = code.partition("-")
    return int(n) if n.isdigit() else 1


def code_table(soiree, numero):
    return soiree if numero == 1 else f"{soiree}-{numero}"


def tables_de(soiree):
    """Codes des tables existantes de la soiree, dans l ordre."""
    return sorted((c for c in salons if soiree_de(c) == soiree), key=numero_table)


def soiree_existe(soiree):
    return soiree in couches or any(soiree_de(c) == soiree for c in salons)


def generer_code():
    """
    Genere un code de soiree unique a 4 lettres. Tirage cryptographique
    (secrets) : random (Mersenne Twister) se predit apres assez de tirages
    observes, on aurait pu deviner les codes des autres soirees en
    appelant /creer-salon a la chaine.
    """
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
        # Un code dont la soiree vit encore (une table, ou une couche en cours
        # comme l Enquete, meme si tous ses salons ont ete liberes) n est pas libre.
        if not soiree_existe(code):
            return code


def nouveau_salon():
    return {"joueurs": [], "etat": None, "jeu": None, "cree": time.time()}


def salons_pleins():
    return len(salons) >= SALONS_MAX


def refus_plein():
    journal.warning("Plafond de %d salons atteint", SALONS_MAX)
    return JSONResponse({"detail": "Serveur complet, réessaie plus tard."}, status_code=503)


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


def lire_message(brut):
    """
    Decode un message recu : un objet JSON, sinon None (ignore). Un JSON trop
    imbrique ([[[[...]]]]) leve RecursionError et non ValueError : il coupait
    la connexion au lieu d etre simplement ignore.
    """
    try:
        message = json.loads(brut)
    except (ValueError, RecursionError):
        return None
    return message if isinstance(message, dict) else None


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
            # Le jeu qui tourne a la table : une page qui revient le sait
            # avant meme d avoir recu la partie (lente a arriver si le
            # reseau rame) et n affiche pas une salle d attente trompeuse.
            "jeu": salon.get("jeu"),
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
    code_salon = normaliser_code(code_salon)
    if code_salon is None:
        await ws.close(4004)        # refus avant l ouverture (HTTP 403)
        return
    admise, ip = admettre_connexion(ws, code_salon)
    if not admise:
        await ws.close(1013)
        return
    try:
        await connexion_table(ws, code_salon, nom_joueur)
    finally:
        connexions_ip.sortir(ip)


async def connexion_table(ws, code_salon, nom_joueur):
    if code_salon not in salons and salons_pleins():
        refus_plein()
        await ws.close(1013)
        return
    await ws.accept()
    demarrer_veilleur()
    nom_joueur = nettoyer_prenom(nom_joueur) or "Joueur"

    # On recupere ou cree le salon
    if code_salon not in salons:
        salons[code_salon] = nouveau_salon()
    salon = salons[code_salon]

    async with verrou(salon):
        # Le salon a pu etre libere pendant qu on attendait le verrou.
        if salons.get(code_salon) is not salon:
            salons[code_salon] = salon
        joueur = await arrivee(code_salon, salon, ws, nom_joueur)
    if joueur is None:
        await fermer(ws, 4005)
        return

    try:
        # Boucle principale : on ecoute les messages de ce joueur
        debit = Debit(DEBIT_MESSAGES, RAFALE_MESSAGES)
        async for message_brut in ws.iter_text():
            if len(message_brut) > TAILLE_MAX_MESSAGE:
                journal.warning("Message trop gros (%d) dans le salon %s", len(message_brut), code_salon)
                await fermer(ws, 1009)
                break
            if not debit.accepter():
                if debit.refus_de_suite >= REFUS_AVANT_COUPURE:
                    journal.warning("Inondation de messages dans le salon %s", code_salon)
                    await fermer(ws, 1008)
                    break
                continue
            message = lire_message(message_brut)
            if message is None:
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

    # La page d ou il vient (?page=kinito). S il arrive d un AUTRE jeu que
    # celui de la table et que plus personne n est actif dans cette partie
    # (tout le monde l a quittee), elle est abandonnee : on la clot, la table
    # repasse en salle d attente pour le nouveau jeu. Sans ca, il etait remis
    # a sa place dans l ancien jeu et renvoye vers son ancienne page.
    page = ws.query_params.get("page")
    if page in JEUX and salon.get("etat") is not None and salon.get("jeu") != page:
        actifs = [j for j in salon["joueurs"] if not j.get("parti") and not meme_prenom(j["nom"], nom_joueur)]
        if not actifs:
            await terminer_jeu(code_salon, "abandonne")
    if page in JEUX and salon.get("etat") is None:
        salon["choix"] = page          # le jeu qui attend en salle d attente (affiche sur l accueil)

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
        if len(salon["joueurs"]) >= PLACES_MAX:
            # Table pleine : refus annonce avec le message d erreur habituel.
            try:
                await asyncio.wait_for(ws.send_text(json.dumps({
                    "type": "erreur",
                    "message": f"Table complète ({PLACES_MAX} places). Ouvre une autre table.",
                })), 1)
            except Exception:
                pass
            return None
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
    liberer_autres_tables(code_salon, joueur["nom"])

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


def liberer_autres_tables(code_salon, nom):
    """
    Un prenom n est qu a une table de la soiree a la fois : en s asseyant ici,
    il quitte l autre (sa place y est liberee comme pour un depart). Une
    connexion encore ouverte la-bas (autre telephone) est prevenue qu elle a
    ete remplacee, comme pour un onglet en double. Le depart est traite a
    part, sous le verrou de l AUTRE table.
    """
    for code in tables_de(soiree_de(code_salon)):
        if code == code_salon:
            continue
        autre = salons.get(code)
        for j in list(autre["joueurs"]) if autre else []:
            if meme_prenom(j["nom"], nom) and not j.get("parti"):
                asyncio.ensure_future(_quitter_table(code, autre, j))


async def _quitter_table(code, salon, joueur):
    ws = joueur.get("ws")
    if ws is not None:
        try:
            await asyncio.wait_for(ws.send_text('{"type":"remplace"}'), 1)
        except Exception:
            pass
    async with verrou(salon):
        if salons.get(code) is salon:
            await depart(code, joueur, ws)
    if ws is not None:
        asyncio.ensure_future(fermer(ws, 4002))


def arrivee_en_partie(salon, joueur):
    """Un nouveau venu en pleine partie : on lui fait une place si le jeu le permet."""
    # Arrivee en pleine partie : on agrandit le plateau pour que le
    # nouveau venu entre vraiment dans le tour, au lieu de rester
    # spectateur avec un index hors bornes. Ajouter en fin de liste ne
    # decale l index de personne.
    jeu = JEUX.get(salon.get("jeu"))
    if salon.get("etat") is not None and jeu is not None and jeu.arrivee is not None:
        jeu.arrivee(salon, joueur)


def _kinito_arrivee(salon, joueur):
    etat_courant = salon["etat"]
    etat_courant["nb_joueurs"] = len(salon["joueurs"])
    etat_courant["partis"].append(False)
    etat_courant["message"] = joueur["nom"] + " rejoint la partie."
    kinito_reprendre(etat_courant)


def _buckshot_arrivee(salon, joueur):
    # Buckshot n a pas de fin de partie : un arrivant laisse spectateur
    # le resterait pour toujours. On lui fait donc une place, verre vide
    # (il sera le chargeur de la prochaine manche) et sans objet jusqu a
    # la prochaine distribution. Au-dela de 8 presents, il regarde.
    etat_courant = salon["etat"]
    presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
    if presents <= BT_JOUEURS_MAX and joueur["index"] == etat_courant["nb_joueurs"]:
        etat_courant["nb_joueurs"] += 1
        etat_courant["gorgees"].append(0)
        etat_courant["objets"].append(None)
        etat_courant["menottes"].append(False)
        etat_courant["dernier_reel"].append(0)
        etat_courant["message"] = joueur["nom"] + " rejoint la partie."


def _picolopoly_arrivee(salon, joueur):
    # Picolopoly : une partie dure une heure, on fait une place a
    # l arrivant (argent de depart, case Depart) tant qu on reste a 8.
    etat_courant = salon["etat"]
    presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
    if (presents <= PP_JOUEURS_MAX and etat_courant["phase"] != "fin"
            and joueur["index"] == etat_courant["nb_joueurs"]):
        pp_ajouter_joueur(etat_courant, joueur["nom"])


def _casino_arrivee(salon, joueur):
    # Casino : on s assoit a tout moment avec ses jetons de depart, et on
    # joue des le coup suivant.
    etat_courant = salon["etat"]
    presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
    if presents <= CS_JOUEURS_MAX and joueur["index"] == etat_courant["nb_joueurs"]:
        cs_ajouter_joueur(etat_courant, joueur["nom"])


async def revenir(code_salon, salon, joueur, annoncer=True):
    """Le joueur reprend sa place (apres une coupure ou un silence)."""
    etait_parti = joueur.get("parti")
    joueur["parti"] = False
    joueur["vu"] = time.time()
    annuler_liberation(salon)
    etat = salon.get("etat")
    if not etait_parti or etat is None:
        return
    jeu = JEUX.get(salon.get("jeu"))
    if jeu is not None and jeu.joueur_revenu is not None:
        jeu.joueur_revenu(code_salon, salon, joueur["index"])
    if salon.get("jeu") == "kinito":
        # Le retour a pu relancer une partie en attente (kinito_reprendre) :
        # toute la table doit le voir, pas seulement le revenant. Sinon les
        # autres restaient sur « Il ne reste plus assez de joueurs », le
        # revenant note parti, et personne n avait la main.
        await diffuser(code_salon, construire_etat_public(code_salon))
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
    jeu = JEUX.get(salon.get("jeu"))
    if jeu is not None:
        await jeu.instantane(code_salon, joueur, complet, nouveau)



async def _kinito_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
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


async def _beerbattle_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
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


async def _buckshot_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
    if complet:
        await envoyer(joueur, {"type": "bt_demarree", "joueurs": noms})
    await envoyer(joueur, {"type": "bt_mon_index", "index": idx})
    # A tout le monde : un arrivant agrandit la table pour les autres aussi.
    if nouveau:
        await diffuser(code_salon, bt_etat_public(salon))
    else:
        await envoyer(joueur, bt_etat_public(salon))


async def _picolopoly_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
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


async def _menteur_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
    # Sa main est secrete : elle ne part que vers lui. Un arrivant en
    # pleine partie regarde (pas de main) jusqu a la partie suivante.
    if complet:
        await envoyer(joueur, {"type": "mt_demarree", "joueurs": noms})
    await envoyer(joueur, mt_etat_public(etat, time.time()))
    main = mt_main(etat, idx)
    if main is not None:
        await envoyer(joueur, main)


async def _undercover_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    noms = [j["nom"] for j in salon["joueurs"]]
    # Son mot est secret : il ne part que vers lui. Un arrivant en pleine
    # partie regarde (pas de mot) jusqu a la partie suivante.
    if complet:
        await envoyer(joueur, {"type": "uc_demarree", "joueurs": noms})
    await envoyer(joueur, uc_etat_public(etat, time.time()))
    mot = uc_mot(etat, joueur["index"])
    if mot is not None:
        await envoyer(joueur, mot)


async def _telephone_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    noms = [j["nom"] for j in salon["joueurs"]]
    # Sa tache (la seule contribution precedente) ne part que vers lui ; un
    # arrivant en pleine partie regarde jusqu a la partie suivante.
    if complet:
        await envoyer(joueur, {"type": "td_demarree", "joueurs": noms})
    await envoyer(joueur, td_etat_public(etat, time.time()))
    tache = td_tache(etat, joueur["index"], time.time())
    if tache is not None:
        await envoyer(joueur, tache)
    galerie = td_galerie(etat)
    if galerie is not None:
        await envoyer(joueur, galerie)


async def _conquete_instantane(code_salon, joueur, complet, nouveau):
    # Tout est public : la carte et le catalogue (sw_demarree), puis l etat.
    # La page ne reconstruit son plateau que si la carte ou les joueurs changent.
    etat = salons[code_salon]["etat"]
    await envoyer(joueur, sw_catalogue(etat))
    await envoyer(joueur, sw_etat_public(etat, time.time()))


async def _justepinte_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    # Sa reponse reste secrete jusqu a la revelation : elle ne revient qu a
    # lui (page rechargee). Un arrivant en pleine partie regarde.
    if complet:
        await envoyer(joueur, {"type": "jp_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer(joueur, jp_etat_public(etat, time.time()))
    rep = jp_reponse(etat, joueur["index"])
    if rep is not None:
        await envoyer(joueur, rep)


async def _quidenous_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    # Son vote reste secret jusqu a la revelation : il ne revient qu a lui
    # (page rechargee). Un arrivant en pleine partie regarde.
    if complet:
        await envoyer(joueur, {"type": "qn_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer(joueur, qn_etat_public(etat, time.time()))
    vote = qn_vote(etat, joueur["index"])
    if vote is not None:
        await envoyer(joueur, vote)


async def _tournee_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    # Son score de mini-jeu reste secret jusqu aux resultats : il ne revient
    # qu a lui (page rechargee). Un arrivant en pleine partie regarde.
    if complet:
        await envoyer(joueur, {"type": "tr_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer(joueur, tr_etat_public(etat, time.time()))
    sc = tr_score(etat, joueur["index"])
    if sc is not None:
        await envoyer(joueur, sc)


async def _casino_instantane(code_salon, joueur, complet, nouveau):
    salon = salons[code_salon]
    etat = salon["etat"]
    idx = joueur["index"]
    noms = [j["nom"] for j in salon["joueurs"]]
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
        jeu = JEUX.get(salon.get("jeu"))
        if jeu is not None and jeu.joueur_parti is not None:
            await jeu.joueur_parti(code_salon, index_parti)

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
            arreter_minuteurs(salon)
            # Les couches de la soiree (l Enquete) survivent au salon : elles
            # restent attachees au code, qui ne sera pas redonne.
            salons.pop(code_salon, None)

    salon["liberation"] = asyncio.ensure_future(liberer())


def demarrer_veilleur():
    global _veilleur
    if _veilleur is None or _veilleur.done():
        _veilleur = asyncio.ensure_future(veilleur())


async def veilleur():
    """Repere les joueurs muets (telephone en veille) et les traite comme partis."""
    dernier_nettoyage = time.time()
    while True:
        await asyncio.sleep(1)
        if time.time() - dernier_nettoyage >= NETTOYAGE_TOUTES_LES:
            dernier_nettoyage = time.time()
            try:
                nettoyer()
            except Exception:
                journal.exception("Erreur du nettoyage")
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


def soiree_ouverte_ailleurs(soiree, maintenant):
    """Quelqu un a la soiree ouverte sans etre assis : Enquete, ou accueil recent."""
    if abonnes_soirees.get(soiree):
        return True
    return any(maintenant - v["vu"] <= 2 * PRESENCE_ACCUEIL
               for v in presences_accueil.get(soiree, {}).values())


def nettoyer(maintenant=None):
    """
    Passage periodique (veilleur) : retire ce qui ne se liberait jamais.
    - salons crees mais jamais rejoints (aucune liberation programmee) ;
    - verrous de soiree et presences de l accueil des soirees disparues ;
    - compteurs des limites par adresse devenus inutiles.
    """
    maintenant = time.time() if maintenant is None else maintenant
    for code, salon in list(salons.items()):
        liberation = salon.get("liberation")
        if (not salon["joueurs"] and (liberation is None or liberation.done())
                and maintenant - salon.get("cree", maintenant) > DELAI_SALON_ABANDONNE
                and not soiree_ouverte_ailleurs(soiree_de(code), maintenant)
                and not verrou(salon).locked()):
            arreter_minuteurs(salon)
            salons.pop(code, None)
    for soiree, vus in list(presences_accueil.items()):
        for k in [k for k, v in vus.items() if maintenant - v["vu"] > 4 * PRESENCE_ACCUEIL]:
            del vus[k]
        if not vus:
            del presences_accueil[soiree]
    for soiree, v in list(_verrous_soirees.items()):
        if not v.locked() and not soiree_existe(soiree) and not abonnes_soirees.get(soiree):
            del _verrous_soirees[soiree]
    for limite in (ouvertures_ip, requetes_ip, creations_ip, rates_ip):
        limite.purger()


# =============================================================
# TRAITEMENT DES MESSAGES
# Chaque action d un joueur arrive ici.
# =============================================================

async def traiter_message(code_salon, joueur, message):
    """
    Aiguille chaque message vers la bonne fonction de traitement.
    Le "type" du message indique quelle action le joueur veut faire :
    - le message de demarrage d un jeu passe par lancer_jeu (regle unique
      de lancement et de changement de jeu) ;
    - une action d un jeu ne compte que si CE jeu est en cours (sinon etat
      vaut None ou appartient a un autre jeu, et l action plantait) ;
    - "<prefixe>_..." d une couche (l Enquete) va a la couche, quel que soit
      le jeu en cours ;
    - "retour_salon" ramene tout le monde en salle d attente.
    """
    type_msg = message.get("type")
    salon = salons[code_salon]
    if not isinstance(type_msg, str):
        return
    if type_msg == "retour_salon":
        if salon.get("etat") is not None:
            await terminer_jeu(code_salon, "retour")
            await diffuser(code_salon, {"type": "salon_retour",
                                        "joueurs": [j["nom"] for j in salon["joueurs"]]})
        return
    if type_msg.split("_", 1)[0] in TYPES_COUCHES:
        await couche_action(soiree_de(code_salon), joueur["nom"], message)
        return
    for nom, jeu in JEUX.items():
        if type_msg == jeu.demarrage:
            await lancer_jeu(code_salon, nom, message, joueur)
            return
        if jeu.possede(type_msg):
            if salon.get("etat") is not None and salon.get("jeu") == nom:
                await jeu.traiter(code_salon, joueur, message)
            return


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
        etat["message"] = "51 : tout le monde boit 1 gorgée, on repart de zéro !"
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
            "message": "Ce score ne peut pas être annoncé.",
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
                             f"{nom_annonceur} a été démasqué !", gorgees, score_reel, score_annonce)
        else:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} a accusé à tort", gorgees, score_reel, score_annonce)

    elif choix == "moins":
        # Le reacteur doit avoir cite le bon score precedent
        if etat["premiere_annonce"]:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} a accusé alors qu'il n'y avait pas d'annonce", gorgees, None, score_annonce)
            return

        if score_cite != annonce_prec:
            gorgees = penalite(score_annonce, False)
            await fin_manche(code_salon, index_reacteur, False,
                             f"{nom_reacteur} n'a pas cité le bon score précédent", gorgees, None, score_annonce)
        else:
            annonce_trop_basse = rang(score_annonce) < rang(annonce_prec)
            if annonce_trop_basse:
                gorgees = penalite(annonce_prec, True)
                await fin_manche(code_salon, etat["joueur_courant"], True,
                                 f"{nom_annonceur} a annoncé moins que le précédent", gorgees, None, annonce_prec)
            else:
                gorgees = penalite(score_annonce, False)
                await fin_manche(code_salon, index_reacteur, False,
                                 f"{nom_reacteur} a accusé à tort", gorgees, None, score_annonce)


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
    await signaler(code_salon, "kinito", "manche", perdants=[nom_perdant],
                   details={"gorgees": gorgees, "menteur": menteur})


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
BB_TAILLE = 6           # plateau 6x6 jusqu a 5 joueurs...
BB_TAILLE_GRANDE = 8    # ... 8x8 avec deux paquets de 6 a 8 joueurs
BB_JOUEURS_MAX = 8
BB_VERRE_MAX = 20       # gorgees pour etre elimine


def bb_format(nb_joueurs):
    """
    (taille du plateau, nombre de paquets) selon la table. Un paquet de 54
    cartes couvre un plateau 6x6 (36) et 5 mains ; au-dela on passe a un
    plateau 8x8 (64) et deux paquets (108 cartes pour 64 + 8 mains de 4).
    Les regles ne changent pas, seule la taille grandit.
    """
    return (BB_TAILLE, 1) if nb_joueurs <= 5 else (BB_TAILLE_GRANDE, 2)


def bb_taille(etat):
    return len(etat["plateau"])
 
 
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
    taille, nb_paquets = bb_format(nb_joueurs)
    paquet = []
    for _ in range(nb_paquets):
        paquet += bb_creer_paquet()
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
 
    # Remplissage du plateau (6x6 ou 8x8)
    plateau = []
    for ligne in range(taille):
        rangee = []
        for colonne in range(taille):
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
        "taille": bb_taille(etat),
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
    # Au-dela de 8, meme deux paquets sur un plateau 8x8 ne suffisent plus
    # (bb_format) : on refuse proprement.
    if nb > BB_JOUEURS_MAX:
        await diffuser(code_salon, {
            "type": "erreur",
            "message": f"BeerBattle se joue à {BB_JOUEURS_MAX} au maximum ({nb} connectés).",
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
 
    if not bb_case_valide(ligne, colonne, bb_taille(etat)):
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
    if etat["action_faite"] or not bb_case_valide(ligne, colonne, bb_taille(etat)):
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
 

def bb_carte_main(etat, idx, index_carte, types=None):
    """
    La carte index_carte de la main du joueur idx, si l index est valide et
    la carte d un des types demandes (types None : n importe laquelle).
    Sinon None : l action est refusee (avant, un index absent ou hors bornes
    levait une exception, et n importe quelle carte passait pour une Dame).
    """
    main = etat["mains"][idx]
    if isinstance(index_carte, bool) or not isinstance(index_carte, int) \
            or not 0 <= index_carte < len(main):
        return None
    carte = main[index_carte]
    return carte if types is None or carte["type"] in types else None


def bb_arme_a_portee(etat, idx, cible):
    """La premiere arme posee du joueur qui atteint la cible (Roi : 6, Valet : 2)."""
    dist = bb_distance(etat["positions"][idx], etat["positions"][cible])
    for arme in etat["armes"][idx]:
        portee = 6 if arme["type"] == "roi" else 2
        if dist <= portee:
            return arme
    return None


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
    if not bb_case_valide(ligne, colonne, bb_taille(etat)):
        return
    if not isinstance(cible, int) or not 0 <= cible < etat["nb_joueurs"]:
        return
    if bb_carte_main(etat, idx, index_carte, ("as",)) is None:
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
        carte = bb_carte_main(etat, idx, index_carte, ("gorgee",))
        if carte is None:
            return
        bb_appliquer_gorgees(etat, cible, carte["valeur"])
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} attaque ({carte['valeur']} gorgées)"
 
    elif type_attaque == "arme":
        # index_carte = carte gorgee ; le serveur verifie la portee via l arme la plus adaptee
        carte = bb_carte_main(etat, idx, index_carte, ("gorgee",))
        if carte is None:
            return
        # On determine quelle arme peut atteindre la cible
        arme_utilisee = bb_arme_a_portee(etat, idx, cible)
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
        if bb_carte_main(etat, idx, index_carte, ("as",)) is None:
            return
        bb_mi_verre(etat, cible)
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} frappe à l'As (mi-verre)"
 
    elif type_attaque == "joker":
        # Le Joker se tire avec une arme posee, cible a portee (regle et page).
        if bb_carte_main(etat, idx, index_carte, ("joker",)) is None:
            return
        if bb_arme_a_portee(etat, idx, cible) is None:
            return
        bb_mi_verre(etat, cible)
        main.pop(index_carte)
        etat["message"] = f"{salon['joueurs'][idx]['nom']} tire au Joker (mi-verre)"

    else:
        return      # type d attaque inconnu : rien ne se passe

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
    carte = bb_carte_main(etat, idx, index_carte, ("roi", "valet"))
    if carte is None:
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
    if bb_carte_main(etat, idx, index_carte, ("dame",)) is None:
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
    elif decision == "echanger" and carte_sol is not None:
        if bb_carte_main(etat, idx, index_echange) is None:
            return      # carte a echanger invalide : le tour n est pas fini
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


def bb_case_valide(ligne, colonne, taille=BB_TAILLE):
    return (isinstance(ligne, int) and isinstance(colonne, int)
            and 0 <= ligne < taille and 0 <= colonne < taille)


def bb_avancer_placement(salon):
    """
    Au placement, un absent ne choisira jamais sa case : on le place d office
    sur une case libre au hasard, pour que la partie puisse commencer.
    """
    etat = salon["etat"]
    absents = bb_absents(salon)
    while etat["joueur_en_placement"] < etat["nb_joueurs"] and etat["joueur_en_placement"] in absents:
        i = etat["joueur_en_placement"]
        t = bb_taille(etat)
        libres = [(l, c) for l in range(t) for c in range(t)
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
    bb_programmer_seul(code_salon)
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
        await bb_declarer_vainqueur(code_salon, salon, survivants[0] if survivants else None)
        return True
    return False


async def bb_declarer_vainqueur(code_salon, salon, gagnant):
    etat = salon["etat"]
    annuler_bb_seul(salon)
    nom = salon["joueurs"][gagnant]["nom"] if gagnant is not None else "Personne"
    etat["phase"] = "fini"
    etat["gagnant_nom"] = nom
    await diffuser(code_salon, {"type": "bb_victoire", "gagnant": nom})
    await signaler(code_salon, "beerbattle", "victoire",
                   gagnants=[nom] if gagnant is not None else [])


# --- Seul present ---
# La victoire ne compte que les elimines : si tous les autres joueurs encore
# en lice quittent la table, le dernier present jouait seul sans fin. S il le
# reste BB_DELAI_SEUL secondes, il gagne. Pas tout de suite : une simple
# coupure de reseau ferait perdre les autres.
BB_DELAI_SEUL = 120


def bb_seul_present(salon):
    """L index du seul joueur en lice present, quand les autres en lice sont absents."""
    etat = salon["etat"]
    absents = bb_absents(salon)
    en_lice = [i for i in range(etat["nb_joueurs"]) if not etat["elimine"][i]]
    presents = [i for i in en_lice if i not in absents]
    return presents[0] if len(presents) == 1 and len(en_lice) >= 2 else None


def annuler_bb_seul(salon):
    t = salon.get("bb_seul")
    if t is not None and not t.done() and t is not asyncio.current_task():
        t.cancel()
    salon["bb_seul"] = None


def bb_programmer_seul(code_salon):
    """(Re)arme le delai si un seul joueur en lice reste present, sinon l annule."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "beerbattle" or not salon.get("etat"):
        return
    annuler_bb_seul(salon)
    if salon["etat"]["phase"] != "fini" and bb_seul_present(salon) is not None:
        salon["bb_seul"] = asyncio.ensure_future(bb_minuteur_seul(code_salon, salon, salon["etat"]))


async def bb_minuteur_seul(code_salon, salon, etat):
    await asyncio.sleep(BB_DELAI_SEUL)
    if salons.get(code_salon) is not salon:
        return
    async with verrou(salon):
        if salon.get("bb_seul") is not asyncio.current_task():
            return
        salon["bb_seul"] = None
        if salon.get("etat") is not etat or salon.get("jeu") != "beerbattle" or etat["phase"] == "fini":
            return
        gagnant = bb_seul_present(salon)
        if gagnant is None:
            return
        etat["message"] = salon["joueurs"][gagnant]["nom"] + " est le dernier à table : il gagne."
        await bb_declarer_vainqueur(code_salon, salon, gagnant)
        await diffuser(code_salon, bb_etat_public(salon))
 



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
    {"id": "telephone", "nom": "Téléphone", "icone": "\U0001F4F1"},
    {"id": "inverseur", "nom": "Inverseur", "icone": "\U0001F504"},
    {"id": "biere",     "nom": "Bière",     "icone": "\U0001F37A"},
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
        await diffuser(code_salon, {"type": "erreur", "message": "Buckshot se joue à 8 joueurs maximum."})
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
    etat["message"] = "Nouvelle manche : " + str(reel) + " réelle(s) sur " + str(total) + "."
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
        etat["message"] = joueur["nom"] + " utilise le Téléphone."
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
        etat["message"] = joueur["nom"] + " défausse la carte du dessus."
        if not etat["pile"]:
            await bt_manche_fin(code_salon, salons, diffuser)
            return
        await diffuser(code_salon, bt_etat_public(salon))

    elif oid == "scie":
        o["utilise"] = True
        etat["scie_active"] = True
        etat["message"] = joueur["nom"] + " arme la Scie : prochaine réelle doublée."
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
    etat["message"] = "Manche terminée."
    await diffuser(code_salon, {
        "type": "bt_manche_fin",
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "gorgees": etat["gorgees"],
        "chargeur": etat["chargeur"],
    })
    await diffuser(code_salon, bt_etat_public(salon))
    # Les gorgees de Buckshot se cumulent : les couches recoivent celles de
    # CETTE manche, pour les presents.
    avant = etat.get("gorgees_signalees") or []
    manche = {}
    for j in salon["joueurs"]:
        i = j["index"]
        if not j.get("parti") and i < etat["nb_joueurs"]:
            manche[j["nom"]] = etat["gorgees"][i] - (avant[i] if i < len(avant) else 0)
    etat["gorgees_signalees"] = list(etat["gorgees"])
    await signaler(code_salon, "buckshot", "manche", details={"gorgees": manche})


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
        etat["message"] = nom + " est parti : " + salon["joueurs"][etat["chargeur"]]["nom"] + " charge à sa place."
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
        await diffuser(code_salon, {"type": "erreur", "message": "Picopoly se joue à 8 joueurs maximum."})
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
    fin = None
    for e in evenements:
        if e["msg"].get("type") == "pp_fin":
            fin = e["msg"]
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, pp_etat_public(salon["etat"], time.time()))
    pp_programmer(code_salon)
    if fin is not None:
        noms = salon["etat"]["noms"]
        nom = lambda i: noms[i] if i is not None and i < len(noms) else None
        await signaler(code_salon, "picolopoly", "victoire",
                       gagnants=[n for n in [nom(fin.get("gagnant"))] if n],
                       details={"raison": fin.get("raison"),
                                "classement": [nom(c["joueur"]) for c in fin.get("classement", [])]})


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
        await diffuser(code_salon, {"type": "erreur", "message": "Cal 12 demande au moins 2 joueurs."})
        return
    if nb > MT_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": "Cal 12 se joue à 8 joueurs maximum."})
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
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        g = etat.get("gagnant")
        noms = [j["nom"] for j in salon["joueurs"]]
        await signaler(code_salon, "menteur", "victoire",
                       gagnants=[noms[g]] if g is not None and g < len(noms) else [])


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
# UNDERCOVER (deduction sociale, indices a l oral)
# Les regles sont dans undercover.py. Ici : le reseau, le mot secret de
# chacun, et le minuteur qui fait avancer la parole, le vote et les
# revelations. Les points et les paires deja jouees passent d une partie a
# la suivante dans le meme salon.
# =============================================================

async def uc_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "undercover" and etat.get("phase") == "fin"):
        return
    precedent = etat if salon.get("jeu") == "undercover" else salon.get("uc_memoire")
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < UC_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur", "message": "Undercover demande au moins 3 joueurs."})
        return
    if nb > UC_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": f"Undercover se joue à {UC_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = uc_initialiser(
        [j["nom"] for j in salon["joueurs"]], time.time(),
        message.get("famille"), message.get("undercover"), message.get("white"),
        points=(precedent or {}).get("points"), deja=(precedent or {}).get("deja"))
    salon["jeu"] = "undercover"
    salon["uc_memoire"] = {"points": salon["etat"]["points"], "deja": salon["etat"]["deja"]}
    await diffuser(code_salon, {"type": "uc_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await uc_publier(code_salon, [])


async def uc_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous et son mot a chacun."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "undercover" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, uc_etat_public(etat, time.time()))
    for joueur in salon["joueurs"]:
        if not joueur.get("parti"):
            mot = uc_mot(etat, joueur["index"])
            if mot is not None:
                await envoyer(joueur, mot)
    uc_programmer(code_salon)
    salon["uc_memoire"] = {"points": etat["points"], "deja": etat["deja"]}
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        noms = [j["nom"] for j in salon["joueurs"]]
        res = etat.get("resultat") or {}
        gagnants = [noms[g] for g in res.get("gagnants", []) if g < len(noms)]
        doses = {noms[b["joueur"]]: b["gorgees"] for b in res.get("gorgees", []) if b["joueur"] < len(noms)}
        await signaler(code_salon, "undercover", "victoire", gagnants=gagnants,
                       perdants=[n for n in noms if n not in gagnants],
                       details={"vainqueur": res.get("vainqueur"), "gorgees": doses})


def uc_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("uc_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = uc_prochaine_echeance(etat) if etat and salon.get("jeu") == "undercover" else None
    salon["uc_minuteur"] = None if echeance is None else asyncio.create_task(
        uc_minuteur(code_salon, salon, echeance))


async def uc_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "undercover":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("uc_minuteur") is not moi:
            return
        salon["uc_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = uc_expirer(etat, time.time())
        if etat["version"] != version:
            await uc_publier(code_salon, evenements)
        else:
            uc_programmer(code_salon)


async def uc_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "undercover" or etat is None:
        return
    version = etat["version"]
    evenements = uc_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await uc_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# LE JUSTE PINTE (estimation, prefixe jp_)
# Les regles sont dans justepinte.py (chiffres : justepinte_config.json,
# questions : justepinte_questions.py). Ici : le reseau et le minuteur qui
# revele la reponse et enchaine les manches. Les questions deja posees
# passent d une partie a la suivante dans le meme salon.
# =============================================================

async def jp_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "justepinte" and etat.get("phase") == "fin"):
        return
    precedent = etat if salon.get("jeu") == "justepinte" else salon.get("jp_memoire")
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < JP_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Le Juste Pinte demande au moins {JP_JOUEURS_MIN} joueurs."})
        return
    if nb > JP_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Le Juste Pinte se joue à {JP_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    categories = message.get("categories")
    salon["etat"] = jp_initialiser(
        [j["nom"] for j in salon["joueurs"]], time.time(),
        manches=message.get("manches"),
        categories=categories if isinstance(categories, list) else None,
        deja=(precedent or {}).get("deja"))
    salon["jeu"] = "justepinte"
    salon["jp_memoire"] = {"deja": salon["etat"]["deja"]}
    await diffuser(code_salon, {"type": "jp_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await jp_publier(code_salon, [])


async def jp_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "justepinte" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, jp_etat_public(etat, time.time()))
    jp_programmer(code_salon)
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        noms = [j["nom"] for j in salon["joueurs"]]
        final = etat.get("final") or {}
        gagnants = [noms[g] for g in final.get("gagnants", []) if g < len(noms)]
        # Les gorgees de la fin seulement (celles des manches ont deja ete bues).
        doses = {noms[d["joueur"]]: d["boit"] for d in final.get("derniers", []) if d["joueur"] < len(noms)}
        await signaler(code_salon, "justepinte", "victoire", gagnants=gagnants,
                       perdants=[n for n in noms if n not in gagnants],
                       details={"points": dict(zip(noms, etat["points"])), "gorgees": doses})


def jp_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("jp_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = jp_prochaine_echeance(etat) if etat and salon.get("jeu") == "justepinte" else None
    salon["jp_minuteur"] = None if echeance is None else asyncio.create_task(
        jp_minuteur(code_salon, salon, echeance))


async def jp_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "justepinte":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("jp_minuteur") is not moi:
            return
        salon["jp_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = jp_expirer(etat, time.time())
        if etat["version"] != version:
            await jp_publier(code_salon, evenements)
        else:
            jp_programmer(code_salon)


async def jp_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "justepinte" or etat is None:
        return
    version = etat["version"]
    evenements = jp_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await jp_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# QUI DE NOUS ? (vote social, prefixe qn_)
# Les regles sont dans quidenous.py (chiffres : quidenous_config.json,
# cartes : quidenous_cartes.py). Ici : le reseau et le minuteur qui revele
# les votes et enchaine les manches. Les cartes deja tirees passent d une
# partie a la suivante dans le meme salon.
# =============================================================

async def qn_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "quidenous" and etat.get("phase") == "fin"):
        return
    precedent = etat if salon.get("jeu") == "quidenous" else salon.get("qn_memoire")
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < QN_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Qui de nous ? demande au moins {QN_JOUEURS_MIN} joueurs."})
        return
    if nb > QN_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Qui de nous ? se joue à {QN_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = qn_initialiser(
        [j["nom"] for j in salon["joueurs"]], time.time(),
        manches=message.get("manches"),
        paquet=message.get("paquet"),
        anonyme=message.get("anonyme") is not False,
        deja=(precedent or {}).get("deja"))
    salon["jeu"] = "quidenous"
    salon["qn_memoire"] = {"deja": salon["etat"]["deja"]}
    await diffuser(code_salon, {"type": "qn_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await qn_publier(code_salon, [])


async def qn_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "quidenous" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, qn_etat_public(etat, time.time()))
    qn_programmer(code_salon)
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        noms = [j["nom"] for j in salon["joueurs"]]
        final = etat.get("final") or {}
        gagnants = [noms[g] for g in final.get("gagnants", []) if g < len(noms)]
        # Les gorgees de la fin seulement (celles des manches ont deja ete bues).
        doses = {noms[d["joueur"]]: d["boit"] for d in final.get("derniers", []) if d["joueur"] < len(noms)}
        await signaler(code_salon, "quidenous", "victoire", gagnants=gagnants,
                       perdants=[n for n in noms if n not in gagnants],
                       details={"points": dict(zip(noms, etat["points"])), "gorgees": doses,
                                "titre": [noms[t] for t in final.get("titre", []) if t < len(noms)]})


def qn_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("qn_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = qn_prochaine_echeance(etat) if etat and salon.get("jeu") == "quidenous" else None
    salon["qn_minuteur"] = None if echeance is None else asyncio.create_task(
        qn_minuteur(code_salon, salon, echeance))


async def qn_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "quidenous":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("qn_minuteur") is not moi:
            return
        salon["qn_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = qn_expirer(etat, time.time())
        if etat["version"] != version:
            await qn_publier(code_salon, evenements)
        else:
            qn_programmer(code_salon)


async def qn_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "quidenous" or etat is None:
        return
    version = etat["version"]
    evenements = qn_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await qn_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# LA TOURNEE (party game plateau + mini-jeux, prefixe tr_)
# Les regles sont dans tournee.py (chiffres : tournee_config.json, mini-jeux :
# tournee_minijeux.json). Ici : le reseau et le minuteur qui enchaine des,
# deplacements, mini-jeu et resultats. Les mini-jeux se jouent sur les
# telephones : le serveur ne recoit que les scores.
# =============================================================

async def tr_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "tournee" and etat.get("phase") == "fin"):
        return
    precedent = etat if salon.get("jeu") == "tournee" else salon.get("tr_memoire")
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < TR_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"La Tournée demande au moins {TR_JOUEURS_MIN} joueurs."})
        return
    if nb > TR_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"La Tournée se joue à {TR_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    salon["etat"] = tr_initialiser(
        [j["nom"] for j in salon["joueurs"]], time.time(),
        tours=message.get("tours"),
        deja=precedent)
    salon["jeu"] = "tournee"
    salon["tr_memoire"] = {"dernier_minijeu": salon["etat"]["dernier_minijeu"]}
    await diffuser(code_salon, {"type": "tr_demarree", "joueurs": [j["nom"] for j in salon["joueurs"]]})
    await envoyer_indices(code_salon)
    await tr_publier(code_salon, [])


async def tr_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "tournee" or not salon.get("etat"):
        return
    etat = salon["etat"]
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])
    await diffuser(code_salon, tr_etat_public(etat, time.time()))
    tr_programmer(code_salon)
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        noms = [j["nom"] for j in salon["joueurs"]]
        final = etat.get("final") or {}
        gagnants = [noms[g] for g in final.get("gagnants", []) if g < len(noms)]
        # Les gorgees de la fin seulement (celles des manches ont deja ete bues).
        doses = {noms[d["joueur"]]: d["boit"] for d in final.get("derniers", []) if d["joueur"] < len(noms)}
        await signaler(code_salon, "tournee", "victoire", gagnants=gagnants,
                       perdants=[n for n in noms if n not in gagnants],
                       details={"capsules": dict(zip(noms, etat["capsules"])), "pieces": dict(zip(noms, etat["pieces"])),
                                "gorgees": doses})


def tr_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("tr_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = tr_prochaine_echeance(etat) if etat and salon.get("jeu") == "tournee" else None
    salon["tr_minuteur"] = None if echeance is None else asyncio.create_task(
        tr_minuteur(code_salon, salon, echeance))


async def tr_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "tournee":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("tr_minuteur") is not moi:
            return
        salon["tr_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = tr_expirer(etat, time.time())
        if etat["version"] != version:
            await tr_publier(code_salon, evenements)
        else:
            tr_programmer(code_salon)


async def tr_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "tournee" or etat is None:
        return
    version = etat["version"]
    evenements = tr_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await tr_publier(code_salon, evenements)
    else:
        for e in evenements:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


# =============================================================
# TELEPHONE DESSINE (on ecrit, on dessine, on devine)
# Les regles sont dans telephone.py (chiffres : telephone_config.json).
# Ici : le reseau, la tache privee de chacun (la SEULE contribution
# precedente de son album), et le minuteur des etapes et de la revelation.
# Les phrases de la banque deja sorties passent d une partie a la suivante.
# =============================================================

async def td_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "telephone" and etat.get("phase") == "fin"):
        return
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < TD_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Le Gribouillophone demande au moins {TD_JOUEURS_MIN} joueurs."})
        return
    if nb > TD_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur",
                                    "message": f"Le Gribouillophone se joue à {TD_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    noms = [j["nom"] for j in salon["joueurs"]]
    par = message.get("_par")
    hote = next((i for i, n in enumerate(noms) if par is not None and meme_prenom(n, par)), 0)
    salon["etat"] = td_initialiser(
        noms, time.time(), hote=hote, vitesse=message.get("vitesse"), source=message.get("source"),
        etapes=message.get("etapes"), mode=message.get("mode"), deja=salon.get("td_deja"))
    salon["jeu"] = "telephone"
    salon["td_tache_envoyee"] = None
    salon["td_phase"] = None
    salon["td_deja"] = salon["etat"]["deja"]
    await diffuser(code_salon, {"type": "td_demarree", "joueurs": noms})
    await envoyer_indices(code_salon)
    await td_publier(code_salon, [])


async def td_envoyer_evenements(salon, code_salon, evenements):
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


async def td_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous et sa tache a chacun."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "telephone" or not salon.get("etat"):
        return
    etat = salon["etat"]
    phase_avant = salon.get("td_phase")
    await td_envoyer_evenements(salon, code_salon, evenements)
    await diffuser(code_salon, td_etat_public(etat, time.time()))
    # La tache ne change qu avec l etape : inutile de renvoyer un dessin a
    # chaque contribution des autres.
    cle = (etat["phase"], etat["etape"])
    if salon.get("td_tache_envoyee") != cle:
        salon["td_tache_envoyee"] = cle
        for joueur in salon["joueurs"]:
            if not joueur.get("parti"):
                tache = td_tache(etat, joueur["index"], time.time())
                if tache is not None:
                    await envoyer(joueur, tache)
    # Tous les albums, une seule fois, quand toute la revelation est passee.
    if etat["phase"] in ("vote_final", "fin") and phase_avant not in ("vote_final", "fin"):
        galerie = td_galerie(etat)
        if galerie is not None:
            await diffuser(code_salon, galerie)
    salon["td_phase"] = etat["phase"]
    salon["td_deja"] = etat["deja"]
    td_programmer(code_salon)


def td_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("td_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = td_prochaine_echeance(etat) if etat and salon.get("jeu") == "telephone" else None
    salon["td_minuteur"] = None if echeance is None else asyncio.create_task(
        td_minuteur(code_salon, salon, echeance))


async def td_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "telephone":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("td_minuteur") is not moi:
            return
        salon["td_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = td_expirer(etat, time.time())
        if etat["version"] != version:
            await td_publier(code_salon, evenements)
        else:
            td_programmer(code_salon)


async def td_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "telephone" or etat is None:
        return
    version = etat["version"]
    evenements = td_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await td_publier(code_salon, evenements)
    else:
        await td_envoyer_evenements(salon, code_salon, evenements)


def _td_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        td_joueur_revenu(etat, idx, time.time())
        td_programmer(code_salon)


async def _td_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        td_joueur_parti(etat, index, time.time())
        await td_publier(code_salon, [])


# =============================================================
# ROYAUMES (conquete, prefixe sw_)
# Les regles sont dans conquete.py (chiffres : conquete_config.json, cartes :
# conquete_cartes.json). Ici : le reseau et le minuteur du tour, qui finit
# le tour d un joueur trop lent ou parti (redeploiement sur).
# =============================================================

async def sw_demarrer(code_salon, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if etat is not None and not (salon.get("jeu") == "conquete" and etat.get("phase") == "fin"):
        return
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    nb = len(salon["joueurs"])
    if nb < SW_JOUEURS_MIN:
        await diffuser(code_salon, {"type": "erreur", "message": f"Royaumes demande au moins {SW_JOUEURS_MIN} joueurs."})
        return
    if nb > SW_JOUEURS_MAX:
        await diffuser(code_salon, {"type": "erreur", "message": f"Royaumes se joue à {SW_JOUEURS_MAX} joueurs maximum."})
        return
    for i, j in enumerate(salon["joueurs"]):
        j["index"] = i
    options = {k: message.get(k) for k in ("vitesse", "tours", "aleatoire", "vengeance")}
    salon["etat"] = sw_initialiser([j["nom"] for j in salon["joueurs"]], time.time(), options)
    salon["jeu"] = "conquete"
    await diffuser(code_salon, sw_catalogue(salon["etat"]))
    await envoyer_indices(code_salon)
    await sw_publier(code_salon, [])


async def sw_diffuser_evenements(salon, code_salon, evenements):
    for e in evenements:
        if e["a"] is None:
            await diffuser(code_salon, e["msg"])
        else:
            await bt_envoyer_prive(salon, e["a"], e["msg"])


async def sw_publier(code_salon, evenements):
    """Evenements du moteur, puis l etat a tous."""
    salon = salons.get(code_salon)
    if not salon or salon.get("jeu") != "conquete" or not salon.get("etat"):
        return
    etat = salon["etat"]
    await sw_diffuser_evenements(salon, code_salon, evenements)
    await diffuser(code_salon, sw_etat_public(etat, time.time()))
    sw_programmer(code_salon)
    if etat.get("phase") == "fin" and not etat.get("fin_signalee"):
        etat["fin_signalee"] = True
        noms = etat["joueurs"]
        res = etat.get("resultat") or {}
        gagnant = res.get("gagnant")
        gagnants = [noms[gagnant]] if gagnant is not None else []
        # A boire maintenant : la fin de partie seulement (les gorgees des
        # conquetes ont deja ete annoncees au fil des tours).
        doses = {noms[x["joueur"]]: x["boit"] for x in res.get("gorgees", []) if x.get("boit")}
        await signaler(code_salon, "conquete", "victoire", gagnants=gagnants,
                       perdants=[n for n in noms if n not in gagnants],
                       details={"gorgees": doses})


def sw_programmer(code_salon):
    salon = salons.get(code_salon)
    if not salon:
        return
    ancien = salon.get("sw_minuteur")
    if ancien is not None and not ancien.done():
        ancien.cancel()
    etat = salon.get("etat")
    echeance = sw_prochaine_echeance(etat) if etat and salon.get("jeu") == "conquete" else None
    salon["sw_minuteur"] = None if echeance is None else asyncio.create_task(
        sw_minuteur(code_salon, salon, echeance))


async def sw_minuteur(code_salon, salon, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if salons.get(code_salon) is not salon or salon.get("jeu") != "conquete":
        return
    moi = asyncio.current_task()
    async with verrou(salon):
        if salon.get("sw_minuteur") is not moi:
            return
        salon["sw_minuteur"] = None
        etat = salon["etat"]
        if all(j.get("parti") for j in salon["joueurs"]):
            return
        version = etat["version"]
        evenements = sw_expirer(etat, time.time())
        if etat["version"] != version:
            await sw_publier(code_salon, evenements)
        else:
            sw_programmer(code_salon)


async def sw_traiter(code_salon, joueur, message):
    salon = salons[code_salon]
    etat = salon.get("etat")
    if salon.get("jeu") != "conquete" or etat is None:
        return
    version = etat["version"]
    evenements = sw_action(etat, joueur["index"], message, time.time())
    if etat["version"] != version:
        await sw_publier(code_salon, evenements)
    else:
        # refus (prive) ou reaction (pour tous) : l etat n a pas change
        await sw_diffuser_evenements(salon, code_salon, evenements)


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
        fini = (jeu in ("menteur", "undercover", "telephone", "conquete", "justepinte", "quidenous", "tournee") and etat.get("phase") == "fin") or \
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
# REGISTRE DES JEUX
# Chaque jeu se declare UNE fois ici, avec la meme interface. Le reste du
# serveur (aiguillage des messages, arrivees, departs, retours, reprise
# apres coupure, liberation du salon) passe par ce registre au lieu de
# chaines de "if jeu == ...". Ajouter un jeu = ajouter une entree.
# =============================================================

class Jeu:
    def __init__(self, nom, titre, page, demarrage, possede, demarrer, traiter, instantane,
                 joueur_parti=None, joueur_revenu=None, arrivee=None,
                 peut_relancer=None, minuteurs=(), min_joueurs=2, max_joueurs=8):
        self.nom = nom
        self.titre = titre
        self.page = page                    # page du site qui joue ce jeu
        self.demarrage = demarrage          # type du message qui lance une partie
        self.possede = possede              # (type_msg) -> ce message est une action du jeu
        self.demarrer = demarrer            # async (code, message)
        self.traiter = traiter              # async (code, joueur, message)
        self.instantane = instantane        # async (code, joueur, complet, nouveau)
        self.joueur_parti = joueur_parti    # async (code, index)
        self.joueur_revenu = joueur_revenu  # (code, salon, index)
        self.arrivee = arrivee              # (salon, joueur) : nouveau venu en pleine partie
        # Relancer le MEME jeu alors qu il occupe le salon : seulement s il
        # est fini (sinon un double clic remettait tout a zero).
        self.peut_relancer = peut_relancer or (lambda etat: False)
        self.minuteurs = minuteurs          # cles des minuteurs dans le salon
        self.min_joueurs = min_joueurs
        self.max_joueurs = max_joueurs


ACTIONS_KINITO = ("lancer", "annoncer", "reaction", "abandonner", "nouvelle_manche")


async def _kinito_traiter(code_salon, joueur, message):
    t = message["type"]
    if t == "lancer":
        await kinito_lancer(code_salon, joueur)
    elif t == "annoncer":
        await kinito_annoncer(code_salon, joueur, message.get("score"))
    elif t == "reaction":
        await kinito_reaction(code_salon, joueur, message.get("choix"), message.get("score_cite"))
    elif t == "abandonner":
        await kinito_abandonner(code_salon, joueur)
    elif t == "nouvelle_manche":
        await kinito_nouvelle_manche(code_salon, joueur)


def _kinito_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < len(etat.get("partis", [])):
        etat["partis"][idx] = False
        kinito_reprendre(etat)


async def _bb_traiter(code_salon, joueur, m):
    t = m["type"]
    if t == "bb_placer":
        await bb_placer(code_salon, salons, diffuser, joueur, m.get("ligne"), m.get("colonne"))
    elif t == "bb_deplacer":
        await bb_deplacer(code_salon, salons, diffuser, joueur, m.get("ligne"), m.get("colonne"), m.get("via_carte"), m.get("index_carte"))
    elif t == "bb_attaquer":
        await bb_attaquer(code_salon, salons, diffuser, joueur, m.get("cible"), m.get("index_carte"), m.get("type_attaque"))
    elif t == "bb_as_deplacer":
        await bb_as_deplacer_frapper(code_salon, salons, diffuser, joueur, m.get("ligne"), m.get("colonne"), m.get("cible"), m.get("index_carte"))
    elif t == "bb_poser_arme":
        await bb_poser_arme(code_salon, salons, diffuser, joueur, m.get("index_carte"))
    elif t == "bb_dame":
        await bb_dame(code_salon, salons, diffuser, joueur, m.get("cible"), m.get("index_carte"))
    elif t == "bb_ne_rien_faire":
        await bb_ne_rien_faire(code_salon, salons, diffuser, joueur)
    elif t == "bb_ramasser":
        await bb_ramasser(code_salon, salons, diffuser, joueur, m.get("decision"), m.get("index_echange"))


def _bb_revenu(code_salon, salon, idx):
    # Un joueur revient : le dernier present n est plus seul (delai annule).
    bb_programmer_seul(code_salon)


async def _bt_traiter(code_salon, joueur, m):
    t = m["type"]
    if t == "bt_composer":
        await bt_composer(code_salon, salons, diffuser, joueur, m.get("total"), m.get("reel"))
    elif t == "bt_jouer_objet":
        await bt_jouer_objet(code_salon, salons, diffuser, joueur, m.get("cible"))
    elif t == "bt_tirer":
        await bt_tirer(code_salon, salons, diffuser, joueur, m.get("cible"))


def _pp_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < len(etat.get("partis", [])):
        etat["partis"][idx] = False
        etat["version"] += 1
        pp_rearmer_si_attendu(etat, idx)


def _mt_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        mt_joueur_revenu(etat, idx, time.time())
        mt_programmer(code_salon)


async def _mt_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        mt_joueur_parti(etat, index, time.time())
        await mt_publier(code_salon, [])


def _uc_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        uc_joueur_revenu(etat, idx, time.time())
        uc_programmer(code_salon)


async def _uc_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        uc_joueur_parti(etat, index, time.time())
        await uc_publier(code_salon, [])


def _jp_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        jp_joueur_revenu(etat, idx, time.time())
        jp_programmer(code_salon)


async def _jp_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        jp_joueur_parti(etat, index, time.time())
        await jp_publier(code_salon, [])


def _qn_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        qn_joueur_revenu(etat, idx, time.time())
        qn_programmer(code_salon)


async def _qn_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        qn_joueur_parti(etat, index, time.time())
        await qn_publier(code_salon, [])


def _tr_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        tr_joueur_revenu(etat, idx, time.time())
        tr_programmer(code_salon)


async def _tr_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        tr_joueur_parti(etat, index, time.time())
        await tr_publier(code_salon, [])


def _cs_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        cs_joueur_revenu(etat, idx, time.time())
        cs_programmer(code_salon)


async def _cs_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        evenements = cs_joueur_parti(etat, index, time.time())
        await cs_publier(code_salon, evenements)


def _sw_revenu(code_salon, salon, idx):
    etat = salon["etat"]
    if idx < etat["nb_joueurs"]:
        sw_joueur_revenu(etat, idx, time.time())
        sw_programmer(code_salon)


async def _sw_parti(code_salon, index):
    etat = salons[code_salon]["etat"]
    if index < etat["nb_joueurs"]:
        sw_joueur_parti(etat, index, time.time())
        await sw_publier(code_salon, [])


def _prefixe(p):
    return lambda t: t.startswith(p)


JEUX = {
    "kinito": Jeu(
        "kinito", "Kinito", "kinito-3d-jeu.html", "demarrer",
        lambda t: t in ACTIONS_KINITO,
        demarrer_kinito, _kinito_traiter, _kinito_instantane,
        joueur_parti=kinito_joueur_parti, joueur_revenu=_kinito_revenu, arrivee=_kinito_arrivee,
        min_joueurs=2, max_joueurs=99),
    "beerbattle": Jeu(
        "beerbattle", "BeerBattle", "beerbattle-3d-jeu.html", "bb_demarrer", _prefixe("bb_"),
        lambda code, m: bb_demarrer(code, salons, diffuser), _bb_traiter, _beerbattle_instantane,
        joueur_parti=bb_joueur_parti, joueur_revenu=_bb_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fini", minuteurs=("bb_seul",),
        min_joueurs=3, max_joueurs=BB_JOUEURS_MAX),
    "buckshot": Jeu(
        "buckshot", "Buckshot", "buckshot-3d-jeu.html", "bt_demarrer", _prefixe("bt_"),
        lambda code, m: bt_demarrer(code, salons, diffuser), _bt_traiter, _buckshot_instantane,
        joueur_parti=bt_joueur_parti, arrivee=_buckshot_arrivee,
        min_joueurs=2, max_joueurs=BT_JOUEURS_MAX),
    "picolopoly": Jeu(
        "picolopoly", "Picopoly", "picolopoly-3d-jeu.html", "pp_demarrer", _prefixe("pp_"),
        lambda code, m: pp_demarrer(code, m.get("difficulte")), pp_traiter, _picolopoly_instantane,
        joueur_parti=pp_joueur_parti, joueur_revenu=_pp_revenu, arrivee=_picolopoly_arrivee,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("pp_minuteur",),
        min_joueurs=PP_JOUEURS_MIN, max_joueurs=PP_JOUEURS_MAX),
    "menteur": Jeu(
        "menteur", "Cal 12", "menteur-3d-jeu.html", "mt_demarrer", _prefixe("mt_"),
        lambda code, m: mt_demarrer(code, m.get("mode")), mt_traiter, _menteur_instantane,
        joueur_parti=_mt_parti, joueur_revenu=_mt_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("mt_minuteur",),
        min_joueurs=MT_JOUEURS_MIN, max_joueurs=MT_JOUEURS_MAX),
    "casino": Jeu(
        "casino", "Casino", "casino-3d-jeu.html", "cs_demarrer", _prefixe("cs_"),
        lambda code, m: cs_demarrer(code), cs_traiter, _casino_instantane,
        joueur_parti=_cs_parti, joueur_revenu=_cs_revenu, arrivee=_casino_arrivee,
        minuteurs=("cs_minuteur",),
        min_joueurs=CS_JOUEURS_MIN, max_joueurs=CS_JOUEURS_MAX),
    "undercover": Jeu(
        "undercover", "Undercover", "undercover-3d-jeu.html", "uc_demarrer", _prefixe("uc_"),
        uc_demarrer, uc_traiter, _undercover_instantane,
        joueur_parti=_uc_parti, joueur_revenu=_uc_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("uc_minuteur",),
        min_joueurs=UC_JOUEURS_MIN, max_joueurs=UC_JOUEURS_MAX),
    "telephone": Jeu(
        "telephone", "Gribouillophone", "telephone-jeu.html", "td_demarrer", _prefixe("td_"),
        td_demarrer, td_traiter, _telephone_instantane,
        joueur_parti=_td_parti, joueur_revenu=_td_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("td_minuteur",),
        min_joueurs=TD_JOUEURS_MIN, max_joueurs=TD_JOUEURS_MAX),
    "conquete": Jeu(
        "conquete", "Royaumes", "conquete-3d-jeu.html", "sw_demarrer", _prefixe("sw_"),
        sw_demarrer, sw_traiter, _conquete_instantane,
        joueur_parti=_sw_parti, joueur_revenu=_sw_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("sw_minuteur",),
        min_joueurs=SW_JOUEURS_MIN, max_joueurs=SW_JOUEURS_MAX),
    "justepinte": Jeu(
        "justepinte", "Le Juste Pinte", "justepinte-3d-jeu.html", "jp_demarrer", _prefixe("jp_"),
        jp_demarrer, jp_traiter, _justepinte_instantane,
        joueur_parti=_jp_parti, joueur_revenu=_jp_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("jp_minuteur",),
        min_joueurs=JP_JOUEURS_MIN, max_joueurs=JP_JOUEURS_MAX),
    "quidenous": Jeu(
        "quidenous", "Qui de nous ?", "quidenous-3d-jeu.html", "qn_demarrer", _prefixe("qn_"),
        qn_demarrer, qn_traiter, _quidenous_instantane,
        joueur_parti=_qn_parti, joueur_revenu=_qn_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("qn_minuteur",),
        min_joueurs=QN_JOUEURS_MIN, max_joueurs=QN_JOUEURS_MAX),
    "tournee": Jeu(
        "tournee", "La Tournée", "tournee-3d-jeu.html", "tr_demarrer", _prefixe("tr_"),
        tr_demarrer, tr_traiter, _tournee_instantane,
        joueur_parti=_tr_parti, joueur_revenu=_tr_revenu,
        peut_relancer=lambda etat: etat.get("phase") == "fin", minuteurs=("tr_minuteur",),
        min_joueurs=TR_JOUEURS_MIN, max_joueurs=TR_JOUEURS_MAX),
}


def arreter_minuteurs(salon):
    for jeu in JEUX.values():
        for cle in jeu.minuteurs:
            m = salon.get(cle)
            if m is not None and not m.done():
                m.cancel()
            salon[cle] = None


async def terminer_jeu(code_salon, raison):
    """
    Le jeu en cours s arrete (remplace par un autre, ou retour en salle
    d attente). Les couches en sont prevenues, les minuteurs coupes, et les
    places laissees vides sont retirees : la suite repart des seuls presents.
    """
    salon = salons[code_salon]
    nom = salon.get("jeu")
    if salon.get("etat") is None:
        return
    await signaler(code_salon, nom, "fin_jeu", details={"raison": raison})
    arreter_minuteurs(salon)
    salon["etat"] = None
    salon["jeu"] = None
    salon["choix"] = None
    salon["joueurs"] = [j for j in salon["joueurs"] if not j.get("parti")]
    await envoyer_indices(code_salon)      # renumerote : on est en salle d attente


async def lancer_jeu(code_salon, nom, message, joueur=None):
    """
    Regle UNIQUE de lancement, la meme pour tous les jeux :
    - salle d attente : le jeu demarre ;
    - le MEME jeu occupe le salon : relance seulement s il est fini (un double
      clic ou un vieil onglet ne remet pas tout a zero) ;
    - un AUTRE jeu occupe le salon : il est remplace. Avant, selon le jeu,
      c etait permis, refuse sans un mot, ou permis seulement si l ancien
      etait fini : avec le meme code toute la soiree, on restait bloque.
    Le nombre de joueurs est verifie AVANT de remplacer : un lancement refuse
    ne doit pas detruire la partie en cours.
    Remplacer une partie pas finie exige "remplacer": true, que la page
    n envoie qu apres avoir fait confirmer le joueur. Sans ca, un joueur qui
    rame (sa page n a pas encore recu la partie, ou n a pas pu verifier
    qu une partie tournait) remplacait une partie d une demi-heure pour tout
    le monde sans qu on lui demande rien.
    """
    salon = salons[code_salon]
    jeu = JEUX[nom]
    etat = salon.get("etat")
    if etat is not None:
        if salon.get("jeu") == nom:
            if not jeu.peut_relancer(etat):
                return
        else:
            ancien = JEUX.get(salon.get("jeu"))
            fini = ancien is not None and ancien.peut_relancer(etat)
            if not fini and message.get("remplacer") is not True:
                actifs = [j["nom"] for j in salon["joueurs"] if not j.get("parti")]
                texte = (f"Une partie de {ancien.titre if ancien else 'un autre jeu'} est en cours à ta table"
                         + (" (" + ", ".join(actifs) + ")" if actifs else "")
                         + f" : {jeu.titre} n'a pas été lancé. Rejoins la partie depuis l'accueil.")
                refus = {"type": "erreur", "message": texte, "partie_en_cours": salon.get("jeu")}
                if joueur is not None:
                    await envoyer(joueur, refus)
                else:
                    await diffuser(code_salon, refus)
                return
            presents = sum(1 for j in salon["joueurs"] if not j.get("parti"))
            if not (jeu.min_joueurs <= presents <= jeu.max_joueurs):
                borne = (f"de {jeu.min_joueurs} à {jeu.max_joueurs} joueurs"
                         if jeu.max_joueurs < 99 else f"a partir de {jeu.min_joueurs} joueurs")
                await diffuser(code_salon, {"type": "erreur",
                                            "message": f"{jeu.titre} se joue {borne} ({presents} présents)."})
                return
            await terminer_jeu(code_salon, "remplace")
    avant = salon.get("etat")
    if joueur is not None:
        # Qui a lance (l hote du Gribouillophone) ; les autres jeux l ignorent.
        message = {**message, "_par": joueur.get("nom")}
    await jeu.demarrer(code_salon, message)
    if salon.get("etat") is not None and salon.get("etat") is not avant and salon.get("jeu") == nom:
        salon["debut_jeu"] = time.time()
        # Les pages des AUTRES jeux ne comprennent pas le message de debut de
        # ce jeu : celui-ci leur dit lequel tourne et ou le rejoindre.
        await diffuser(code_salon, {"type": "salon_jeu", "jeu": nom, "titre": jeu.titre, "page": jeu.page})


# =============================================================
# COUCHES PARALLELES (l Enquete...) ET SAUVEGARDE
# Voir couches.py. Les couches vivent a cote des salons, attachees au code :
# un salon libere (tout le monde parti un moment) ne les efface pas.
# =============================================================

couches = {}            # code de SOIREE -> {prefixe: Couche}
_minuteurs_couches = {}  # (code, prefixe) -> tache
stock = None
sauvegarde = None


def _noms_presents(soiree):
    """Les presents de TOUTES les tables de la soiree."""
    noms = []
    for code in tables_de(soiree):
        noms += [j["nom"] for j in salons[code]["joueurs"] if not j.get("parti")]
    return noms


def _tables(soiree):
    return [{"code": c, "numero": numero_table(c), "jeu": salons[c].get("jeu"),
             "attente": salons[c].get("choix") if salons[c].get("etat") is None else None,
             "joueurs": [j["nom"] for j in salons[c]["joueurs"] if not j.get("parti")]}
            for c in tables_de(soiree)]


# --- Connexions « soiree » (/ws-soiree) ---
# Distinctes des places aux tables : la page de l Enquete s ouvre par-dessus
# un jeu sans lui prendre sa connexion. Un prenom = une connexion soiree
# (la plus recente gagne, l ancienne est prevenue comme pour un onglet en
# double). Les couches parlent aux joueurs par ici.
# Une connexion par prenom ET par page (?page=bingo) : l Enquete et le Bingo
# ouverts en meme temps ne s ejectent pas ; le meme prenom deux fois sur la
# meme page, si (onglet en double).
abonnes_soirees = {}    # soiree -> {cle du prenom + "|" + page: {"nom", "cle", "page", "ws"}}
_verrous_soirees = {}


def cle_prenom(nom):
    return " ".join(nom.split()).casefold()


def verrou_soiree(soiree):
    """Un verrou par soiree pour les couches (pris APRES celui d une table, jamais avant)."""
    v = _verrous_soirees.get(soiree)
    if v is None:
        v = _verrous_soirees[soiree] = asyncio.Lock()
    return v


def _abonnes(soiree):
    """Les prenoms ayant au moins une connexion soiree ouverte (une fois chacun)."""
    vus = {}
    for a in abonnes_soirees.get(soiree, {}).values():
        vus.setdefault(a["cle"], a["nom"])
    return list(vus.values())


async def diffuser_soiree(soiree, message):
    for a in list(abonnes_soirees.get(soiree, {}).values()):
        await envoyer(a, message)


async def envoyer_nom(soiree, nom, message):
    """A toutes les connexions soiree de ce prenom (une par page ouverte)."""
    k = cle_prenom(nom)
    for a in [a for a in abonnes_soirees.get(soiree, {}).values() if a["cle"] == k]:
        await envoyer(a, message)


def _contexte(soiree, couche):
    return Contexte(
        soiree,
        diffuser=lambda m: diffuser_soiree(soiree, m),
        envoyer_nom=lambda nom, m: envoyer_nom(soiree, nom, m),
        noms_presents=lambda: _noms_presents(soiree),
        abonnes=lambda: _abonnes(soiree),
        tables=lambda: _tables(soiree),
        sauvegarder=lambda: _sauvegarder_couche(soiree, couche),
        maintenant=time.time,
        couche=lambda prefixe: couches.get(soiree, {}).get(prefixe),
        gorgees_ailleurs=lambda nom, t: _gorgees_ailleurs(soiree, couche, nom, t),
    )


def _gorgees_ailleurs(soiree, couche, nom, maintenant):
    """Plafond commun : gorgees bues dans l heure dans les autres couches de la soiree."""
    total = 0
    for autre in list(couches.get(soiree, {}).values()):
        if autre is not couche:
            try:
                total += autre.gorgees_heure(nom, maintenant) or 0
            except Exception:
                journal.exception("Compteur de gorgees de la couche %s", autre.prefixe)
    return total


def _cle_couche(code_salon, prefixe):
    return f"couche:{code_salon}:{prefixe}"


def _sauvegarder_couche(code_salon, couche):
    if sauvegarde is None:
        return
    def produire():
        if couche.terminee():
            return None
        return {"type": couche.prefixe, "donnees": couche.exporter()}
    sauvegarde.marquer(_cle_couche(code_salon, couche.prefixe), produire)


def _apres_couche(code_salon, couche):
    """Apres chaque appel a une couche : sauvegarde, minuteur, nettoyage."""
    _sauvegarder_couche(code_salon, couche)
    cle = (code_salon, couche.prefixe)
    ancien = _minuteurs_couches.pop(cle, None)
    if ancien is not None and not ancien.done() and ancien is not asyncio.current_task():
        ancien.cancel()
    if couche.terminee():
        par_code = couches.get(code_salon, {})
        if par_code.get(couche.prefixe) is couche:
            del par_code[couche.prefixe]
        if not par_code:
            couches.pop(code_salon, None)
        return
    echeance = couche.prochaine_echeance()
    if echeance is not None:
        _minuteurs_couches[cle] = asyncio.ensure_future(_minuteur_couche(code_salon, couche, echeance))


async def _minuteur_couche(code_salon, couche, echeance):
    await asyncio.sleep(max(0.0, echeance - time.time()) + 0.05)
    if couches.get(code_salon, {}).get(couche.prefixe) is not couche:
        return
    async with verrou_soiree(code_salon):
        if couches.get(code_salon, {}).get(couche.prefixe) is couche:
            await _appeler_couche(code_salon, couche, couche.echeance)


async def _appeler_couche(code_salon, couche, methode, *args):
    """Une couche ne fait jamais planter un jeu : ses erreurs sont journalisees."""
    try:
        await methode(_contexte(code_salon, couche), *args)
    except Exception:
        journal.exception("Erreur de la couche %s dans le salon %s", couche.prefixe, code_salon)
    _apres_couche(code_salon, couche)


async def couche_action(code_salon, nom, message):
    """Un message "<prefixe>_..." d un joueur (code_salon : code de la SOIREE)."""
    async with verrou_soiree(code_salon):
        await _couche_action(code_salon, nom, message)


async def _couche_action(code_salon, nom, message):
    avant = sorted(couches.get(code_salon, {}))
    await _couche_action_brute(code_salon, nom, message)
    apres = sorted(couches.get(code_salon, {}))
    if apres != avant:
        # Une couche vient d etre creee ou de se terminer : tous les abonnes
        # de la soiree doivent le savoir, pas seulement celui qui a agi.
        await diffuser_soiree(code_salon, {"type": "soiree_couches", "soiree": code_salon, "couches": apres})


async def _couche_action_brute(code_salon, nom, message):
    prefixe = message["type"].split("_", 1)[0]
    couche = couches.get(code_salon, {}).get(prefixe)
    if couche is None:
        # Pas encore de couche de ce type dans la soiree : seule sa classe
        # decide si ce message en cree une (ex. l hote lance l Enquete).
        classe = TYPES_COUCHES[prefixe]
        try:
            couche = await classe.creer(code_salon, nom, message)
        except Exception:
            journal.exception("Creation de la couche %s ratee dans %s", prefixe, code_salon)
            couche = None
        if couche is None:
            return
        couches.setdefault(code_salon, {})[prefixe] = couche
    await _appeler_couche(code_salon, couche, couche.action, nom, message)


async def couches_arrivee(soiree, nom):
    """Un joueur ouvre sa connexion soiree : chaque couche lui envoie ce qu il doit voir."""
    async with verrou_soiree(soiree):
        await diffuser_soiree_nom(soiree, nom)
        for couche in list(couches.get(soiree, {}).values()):
            await _appeler_couche(soiree, couche, couche.arrivee, nom)


async def diffuser_soiree_nom(soiree, nom):
    await envoyer_nom(soiree, nom, {"type": "soiree_couches", "soiree": soiree,
                                    "couches": sorted(couches.get(soiree, {}))})


async def signaler(code_salon, jeu, type_evt, gagnants=(), perdants=(), details=None):
    """
    Point d entree UNIQUE par lequel les jeux annoncent leurs resultats aux
    couches (fin de manche, victoire, fin de jeu). Les jeux ne savent pas
    qui ecoute ; sans couche active, ca ne fait rien. Les couches sont celles
    de la soiree : une victoire a n importe quelle table compte.
    """
    table = code_salon
    code_salon = soiree_de(code_salon)
    salon = salons.get(table)
    if salon is not None:
        await annoncer_resultat(table, salon, jeu, type_evt, gagnants, perdants, details or {})
    if not couches.get(code_salon):
        return
    async with verrou_soiree(code_salon):
        await _signaler(code_salon, salon, table, jeu, type_evt, gagnants, perdants, details)


async def annoncer_resultat(table, salon, jeu, type_evt, gagnants, perdants, details):
    """
    Annonce a la table qui a perdu (et gagne), pour l animation de defaite
    sur les telephones (commun/soiree.js). Un seul message pour tous les jeux :
    - perdants donnes par le jeu (Kinito : le perdant de la manche) ;
    - fin de partie (victoire) : tous ceux qui ne gagnent pas ;
    - Buckshot : ceux qui boivent le plus sur la manche.
    """
    presents = [j["nom"] for j in salon["joueurs"] if not j.get("parti")]
    gorgees = {}
    perdus = list(perdants)
    if not perdus and type_evt == "victoire" and gagnants:
        g = {cle_prenom(n) for n in gagnants}
        perdus = [n for n in presents if cle_prenom(n) not in g]
    if not perdus and jeu == "buckshot" and type_evt == "manche":
        par = {n: v for n, v in (details.get("gorgees") or {}).items() if v}
        if par:
            pire = max(par.values())
            perdus = [n for n, v in par.items() if v == pire]
            gorgees = par
    if jeu == "kinito" and perdus:
        gorgees = {perdus[0]: details.get("gorgees")}      # None : cul sec
    elif jeu != "buckshot" and isinstance(details.get("gorgees"), dict):
        # Gorgees a boire maintenant, par prenom (Undercover : les perdants).
        gorgees = {n: v for n, v in details["gorgees"].items() if v is None or (isinstance(v, int) and v > 0)}
    if not perdus:
        return
    try:
        # « joueurs » : toute la table, pour que l ecran de resultat (le meme
        # chez tous) liste aussi ceux qui ne boivent pas.
        await diffuser(table, {"type": "table_resultat", "jeu": jeu, "evt": type_evt, "gagnants": list(gagnants),
                               "perdants": perdus, "gorgees": gorgees, "joueurs": presents})
    except Exception:
        journal.exception("Annonce du resultat a la table %s", table)


async def _signaler(code_salon, salon, table, jeu, type_evt, gagnants, perdants, details):
    evenement = {
        "jeu": jeu, "type": type_evt, "table": table,
        "joueurs": [j["nom"] for j in salon["joueurs"] if not j.get("parti")] if salon else [],
        "gagnants": list(gagnants), "perdants": list(perdants),
        "details": details or {},
    }
    for couche in list(couches.get(code_salon, {}).values()):
        await _appeler_couche(code_salon, couche, couche.evenement_jeu, evenement)


async def demarrage(stockage=None):
    """Au lancement du serveur : ouvre le stockage et recharge les couches en cours."""
    global stock, sauvegarde
    stock = stockage if stockage is not None else await creer_stockage()
    sauvegarde = Sauvegarde(stock)
    try:
        lignes = await stock.lister("couche:")
    except Exception:
        journal.exception("Lecture des couches sauvegardees impossible")
        lignes = []
    for cle, valeur in lignes:
        try:
            _, code, prefixe = cle.split(":", 2)
            classe = TYPES_COUCHES.get(prefixe)
            if classe is None:
                continue
            couche = classe.importer(code, valeur["donnees"])
            couches.setdefault(code, {})[prefixe] = couche
            _apres_couche(code, couche)
        except Exception:
            journal.exception("Couche sauvegardee illisible : %s", cle)


async def arret():
    """A l arret (redeploiement) : la derniere sauvegarde part avant de couper."""
    if sauvegarde is not None:
        await sauvegarde.vider()
    if stock is not None:
        await stock.fermer()


async def abonner(soiree, nom, ws, page=""):
    """Ouvre la connexion soiree d un joueur (sur la meme page, la plus recente remplace l ancienne)."""
    k = cle_prenom(nom) + "|" + page
    entree = {"nom": nom, "cle": cle_prenom(nom), "page": page, "k": k, "ws": ws, "parti": False}
    ancien = abonnes_soirees.setdefault(soiree, {}).get(k)
    abonnes_soirees[soiree][k] = entree
    if ancien is not None and ancien["ws"] is not ws:
        try:
            await asyncio.wait_for(ancien["ws"].send_text('{"type":"remplace"}'), 1)
        except Exception:
            pass
        asyncio.ensure_future(fermer(ancien["ws"], 4002))
    await couches_arrivee(soiree, nom)
    return entree


def desabonner(soiree, entree):
    par_nom = abonnes_soirees.get(soiree, {})
    k = entree["k"]
    if par_nom.get(k) is entree:
        del par_nom[k]
    if not par_nom:
        abonnes_soirees.pop(soiree, None)


@app.websocket("/ws-soiree/{code}/{nom_joueur}")
async def websocket_soiree(ws: WebSocket, code: str, nom_joueur: str, page: str = ""):
    """
    Connexion « soiree » d un joueur : celle des couches paralleles (l Enquete),
    a cote de sa place a une table. Messages "<prefixe>_..." et battements de
    coeur ; rien d autre.
    """
    code = normaliser_code(code)
    if code is None:
        await ws.close(4004)
        return
    admise, ip = admettre_connexion(ws, code)
    if not admise:
        await ws.close(1013)
        return
    try:
        await connexion_soiree(ws, code, nom_joueur, page if PAGE_SOIREE.fullmatch(page or "") else "")
    finally:
        connexions_ip.sortir(ip)


PAGE_SOIREE = re.compile(r"[a-z]{0,12}")


async def connexion_soiree(ws, code, nom_joueur, page=""):
    await ws.accept()
    soiree = soiree_de(code)
    nom = nettoyer_prenom(nom_joueur) or "Joueur"
    entree = await abonner(soiree, nom, ws, page)
    try:
        debit = Debit(DEBIT_MESSAGES, RAFALE_MESSAGES)
        async for brut in ws.iter_text():
            if len(brut) > TAILLE_MAX_MESSAGE:
                journal.warning("Message trop gros (%d) dans la soiree %s", len(brut), soiree)
                await fermer(ws, 1009)
                break
            if not debit.accepter():
                if debit.refus_de_suite >= REFUS_AVANT_COUPURE:
                    journal.warning("Inondation de messages dans la soiree %s", soiree)
                    await fermer(ws, 1008)
                    break
                continue
            message = lire_message(brut)
            if message is None or not isinstance(message.get("type"), str):
                continue
            if message["type"] == "ping":
                try:
                    await asyncio.wait_for(ws.send_text('{"type":"pong"}'), DELAI_ENVOI)
                except Exception:
                    pass
                continue
            if abonnes_soirees.get(soiree, {}).get(entree["k"]) is not entree:
                continue        # remplacee par une connexion plus recente
            if message["type"].split("_", 1)[0] in TYPES_COUCHES:
                try:
                    await couche_action(soiree, nom, message)
                except Exception:
                    journal.exception("Erreur couche %r dans la soiree %s", message["type"], soiree)
            elif message["type"] == "soiree_etat":
                await couches_arrivee(soiree, nom)
    except Exception:
        pass
    finally:
        desabonner(soiree, entree)


# =============================================================
# ENDPOINT HTTP : creer un salon
# Les joueurs font une requete HTTP pour obtenir un code de salon.
# =============================================================

@app.get("/creer-salon")
async def creer_salon(request: Request = None):
    """Cree un nouveau salon vide et retourne son code."""
    if not creations_ip.autoriser(_ip_requete(request)):
        return JSONResponse({"detail": "Trop de soirées créées, réessaie dans une minute."}, status_code=429)
    if salons_pleins():
        return refus_plein()
    code = generer_code()
    salons[code] = nouveau_salon()
    return {"code": code}


@app.get("/salon/{code}")
async def info_salon(code: str, request: Request = None):
    """Retourne les infos d une table (pour verifier qu elle existe)."""
    code = code.upper()
    if deviner_bloque(_ip_requete(request), code):
        return {"existe": False}
    if code not in salons and not (code == soiree_de(code) and soiree_existe(code)):
        return {"existe": False}
    salon = salons.get(code) or nouveau_salon()
    jeu = JEUX.get(salon["jeu"])
    return {
        "existe": True,
        "joueurs": [j["nom"] for j in salon["joueurs"]],
        "jeu": salon["jeu"],
        "page": jeu.page if jeu else None,
        # Depuis combien de secondes le jeu tourne, et s il est fini : la
        # confirmation de remplacement le dit (« en cours depuis 32 min »).
        "depuis": round(time.time() - salon["debut_jeu"]) if salon.get("etat") is not None and salon.get("debut_jeu") else None,
        "fini": bool(jeu and salon.get("etat") is not None and jeu.peut_relancer(salon["etat"])),
        "couches": sorted(couches.get(soiree_de(code), {})),
    }


# --- Qui est dans la soiree ---
# L accueil n a pas de connexion permanente : il relit l etat de la soiree
# toutes les quelques secondes en donnant son prenom (?nom=), ce qui le
# compte present. S y ajoutent les joueurs assis aux tables et ceux qui ont
# l Enquete ouverte (connexion soiree).
presences_accueil = {}    # soiree -> {cle du prenom: {"nom", "vu", "arrive"}}
PRESENCE_ACCUEIL = 30     # secondes sans nouvelles avant de ne plus compter


def presents_soiree(soiree, tables):
    maintenant = time.time()
    vus = presences_accueil.get(soiree, {})
    for k in [k for k, v in vus.items() if maintenant - v["vu"] > 4 * PRESENCE_ACCUEIL]:
        del vus[k]
    out = {}
    codes = {t["code"] for t in tables}
    for t in tables:
        for nom in t["joueurs"]:
            out.setdefault(cle_prenom(nom), {"nom": nom, "ou": "Table " + str(t["numero"]) + (" · " + t["titre"] if t.get("titre") else ""),
                                             "table": t["code"]})
    for a in abonnes_soirees.get(soiree, {}).values():
        out.setdefault(cle_prenom(a["nom"]), {"nom": a["nom"], "ou": "Bingo" if a.get("page") == "bingo" else "Enquête",
                                              "table": None})
    for k, v in vus.items():
        if maintenant - v["vu"] <= PRESENCE_ACCUEIL:
            # Sur l accueil : sa table (en salle d attente) si elle existe encore
            table = v.get("table") if v.get("table") in codes else None
            out.setdefault(k, {"nom": v["nom"], "ou": "Accueil", "table": table})
    return sorted(out.values(), key=lambda x: x["nom"].casefold())


def _couches_en_cours(soiree):
    """Les couches lancees et pas finies (les onglets 🔍 / 🎯 des jeux ne montrent qu elles)."""
    out = []
    for prefixe, couche in sorted(couches.get(soiree, {}).items()):
        try:
            if couche.en_cours():
                out.append(prefixe)
        except Exception:
            journal.exception("Couche %s : en_cours()", prefixe)
    return out


@app.get("/soiree/{code}")
async def info_soiree(code: str, nom: str = "", table: str = "", request: Request = None):
    """La soiree : ses tables (jeu, joueurs presents), ses couches et qui est la."""
    code = soiree_de(code.upper())
    if deviner_bloque(_ip_requete(request), code) or not soiree_existe(code):
        return {"existe": False}
    nom = nettoyer_prenom(nom)
    if nom:
        vus = presences_accueil.setdefault(code, {})
        k = cle_prenom(nom)
        vus[k] = {"nom": nom, "vu": time.time(), "arrive": vus.get(k, {}).get("arrive", time.time()),
                  "table": table.upper() if soiree_de(table.upper()) == code else None}
    tables = []
    for t in _tables(code):
        jeu = JEUX.get(t["jeu"])
        t["titre"] = jeu.titre if jeu else None
        t["page"] = jeu.page if jeu else None
        tables.append(t)
    return {"existe": True, "code": code, "tables": tables, "couches": sorted(couches.get(code, {})),
            "couches_en_cours": _couches_en_cours(code),
            "presents": presents_soiree(code, tables)}


@app.get("/soiree/{code}/rouvrir")
async def rouvrir_soiree(code: str, request: Request = None):
    """
    Une soiree que le serveur ne connait plus (il a redemarre : les salons
    vivent en memoire) est rouverte sous le meme code, tables vides. Le code
    et le QR deja partages restent valables.
    """
    code = soiree_de(code.upper())
    if not (len(code) == 4 and code.isalpha() and code.isupper()):
        return {"existe": False}
    if not soiree_existe(code):
        if not creations_ip.autoriser(_ip_requete(request)):
            return JSONResponse({"detail": "Trop de soirées rouvertes, réessaie dans une minute."}, status_code=429)
        if salons_pleins():
            return refus_plein()
        salons[code] = nouveau_salon()
        return {"existe": True, "rouverte": True}
    return {"existe": True, "rouverte": False}


@app.get("/soiree/{code}/nouvelle-table")
async def nouvelle_table(code: str, request: Request = None):
    """Ouvre la prochaine table libre de la soiree (ABCD-2, ABCD-3...)."""
    # Meme controle que rouvrir : un code de travers (minuscules, trop long)
    # ne doit pas creer de table fantome a cote de la vraie soiree.
    code = soiree_de(code.upper())
    if not (len(code) == 4 and code.isalpha()):
        return {"existe": False}
    if not creations_ip.autoriser(_ip_requete(request)):
        return JSONResponse({"detail": "Trop de tables ouvertes, réessaie dans une minute."}, status_code=429)
    if salons_pleins():
        return refus_plein()
    if not soiree_existe(code):
        await rouvrir_soiree(code)
    if code not in salons:
        salons[code] = nouveau_salon()      # la table 1 avait ete liberee
        return {"existe": True, "code": code}
    for n in range(2, TABLES_MAX + 1):
        c = code_table(code, n)
        if c not in salons:
            salons[c] = nouveau_salon()
            return {"existe": True, "code": c}
    return {"existe": True, "code": None, "message": f"{TABLES_MAX} tables au plus par soirée."}


@app.get("/")
async def racine():
    """Page d accueil du serveur (pour verifier qu il tourne)."""
    return {
        "status": "Le serveur tourne",
        "salons_actifs": len(salons),
        "version": VERSION,
        "stockage": "postgres" if getattr(stock, "durable", False) else "memoire",
    }
