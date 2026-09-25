"""
Moteur de regles de Picopoly (Monopoly a boire).

Moteur PUR : pas de WebSocket, pas d asyncio, pas d horloge implicite (le
temps est toujours passe en parametre). Chaque action prend l etat -- un
dict serialisable en JSON, pour pouvoir un jour le sauvegarder et survivre
a un redemarrage du serveur -- et renvoie la liste des messages a envoyer.
serveur.py s occupe du reseau, des minuteurs, et diffuse pp_etat apres
chaque action acceptee.

Un message a envoyer est {"a": index du destinataire ou None pour tous,
"msg": {...}}. Picopoly n a presque aucun secret : seul l ordre des
paquets Chance / Caisse de communaute n est jamais diffuse.

Deroulement d un tour :
  "tour"        le joueur courant gere (construire, hypothequer, echanger
                avant son premier lancer, sortir de prison) puis lance
  "resolution"  les consequences du lancer s enchainent via la file
                etat["suite"] ; elle s arrete des qu un choix
                (etat["attente"]) ou une dette (etat["dette"]) la bloque
  "fin_tour"    le joueur gere encore, puis termine son tour
  "fin"         partie terminee (faillites ou chrono)
Tout ce qu on attend d un joueur a une echeance : un absent ne fige jamais
la table, le serveur joue le choix par defaut a sa place.
"""
import random
import time

# =============================================================
# CONSTANTES DE REGLES -- tout le calibrage se regle ici
# =============================================================
PP_JOUEURS_MIN = 2
PP_JOUEURS_MAX = 6

# Monopoly classique francais
PP_ARGENT_DEPART = 1500
PP_SALAIRE = 200              # passage par la case Depart
PP_AMENDE_PRISON = 50
PP_ESSAIS_PRISON = 3          # au 3e double rate, on paie et on sort
PP_LOYER_GARE = 25            # 25 / 50 / 100 / 200 selon le nombre de gares
PP_MULT_COMPAGNIE = (4, 10)   # x des : une compagnie / les deux
PP_INTERET_LEVEE = 10         # % ajoutes pour lever une hypotheque

# Gorgees
PP_GORGEES_DISTRIBUTION = 1   # carte "Distribution generale", par adversaire
PP_RETOUR_DEFAUT = 2          # "Retour de baton" sans penalite precedente
# Difficulte, choisie par le createur du salon. Elle ne fait pas que
# multiplier : elle decide aussi QUAND on boit.
#   tranche / plafond  loyers, taxes, cartes : 1 gorgee par tranche de
#                      "tranche" EUR, au moins 1, au plus "plafond"
#   depart             passage au Depart : toute la table trinque ("tous"),
#                      celui qui passe seulement ("joueur"), ou personne
#   miroir             le proprietaire qui encaisse un loyer fait boire N
#                      gorgees a qui il veut (0 = il ne distribue rien)
#   prison / echange   entree en prison ; toast collectif d un echange conclu
#   duel / cadeau / sommelier   les cartes speciales ({n} dans leur texte)
# La cagnotte et la roulette ne bougent pas : ce sont des choix, pas des
# penalites. "difficile" est la partie d origine, et le defaut quand un
# client n envoie rien.
PP_DIFFICULTES = {
    "facile": {"tranche": 100, "plafond": 3, "depart": "personne", "miroir": 0,
               "prison": 1, "echange": 0, "duel": 2, "cadeau": 2, "sommelier": 4},
    "normal": {"tranche": 50, "plafond": 4, "depart": "joueur", "miroir": 0,
               "prison": 1, "echange": 1, "duel": 3, "cadeau": 3, "sommelier": 5},
    "difficile": {"tranche": 50, "plafond": 5, "depart": "tous", "miroir": 1,
                  "prison": 1, "echange": 1, "duel": 3, "cadeau": 3, "sommelier": 6},
}
PP_DIFFICULTE_DEFAUT = "difficile"
# Cagnotte du Parc Blossac : taxes, amendes des cartes et sorties de prison
# payees s y accumulent ; s arreter au Parc permet de la rafler contre
# 1 gorgee par tranche de 50 EUR.
PP_CAGNOTTE_PAR_GORGEE = 50
PP_MOTIFS_CAGNOTTE = ["taxe", "carte", "prison"]
# Roulette : une mise par tour, par tranches de 50 EUR, 1 gorgee par tranche
# misee (bue quoi qu il sorte). Une mise perdue part dans la cagnotte.
# Gains : la mise est rendue multipliee par PP_ROULETTE_GAINS[pari].
PP_ROULETTE_MISE = 50
PP_ROULETTE_GAINS = {"rouge": 2, "noir": 2, "pair": 2, "impair": 2, "douzaine": 3, "plein": 36}
PP_ROULETTE_ROUGES = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]

# Rythme
PP_DUREE_PARTIE = 3600        # chrono dur, en secondes
PP_DELAI_CHOIX = 30           # pour repondre a un choix (achat, cible...)
PP_DELAI_TOUR = 90            # pour lancer, finir son tour, regler une dette
PP_DELAI_ABSENT = 3           # quand on attend un joueur parti
PP_ECHANGES_PAR_TOUR = 3
# De rapide : 1, 2, 3, Bus, et le symbole Monopoly en double.
PP_DE_RAPIDE = [1, 2, 3, "bus", "monopoly", "monopoly"]
# Bus officiel : avancer d un de blanc OU de leur somme. True = n importe
# quelle case (lecture litterale de la spec, qui rejoint alors le triple).
PP_BUS_CASE_LIBRE = False

PP_ACHETABLES = ("propriete", "gare", "compagnie")


# =============================================================
# PLATEAU -- 40 cases, montants du Monopoly classique francais
# =============================================================

def _prop(nom, groupe, prix, loyers, maison):
    # loyers : terrain nu, 1 a 4 maisons, hotel (= 5 "maisons")
    return {"type": "propriete", "nom": nom, "groupe": groupe,
            "prix": prix, "loyers": loyers, "maison": maison}


def _gare(nom):
    return {"type": "gare", "nom": nom, "groupe": "gare", "prix": 200}


def _compagnie(nom):
    return {"type": "compagnie", "nom": nom, "groupe": "compagnie", "prix": 150}


def _case(type_, nom, **extra):
    c = {"type": type_, "nom": nom}
    c.update(extra)
    return c


PP_CASES = [
    _case("depart", "Départ"),
    _prop("Club House", "marron", 60, [2, 10, 30, 90, 160, 250], 50),
    _case("caisse", "Caisse de communauté"),
    _prop("Le Palais de la Bière", "marron", 60, [4, 20, 60, 180, 320, 450], 50),
    _case("taxe", "Impôt sur le revenu", montant=200),
    _gare("Gare de Poitiers"),
    _prop("La Minute Blonde", "bleu_clair", 100, [6, 30, 90, 270, 400, 550], 50),
    _case("chance", "Chance"),
    _prop("Drop'N Shoot Café", "bleu_clair", 100, [6, 30, 90, 270, 400, 550], 50),
    _prop("Le Republic Corner", "bleu_clair", 120, [8, 40, 100, 300, 450, 600], 50),
    _case("prison", "Prison / Simple visite"),
    _prop("Wallabys", "rose", 140, [10, 50, 150, 450, 625, 750], 100),
    _compagnie("La Cave de la Grand Rue"),
    _prop("Chez Alphonse", "rose", 140, [10, 50, 150, 450, 625, 750], 100),
    _prop("Le Bar des Papas", "rose", 160, [12, 60, 180, 500, 700, 900], 100),
    _gare("Notre-Dame"),
    _prop("L'Est Ouest", "orange", 180, [14, 70, 200, 550, 750, 950], 100),
    _case("caisse", "Caisse de communauté"),
    _prop("Rémi's Pub", "orange", 180, [14, 70, 200, 550, 750, 950], 100),
    _prop("La Mie Câline", "orange", 200, [16, 80, 220, 600, 800, 1000], 100),
    _case("parc", "Parc Blossac"),
    _prop("L'Istanbul", "rouge", 220, [18, 90, 250, 700, 875, 1050], 150),
    _case("chance", "Chance"),
    _prop("Le CAP Ristobar", "rouge", 220, [18, 90, 250, 700, 875, 1050], 150),
    _prop("Le Baffalou", "rouge", 240, [20, 100, 300, 750, 925, 1100], 150),
    _gare("Palais de Justice"),
    _prop("L'Île au Tison / Guinguette Pictave", "jaune", 260, [22, 110, 330, 800, 975, 1150], 150),
    _prop("Au WC", "jaune", 260, [22, 110, 330, 800, 975, 1150], 150),
    _compagnie("V and B Poitiers"),
    _prop("Aux Heures Heureuses", "jaune", 280, [24, 120, 360, 850, 1025, 1200], 150),
    _case("allez_prison", "Allez en prison"),
    _prop("Chill Bar", "vert", 300, [26, 130, 390, 900, 1100, 1275], 200),
    _prop("Crafty Brewpub", "vert", 300, [26, 130, 390, 900, 1100, 1275], 200),
    _case("caisse", "Caisse de communauté"),
    _prop("Le Reza", "vert", 320, [28, 150, 450, 1000, 1200, 1400], 200),
    _gare("Hôtel de Ville"),
    _case("chance", "Chance"),
    _prop("Le Rooftop", "bleu_fonce", 350, [35, 175, 500, 1100, 1300, 1500], 200),
    _case("taxe", "Taxe de luxe", montant=100),
    _prop("Les 3 Boulevards", "bleu_fonce", 400, [50, 200, 600, 1400, 1700, 2000], 200),
]
assert len(PP_CASES) == 40
PP_CASE_PRISON = 10

# groupe -> liste des cases (proprietes, gares et compagnies)
PP_GROUPES = {}
for _i, _c in enumerate(PP_CASES):
    if _c["type"] in PP_ACHETABLES:
        PP_GROUPES.setdefault(_c["groupe"], []).append(_i)


# =============================================================
# CARTES
# Gains : surtout "distribuer", un "boire" contre-intuitif.
# Pertes : surtout "boire", un "distribuer" malgre la perte.
# Les gorgees d un gain / d une perte suivent la meme echelle que les
# loyers (pp_gorgees_pour). {n} dans un texte : le nombre de gorgees de
# la difficulte en cours (_texte_carte).
# =============================================================

PP_CHANCE = [
    {"id": "ch_depart", "effet": "aller", "case": 0,
     "texte": "Avancez jusqu'à la case Départ."},
    {"id": "ch_3bd", "effet": "aller", "case": 39,
     "texte": "Rendez-vous aux 3 Boulevards."},
    {"id": "ch_baffalou", "effet": "aller", "case": 24,
     "texte": "Rendez-vous au Baffalou. Si vous passez par la case Départ, recevez 200 €."},
    {"id": "ch_wallabys", "effet": "aller", "case": 11,
     "texte": "Rendez-vous au Wallabys. Si vous passez par la case Départ, recevez 200 €."},
    {"id": "ch_gare", "effet": "aller", "case": 15,
     "texte": "Prenez le bus jusqu'à " + PP_CASES[15]["nom"] + "."},
    {"id": "ch_recule", "effet": "reculer", "n": 3,
     "texte": "Reculez de trois cases."},
    {"id": "ch_prison", "effet": "prison",
     "texte": "Allez en prison. Ne passez pas par la case Départ."},
    {"id": "ch_sortie", "effet": "sortie_prison",
     "texte": "Vous êtes libéré de prison. Gardez cette carte."},
    {"id": "ch_dividende", "effet": "gain", "montant": 50, "mode": "distribuer",
     "texte": "La banque vous verse un dividende de 50 €. Offrez la gorgée à qui vous voulez."},
    {"id": "ch_immeuble", "effet": "gain", "montant": 150, "mode": "distribuer",
     "texte": "Votre immeuble vous rapporte 150 €. Distribuez vos gorgées."},
    {"id": "ch_flechettes", "effet": "gain", "montant": 100, "mode": "boire",
     "texte": "Vous gagnez le tournoi de fléchettes : 100 €. La tournée d'honneur est pour vous : buvez."},
    {"id": "ch_exces", "effet": "perte", "montant": 15, "mode": "boire",
     "texte": "Amende pour excès de vitesse : 15 €. Buvez."},
    {"id": "ch_reparations", "effet": "reparations", "maison": 25, "hotel": 100, "mode": "boire",
     "texte": "Réparations : 25 € par maison, 100 € par hôtel. Buvez à la facture."},
    {"id": "ch_tournee", "effet": "perte", "montant": 50, "mode": "distribuer",
     "texte": "Vous payez une tournée : 50 €. Au moins, vous choisissez qui trinque."},
    {"id": "ch_retour", "effet": "retour_baton",
     "texte": "Retour de bâton : un adversaire boit le double de votre dernière pénalité."},
    {"id": "ch_duel", "effet": "duel",
     "texte": "Duel : défiez un adversaire au dé. Le plus petit score boit {n} gorgées."},
    {"id": "ch_immunite", "effet": "immunite",
     "texte": "Immunité : vous ne boirez pas votre prochaine pénalité, quelle qu'elle soit."},
]

PP_CAISSE = [
    {"id": "ca_depart", "effet": "aller", "case": 0,
     "texte": "Avancez jusqu'à la case Départ."},
    {"id": "ca_prison", "effet": "prison",
     "texte": "Allez en prison. Ne passez pas par la case Départ."},
    {"id": "ca_sortie", "effet": "sortie_prison",
     "texte": "Vous êtes libéré de prison. Gardez cette carte."},
    {"id": "ca_erreur", "effet": "gain", "montant": 200, "mode": "distribuer",
     "texte": "Erreur de la banque en votre faveur : 200 €. Distribuez vos gorgées."},
    {"id": "ca_heritage", "effet": "gain", "montant": 100, "mode": "distribuer",
     "texte": "Vous héritez de 100 €. Distribuez vos gorgées."},
    {"id": "ca_stock", "effet": "gain", "montant": 50, "mode": "distribuer",
     "texte": "La vente de votre stock vous rapporte 50 €. Offrez la gorgée."},
    {"id": "ca_assurance", "effet": "gain", "montant": 100, "mode": "distribuer",
     "texte": "Votre assurance-vie vous rapporte 100 €. Distribuez vos gorgées."},
    {"id": "ca_beaute", "effet": "gain", "montant": 10, "mode": "boire",
     "texte": "Deuxième prix de beauté : 10 €. Buvez à votre santé."},
    {"id": "ca_hopital", "effet": "perte", "montant": 100, "mode": "boire",
     "texte": "Frais d'hôpital : 100 €. Buvez."},
    {"id": "ca_medecin", "effet": "perte", "montant": 50, "mode": "boire",
     "texte": "Visite chez le médecin : 50 €. Buvez."},
    {"id": "ca_scolarite", "effet": "perte", "montant": 50, "mode": "distribuer",
     "texte": "Frais de scolarité : 50 €. Consolez-vous en faisant boire quelqu'un."},
    {"id": "ca_voirie", "effet": "reparations", "maison": 40, "hotel": 115, "mode": "boire",
     "texte": "Travaux de voirie : 40 € par maison, 115 € par hôtel. Buvez à la facture."},
    {"id": "ca_distribution", "effet": "distribution_generale",
     "texte": "Distribution générale : tous vos adversaires boivent une gorgée."},
    {"id": "ca_cadeau", "effet": "cadeau", "montant": 100,
     "texte": "Cadeau empoisonné : la banque vous offre 100 €, mais vous buvez {n} gorgées."},
    {"id": "ca_sommelier", "effet": "sommelier",
     "texte": "Sommelier : répartissez {n} gorgées entre vos adversaires comme il vous plaît."},
]

PP_CARTES = {c["id"]: c for c in PP_CHANCE + PP_CAISSE}


# =============================================================
# OUTILS
# =============================================================

def pp_regle(etat):
    """Les reglages de gorgees de la difficulte de la partie."""
    return PP_DIFFICULTES[etat["difficulte"]]


def pp_gorgees_pour(etat, montant):
    """Gorgees pour un montant : 1 par tranche complete, au moins 1, plafonnees."""
    if montant <= 0:
        return 0
    r = pp_regle(etat)
    return min(r["plafond"], max(1, montant // r["tranche"]))


def _texte_carte(etat, carte):
    """{n} : les gorgees de la carte (duel, cadeau, sommelier) a cette difficulte."""
    if "{n}" not in carte["texte"]:
        return carte["texte"]
    return carte["texte"].replace("{n}", str(pp_regle(etat)[carte["effet"]]))


def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _nom(etat, i):
    if i is None:
        return "la banque"
    return etat["noms"][i] if i < len(etat["noms"]) else "?"


def _vivants(etat):
    return [i for i in range(etat["nb_joueurs"]) if not etat["faillite"][i]]


def _adversaires(etat, i, par_carte):
    """
    Adversaires ciblables par i. Une carte ne peut pas viser un joueur en
    prison : c est tout l interet d y rester (voir la spec).
    """
    return [j for j in _vivants(etat)
            if j != i and not (par_carte and etat["en_prison"][j])]


def _entier(v):
    try:
        if isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _nb_possedees(etat, joueur, groupe):
    return sum(1 for c in PP_GROUPES[groupe]
               if etat["proprietes"][c]["proprietaire"] == joueur)


def _groupe_complet(etat, joueur, groupe):
    return _nb_possedees(etat, joueur, groupe) == len(PP_GROUPES[groupe])


def _maisons_groupe(etat, groupe):
    return [etat["proprietes"][c]["maisons"] for c in PP_GROUPES[groupe]]


def pp_loyer(etat, case):
    """Loyer du a l arrivee sur une case possedee (0 si libre ou hypothequee)."""
    p = etat["proprietes"][case]
    o = p["proprietaire"]
    if o is None or p["hypotheque"]:
        return 0
    c = PP_CASES[case]
    if c["type"] == "propriete":
        if p["maisons"] > 0:
            return c["loyers"][p["maisons"]]
        base = c["loyers"][0]
        return base * 2 if _groupe_complet(etat, o, c["groupe"]) else base
    if c["type"] == "gare":
        return PP_LOYER_GARE * 2 ** (_nb_possedees(etat, o, "gare") - 1)
    des = etat.get("des") or {}
    somme = (des.get("d1") or 0) + (des.get("d2") or 0) or 7
    mult = PP_MULT_COMPAGNIE[1] if _nb_possedees(etat, o, "compagnie") == 2 else PP_MULT_COMPAGNIE[0]
    return mult * somme


def pp_patrimoine(etat, i):
    """Argent + valeur des proprietes (moitie si hypothequee) + constructions."""
    total = etat["argent"][i]
    for c, p in enumerate(etat["proprietes"]):
        if p is None or p["proprietaire"] != i:
            continue
        case = PP_CASES[c]
        total += case["prix"] // 2 if p["hypotheque"] else case["prix"]
        total += p["maisons"] * case.get("maison", 0)
    return total


# =============================================================
# INITIALISATION
# =============================================================

def pp_initialiser(noms, maintenant=None, difficulte=None):
    """
    Etat d une nouvelle partie. noms : prenoms dans l ordre des places.
    difficulte : cle de PP_DIFFICULTES ; toute autre valeur (absente, ou
    envoyee de travers par un client) donne la difficulte par defaut.
    """
    if maintenant is None:
        maintenant = time.time()
    if not isinstance(difficulte, str) or difficulte not in PP_DIFFICULTES:
        difficulte = PP_DIFFICULTE_DEFAUT
    nb = len(noms)
    chance = [c["id"] for c in PP_CHANCE]
    caisse = [c["id"] for c in PP_CAISSE]
    random.shuffle(chance)
    random.shuffle(caisse)
    etat = {
        "nb_joueurs": nb,
        "noms": list(noms),
        "argent": [PP_ARGENT_DEPART] * nb,
        "positions": [0] * nb,
        "en_prison": [False] * nb,
        "tours_prison": [0] * nb,
        "cartes_sortie": [[] for _ in range(nb)],   # paquet d origine de chaque carte
        "immunites": [0] * nb,
        "derniere_penalite": [0] * nb,
        "faillite": [False] * nb,
        "de_rapide": [False] * nb,                   # actif apres un passage au Depart
        "gorgees": [0] * nb,                         # total bu (hors cul sec)
        "partis": [False] * nb,                      # tenu a jour par serveur.py
        "proprietes": [
            {"proprietaire": None, "maisons": 0, "hypotheque": False}
            if c["type"] in PP_ACHETABLES else None
            for c in PP_CASES
        ],
        "courant": 0,
        "phase": "tour",
        "a_lance": False,
        "rejouer": False,
        "doubles": 0,
        "echanges_tour": 0,
        "roulette_jouee": False,
        "des": None,
        "attente": None,
        "dette": None,
        "suite": [],
        "paquets": {"chance": chance, "caisse": caisse},   # SECRET : jamais diffuse
        "debut": maintenant,
        "fin_prevue": maintenant + PP_DUREE_PARTIE,
        "echeance": None,
        "version": 0,
        "message": "La partie commence : à " + noms[0] + " de lancer les dés.",
        "classement": None,
        "cagnotte": 0,
        "difficulte": difficulte,
    }
    pp_armer(etat, maintenant)
    return etat


def pp_ajouter_joueur(etat, nom):
    """Un arrivant en pleine partie prend une place en fin de tour de table."""
    etat["nb_joueurs"] += 1
    etat["noms"].append(nom)
    etat["argent"].append(PP_ARGENT_DEPART)
    etat["positions"].append(0)
    etat["en_prison"].append(False)
    etat["tours_prison"].append(0)
    etat["cartes_sortie"].append([])
    etat["immunites"].append(0)
    etat["derniere_penalite"].append(0)
    etat["faillite"].append(False)
    etat["de_rapide"].append(False)
    etat["gorgees"].append(0)
    etat["partis"].append(False)
    etat["message"] = nom + " rejoint la partie."
    etat["version"] += 1


def pp_etat_public(etat, maintenant=None):
    """Tout est public sauf l ordre des paquets."""
    if maintenant is None:
        maintenant = time.time()
    echeance = etat.get("echeance")
    return {
        "type": "pp_etat",
        "joueurs": etat["noms"],
        "argent": etat["argent"],
        "positions": etat["positions"],
        "en_prison": etat["en_prison"],
        "tours_prison": etat["tours_prison"],
        "cartes_sortie": [len(c) for c in etat["cartes_sortie"]],
        "immunites": etat["immunites"],
        "faillite": etat["faillite"],
        "de_rapide": etat["de_rapide"],
        "gorgees": etat["gorgees"],
        "partis": etat["partis"],
        "proprietes": etat["proprietes"],
        "courant": etat["courant"],
        "phase": etat["phase"],
        "a_lance": etat["a_lance"],
        "roulette_jouee": etat["roulette_jouee"],
        "doubles": etat["doubles"],
        "des": etat["des"],
        "attente": etat["attente"],
        "dette": etat["dette"],
        "message": etat["message"],
        # Des durees et non des heures : l horloge des clients n est pas la notre.
        "temps_restant": max(0, int(etat["fin_prevue"] - maintenant)),
        "delai_restant": None if echeance is None else max(0, int(echeance - maintenant)),
        "classement": etat["classement"],
        "cagnotte": etat["cagnotte"],
        "difficulte": etat["difficulte"],
    }


# =============================================================
# BOIRE, PAYER, SE DEPLACER
# =============================================================

def _boire(etat, ev, liste, raison, cul_sec=False, volontaire=False):
    """
    liste : [(joueur, gorgees)]. L immunite annule la prochaine penalite,
    quelle qu en soit l origine -- cul sec de faillite compris (spec).
    Un seul message pour tout le groupe : un toast est UN evenement.
    volontaire : des gorgees qu on a choisi de boire (le prix de la
    cagnotte). Ce n est pas une penalite : l immunite ne s en mele pas --
    sinon elle rendrait la cagnotte gratuite -- et le "Retour de baton"
    ne les compte pas.
    """
    boivent = []
    for i, n in liste:
        if etat["faillite"][i] or (n <= 0 and not cul_sec):
            continue
        if etat["immunites"][i] > 0 and not volontaire:
            etat["immunites"][i] -= 1
            boivent.append({"joueur": i, "nombre": 0, "immunise": True, "cul_sec": False})
            continue
        boivent.append({"joueur": i, "nombre": n, "immunise": False, "cul_sec": cul_sec})
        if not cul_sec:
            etat["gorgees"][i] += n
            if not volontaire:
                etat["derniere_penalite"][i] = n
    if boivent:
        ev.append(_tous({"type": "pp_gorgees", "raison": raison, "boivent": boivent}))


def _payer(etat, ev, i, montant, creancier, motif):
    """Paie si possible ; sinon ouvre une dette que le joueur doit regler."""
    if montant <= 0:
        return
    if etat["argent"][i] >= montant:
        etat["argent"][i] -= montant
        if creancier is not None:
            etat["argent"][creancier] += montant
        elif motif in PP_MOTIFS_CAGNOTTE:
            etat["cagnotte"] += montant
        ev.append(_tous({"type": "pp_paiement", "de": i, "vers": creancier,
                         "montant": montant, "motif": motif}))
    else:
        etat["dette"] = {"joueur": i, "montant": montant, "creancier": creancier, "motif": motif}
        etat["message"] = (_nom(etat, i) + " doit " + str(montant) + " € à "
                           + _nom(etat, creancier) + " : vendez ou hypothéquez, ou déclarez faillite.")


def _avancer_vers(etat, ev, i, dest, motif):
    """Deplacement vers l avant. Passer (ou s arreter sur) le Depart paie et trinque."""
    pos = etat["positions"][i]
    etat["positions"][i] = dest
    ev.append(_tous({"type": "pp_deplacement", "joueur": i, "de": pos, "vers": dest,
                     "sens": 1, "motif": motif}))
    if dest < pos:
        _passer_depart(etat, ev, i)


def _passer_depart(etat, ev, i):
    etat["argent"][i] += PP_SALAIRE
    ev.append(_tous({"type": "pp_paiement", "de": None, "vers": i,
                     "montant": PP_SALAIRE, "motif": "depart"}))
    if not etat["de_rapide"][i]:
        etat["de_rapide"][i] = True
        ev.append(_tous({"type": "pp_de_rapide", "joueur": i}))
    qui = pp_regle(etat)["depart"]
    trinquent = _vivants(etat) if qui == "tous" else [i] if qui == "joueur" else []
    _boire(etat, ev, [(j, 1) for j in trinquent], "toast_depart")


def _envoyer_prison(etat, ev, i):
    """Finit le tour. Avec une carte de sortie en poche, le joueur choisit."""
    etat["suite"] = []
    etat["rejouer"] = False
    etat["doubles"] = 0
    if etat["cartes_sortie"][i]:
        etat["attente"] = {"type": "prison_carte", "joueur": i}
        etat["message"] = _nom(etat, i) + " va en prison... mais a une carte pour en sortir."
        return
    _emprisonner(etat, ev, i)


def _emprisonner(etat, ev, i):
    pos = etat["positions"][i]
    etat["positions"][i] = PP_CASE_PRISON
    etat["en_prison"][i] = True
    etat["tours_prison"][i] = 0
    ev.append(_tous({"type": "pp_deplacement", "joueur": i, "de": pos,
                     "vers": PP_CASE_PRISON, "sens": 0, "motif": "prison"}))
    etat["message"] = _nom(etat, i) + " va en prison."
    _boire(etat, ev, [(i, pp_regle(etat)["prison"])], "prison")


def _rendre_carte_sortie(etat, i):
    paquet = etat["cartes_sortie"][i].pop()
    cid = "ch_sortie" if paquet == "chance" else "ca_sortie"
    etat["paquets"][paquet].append(cid)


# =============================================================
# LA FILE DE RESOLUTION
# =============================================================

def _derouler(etat, ev):
    """Enchaine les etapes tant que rien ne bloque (choix, dette, fin)."""
    while (etat["phase"] == "resolution" and etat["attente"] is None
           and etat["dette"] is None):
        if not etat["suite"]:
            i = etat["courant"]
            if etat["rejouer"] and not etat["en_prison"][i]:
                etat["rejouer"] = False
                etat["phase"] = "tour"
                etat["message"] = "Double ! " + _nom(etat, i) + " relance."
            else:
                etat["rejouer"] = False
                etat["phase"] = "fin_tour"
            return
        etape = etat["suite"].pop(0)
        _executer(etat, ev, etape)


def _executer(etat, ev, etape):
    i = etat["courant"]
    t = etape["t"]
    if t == "arrivee":
        _arrivee(etat, ev)
    elif t == "avancer":
        _avancer_vers(etat, ev, i, (etat["positions"][i] + etape["n"]) % 40, "des")
        etat["suite"].insert(0, {"t": "arrivee"})
    elif t == "payer":
        _payer(etat, ev, i, etape["montant"], etape["creancier"], etape["motif"])
    elif t == "miroir":
        o = etape["proprio"]
        if not etat["faillite"][o]:
            _demander_cible(etat, o, "miroir", pp_regle(etat)["miroir"], par_carte=False)
    elif t == "monopoly":
        _monsieur_monopoly(etat, ev, i)


def _arrivee(etat, ev):
    i = etat["courant"]
    c = etat["positions"][i]
    case = PP_CASES[c]
    t = case["type"]
    if t in PP_ACHETABLES:
        p = etat["proprietes"][c]
        o = p["proprietaire"]
        if o is None:
            if etat["argent"][i] >= case["prix"]:
                etat["attente"] = {"type": "achat", "joueur": i, "case": c, "prix": case["prix"]}
                etat["message"] = _nom(etat, i) + " peut acheter " + case["nom"] + " (" + str(case["prix"]) + " €)."
            else:
                etat["message"] = _nom(etat, i) + " n'a pas de quoi acheter " + case["nom"] + "."
        elif o != i and not p["hypotheque"]:
            loyer = pp_loyer(etat, c)
            etat["message"] = _nom(etat, i) + " paie " + str(loyer) + " € de loyer à " + _nom(etat, o) + "."
            _boire(etat, ev, [(i, pp_gorgees_pour(etat, loyer))], "loyer")
            etapes = [{"t": "payer", "montant": loyer, "creancier": o, "motif": "loyer"}]
            if pp_regle(etat)["miroir"] > 0:
                etapes.append({"t": "miroir", "proprio": o})
            etat["suite"][0:0] = etapes
    elif t == "taxe":
        etat["message"] = _nom(etat, i) + " paie " + case["nom"] + " : " + str(case["montant"]) + " €."
        _boire(etat, ev, [(i, pp_gorgees_pour(etat, case["montant"]))], "taxe")
        etat["suite"].insert(0, {"t": "payer", "montant": case["montant"],
                                 "creancier": None, "motif": "taxe"})
    elif t in ("chance", "caisse"):
        _piocher(etat, ev, i, t)
    elif t == "allez_prison":
        _envoyer_prison(etat, ev, i)
    elif t == "parc" and etat["cagnotte"] > 0:
        montant = etat["cagnotte"]
        etat["attente"] = {"type": "parc", "joueur": i, "montant": montant,
                           "gorgees": _gorgees_cagnotte(montant)}
        etat["message"] = (_nom(etat, i) + " peut rafler la cagnotte : " + str(montant)
                           + " € contre " + str(_gorgees_cagnotte(montant)) + " gorgée(s).")


def _gorgees_cagnotte(montant):
    """1 gorgee par tranche de 50 EUR, sans plafond (au moins 1)."""
    return max(1, montant // PP_CAGNOTTE_PAR_GORGEE)


def _monsieur_monopoly(etat, ev, i):
    """
    De rapide, symbole Monopoly : avancer jusqu a la prochaine propriete
    libre ; s il n y en a plus, jusqu a la prochaine ou l on doit payer.
    """
    pos = etat["positions"][i]
    ordre = [(pos + k) % 40 for k in range(1, 40)]
    dest = next((c for c in ordre if etat["proprietes"][c] is not None
                 and etat["proprietes"][c]["proprietaire"] is None), None)
    if dest is None:
        dest = next((c for c in ordre if etat["proprietes"][c] is not None
                     and etat["proprietes"][c]["proprietaire"] not in (None, i)
                     and not etat["proprietes"][c]["hypotheque"]), None)
    if dest is None:
        etat["message"] = "Monsieur Monopoly ne trouve rien à visiter."
        return
    _avancer_vers(etat, ev, i, dest, "monopoly")
    etat["suite"].insert(0, {"t": "arrivee"})


def _demander_cible(etat, qui, raison, nombre, par_carte=True):
    cibles = _adversaires(etat, qui, par_carte)
    if not cibles:
        etat["message"] = "Personne à cibler : tout le monde est à l'abri."
        return
    etat["attente"] = {"type": "cible", "joueur": qui, "raison": raison,
                       "nombre": nombre, "cibles": cibles}


def _piocher(etat, ev, i, paquet):
    ids = etat["paquets"][paquet]
    cid = ids.pop(0)
    carte = PP_CARTES[cid]
    effet = carte["effet"]
    if effet != "sortie_prison":
        ids.append(cid)     # remise sous le paquet
    texte = _texte_carte(etat, carte)
    ev.append(_tous({"type": "pp_carte", "joueur": i, "paquet": paquet,
                     "carte": {"id": cid, "effet": effet, "texte": texte}}))
    etat["message"] = _nom(etat, i) + " : " + texte

    if effet == "aller":
        _avancer_vers(etat, ev, i, carte["case"], "carte")
        etat["suite"].insert(0, {"t": "arrivee"})
    elif effet == "reculer":
        pos = etat["positions"][i]
        dest = (pos - carte["n"]) % 40
        etat["positions"][i] = dest
        ev.append(_tous({"type": "pp_deplacement", "joueur": i, "de": pos, "vers": dest,
                         "sens": -1, "motif": "carte"}))
        etat["suite"].insert(0, {"t": "arrivee"})
    elif effet == "prison":
        _envoyer_prison(etat, ev, i)
    elif effet == "sortie_prison":
        etat["cartes_sortie"][i].append(paquet)
    elif effet == "gain":
        etat["argent"][i] += carte["montant"]
        g = pp_gorgees_pour(etat, carte["montant"])
        if carte["mode"] == "boire":
            _boire(etat, ev, [(i, g)], "carte")
        else:
            _demander_cible(etat, i, "distribuer", g)
    elif effet in ("perte", "reparations"):
        if effet == "perte":
            montant = carte["montant"]
        else:
            montant = 0
            for p in etat["proprietes"]:
                if p is not None and p["proprietaire"] == i:
                    montant += carte["hotel"] if p["maisons"] == 5 else p["maisons"] * carte["maison"]
        g = pp_gorgees_pour(etat, montant)
        etat["suite"].insert(0, {"t": "payer", "montant": montant,
                                 "creancier": None, "motif": "carte"})
        if carte["mode"] == "boire":
            _boire(etat, ev, [(i, g)], "carte")
        elif g > 0:
            _demander_cible(etat, i, "distribuer", g)
    elif effet == "distribution_generale":
        cibles = _adversaires(etat, i, par_carte=True)
        _boire(etat, ev, [(j, PP_GORGEES_DISTRIBUTION) for j in cibles], "distribution")
    elif effet == "retour_baton":
        dernier = etat["derniere_penalite"][i]
        _demander_cible(etat, i, "retour", 2 * dernier if dernier > 0 else PP_RETOUR_DEFAUT)
    elif effet == "duel":
        _demander_cible(etat, i, "duel", pp_regle(etat)["duel"])
    elif effet == "cadeau":
        etat["argent"][i] += carte["montant"]
        _boire(etat, ev, [(i, pp_regle(etat)["cadeau"])], "cadeau")
    elif effet == "immunite":
        etat["immunites"][i] += 1
    elif effet == "sommelier":
        cibles = _adversaires(etat, i, par_carte=True)
        if cibles:
            etat["attente"] = {"type": "sommelier", "joueur": i,
                               "total": pp_regle(etat)["sommelier"], "cibles": cibles}
        else:
            etat["message"] = "Personne à servir : tout le monde est à l'abri."


# =============================================================
# ACTIONS DES JOUEURS
# Chaque fonction renvoie un message d erreur, ou None si l action est
# acceptee. Elles valident TOUT avant de modifier quoi que ce soit.
# =============================================================

def _libre(etat):
    return etat["attente"] is None and etat["dette"] is None


def _lancer(etat, ev, i, message=None):
    if etat["phase"] != "tour" or etat["courant"] != i or not _libre(etat):
        return "Ce n'est pas le moment de lancer."
    etat["a_lance"] = True
    etat["phase"] = "resolution"
    etat["rejouer"] = False
    d1, d2 = random.randint(1, 6), random.randint(1, 6)

    if etat["en_prison"][i]:
        # En prison le de rapide ne compte pas : seuls les deux des blancs.
        etat["des"] = {"d1": d1, "d2": d2, "rapide": None}
        ev.append(_tous({"type": "pp_des", "joueur": i, "d1": d1, "d2": d2, "rapide": None}))
        if d1 == d2:
            etat["en_prison"][i] = False
            etat["tours_prison"][i] = 0
            etat["message"] = _nom(etat, i) + " fait un double et sort de prison."
            etat["suite"] = [{"t": "avancer", "n": d1 + d2}]
        else:
            etat["tours_prison"][i] += 1
            if etat["tours_prison"][i] >= PP_ESSAIS_PRISON:
                etat["en_prison"][i] = False
                etat["tours_prison"][i] = 0
                etat["message"] = _nom(etat, i) + " paie " + str(PP_AMENDE_PRISON) + " € et sort de prison."
                etat["suite"] = [{"t": "payer", "montant": PP_AMENDE_PRISON,
                                  "creancier": None, "motif": "prison"},
                                 {"t": "avancer", "n": d1 + d2}]
            else:
                etat["message"] = _nom(etat, i) + " reste en prison."
                etat["suite"] = []
        _derouler(etat, ev)
        return None

    rapide = random.choice(PP_DE_RAPIDE) if etat["de_rapide"][i] else None
    etat["des"] = {"d1": d1, "d2": d2, "rapide": rapide}
    ev.append(_tous({"type": "pp_des", "joueur": i, "d1": d1, "d2": d2, "rapide": rapide}))
    pos = etat["positions"][i]

    if isinstance(rapide, int) and d1 == d2 == rapide:
        # Triple : n importe ou sur le plateau, et le tour s arrete la.
        etat["doubles"] = 0
        etat["attente"] = {"type": "deplacement", "joueur": i, "raison": "triple",
                           "options": list(range(40))}
        etat["message"] = "Triple ! " + _nom(etat, i) + " va où il veut."
        return None

    if d1 == d2:
        etat["doubles"] += 1
        if etat["doubles"] >= 3:
            etat["message"] = "Trois doubles d'affilée : " + _nom(etat, i) + " va en prison."
            _envoyer_prison(etat, ev, i)
            _derouler(etat, ev)
            return None
        etat["rejouer"] = True

    if rapide == "bus":
        if PP_BUS_CASE_LIBRE:
            options = list(range(40))
        else:
            options = sorted({(pos + d1) % 40, (pos + d2) % 40, (pos + d1 + d2) % 40})
        etat["attente"] = {"type": "deplacement", "joueur": i, "raison": "bus", "options": options}
        etat["message"] = "Bus ! " + _nom(etat, i) + " choisit sa case."
        return None

    n = d1 + d2 + (rapide if isinstance(rapide, int) else 0)
    etat["suite"] = [{"t": "avancer", "n": n}]
    if rapide == "monopoly":
        etat["suite"].append({"t": "monopoly"})
    _derouler(etat, ev)
    return None


def _terminer(etat, ev, i, message=None):
    if etat["phase"] != "fin_tour" or etat["courant"] != i or not _libre(etat):
        return "Vous ne pouvez pas terminer votre tour maintenant."
    _tour_suivant(etat, ev)
    return None


def _tour_suivant(etat, ev):
    """Main au prochain joueur en jeu et present (un absent est saute)."""
    n = etat["nb_joueurs"]
    c = etat["courant"]
    candidats = [(c + k) % n for k in range(1, n + 1)]
    suivant = next((j for j in candidats
                    if not etat["faillite"][j] and not etat["partis"][j]), None)
    if suivant is None:
        suivant = next((j for j in candidats if not etat["faillite"][j]), c)
    etat["courant"] = suivant
    etat["phase"] = "tour"
    etat["a_lance"] = False
    etat["rejouer"] = False
    etat["doubles"] = 0
    etat["echanges_tour"] = 0
    etat["roulette_jouee"] = False
    etat["suite"] = []
    etat["message"] = "À " + _nom(etat, suivant) + " de jouer."
    ev.append(_tous({"type": "pp_tour", "joueur": suivant}))


def _prison_payer(etat, ev, i, message=None):
    if (etat["phase"] != "tour" or etat["courant"] != i or not _libre(etat)
            or not etat["en_prison"][i] or etat["a_lance"]):
        return "Vous ne pouvez pas payer votre sortie maintenant."
    if etat["argent"][i] < PP_AMENDE_PRISON:
        return "Pas assez d'argent pour payer la sortie."
    _payer(etat, ev, i, PP_AMENDE_PRISON, None, "prison")
    etat["en_prison"][i] = False
    etat["tours_prison"][i] = 0
    etat["message"] = _nom(etat, i) + " paie et sort de prison."
    return None


def _prison_carte(etat, ev, i, message=None):
    if (etat["phase"] != "tour" or etat["courant"] != i or not _libre(etat)
            or not etat["en_prison"][i] or etat["a_lance"]):
        return "Vous ne pouvez pas utiliser la carte maintenant."
    if not etat["cartes_sortie"][i]:
        return "Vous n'avez pas de carte de sortie."
    _rendre_carte_sortie(etat, i)
    etat["en_prison"][i] = False
    etat["tours_prison"][i] = 0
    etat["message"] = _nom(etat, i) + " utilise sa carte et sort de prison."
    return None


# --- Gestion : maisons et hypotheques -------------------------

def _peut_gerer(etat, i, en_dette_aussi):
    if etat["attente"] is not None:
        return False
    if etat["dette"] is not None:
        return en_dette_aussi and etat["dette"]["joueur"] == i
    return etat["phase"] in ("tour", "fin_tour") and etat["courant"] == i


def _case_a_moi(etat, i, message, types=PP_ACHETABLES):
    c = _entier(message.get("case"))
    if c is None or not 0 <= c < 40 or PP_CASES[c]["type"] not in types:
        return None
    if etat["proprietes"][c]["proprietaire"] != i:
        return None
    return c


def _construire(etat, ev, i, message):
    if not _peut_gerer(etat, i, False):
        return "Vous ne pouvez construire que pendant votre tour."
    c = _case_a_moi(etat, i, message, ("propriete",))
    if c is None:
        return "Cette propriété n'est pas à vous."
    case = PP_CASES[c]
    g = case["groupe"]
    p = etat["proprietes"][c]
    if not _groupe_complet(etat, i, g):
        return "Il faut posséder tout le groupe pour construire."
    if any(etat["proprietes"][k]["hypotheque"] for k in PP_GROUPES[g]):
        return "Levez d'abord les hypothèques du groupe."
    if p["maisons"] >= 5:
        return "Il y a déjà un hôtel."
    if p["maisons"] > min(_maisons_groupe(etat, g)):
        return "Construisez d'abord sur les autres cases du groupe."
    if etat["argent"][i] < case["maison"]:
        return "Pas assez d'argent."
    etat["argent"][i] -= case["maison"]
    p["maisons"] += 1
    ev.append(_tous({"type": "pp_construction", "joueur": i, "case": c, "maisons": p["maisons"]}))
    etat["message"] = _nom(etat, i) + (" pose un hôtel sur " if p["maisons"] == 5 else " construit sur ") + case["nom"] + "."
    return None


def _vendre(etat, ev, i, message):
    if not _peut_gerer(etat, i, True):
        return "Vous ne pouvez pas vendre maintenant."
    c = _case_a_moi(etat, i, message, ("propriete",))
    if c is None:
        return "Cette propriété n'est pas à vous."
    case = PP_CASES[c]
    p = etat["proprietes"][c]
    if p["maisons"] == 0:
        return "Rien à vendre sur cette case."
    if p["maisons"] < max(_maisons_groupe(etat, case["groupe"])):
        return "Vendez d'abord sur les autres cases du groupe."
    p["maisons"] -= 1
    etat["argent"][i] += case["maison"] // 2
    ev.append(_tous({"type": "pp_construction", "joueur": i, "case": c, "maisons": p["maisons"]}))
    etat["message"] = _nom(etat, i) + " revend une construction sur " + case["nom"] + "."
    _verifier_dette(etat, ev)
    return None


def _hypothequer(etat, ev, i, message):
    if not _peut_gerer(etat, i, True):
        return "Vous ne pouvez pas hypothéquer maintenant."
    c = _case_a_moi(etat, i, message)
    if c is None:
        return "Cette propriété n'est pas à vous."
    case = PP_CASES[c]
    p = etat["proprietes"][c]
    if p["hypotheque"]:
        return "Déjà hypothéquée."
    if case["type"] == "propriete" and any(_maisons_groupe(etat, case["groupe"])):
        return "Vendez d'abord les constructions du groupe."
    p["hypotheque"] = True
    etat["argent"][i] += case["prix"] // 2
    ev.append(_tous({"type": "pp_hypotheque", "joueur": i, "case": c, "hypotheque": True}))
    etat["message"] = _nom(etat, i) + " hypothèque " + case["nom"] + "."
    _verifier_dette(etat, ev)
    return None


def pp_cout_levee(case):
    moitie = PP_CASES[case]["prix"] // 2
    return (moitie * (100 + PP_INTERET_LEVEE) + 99) // 100


def _lever(etat, ev, i, message):
    if not _peut_gerer(etat, i, False):
        return "Vous ne pouvez lever une hypothèque que pendant votre tour."
    c = _case_a_moi(etat, i, message)
    if c is None:
        return "Cette propriété n'est pas à vous."
    p = etat["proprietes"][c]
    if not p["hypotheque"]:
        return "Cette propriété n'est pas hypothéquée."
    cout = pp_cout_levee(c)
    if etat["argent"][i] < cout:
        return "Pas assez d'argent."
    etat["argent"][i] -= cout
    p["hypotheque"] = False
    ev.append(_tous({"type": "pp_hypotheque", "joueur": i, "case": c, "hypotheque": False}))
    etat["message"] = _nom(etat, i) + " lève l'hypothèque de " + PP_CASES[c]["nom"] + "."
    return None


# --- Roulette ------------------------------------------------

def _libelle_pari(pari, numero):
    if pari == "douzaine":
        return "la " + ("1re" if numero == 1 else str(numero) + "e") + " douzaine"
    if pari == "plein":
        return "le " + str(numero)
    return pari


def _pari_gagne(pari, numero, tirage):
    if pari == "plein":
        return tirage == numero
    if tirage == 0:
        return False      # le zero fait perdre tous les paris simples
    if pari == "rouge":
        return tirage in PP_ROULETTE_ROUGES
    if pari == "noir":
        return tirage not in PP_ROULETTE_ROUGES
    if pari == "pair":
        return tirage % 2 == 0
    if pari == "impair":
        return tirage % 2 == 1
    return (tirage - 1) // 12 + 1 == numero


def _roulette(etat, ev, i, message):
    """
    Une mise par tour, avant ou apres le lancer. Les gorgees sont le prix
    d entree, choisi : volontaires, comme celles de la cagnotte.
    """
    if not _peut_gerer(etat, i, False):
        return "La roulette ne tourne que pendant votre tour."
    if etat["roulette_jouee"]:
        return "Une seule mise par tour."
    pari = message.get("pari")
    if not isinstance(pari, str) or pari not in PP_ROULETTE_GAINS:
        return "Pari invalide."
    numero = None
    if pari in ("douzaine", "plein"):
        numero = _entier(message.get("numero"))
        haut = 3 if pari == "douzaine" else 36
        bas = 1 if pari == "douzaine" else 0
        if numero is None or not bas <= numero <= haut:
            return "Numéro invalide."
    mise = _entier(message.get("mise"))
    if mise is None or mise < PP_ROULETTE_MISE or mise % PP_ROULETTE_MISE != 0:
        return "Misez par tranches de " + str(PP_ROULETTE_MISE) + " €."
    if mise > etat["argent"][i]:
        return "Pas assez d'argent pour cette mise."

    etat["roulette_jouee"] = True
    etat["argent"][i] -= mise
    _boire(etat, ev, [(i, mise // PP_ROULETTE_MISE)], "roulette", volontaire=True)
    tirage = random.randint(0, 36)
    gagne = _pari_gagne(pari, numero, tirage)
    gain = mise * PP_ROULETTE_GAINS[pari] if gagne else 0
    if gagne:
        etat["argent"][i] += gain
    else:
        etat["cagnotte"] += mise
    ev.append(_tous({"type": "pp_roulette", "joueur": i, "mise": mise, "pari": pari,
                     "numero": numero, "tirage": tirage, "gagne": gagne, "gain": gain}))
    etat["message"] = (_nom(etat, i) + " mise " + str(mise) + " € sur " + _libelle_pari(pari, numero)
                       + " : le " + str(tirage) + " sort. "
                       + ("Gagné, " + str(gain) + " € !" if gagne else "Perdu, la mise file dans la cagnotte."))
    return None


# --- Dette et faillite ----------------------------------------

def _verifier_dette(etat, ev):
    """Des que le debiteur a de quoi payer, on paie et la partie reprend."""
    d = etat["dette"]
    if d is None or etat["argent"][d["joueur"]] < d["montant"]:
        return
    etat["dette"] = None
    _payer(etat, ev, d["joueur"], d["montant"], d["creancier"], d["motif"])
    _derouler(etat, ev)


def _declarer_faillite(etat, ev, i, message=None):
    d = etat["dette"]
    if d is None or d["joueur"] != i:
        return "Vous n'êtes pas en dette."
    _faillite(etat, ev, i, d["creancier"])
    return None


def _faillite(etat, ev, i, creancier):
    # Les constructions sont revendues a la banque d abord.
    for c, p in enumerate(etat["proprietes"]):
        if p is not None and p["proprietaire"] == i and p["maisons"]:
            etat["argent"][i] += p["maisons"] * (PP_CASES[c]["maison"] // 2)
            p["maisons"] = 0
    _boire(etat, ev, [(i, 0)], "faillite", cul_sec=True)

    vers_joueur = creancier is not None and not etat["faillite"][creancier]
    if vers_joueur:
        etat["argent"][creancier] += etat["argent"][i]
    for p in etat["proprietes"]:
        if p is not None and p["proprietaire"] == i:
            if vers_joueur:
                p["proprietaire"] = creancier
            else:
                # Pas d encheres : ce qui revient a la banque redevient libre.
                p["proprietaire"] = None
                p["hypotheque"] = False
    while etat["cartes_sortie"][i]:
        if vers_joueur:
            etat["cartes_sortie"][creancier].append(etat["cartes_sortie"][i].pop())
        else:
            _rendre_carte_sortie(etat, i)

    etat["argent"][i] = 0
    etat["faillite"][i] = True
    etat["en_prison"][i] = False
    etat["dette"] = None
    ev.append(_tous({"type": "pp_faillite", "joueur": i, "creancier": creancier}))
    etat["message"] = _nom(etat, i) + " fait faillite : cul sec !"

    if len(_vivants(etat)) <= 1:
        _fin(etat, ev, "faillites")
        return
    if etat["courant"] == i:
        etat["attente"] = None
        _tour_suivant(etat, ev)


def _liquider(etat, ev, i):
    """Par defaut, pour un debiteur qui ne repond pas : on vend tout ce qu il faut."""
    while etat["dette"] is not None:
        ventes = [c for c, p in enumerate(etat["proprietes"])
                  if p is not None and p["proprietaire"] == i and p["maisons"] > 0
                  and p["maisons"] == max(_maisons_groupe(etat, PP_CASES[c]["groupe"]))]
        if ventes:
            _vendre(etat, ev, i, {"case": ventes[0]})
            continue
        hypos = [c for c, p in enumerate(etat["proprietes"])
                 if p is not None and p["proprietaire"] == i and not p["hypotheque"]]
        if hypos:
            _hypothequer(etat, ev, i, {"case": hypos[0]})
            continue
        _faillite(etat, ev, i, etat["dette"]["creancier"])
        return


def _fin(etat, ev, raison):
    classement = sorted(
        ({"joueur": i, "patrimoine": pp_patrimoine(etat, i), "faillite": etat["faillite"][i]}
         for i in range(etat["nb_joueurs"])),
        key=lambda e: (not e["faillite"], e["patrimoine"]), reverse=True)
    etat["phase"] = "fin"
    etat["attente"] = None
    etat["dette"] = None
    etat["suite"] = []
    etat["echeance"] = None
    etat["classement"] = classement
    gagnant = classement[0]["joueur"]
    etat["message"] = ("Temps écoulé ! " if raison == "chrono" else "") + _nom(etat, gagnant) + " remporte la partie."
    ev.append(_tous({"type": "pp_fin", "raison": raison, "gagnant": gagnant, "classement": classement}))


# --- Echanges -------------------------------------------------

def _lire_echange(etat, i, message):
    """Normalise et valide une proposition. Renvoie (proposition, erreur)."""
    cible = _entier(message.get("cible"))
    if cible is None or not 0 <= cible < etat["nb_joueurs"] or cible == i or etat["faillite"][cible]:
        return None, "Partenaire d'échange invalide."
    if etat["partis"][cible]:
        return None, "Ce joueur est absent."
    donne, recoit = message.get("donne") or [], message.get("recoit") or []
    if not isinstance(donne, list) or not isinstance(recoit, list):
        return None, "Proposition invalide."
    donne = [_entier(c) for c in donne]
    recoit = [_entier(c) for c in recoit]
    a_donne = _entier(message.get("argent_donne") or 0)
    a_recu = _entier(message.get("argent_recu") or 0)
    if None in donne or None in recoit or a_donne is None or a_recu is None:
        return None, "Proposition invalide."
    if len(set(donne)) != len(donne) or len(set(recoit)) != len(recoit):
        return None, "Proposition invalide."
    if not (donne or recoit or a_donne or a_recu):
        return None, "Proposition vide."
    if a_donne < 0 or a_recu < 0:
        return None, "Montant invalide."
    for cases, proprio in ((donne, i), (recoit, cible)):
        for c in cases:
            if not 0 <= c < 40 or etat["proprietes"][c] is None:
                return None, "Case invalide."
            if etat["proprietes"][c]["proprietaire"] != proprio:
                return None, PP_CASES[c]["nom"] + " n'appartient pas à " + _nom(etat, proprio) + "."
            if PP_CASES[c]["type"] == "propriete" and any(_maisons_groupe(etat, PP_CASES[c]["groupe"])):
                return None, "Vendez d'abord les constructions du groupe de " + PP_CASES[c]["nom"] + "."
    if a_donne > etat["argent"][i] or a_recu > etat["argent"][cible]:
        return None, "Pas assez d'argent pour cet échange."
    return {"proposant": i, "cible": cible, "donne": donne, "recoit": recoit,
            "argent_donne": a_donne, "argent_recu": a_recu}, None


def _proposer_echange(etat, ev, i, message):
    if etat["phase"] != "tour" or etat["courant"] != i or etat["a_lance"] or not _libre(etat):
        return "On ne propose un échange qu'au début de son tour."
    if etat["echanges_tour"] >= PP_ECHANGES_PAR_TOUR:
        return "Assez d'échanges pour ce tour."
    prop, err = _lire_echange(etat, i, message)
    if err:
        return err
    etat["echanges_tour"] += 1
    etat["attente"] = {"type": "echange", "joueur": prop["cible"], "proposition": prop}
    etat["message"] = _nom(etat, i) + " propose un échange à " + _nom(etat, prop["cible"]) + "."
    return None


def _conclure_echange(etat, ev, prop):
    i, cible = prop["proposant"], prop["cible"]
    # Rien n a pu bouger depuis la proposition (la table attendait), mais
    # on revalide : ce qui part ici est de l argent et des titres.
    _, err = _lire_echange(etat, i, prop)
    if err:
        etat["message"] = "Échange annulé : " + err
        return
    for c in prop["donne"]:
        etat["proprietes"][c]["proprietaire"] = cible
    for c in prop["recoit"]:
        etat["proprietes"][c]["proprietaire"] = i
    etat["argent"][i] += prop["argent_recu"] - prop["argent_donne"]
    etat["argent"][cible] += prop["argent_donne"] - prop["argent_recu"]
    ev.append(_tous({"type": "pp_echange", "accepte": True, "proposition": prop}))
    etat["message"] = "Marché conclu entre " + _nom(etat, i) + " et " + _nom(etat, cible) + " : tournée générale !"
    _boire(etat, ev, [(j, pp_regle(etat)["echange"]) for j in _vivants(etat)], "toast_echange")


# --- Reponse a un choix ---------------------------------------

def _choix(etat, ev, i, message):
    a = etat["attente"]
    if a is None or a["joueur"] != i:
        return "Aucun choix ne vous est demandé."
    v = message.get("valeur")
    t = a["type"]

    # Validation d abord, pour ne rien consommer sur une reponse invalide.
    if t in ("achat", "prison_carte", "echange", "parc"):
        if not isinstance(v, bool):
            return "Réponse attendue : oui ou non."
    elif t == "cible":
        v = _entier(v)
        if v not in a["cibles"]:
            return "Cible invalide."
    elif t == "deplacement":
        v = _entier(v)
        if v not in a["options"]:
            return "Case invalide."
    elif t == "sommelier":
        if not isinstance(v, dict):
            return "Répartition invalide."
        repartition = {}
        for k, n in v.items():
            k, n = _entier(k), _entier(n)
            if k not in a["cibles"] or n is None or n < 0:
                return "Répartition invalide."
            repartition[k] = repartition.get(k, 0) + n
        if sum(repartition.values()) != a["total"]:
            return "Il faut répartir exactement " + str(a["total"]) + " gorgées."
        v = repartition

    etat["attente"] = None
    if t == "achat":
        c = a["case"]
        if v and etat["argent"][i] >= a["prix"]:
            etat["argent"][i] -= a["prix"]
            etat["proprietes"][c]["proprietaire"] = i
            ev.append(_tous({"type": "pp_achat", "joueur": i, "case": c, "prix": a["prix"]}))
            etat["message"] = _nom(etat, i) + " achète " + PP_CASES[c]["nom"] + "."
        else:
            etat["message"] = _nom(etat, i) + " laisse passer " + PP_CASES[c]["nom"] + "."
    elif t == "cible":
        if a["raison"] == "duel":
            _duel(etat, ev, i, v, a["nombre"])
        else:
            etat["message"] = _nom(etat, i) + " fait boire " + _nom(etat, v) + "."
            _boire(etat, ev, [(v, a["nombre"])], a["raison"])
    elif t == "sommelier":
        etat["message"] = _nom(etat, i) + " fait le service."
        _boire(etat, ev, sorted(v.items()), "sommelier")
    elif t == "deplacement":
        _avancer_vers(etat, ev, i, v, a["raison"])
        etat["suite"].insert(0, {"t": "arrivee"})
    elif t == "prison_carte":
        if v:
            _rendre_carte_sortie(etat, i)
            pos = etat["positions"][i]
            etat["positions"][i] = PP_CASE_PRISON     # simple visite
            ev.append(_tous({"type": "pp_deplacement", "joueur": i, "de": pos,
                             "vers": PP_CASE_PRISON, "sens": 0, "motif": "carte_sortie"}))
            etat["message"] = _nom(etat, i) + " sort sa carte : ni prison, ni gorgée."
        else:
            _emprisonner(etat, ev, i)
    elif t == "echange":
        if v:
            _conclure_echange(etat, ev, a["proposition"])
        else:
            ev.append(_tous({"type": "pp_echange", "accepte": False, "proposition": a["proposition"]}))
            etat["message"] = _nom(etat, i) + " refuse l'échange."
    elif t == "parc":
        if v:
            montant = etat["cagnotte"]
            gorgees = _gorgees_cagnotte(montant)
            etat["argent"][i] += montant
            etat["cagnotte"] = 0
            ev.append(_tous({"type": "pp_cagnotte", "joueur": i, "montant": montant, "gorgees": gorgees}))
            etat["message"] = _nom(etat, i) + " rafle la cagnotte : " + str(montant) + " €."
            _boire(etat, ev, [(i, gorgees)], "cagnotte", volontaire=True)
        else:
            etat["message"] = _nom(etat, i) + " laisse la cagnotte."

    _derouler(etat, ev)
    return None


def _duel(etat, ev, i, cible, nombre):
    lancers = []
    while True:
        a, b = random.randint(1, 6), random.randint(1, 6)
        lancers.append([a, b])
        if a != b:
            break
    perdant = i if a < b else cible
    ev.append(_tous({"type": "pp_duel", "joueur": i, "cible": cible,
                     "lancers": lancers, "perdant": perdant}))
    etat["message"] = "Duel : " + _nom(etat, perdant) + " perd et boit."
    _boire(etat, ev, [(perdant, nombre)], "duel")


def _valeur_defaut(etat, a):
    t = a["type"]
    if t in ("achat", "echange", "parc"):
        return False
    if t == "prison_carte":
        return True
    if t == "cible":
        return random.choice(a["cibles"])
    if t == "deplacement":
        return random.choice(a["options"])
    if t == "sommelier":
        rep = {}
        for _ in range(a["total"]):
            k = random.choice(a["cibles"])
            rep[k] = rep.get(k, 0) + 1
        return rep
    return None


# =============================================================
# POINTS D ENTREE (appeles par serveur.py)
# =============================================================

PP_ACTIONS = {
    "pp_lancer": _lancer,
    "pp_terminer": _terminer,
    "pp_choix": _choix,
    "pp_construire": _construire,
    "pp_vendre": _vendre,
    "pp_hypothequer": _hypothequer,
    "pp_lever": _lever,
    "pp_prison_payer": _prison_payer,
    "pp_prison_carte": _prison_carte,
    "pp_proposer_echange": _proposer_echange,
    "pp_faillite": _declarer_faillite,
    "pp_roulette": _roulette,
}


def pp_action(etat, i, message, maintenant=None):
    """
    Applique l intention du joueur i. Renvoie les messages a envoyer.
    Une action refusee ne touche pas l etat et renvoie une erreur privee ;
    etat["version"] n augmente que si quelque chose a change.
    """
    if maintenant is None:
        maintenant = time.time()
    ev = []
    if etat["phase"] == "fin":
        return ev
    if maintenant >= etat["fin_prevue"]:
        _fin(etat, ev, "chrono")
        etat["version"] += 1
        return ev
    # Un spectateur ou un joueur en faillite n agit plus.
    if not isinstance(i, int) or i >= etat["nb_joueurs"] or etat["faillite"][i]:
        return ev
    action = PP_ACTIONS.get(message.get("type"))
    if action is None:
        return ev
    erreur = action(etat, ev, i, message)
    if erreur:
        return [_prive(i, {"type": "erreur", "message": erreur})]
    etat["version"] += 1
    pp_armer(etat, maintenant)
    return ev


def pp_attendu(etat):
    """(joueur attendu, delai accorde), ou None si on n attend personne."""
    if etat["phase"] == "fin":
        return None
    if etat["attente"] is not None:
        return etat["attente"]["joueur"], PP_DELAI_CHOIX
    if etat["dette"] is not None:
        return etat["dette"]["joueur"], PP_DELAI_TOUR
    return etat["courant"], PP_DELAI_TOUR


def pp_armer(etat, maintenant=None):
    """Recalcule l echeance. A appeler aussi quand un joueur part ou revient."""
    if maintenant is None:
        maintenant = time.time()
    att = pp_attendu(etat)
    if att is None:
        etat["echeance"] = None
        return
    joueur, delai = att
    if etat["partis"][joueur]:
        delai = PP_DELAI_ABSENT
    etat["echeance"] = maintenant + delai


def pp_prochaine_echeance(etat):
    """Instant du prochain reveil utile (choix par defaut ou fin du chrono)."""
    if etat["phase"] == "fin":
        return None
    if etat["echeance"] is None:
        return etat["fin_prevue"]
    return min(etat["echeance"], etat["fin_prevue"])


def pp_expirer(etat, maintenant=None):
    """
    Appele par le minuteur. Fin du chrono, ou choix par defaut pour le joueur
    qui n a pas repondu a temps. Ne fait rien si rien n est echu.
    """
    if maintenant is None:
        maintenant = time.time()
    ev = []
    if etat["phase"] == "fin":
        return ev
    if maintenant >= etat["fin_prevue"]:
        _fin(etat, ev, "chrono")
        etat["version"] += 1
        return ev
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return ev
    if etat["attente"] is not None:
        a = etat["attente"]
        _choix(etat, ev, a["joueur"], {"valeur": _valeur_defaut(etat, a)})
    elif etat["dette"] is not None:
        _liquider(etat, ev, etat["dette"]["joueur"])
    elif etat["phase"] == "tour":
        _lancer(etat, ev, etat["courant"])
    elif etat["phase"] == "fin_tour":
        _tour_suivant(etat, ev)
    etat["version"] += 1
    pp_armer(etat, maintenant)
    return ev
