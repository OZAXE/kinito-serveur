"""
Moteur de regles du Casino (roulette, blackjack, poker Texas Hold'em).

Meme principe que menteur.py : moteur PUR, sans reseau ni horloge implicite
(le temps est passe en parametre). Chaque action prend l etat (un dict
serialisable en JSON) et renvoie la liste des messages a envoyer, au format
{"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse cs_etat et
la main privee de chacun (poker) apres chaque action acceptee, et arme le
minuteur sur cs_prochaine_echeance.

Le casino :
  - Tout le salon est a la meme table. Depuis le hall, n importe qui choisit
    la roulette, le blackjack ou le poker ; entre deux coups, n importe qui
    peut ramener tout le monde au hall pour changer de jeu.
  - Chacun part avec CS_JETONS_DEPART jetons, gardes d un jeu a l autre.
  - A la fin de chaque coup, le bilan de chacun se paie en gorgees :
        perte : 1 gorgee par tranche de CS_JETONS_PAR_GORGEE entamee ;
        gain  : 1 gorgee a distribuer par tranche de CS_JETONS_PAR_DISTRIB.
    (au plus CS_GORGEES_MAX dans les deux cas)
  - Fauche (moins que la plus petite mise) : cul sec, et la banque recharge
    CS_RECHARGE jetons. Le classement compte les recharges.

Roulette europeenne (0 a 36) : chacun mise pendant CS_DELAI_MISES secondes
(ou jusqu a ce que tous les presents soient prets), puis la bille tombe.
Blackjack : mise, deux cartes, tirer / rester / doubler, le croupier tire
jusqu a 17. Blackjack naturel paye 3 pour 2.
Poker : Texas Hold'em sans limite, blinds CS_PETITE_BLIND / CS_GROSSE_BLIND,
pots secondaires pour les tapis.
"""
import itertools
import math
import random

# =============================================================
# CONSTANTES
# =============================================================
CS_JOUEURS_MIN = 1
CS_JOUEURS_MAX = 8
CS_JEUX = ("roulette", "blackjack", "poker")

CS_JETONS_DEPART = 1000
CS_RECHARGE = 1000
CS_MISE_MIN = 10
CS_JETONS_PAR_GORGEE = 100
CS_JETONS_PAR_DISTRIB = 200
CS_GORGEES_MAX = 6

CS_DELAI_MISES = 30          # roulette et blackjack : temps pour miser
CS_DELAI_TOUR = 30           # blackjack : tirer / rester
CS_DELAI_POKER = 40          # poker : parler
CS_DELAI_ABSENT = 3          # quand on attend un joueur parti
CS_DELAI_ROULETTE = 13.0     # zoom sur la roue (5 s + 1,6 s de lecture), puis le bilan
CS_DELAI_BJ_FIN = 4.5        # lecture du resultat du blackjack...
CS_DELAI_PAR_CARTE = 0.8     # ... plus chaque carte tiree par le croupier
CS_DELAI_POKER_FIN = 7.0     # abattage
CS_DELAI_POKER_CARTE = 0.9   # chaque carte du tableau distribuee d un coup (tapis)

CS_PETITE_BLIND = 10
CS_GROSSE_BLIND = 20

# Roulette europeenne
CS_ROUGES = (1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36)
CS_ORDRE_ROUE = (0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8,
                 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12,
                 35, 3, 26)
CS_PARIS_SIMPLES = {
    # pari : (gain pour 1, test sur le numero)
    "rouge": (1, lambda n: n in CS_ROUGES),
    "noir": (1, lambda n: n != 0 and n not in CS_ROUGES),
    "pair": (1, lambda n: n != 0 and n % 2 == 0),
    "impair": (1, lambda n: n % 2 == 1),
    "manque": (1, lambda n: 1 <= n <= 18),
    "passe": (1, lambda n: 19 <= n <= 36),
    "d1": (2, lambda n: 1 <= n <= 12),
    "d2": (2, lambda n: 13 <= n <= 24),
    "d3": (2, lambda n: 25 <= n <= 36),
    "c1": (2, lambda n: n != 0 and n % 3 == 1),
    "c2": (2, lambda n: n != 0 and n % 3 == 2),
    "c3": (2, lambda n: n != 0 and n % 3 == 0),
}
CS_MISES_MAX_PAR_JOUEUR = 20   # nombre de paris differents sur un meme tour

RANGS = "23456789TJQKA"
COULEURS = "SHDC"   # pique, coeur, carreau, trefle
NOMS_MAINS = ["Carte haute", "Paire", "Double paire", "Brelan", "Quinte",
              "Couleur", "Full", "Carré", "Quinte flush"]


# =============================================================
# OUTILS
# =============================================================
def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _erreur(i, texte):
    return [_prive(i, {"type": "erreur", "message": texte})]


def _entier(v):
    if isinstance(v, bool):
        return None
    try:
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _paquet():
    p = [r + c for r in RANGS for c in COULEURS]
    random.shuffle(p)
    return p


def _presents(etat):
    return [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]


# =============================================================
# INITIALISATION ET HALL
# =============================================================
def cs_initialiser(noms, maintenant):
    n = len(noms)
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "jetons": [CS_JETONS_DEPART] * n,
        "gorgees": [0] * n,
        "distribuees": [0] * n,
        "recharges": [0] * n,
        "jeu": None,
        "phase": "hall",
        "manche": 0,
        "echeance": None,
        "message": "Bienvenue au casino : choisissez une table.",
        "bilan": None,          # dernier bilan en gorgees, public
        "num_bilan": 0,
        "historique": [],       # derniers numeros de la roulette
        "bouton": random.randrange(n),
        "t": None,              # la table en cours (voir chaque jeu)
    }
    return etat


def cs_ajouter_joueur(etat, nom):
    """Un arrivant s assoit avec ses jetons : il joue au prochain coup."""
    etat["nb_joueurs"] += 1
    etat["noms"].append(nom)
    etat["partis"].append(False)
    etat["jetons"].append(CS_JETONS_DEPART)
    etat["gorgees"].append(0)
    etat["distribuees"].append(0)
    etat["recharges"].append(0)
    t = etat["t"]
    if t is not None:
        if etat["jeu"] == "roulette":
            t["mises"].append([])
            t["prets"].append(False)
        elif etat["jeu"] == "blackjack":
            t["mises"].append(0)
            t["prets"].append(False)
            t["mains"].append([])
        elif etat["jeu"] == "poker":
            # Il regarde ce coup-ci : hors du coup, considere comme couche.
            for cle, vide in (("dans_coup", False), ("couches", True), ("tapis", False),
                              ("mise_tour", 0), ("engage", 0), ("a_parle", False)):
                t[cle].append(vide)
            t["cartes"].append([])
            if t.get("gagnes") is not None:
                t["gagnes"].append(0)
    etat["version"] += 1


def _aller_au_hall(etat, message):
    etat["jeu"] = None
    etat["phase"] = "hall"
    etat["t"] = None
    etat["echeance"] = None
    etat["message"] = message


def _choisir(etat, ev, i, message, maintenant):
    if etat["phase"] != "hall":
        return _erreur(i, "Une partie est en cours.")
    jeu = message.get("jeu")
    if jeu not in CS_JEUX:
        return _erreur(i, "Table inconnue.")
    if jeu == "poker" and len(_presents(etat)) < 2:
        return _erreur(i, "Le poker se joue à 2 au moins.")
    etat["jeu"] = jeu
    ev.append(_tous({"type": "cs_table", "jeu": jeu, "par": i}))
    _nouveau_coup(etat, ev, maintenant)
    return ev


def _hall(etat, ev, i, message, maintenant):
    """Retour au hall : entre deux coups seulement (mises rendues)."""
    if etat["phase"] == "hall":
        return _erreur(i, "Vous êtes déjà au hall.")
    if etat["phase"] not in ("mises", "resultat"):
        return _erreur(i, "Attendez la fin du coup.")
    t = etat["t"]
    if etat["phase"] == "mises" and t is not None:
        _rendre_mises(etat)
    _aller_au_hall(etat, etat["noms"][i] + " ramène tout le monde au hall.")
    return ev


def _rendre_mises(etat):
    t = etat["t"]
    if etat["jeu"] == "roulette":
        for j, mises in enumerate(t["mises"]):
            etat["jetons"][j] += sum(m["montant"] for m in mises)
            t["mises"][j] = []
    elif etat["jeu"] == "blackjack":
        for j, m in enumerate(t["mises"]):
            etat["jetons"][j] += m
            t["mises"][j] = 0


def _nouveau_coup(etat, ev, maintenant):
    etat["manche"] += 1
    jeu = etat["jeu"]
    if jeu == "roulette":
        _rl_nouveau(etat, maintenant)
    elif jeu == "blackjack":
        _bj_nouveau(etat, maintenant)
    elif jeu == "poker":
        _pk_nouveau(etat, ev, maintenant)


# =============================================================
# BILAN EN GORGEES
# =============================================================
def _gorgees_perte(perte):
    if perte <= 0:
        return 0
    return min(CS_GORGEES_MAX, -(-perte // CS_JETONS_PAR_GORGEE))


def _gorgees_gain(gain):
    if gain <= 0:
        return 0
    return min(CS_GORGEES_MAX, gain // CS_JETONS_PAR_DISTRIB)


def _solder(etat, deltas, details):
    """
    Fin d un coup. deltas : {joueur: gain net (negatif = perte)} pour ceux qui
    ont joue. details : {joueur: texte} pour le bilan. Applique gorgees,
    distributions et recharges, et publie le bilan.
    """
    lignes = []
    for j in sorted(deltas):
        d = deltas[j]
        g = _gorgees_perte(-d)
        dist = _gorgees_gain(d)
        etat["gorgees"][j] += g
        etat["distribuees"][j] += dist
        cul_sec = False
        if etat["jetons"][j] < CS_MISE_MIN:
            cul_sec = True
            etat["jetons"][j] += CS_RECHARGE
            etat["recharges"][j] += 1
        lignes.append({"joueur": j, "delta": d, "gorgees": g, "distribue": dist,
                       "cul_sec": cul_sec, "detail": details.get(j, "")})
    etat["num_bilan"] += 1
    etat["bilan"] = {"num": etat["num_bilan"], "jeu": etat["jeu"],
                     "manche": etat["manche"], "lignes": lignes}


# =============================================================
# ROULETTE
# =============================================================
def cs_pari_valide(pari):
    if not isinstance(pari, str):
        return False
    if pari in CS_PARIS_SIMPLES:
        return True
    if pari.startswith("n"):
        n = _entier(pari[1:])
        return n is not None and 0 <= n <= 36 and pari == "n" + str(n)
    return False


def cs_gain_pari(pari, numero):
    """Gain net pour 1 jeton mise (0 = perdu, la mise part)."""
    if pari in CS_PARIS_SIMPLES:
        mult, test = CS_PARIS_SIMPLES[pari]
        return mult if test(numero) else None
    # « n17 » : plein sur le 17 (attention, « noir » commence aussi par n).
    return 35 if int(pari[1:]) == numero else None


def _rl_nouveau(etat, maintenant):
    n = etat["nb_joueurs"]
    etat["t"] = {
        "mises": [[] for _ in range(n)],
        "prets": [False] * n,
        "numero": None,
    }
    etat["phase"] = "mises"
    etat["echeance"] = maintenant + CS_DELAI_MISES
    etat["message"] = "Roulette : faites vos jeux !"


def _rl_miser(etat, ev, i, message, maintenant):
    t = etat["t"]
    if etat["jeu"] != "roulette" or etat["phase"] != "mises":
        return _erreur(i, "Les jeux sont faits.")
    pari = message.get("pari")
    montant = etat["jetons"][i] if message.get("tapis") else _entier(message.get("montant"))
    if not cs_pari_valide(pari):
        return _erreur(i, "Pari inconnu.")
    if montant is None or montant < CS_MISE_MIN:
        return _erreur(i, "Mise minimum : " + str(CS_MISE_MIN) + " jetons.")
    if montant > etat["jetons"][i]:
        return _erreur(i, "Tu n'as pas assez de jetons.")
    mises = t["mises"][i]
    for m in mises:
        if m["pari"] == pari:
            m["montant"] += montant
            break
    else:
        if len(mises) >= CS_MISES_MAX_PAR_JOUEUR:
            return _erreur(i, "Trop de paris différents.")
        mises.append({"pari": pari, "montant": montant})
    etat["jetons"][i] -= montant
    t["prets"][i] = False
    return ev


def _rl_annuler(etat, ev, i, message, maintenant):
    t = etat["t"]
    if etat["jeu"] != "roulette" or etat["phase"] != "mises":
        return _erreur(i, "Les jeux sont faits.")
    if not t["mises"][i]:
        return _erreur(i, "Tu n'as rien misé.")
    etat["jetons"][i] += sum(m["montant"] for m in t["mises"][i])
    t["mises"][i] = []
    t["prets"][i] = False
    return ev


def _rl_tourner(etat, ev, maintenant):
    t = etat["t"]
    numero = random.choice(CS_ORDRE_ROUE)
    t["numero"] = numero
    etat["historique"] = ([numero] + etat["historique"])[:12]
    deltas, details = {}, {}
    for j, mises in enumerate(t["mises"]):
        if not mises:
            continue
        total = sum(m["montant"] for m in mises)
        retour = 0
        for m in mises:
            g = cs_gain_pari(m["pari"], numero)
            m["gagne"] = g is not None
            if g is not None:
                retour += m["montant"] * (g + 1)
        etat["jetons"][j] += retour
        deltas[j] = retour - total
        details[j] = "misé " + str(total) + ", récupéré " + str(retour)
    couleur = "vert" if numero == 0 else ("rouge" if numero in CS_ROUGES else "noir")
    etat["message"] = "Le " + str(numero) + " " + couleur + " !"
    _solder(etat, deltas, details)
    etat["phase"] = "resultat"
    etat["echeance"] = maintenant + CS_DELAI_ROULETTE
    ev.append(_tous({"type": "cs_roulette", "numero": numero, "manche": etat["manche"]}))


# =============================================================
# PRETS (roulette et blackjack)
# =============================================================
def _pret(etat, ev, i, message, maintenant):
    t = etat["t"]
    if etat["jeu"] not in ("roulette", "blackjack") or etat["phase"] != "mises":
        return _erreur(i, "Ce n'est pas le moment.")
    t["prets"][i] = not t["prets"][i]
    _peut_lancer(etat, ev, maintenant)
    return ev


def _a_mise(etat, j):
    t = etat["t"]
    if etat["jeu"] == "roulette":
        return bool(t["mises"][j])
    return t["mises"][j] > 0


def _peut_lancer(etat, ev, maintenant):
    """Tous les presents sont prets et quelqu un a mise : on lance."""
    t = etat["t"]
    presents = _presents(etat)
    if not presents or not all(t["prets"][j] for j in presents):
        return False
    if not any(_a_mise(etat, j) for j in range(etat["nb_joueurs"])):
        return False
    _lancer(etat, ev, maintenant)
    return True


def _lancer(etat, ev, maintenant):
    if etat["jeu"] == "roulette":
        _rl_tourner(etat, ev, maintenant)
    else:
        _bj_distribuer(etat, ev, maintenant)


# =============================================================
# BLACKJACK
# =============================================================
def cs_valeur_bj(cartes):
    """(total, souple) d une main de blackjack."""
    total, as_ = 0, 0
    for c in cartes:
        r = c[0]
        if r == "A":
            total += 11
            as_ += 1
        elif r in "TJQK":
            total += 10
        else:
            total += int(r)
    while total > 21 and as_:
        total -= 10
        as_ -= 1
    return total, as_ > 0


def _bj_naturel(cartes):
    return len(cartes) == 2 and cs_valeur_bj(cartes)[0] == 21


CS_BJ_MAINS_MAX = 4          # split : 4 mains au plus par joueur


def _bj_valeur_carte(c):
    return 10 if c[0] in "TJQK" else (11 if c[0] == "A" else int(c[0]))


def _bj_nouveau(etat, maintenant):
    n = etat["nb_joueurs"]
    etat["t"] = {
        "mises": [0] * n,          # mise posee pendant la phase des mises
        "prets": [False] * n,
        # Pendant le coup, chaque joueur a une ou plusieurs mains (split) :
        # {"cartes", "mise", "statut", "double", "split", "resultat"}
        # statut : "joue", "reste", "saute", "blackjack".
        "mains": [[] for _ in range(n)],
        "croupier": [],
        "cache": True,             # la 2e carte du croupier est cachee
        "courant": None,           # joueur qui joue...
        "main_courante": None,     # ... et laquelle de ses mains
        "paquet": [],
        "tires_croupier": 0,
    }
    etat["phase"] = "mises"
    etat["echeance"] = maintenant + CS_DELAI_MISES
    etat["message"] = "Blackjack : misez !"


def _bj_miser(etat, ev, i, message, maintenant):
    t = etat["t"]
    if etat["jeu"] != "blackjack" or etat["phase"] != "mises":
        return _erreur(i, "Les mises sont closes.")
    if message.get("tapis"):
        montant = etat["jetons"][i]
    else:
        montant = _entier(message.get("montant"))
    if montant is None or montant < CS_MISE_MIN:
        return _erreur(i, "Mise minimum : " + str(CS_MISE_MIN) + " jetons.")
    if montant > etat["jetons"][i]:
        return _erreur(i, "Tu n'as pas assez de jetons.")
    t["mises"][i] += montant
    etat["jetons"][i] -= montant
    t["prets"][i] = False
    return ev


def _bj_annuler(etat, ev, i, message, maintenant):
    t = etat["t"]
    if etat["jeu"] != "blackjack" or etat["phase"] != "mises":
        return _erreur(i, "Les mises sont closes.")
    if not t["mises"][i]:
        return _erreur(i, "Tu n'as rien misé.")
    etat["jetons"][i] += t["mises"][i]
    t["mises"][i] = 0
    t["prets"][i] = False
    return ev


def _bj_main(cartes, mise, split=False):
    return {"cartes": cartes, "mise": mise, "statut": "joue", "double": False,
            "split": split, "resultat": None}


def _bj_distribuer(etat, ev, maintenant):
    t = etat["t"]
    t["paquet"] = _paquet() + _paquet()
    joueurs = [j for j in range(etat["nb_joueurs"]) if t["mises"][j] > 0]
    for j in joueurs:
        t["mains"][j] = [_bj_main([], t["mises"][j])]
    for _ in range(2):
        for j in joueurs:
            t["mains"][j][0]["cartes"].append(t["paquet"].pop())
        t["croupier"].append(t["paquet"].pop())
    for j in joueurs:
        if _bj_naturel(t["mains"][j][0]["cartes"]):
            t["mains"][j][0]["statut"] = "blackjack"
    etat["phase"] = "jeu"
    etat["message"] = "Cartes distribuées."
    ev.append(_tous({"type": "cs_bj_donne", "manche": etat["manche"]}))
    _bj_suivant(etat, ev, maintenant, -1, -1)


def _bj_suivant(etat, ev, maintenant, j0, h0):
    """La prochaine main a jouer apres (j0, h0), dans l ordre des sieges."""
    t = etat["t"]
    for j in range(max(0, j0), etat["nb_joueurs"]):
        depart = h0 + 1 if j == j0 else 0
        for h in range(depart, len(t["mains"][j])):
            if t["mains"][j][h]["statut"] == "joue":
                t["courant"], t["main_courante"] = j, h
                _armer(etat, maintenant)
                nb = len(t["mains"][j])
                etat["message"] = "Au tour de " + etat["noms"][j] + \
                    (" (main " + str(h + 1) + "/" + str(nb) + ")" if nb > 1 else "") + "."
                return
    t["courant"] = t["main_courante"] = None
    _bj_croupier(etat, ev, maintenant)


def _bj_croupier(etat, ev, maintenant):
    t = etat["t"]
    t["cache"] = False
    mains = [m for j in range(etat["nb_joueurs"]) for m in t["mains"][j]]
    # Le croupier ne tire que s il reste une main a battre.
    if any(m["statut"] == "reste" for m in mains):
        while cs_valeur_bj(t["croupier"])[0] < 17:
            t["croupier"].append(t["paquet"].pop())
    tirees = len(t["croupier"]) - 2
    t["tires_croupier"] = tirees
    total_c = cs_valeur_bj(t["croupier"])[0]
    bj_c = _bj_naturel(t["croupier"])
    deltas, details = {}, {}
    for j in range(etat["nb_joueurs"]):
        if not t["mains"][j]:
            continue
        retour_total, mise_totale, textes = 0, 0, []
        for m in t["mains"][j]:
            mise = m["mise"]
            total = cs_valeur_bj(m["cartes"])[0]
            if m["statut"] == "saute":
                retour, res = 0, "sauté"
            elif m["statut"] == "blackjack":
                retour, res = (mise, "égalité") if bj_c else (mise + (mise * 3) // 2, "blackjack")
            elif bj_c:
                retour, res = 0, "perdu"
            elif total_c > 21 or total > total_c:
                retour, res = 2 * mise, "gagné"
            elif total == total_c:
                retour, res = mise, "égalité"
            else:
                retour, res = 0, "perdu"
            m["resultat"] = res
            retour_total += retour
            mise_totale += mise
            textes.append(res + " (" + str(total) + ")")
        etat["jetons"][j] += retour_total
        deltas[j] = retour_total - mise_totale
        details[j] = ", ".join(textes) + " contre " + str(total_c)
    if bj_c:
        etat["message"] = "Blackjack du croupier !"
    elif total_c > 21:
        etat["message"] = "Le croupier saute avec " + str(total_c) + " !"
    else:
        etat["message"] = "Le croupier fait " + str(total_c) + "."
    _solder(etat, deltas, details)
    etat["phase"] = "resultat"
    etat["echeance"] = maintenant + CS_DELAI_BJ_FIN + CS_DELAI_PAR_CARTE * tirees
    ev.append(_tous({"type": "cs_bj_croupier", "manche": etat["manche"], "tirees": tirees}))


def _bj_main_courante(etat, i):
    """La main que i doit jouer, ou un texte d erreur."""
    t = etat["t"]
    if etat["jeu"] != "blackjack" or etat["phase"] != "jeu" or t["courant"] != i:
        return None, "Ce n'est pas à toi de jouer."
    return t["mains"][i][t["main_courante"]], None


def _bj_fin_main(etat, ev, i, maintenant):
    """La main courante est finie (ou continue) selon son total."""
    t = etat["t"]
    m = t["mains"][i][t["main_courante"]]
    total = cs_valeur_bj(m["cartes"])[0]
    if total > 21:
        m["statut"] = "saute"
    elif total == 21 or m["double"]:
        m["statut"] = "reste"
    if m["statut"] == "joue":
        _armer(etat, maintenant)
    else:
        _bj_suivant(etat, ev, maintenant, i, t["main_courante"])


def _bj_tirer(etat, ev, i, message, maintenant):
    m, err = _bj_main_courante(etat, i)
    if err:
        return _erreur(i, err)
    m["cartes"].append(etat["t"]["paquet"].pop())
    ev.append(_tous({"type": "cs_bj_carte", "joueur": i}))
    _bj_fin_main(etat, ev, i, maintenant)
    return ev


def _bj_rester(etat, ev, i, message, maintenant):
    m, err = _bj_main_courante(etat, i)
    if err:
        return _erreur(i, err)
    m["statut"] = "reste"
    _bj_suivant(etat, ev, maintenant, i, etat["t"]["main_courante"])
    return ev


def _bj_doubler(etat, ev, i, message, maintenant):
    m, err = _bj_main_courante(etat, i)
    if err:
        return _erreur(i, err)
    if len(m["cartes"]) != 2:
        return _erreur(i, "On ne double que sur deux cartes.")
    if etat["jetons"][i] < m["mise"]:
        return _erreur(i, "Pas assez de jetons pour doubler.")
    etat["jetons"][i] -= m["mise"]
    m["mise"] *= 2
    m["double"] = True
    m["cartes"].append(etat["t"]["paquet"].pop())
    ev.append(_tous({"type": "cs_bj_carte", "joueur": i}))
    _bj_fin_main(etat, ev, i, maintenant)
    return ev


def cs_peut_splitter(main, jetons, nb_mains):
    c = main["cartes"]
    return (len(c) == 2 and _bj_valeur_carte(c[0]) == _bj_valeur_carte(c[1])
            and jetons >= main["mise"] and nb_mains < CS_BJ_MAINS_MAX)


def _bj_splitter(etat, ev, i, message, maintenant):
    """Deux cartes de meme valeur : deux mains, une mise de plus."""
    m, err = _bj_main_courante(etat, i)
    if err:
        return _erreur(i, err)
    t = etat["t"]
    if not cs_peut_splitter(m, etat["jetons"][i], len(t["mains"][i])):
        if len(m["cartes"]) != 2 or _bj_valeur_carte(m["cartes"][0]) != _bj_valeur_carte(m["cartes"][1]):
            return _erreur(i, "On ne sépare que deux cartes de même valeur.")
        if etat["jetons"][i] < m["mise"]:
            return _erreur(i, "Pas assez de jetons pour séparer.")
        return _erreur(i, "Quatre mains au plus.")
    etat["jetons"][i] -= m["mise"]
    h = t["main_courante"]
    as_ = m["cartes"][0][0] == "A"
    seconde = _bj_main([m["cartes"].pop()], m["mise"], split=True)
    m["split"] = True
    m["cartes"].append(t["paquet"].pop())
    seconde["cartes"].append(t["paquet"].pop())
    t["mains"][i].insert(h + 1, seconde)
    ev.append(_tous({"type": "cs_bj_split", "joueur": i}))
    if as_:
        # Des As separes : une seule carte chacun, on reste.
        m["statut"] = seconde["statut"] = "reste"
        _bj_suivant(etat, ev, maintenant, i, h + 1)
    else:
        if cs_valeur_bj(seconde["cartes"])[0] == 21:
            seconde["statut"] = "reste"
        _bj_fin_main(etat, ev, i, maintenant)
    etat["message"] = etat["noms"][i] + " sépare sa main."
    return ev


# =============================================================
# POKER : evaluation des mains
# =============================================================
def _valeur_5(cartes):
    """Tuple comparable pour 5 cartes (plus grand = meilleur)."""
    rangs = sorted((RANGS.index(c[0]) for c in cartes), reverse=True)
    couleur = len(set(c[1] for c in cartes)) == 1
    uniques = sorted(set(rangs), reverse=True)
    quinte = None
    if len(uniques) == 5:
        if uniques[0] - uniques[4] == 4:
            quinte = uniques[0]
        elif uniques == [12, 3, 2, 1, 0]:   # A-2-3-4-5
            quinte = 3
    comptes = sorted(((rangs.count(r), r) for r in uniques), reverse=True)
    forme = [c for c, _ in comptes]
    ordre = [r for _, r in comptes]
    if quinte is not None and couleur:
        return (8, quinte)
    if forme == [4, 1]:
        return (7, *ordre)
    if forme == [3, 2]:
        return (6, *ordre)
    if couleur:
        return (5, *rangs)
    if quinte is not None:
        return (4, quinte)
    if forme == [3, 1, 1]:
        return (3, *ordre)
    if forme == [2, 2, 1]:
        return (2, *ordre)
    if forme == [2, 1, 1, 1]:
        return (1, *ordre)
    return (0, *rangs)


def cs_meilleure_main(cartes):
    """(valeur, 5 cartes) de la meilleure main parmi 5 a 7 cartes."""
    meilleure = None
    for combi in itertools.combinations(cartes, 5):
        v = _valeur_5(combi)
        if meilleure is None or v > meilleure[0]:
            meilleure = (v, list(combi))
    return meilleure


def cs_nom_main(valeur):
    return NOMS_MAINS[valeur[0]]


# =============================================================
# POKER : deroulement
# =============================================================
def _pk_suivant(etat, i, condition):
    n = etat["nb_joueurs"]
    for k in range(1, n + 1):
        j = (i + k) % n
        if condition(j):
            return j
    return None


def _pk_nouveau(etat, ev, maintenant):
    n = etat["nb_joueurs"]
    dans = [not etat["partis"][j] and etat["jetons"][j] > 0 for j in range(n)]
    if sum(dans) < 2:
        _aller_au_hall(etat, "Pas assez de joueurs pour le poker : retour au hall.")
        return
    t = {
        "dans_coup": dans,
        "cartes": [[] for _ in range(n)],    # SECRETES
        "tableau": [],
        "paquet": _paquet(),
        "couches": [not d for d in dans],
        "tapis": [False] * n,
        "mise_tour": [0] * n,                # mise de ce tour d enchere
        "engage": [0] * n,                   # total mis dans le coup
        "a_parle": [False] * n,
        "mise_max": 0,
        "relance_min": CS_GROSSE_BLIND,
        "tour": "preflop",
        "courant": None,
        "montrees": None,                    # a l abattage
        "gagnes": None,
        "pots": None,
        "derniere_action": None,
    }
    etat["t"] = t
    bouton = _pk_suivant(etat, etat["bouton"], lambda j: dans[j])
    etat["bouton"] = bouton
    actifs = [j for j in range(n) if dans[j]]
    if len(actifs) == 2:
        petite = bouton                      # tete a tete : le bouton paie la petite
    else:
        petite = _pk_suivant(etat, bouton, lambda j: dans[j])
    grosse = _pk_suivant(etat, petite, lambda j: dans[j])
    t["petite"], t["grosse"] = petite, grosse
    for _ in range(2):
        for j in actifs:
            t["cartes"][j].append(t["paquet"].pop())
    _pk_payer(etat, petite, CS_PETITE_BLIND)
    _pk_payer(etat, grosse, CS_GROSSE_BLIND)
    t["mise_max"] = max(t["mise_tour"])
    etat["phase"] = "jeu"
    etat["message"] = "Nouvelle donne : blinds " + str(CS_PETITE_BLIND) + "/" + str(CS_GROSSE_BLIND) + "."
    ev.append(_tous({"type": "cs_pk_donne", "manche": etat["manche"]}))
    _pk_donner_parole(etat, ev, grosse, maintenant)


def _pk_payer(etat, j, montant):
    t = etat["t"]
    paye = min(montant, etat["jetons"][j])
    etat["jetons"][j] -= paye
    t["mise_tour"][j] += paye
    t["engage"][j] += paye
    if etat["jetons"][j] == 0:
        t["tapis"][j] = True
    return paye


def _pk_peut_agir(etat, j):
    t = etat["t"]
    return t["dans_coup"][j] and not t["couches"][j] and not t["tapis"][j]


def _pk_en_lice(etat):
    t = etat["t"]
    return [j for j in range(etat["nb_joueurs"]) if t["dans_coup"][j] and not t["couches"][j]]


def _pk_doit_parler(etat, j):
    t = etat["t"]
    return _pk_peut_agir(etat, j) and (not t["a_parle"][j] or t["mise_tour"][j] < t["mise_max"])


def _pk_donner_parole(etat, ev, depuis, maintenant):
    """Donne la parole au prochain (apres depuis) qui doit encore parler."""
    t = etat["t"]
    en_lice = _pk_en_lice(etat)
    if len(en_lice) == 1:
        _pk_abattage(etat, ev, maintenant, sans_combat=True)
        return
    j = _pk_suivant(etat, depuis, lambda k: _pk_doit_parler(etat, k))
    if j is None:
        _pk_tour_suivant(etat, ev, maintenant)
        return
    t["courant"] = j
    _armer(etat, maintenant)


def _pk_tour_suivant(etat, ev, maintenant):
    t = etat["t"]
    t["mise_tour"] = [0] * etat["nb_joueurs"]
    t["a_parle"] = [False] * etat["nb_joueurs"]
    t["mise_max"] = 0
    t["relance_min"] = CS_GROSSE_BLIND
    t["courant"] = None
    ordre = ["preflop", "flop", "turn", "river"]
    peuvent = [j for j in range(etat["nb_joueurs"]) if _pk_peut_agir(etat, j)]
    while True:
        k = ordre.index(t["tour"]) if t["tour"] in ordre else 3
        if k == 3:
            _pk_abattage(etat, ev, maintenant, sans_combat=False)
            return
        t["tour"] = ordre[k + 1]
        nb = 3 if t["tour"] == "flop" else 1
        t["paquet"].pop()   # carte brulee
        for _ in range(nb):
            t["tableau"].append(t["paquet"].pop())
        noms = {"flop": "Le flop", "turn": "Le tournant", "river": "La rivière"}
        etat["message"] = noms[t["tour"]] + "."
        ev.append(_tous({"type": "cs_pk_tableau", "tour": t["tour"], "tableau": list(t["tableau"])}))
        # Plus personne (ou un seul) ne peut parler : on deroule le tableau.
        if len(peuvent) >= 2:
            break
    _pk_donner_parole(etat, ev, etat["bouton"], maintenant)


def cs_pots(engage, eligibles):
    """
    Decoupe les mises en pot principal et pots secondaires.
    engage : total mis par chacun ; eligibles : ceux qui n ont pas couche.
    Renvoie [{"montant": m, "eligibles": [...]}, ...].
    """
    niveaux = sorted(set(engage[j] for j in eligibles if engage[j] > 0))
    pots, bas = [], 0
    for niv in niveaux:
        montant = sum(min(e, niv) - min(e, bas) for e in engage)
        elig = [j for j in eligibles if engage[j] >= niv]
        if montant > 0:
            if pots and pots[-1]["eligibles"] == elig:
                pots[-1]["montant"] += montant
            else:
                pots.append({"montant": montant, "eligibles": elig})
        bas = niv
    # Ce qui depasse le plus gros niveau des eligibles (mises des couches)
    reste = sum(engage) - sum(p["montant"] for p in pots)
    if reste > 0:
        if pots:
            pots[-1]["montant"] += reste
        else:
            pots.append({"montant": reste, "eligibles": list(eligibles)})
    return pots


def _pk_abattage(etat, ev, maintenant, sans_combat):
    t = etat["t"]
    n = etat["nb_joueurs"]
    en_lice = _pk_en_lice(etat)
    t["courant"] = None
    gains = [0] * n
    valeurs = {}
    if sans_combat:
        gains[en_lice[0]] = sum(t["engage"])
        t["montrees"] = {}
        t["pots"] = [{"montant": sum(t["engage"]), "eligibles": en_lice, "gagnants": en_lice}]
        etat["message"] = etat["noms"][en_lice[0]] + " remporte le pot sans abattage."
        tirees = 0
    else:
        # On complete le tableau si tout le monde est a tapis avant la riviere.
        tirees = 0
        while len(t["tableau"]) < 5:
            t["tableau"].append(t["paquet"].pop())
            tirees += 1
        for j in en_lice:
            valeurs[j] = cs_meilleure_main(t["cartes"][j] + t["tableau"])
        pots = cs_pots(t["engage"], en_lice)
        for p in pots:
            meilleur = max(valeurs[j][0] for j in p["eligibles"])
            gagnants = [j for j in p["eligibles"] if valeurs[j][0] == meilleur]
            part, reste = divmod(p["montant"], len(gagnants))
            for k, j in enumerate(_pk_ordre_apres_bouton(etat, gagnants)):
                gains[j] += part + (1 if k < reste else 0)
            p["gagnants"] = gagnants
        t["pots"] = pots
        t["montrees"] = {str(j): {"cartes": t["cartes"][j],
                                  "main": cs_nom_main(valeurs[j][0]),
                                  "meilleures": valeurs[j][1]} for j in en_lice}
        g0 = pots[0]["gagnants"]
        etat["message"] = " et ".join(etat["noms"][j] for j in g0) + " gagne" \
            + ("nt" if len(g0) > 1 else "") + " avec : " + cs_nom_main(valeurs[g0[0]][0]) + "."
    deltas, details = {}, {}
    for j in range(n):
        if not t["dans_coup"][j]:
            continue
        etat["jetons"][j] += gains[j]
        deltas[j] = gains[j] - t["engage"][j]
        if str(j) in (t["montrees"] or {}):
            details[j] = t["montrees"][str(j)]["main"]
        elif t["couches"][j]:
            details[j] = "couché"
        else:
            details[j] = "pot sans abattage"
    t["gagnes"] = gains
    _solder(etat, deltas, details)
    etat["phase"] = "resultat"
    etat["echeance"] = maintenant + CS_DELAI_POKER_FIN + CS_DELAI_POKER_CARTE * tirees
    ev.append(_tous({"type": "cs_pk_abattage", "manche": etat["manche"],
                     "tableau": list(t["tableau"]), "montrees": t["montrees"],
                     "sans_combat": sans_combat}))


def _pk_ordre_apres_bouton(etat, joueurs):
    n = etat["nb_joueurs"]
    b = etat["bouton"]
    return sorted(joueurs, key=lambda j: (j - b - 1) % n)


def _pk_verifier(etat, i):
    t = etat["t"]
    if etat["jeu"] != "poker" or etat["phase"] != "jeu" or t["courant"] != i:
        return "Ce n'est pas à toi de parler."
    return None


def _pk_apres_action(etat, ev, i, texte, maintenant):
    t = etat["t"]
    t["a_parle"][i] = True
    t["derniere_action"] = {"joueur": i, "texte": texte}
    etat["message"] = etat["noms"][i] + " " + texte + "."
    ev.append(_tous({"type": "cs_pk_action", "joueur": i, "texte": texte}))
    _pk_donner_parole(etat, ev, i, maintenant)


def _pk_coucher(etat, ev, i, message, maintenant):
    err = _pk_verifier(etat, i)
    if err:
        return _erreur(i, err)
    etat["t"]["couches"][i] = True
    _pk_apres_action(etat, ev, i, "se couche", maintenant)
    return ev


def _pk_suivre(etat, ev, i, message, maintenant):
    """Parole (rien a payer) ou suivre (payer la difference)."""
    err = _pk_verifier(etat, i)
    if err:
        return _erreur(i, err)
    t = etat["t"]
    du = t["mise_max"] - t["mise_tour"][i]
    if du <= 0:
        _pk_apres_action(etat, ev, i, "check", maintenant)
        return ev
    paye = _pk_payer(etat, i, du)
    texte = "suit (" + str(paye) + ")" if not t["tapis"][i] else "fait tapis (" + str(paye) + ")"
    _pk_apres_action(etat, ev, i, texte, maintenant)
    return ev


def _pk_relancer(etat, ev, i, message, maintenant):
    """Relancer A un total de mise_tour (montant = total vise pour ce tour)."""
    err = _pk_verifier(etat, i)
    if err:
        return _erreur(i, err)
    t = etat["t"]
    total = _entier(message.get("montant"))
    max_possible = t["mise_tour"][i] + etat["jetons"][i]
    if message.get("tapis"):
        total = max_possible
    if total is None:
        return _erreur(i, "Montant invalide.")
    if total > max_possible:
        return _erreur(i, "Tu n'as pas autant de jetons.")
    minimum = t["mise_max"] + t["relance_min"]
    tapis = total == max_possible
    if total <= t["mise_max"]:
        if tapis:   # tapis qui ne couvre meme pas la mise : c est un suivre
            return _pk_suivre(etat, ev, i, message, maintenant)
        return _erreur(i, "Relance trop petite.")
    if total < minimum and not tapis:
        return _erreur(i, "Relance minimum : " + str(minimum) + ".")
    personne_avant = t["mise_max"] == 0
    ajout = total - t["mise_tour"][i]
    _pk_payer(etat, i, ajout)
    relance = total - t["mise_max"]
    if relance >= t["relance_min"]:
        t["relance_min"] = relance
        # Une vraie relance rouvre la parole a tout le monde.
        for j in range(etat["nb_joueurs"]):
            if j != i:
                t["a_parle"][j] = False
    else:
        # Tapis trop court pour une relance complete : ceux qui avaient deja
        # parle doivent seulement completer (ils peuvent suivre ou se coucher).
        for j in range(etat["nb_joueurs"]):
            if j != i and t["mise_tour"][j] < total:
                t["a_parle"][j] = False
    t["mise_max"] = total
    if t["tapis"][i]:
        texte = "fait tapis (" + str(total) + ")"
    else:
        texte = ("mise " if personne_avant else "relance à ") + str(total)
    _pk_apres_action(etat, ev, i, texte, maintenant)
    return ev


def cs_main_poker(etat, i):
    """Message prive : les deux cartes de i (ou rien hors poker)."""
    if etat["jeu"] != "poker" or etat["t"] is None or i >= etat["nb_joueurs"]:
        return None
    return {"type": "cs_main", "index": i, "manche": etat["manche"], "cartes": etat["t"]["cartes"][i]}


# =============================================================
# ETAT PUBLIC
# =============================================================
def cs_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    pub = {
        "type": "cs_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "jetons": etat["jetons"],
        "gorgees": etat["gorgees"],
        "distribuees": etat["distribuees"],
        "recharges": etat["recharges"],
        "jeu": etat["jeu"],
        "phase": etat["phase"],
        "manche": etat["manche"],
        "delai": delai,
        "message": etat["message"],
        "bilan": etat["bilan"],
        "historique": etat["historique"],
        "regles": {"mise_min": CS_MISE_MIN, "par_gorgee": CS_JETONS_PAR_GORGEE,
                   "par_distrib": CS_JETONS_PAR_DISTRIB, "recharge": CS_RECHARGE,
                   "petite": CS_PETITE_BLIND, "grosse": CS_GROSSE_BLIND},
        "table": None,
    }
    t = etat["t"]
    if t is None:
        return pub
    if etat["jeu"] == "roulette":
        pub["table"] = {"mises": t["mises"], "prets": t["prets"], "numero": t["numero"]}
    elif etat["jeu"] == "blackjack":
        croupier = list(t["croupier"])
        if t["cache"] and len(croupier) >= 2:
            croupier[1] = "??"
        mains = [[dict(m, valeur=cs_valeur_bj(m["cartes"])[0],
                       peut_splitter=cs_peut_splitter(m, etat["jetons"][j], len(t["mains"][j])))
                  for m in t["mains"][j]] for j in range(etat["nb_joueurs"])]
        pub["table"] = {
            "mises": t["mises"], "prets": t["prets"], "mains": mains, "croupier": croupier,
            "courant": t["courant"], "main_courante": t["main_courante"],
            "valeur_croupier": cs_valeur_bj([c for c in croupier if c != "??"])[0] if croupier else 0,
            "tires_croupier": t["tires_croupier"],
        }
    elif etat["jeu"] == "poker":
        pub["table"] = {
            "dans_coup": t["dans_coup"], "nb_cartes": [len(c) for c in t["cartes"]],
            "tableau": t["tableau"], "couches": t["couches"], "tapis": t["tapis"],
            "mise_tour": t["mise_tour"], "engage": t["engage"], "pot": sum(t["engage"]),
            "mise_max": t["mise_max"], "relance_min": t["relance_min"], "tour": t["tour"],
            "courant": t["courant"], "bouton": etat["bouton"], "petite": t["petite"],
            "grosse": t["grosse"], "montrees": t["montrees"], "gagnes": t["gagnes"],
            "pots": t["pots"], "derniere_action": t["derniere_action"],
        }
    return pub


# =============================================================
# AIGUILLAGE DES ACTIONS
# =============================================================
CS_ACTIONS = {
    "cs_choisir": _choisir,
    "cs_hall": _hall,
    "cs_pret": _pret,
    "cs_rl_miser": _rl_miser,
    "cs_rl_annuler": _rl_annuler,
    "cs_bj_miser": _bj_miser,
    "cs_bj_annuler": _bj_annuler,
    "cs_bj_tirer": _bj_tirer,
    "cs_bj_rester": _bj_rester,
    "cs_bj_doubler": _bj_doubler,
    "cs_bj_splitter": _bj_splitter,
    "cs_pk_coucher": _pk_coucher,
    "cs_pk_suivre": _pk_suivre,
    "cs_pk_relancer": _pk_relancer,
}


def cs_action(etat, i, message, maintenant):
    fn = CS_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
        return []
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR : choix par defaut d un joueur absent ou trop lent
# =============================================================
def cs_attendu(etat):
    """Index du joueur que la table attend, ou None."""
    t = etat["t"]
    if t is None or etat["phase"] != "jeu":
        return None
    return t.get("courant")


def _armer(etat, maintenant):
    """Echeance du tour du joueur attendu (court s il est parti)."""
    i = cs_attendu(etat)
    if i is None:
        return
    if etat["partis"][i]:
        etat["echeance"] = maintenant + CS_DELAI_ABSENT
    else:
        etat["echeance"] = maintenant + (CS_DELAI_POKER if etat["jeu"] == "poker" else CS_DELAI_TOUR)


def cs_prochaine_echeance(etat):
    return etat["echeance"]


def cs_expirer(etat, maintenant):
    """L echeance est passee : on avance a la place de qui on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    phase, jeu = etat["phase"], etat["jeu"]
    if phase == "mises":
        if any(_a_mise(etat, j) for j in range(etat["nb_joueurs"])):
            _lancer(etat, ev, maintenant)
        else:
            # Personne n a mise : on attend encore (sans rien jouer).
            etat["echeance"] = maintenant + CS_DELAI_MISES
            etat["message"] = "Personne n'a misé. Faites vos jeux, ou retournez au hall."
    elif phase == "resultat":
        _nouveau_coup(etat, ev, maintenant)
    elif phase == "jeu" and jeu == "blackjack":
        i = etat["t"]["courant"]
        _bj_rester(etat, ev, i, {}, maintenant)
    elif phase == "jeu" and jeu == "poker":
        t = etat["t"]
        i = t["courant"]
        if t["mise_tour"][i] >= t["mise_max"]:
            _pk_suivre(etat, ev, i, {}, maintenant)
        else:
            _pk_coucher(etat, ev, i, {}, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def cs_joueur_parti(etat, index, maintenant):
    """Renvoie les evenements (le depart peut lancer la roue ou la donne)."""
    ev = []
    if index >= etat["nb_joueurs"]:
        return ev
    etat["partis"][index] = True
    etat["version"] += 1
    if cs_attendu(etat) == index:
        _armer(etat, maintenant)
    elif etat["phase"] == "mises":
        # Celui qui part ne bloque plus le « tous prets ».
        _peut_lancer(etat, ev, maintenant)
    return ev


def cs_joueur_revenu(etat, index, maintenant):
    if index >= etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if cs_attendu(etat) == index:
        _armer(etat, maintenant)
