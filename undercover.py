"""
Moteur de regles d Undercover (deduction sociale, indices a l oral).

Meme principe que menteur.py : moteur PUR, sans reseau ni horloge implicite
(le temps est passe en parametre). Chaque action prend l etat (un dict
serialisable en JSON) et renvoie la liste des messages a envoyer, au format
{"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse uc_etat et
le mot secret de chacun apres chaque action acceptee, et arme le minuteur sur
uc_prochaine_echeance.

Regles :
  - Chacun recoit un mot en secret. Les Civils ont tous le meme, l Undercover
    un mot proche (sans savoir qu il est l Undercover), Mr White n a rien.
  - Phase « lecture » : chacun decouvre son mot et dit qu il est pret.
  - Phase « indices » : chacun a son tour donne un indice A L ORAL, puis
    touche « J ai parle ». Mr White ne commence jamais un tour.
  - Phase « vote » : le debat se fait a voix haute pendant que chacun vote
    (vote modifiable tant que tout le monde n a pas vote, secret jusqu au
    depouillement). Le plus designe est elimine et son role revele.
  - Egalite : les ex aequo redonnent un indice, puis on revote entre eux.
    Nouvelle egalite : le sort tranche.
  - Mr White elimine tente de deviner le mot des Civils. S il trouve
    (accents, majuscules, articles et petites fautes toleres), il gagne seul.
  - Les Civils gagnent quand il ne reste plus d infiltre. Les infiltres
    (Undercovers et Mr White, en vie ou non) gagnent quand il ne reste plus
    qu un Civil.

Gorgees :
  - infiltre demasque : il boit UC_GORGEES_DEMASQUE ;
  - Civil elimine par erreur : il boit UC_GORGEES_CIVIL, et chacun de ceux
    qui ont vote contre lui boit UC_GORGEES_ACCUSATEUR ;
  - fin de partie : chaque perdant boit UC_GORGEES_PERDANT (UC_GORGEES_WHITE
    si Mr White a devine le mot).

Points (bareme de l appli d origine, cumules sur la soiree) : Civil gagnant
2, Mr White gagnant 6, Undercover gagnant 10.
"""
import random
import unicodedata

from undercover_mots import UC_FAMILLES, paires

# =============================================================
# CONSTANTES
# =============================================================
UC_JOUEURS_MIN = 3
UC_JOUEURS_MAX = 15
UC_FAMILLE_DEFAUT = "classique"

UC_DELAI_LECTURE = 30         # pour decouvrir son mot
UC_DELAI_INDICE = 45          # pour donner son indice
UC_DELAI_VOTE = 150           # debat + vote
UC_DELAI_DEVINE = 60          # Mr White cherche le mot
UC_DELAI_ABSENT = 3           # quand on attend un joueur parti
UC_DELAI_REVELATION = 7.0     # lecture du role de l elimine
UC_DELAI_RETOUR_VOTE = 20     # un revenant a au moins ce temps pour voter

UC_GORGEES_DEMASQUE = 3
UC_GORGEES_CIVIL = 1
UC_GORGEES_ACCUSATEUR = 2
UC_GORGEES_PERDANT = 2
UC_GORGEES_WHITE = 3

UC_POINTS = {"civil": 2, "white": 6, "undercover": 10}
UC_NOMS_ROLES = {"civil": "Civil", "undercover": "Undercover", "white": "Mr White"}

ARTICLES = {"le", "la", "les", "l", "un", "une", "des", "du", "de", "d"}


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
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _vivants(etat):
    return [i for i in range(etat["nb_joueurs"]) if etat["vivants"][i]]


def _presents_vivants(etat):
    return [i for i in _vivants(etat) if not etat["partis"][i]]


def uc_repartition(n):
    """Reglage conseille (undercovers, mr_white) pour n joueurs."""
    if n <= 4:
        return 1, 0
    if n <= 6:
        return 1, 1
    if n <= 9:
        return 2, 1
    if n <= 12:
        return 3, 1
    return 3, 2


def uc_repartition_valide(n, nb_u, nb_w):
    """Au moins un infiltre, et toujours plus de Civils que d infiltres."""
    if nb_u is None or nb_w is None or nb_u < 0 or nb_w < 0:
        return False
    infiltres = nb_u + nb_w
    return infiltres >= 1 and n - infiltres >= infiltres + 1


# =============================================================
# LE MOT DE MR WHITE : comparaison tolerante
# =============================================================
def normaliser(texte):
    """« L'Œuf au plat ! » -> « oeufauplat » : sans accents, articles ni pluriel."""
    if not isinstance(texte, str):
        return ""
    t = texte.lower().replace("œ", "oe").replace("æ", "ae")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(c if c.isalnum() else " " for c in t)
    mots = t.split()
    while len(mots) > 1 and mots[0] in ARTICLES:
        mots.pop(0)
    t = "".join(mots)
    if len(t) > 3 and t[-1] in "sx":
        t = t[:-1]
    return t


def _distance(a, b):
    """Distance d edition (Levenshtein)."""
    if len(a) < len(b):
        a, b = b, a
    prec = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cour = [i]
        for j, cb in enumerate(b, 1):
            cour.append(min(prec[j] + 1, cour[j - 1] + 1, prec[j - 1] + (ca != cb)))
        prec = cour
    return prec[-1]


def mot_trouve(proposition, mot_civil, mot_undercover):
    """La proposition de Mr White vaut-elle le mot des Civils ?"""
    p, c, u = normaliser(proposition), normaliser(mot_civil), normaliser(mot_undercover)
    if not p or not c:
        return False
    if p == c:
        return True
    if len(c) < 5:
        return False   # mot court (Chat, Thé) : orthographe exacte
    tolerance = 1 if len(c) < 8 else 2
    d = _distance(p, c)
    # Plus proche du mot de l Undercover (Vin rouge / Vin rosé) : c est raté.
    return d <= tolerance and d < _distance(p, u)


# =============================================================
# INITIALISATION
# =============================================================
def uc_initialiser(noms, maintenant, famille=None, nb_undercover=None, nb_white=None,
                   points=None, deja=None):
    n = len(noms)
    if famille not in UC_FAMILLES:
        famille = UC_FAMILLE_DEFAUT
    nb_u, nb_w = _entier(nb_undercover), _entier(nb_white)
    message = "Découvrez votre mot en secret."
    if not uc_repartition_valide(n, nb_u, nb_w):
        # Reglage absent : le conseille, sans un mot. Reglage envoye mais
        # impossible pour les PRESENTS (des joueurs partis avant « Rejouer ») :
        # meme repli, mais annonce a la table.
        demande = nb_undercover is not None or nb_white is not None
        nb_u, nb_w = uc_repartition(n)
        if demande:
            message = (f"Réglage ajusté pour {n} joueurs : {nb_u} Undercover, "
                       f"{nb_w} Mr White. " + message)

    # Une paire pas encore jouee ce soir, si possible.
    deja = [d for d in (deja or []) if isinstance(d, str)]
    toutes = paires(famille)
    neuves = [p for p in toutes if p[1] + "|" + p[2] not in deja]
    if not neuves:
        neuves, deja = toutes, []
    categorie, a, b = random.choice(neuves)
    deja.append(a + "|" + b)
    if random.random() < 0.5:
        a, b = b, a

    roles = ["undercover"] * nb_u + ["white"] * nb_w + ["civil"] * (n - nb_u - nb_w)
    random.shuffle(roles)

    points = dict(points) if isinstance(points, dict) else {}
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "famille": famille,
        "repartition": {"undercover": nb_u, "white": nb_w, "civil": n - nb_u - nb_w},
        # SECRETS : jamais diffuses avant la fin (chacun ne recoit que son mot).
        "categorie": categorie,
        "mots": {"civil": a, "undercover": b},
        "roles": roles,
        "deja": deja,
        "vivants": [True] * n,
        "gorgees": [0] * n,
        "points": {nom: _entier(points.get(nom)) or 0 for nom in noms},
        "phase": "lecture",
        "prets": [False] * n,
        "tour": 0,
        "premier": None,
        "ordre": [],
        "parole": 0,
        "departage": None,     # [candidats] pendant un departage
        "votes": {},           # str(votant) -> cible : SECRET jusqu au depouillement
        "num_elimination": 0,
        "elimination": None,   # la derniere, publique
        "eliminations": [],    # l historique {joueur, role, tour}
        "devinette": None,
        "resultat": None,
        "echeance": None,
        "message": message,
    }
    uc_armer(etat, maintenant)
    return etat


# =============================================================
# ETAT PUBLIC
# =============================================================
def _orateur(etat):
    if etat["phase"] != "indices" or etat["parole"] >= len(etat["ordre"]):
        return None
    return etat["ordre"][etat["parole"]]


def uc_etat_public(etat, maintenant):
    fin = etat["phase"] == "fin"
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    connus = {e["joueur"] for e in etat["eliminations"]}
    return {
        "type": "uc_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "famille": etat["famille"],
        "repartition": etat["repartition"],
        "vivants": etat["vivants"],
        "gorgees": etat["gorgees"],
        "points": [etat["points"].get(nom, 0) for nom in etat["noms"]],
        "phase": etat["phase"],
        "prets": etat["prets"],
        "tour": etat["tour"],
        "premier": etat["premier"],
        "ordre": etat["ordre"],
        "parole": etat["parole"],
        "orateur": _orateur(etat),
        "departage": etat["departage"],
        "a_vote": [str(i) in etat["votes"] for i in range(etat["nb_joueurs"])],
        "roles": [r if fin or i in connus else None for i, r in enumerate(etat["roles"])],
        "elimination": etat["elimination"],
        "devinette": etat["devinette"],
        "resultat": etat["resultat"],
        "mots": dict(etat["mots"], categorie=etat["categorie"]) if fin else None,
        "delai": delai,
        "message": etat["message"],
    }


def uc_mot(etat, i):
    """Le mot secret du joueur i (None pour Mr White)."""
    if not 0 <= i < etat["nb_joueurs"]:
        return None
    role = etat["roles"][i]
    if role == "white":
        return {"type": "uc_mot", "index": i, "mot": None, "white": True}
    return {"type": "uc_mot", "index": i, "mot": etat["mots"][role], "white": False}


# =============================================================
# DEROULEMENT
# =============================================================
def _ordre_depuis(etat, premier, parmi):
    n = etat["nb_joueurs"]
    return [(premier + k) % n for k in range(n) if (premier + k) % n in parmi]


def _nouveau_tour(etat, maintenant):
    vivants = _vivants(etat)
    n = etat["nb_joueurs"]
    etat["tour"] += 1
    # Qui commence : au hasard au premier tour, puis le suivant du dernier
    # premier. Jamais Mr White, sauf s il ne reste que lui pour parler.
    if etat["premier"] is None:
        candidats = [i for i in vivants if etat["roles"][i] != "white"] or vivants
        premier = random.choice(candidats)
    else:
        premier = None
        for k in range(1, n + 1):
            j = (etat["premier"] + k) % n
            if etat["vivants"][j] and etat["roles"][j] != "white":
                premier = j
                break
        if premier is None:
            premier = vivants[0]
    etat["premier"] = premier
    etat["ordre"] = _ordre_depuis(etat, premier, set(vivants))
    etat["parole"] = 0
    etat["departage"] = None
    etat["votes"] = {}
    etat["devinette"] = None
    etat["phase"] = "indices"
    etat["message"] = "Tour " + str(etat["tour"]) + " : " + etat["noms"][premier] + " commence."
    uc_armer(etat, maintenant)


def _passer_au_vote(etat, maintenant):
    etat["phase"] = "vote"
    etat["votes"] = {}
    if etat["departage"]:
        noms = [etat["noms"][j] for j in etat["departage"]]
        etat["message"] = "Revote entre " + " et ".join(noms) + "."
    else:
        etat["message"] = "Débattez, puis votez pour éliminer un joueur."
    uc_armer(etat, maintenant)


def _avancer_parole(etat, maintenant):
    etat["parole"] += 1
    # Un orateur mort entre-temps (ne devrait pas arriver) est saute.
    while etat["parole"] < len(etat["ordre"]) and not etat["vivants"][etat["ordre"][etat["parole"]]]:
        etat["parole"] += 1
    if etat["parole"] >= len(etat["ordre"]):
        _passer_au_vote(etat, maintenant)
        return
    j = etat["ordre"][etat["parole"]]
    etat["message"] = "Au tour de " + etat["noms"][j] + " de donner son indice."
    uc_armer(etat, maintenant)


def _depouiller(etat, ev, maintenant):
    candidats = etat["departage"] or _vivants(etat)
    votes = [(int(k), v) for k, v in etat["votes"].items()
             if etat["vivants"][int(k)] and v in candidats]
    compte = {}
    for _, c in votes:
        compte[c] = compte.get(c, 0) + 1
    sort = False
    if not compte:
        tete = list(candidats)
    else:
        haut = max(compte.values())
        tete = sorted(c for c, nb in compte.items() if nb == haut)
    if len(tete) == 1:
        elu = tete[0]
    elif not etat["departage"] and compte:
        # Egalite : les ex aequo redonnent un indice, puis on revote.
        etat["departage"] = tete
        etat["ordre"] = _ordre_depuis(etat, tete[0], set(tete))
        etat["parole"] = 0
        etat["votes"] = {}
        etat["phase"] = "indices"
        noms = [etat["noms"][j] for j in tete]
        etat["message"] = "Égalité entre " + " et ".join(noms) + " : un indice de plus chacun."
        ev.append(_tous({"type": "uc_egalite", "candidats": tete,
                         "votes": [{"votant": v, "cible": c} for v, c in votes]}))
        uc_armer(etat, maintenant)
        return
    else:
        elu = random.choice(tete)
        sort = True
    _eliminer(etat, ev, elu, votes, sort, maintenant)


def _eliminer(etat, ev, j, votes, sort, maintenant):
    etat["vivants"][j] = False
    role = etat["roles"][j]
    bus = []
    if role == "civil":
        bus.append({"joueur": j, "gorgees": UC_GORGEES_CIVIL})
        for v, c in votes:
            if c == j and v != j:
                bus.append({"joueur": v, "gorgees": UC_GORGEES_ACCUSATEUR})
    else:
        bus.append({"joueur": j, "gorgees": UC_GORGEES_DEMASQUE})
    for b in bus:
        etat["gorgees"][b["joueur"]] += b["gorgees"]
    etat["num_elimination"] += 1
    etat["elimination"] = {
        "num": etat["num_elimination"],
        "joueur": j,
        "role": role,
        "votes": [{"votant": v, "cible": c} for v, c in votes],
        "sort": sort,
        "gorgees": bus,
        "tour": etat["tour"],
    }
    etat["eliminations"].append({"joueur": j, "role": role, "tour": etat["tour"]})
    etat["votes"] = {}
    etat["departage"] = None
    nom = etat["noms"][j]
    etat["message"] = nom + (" est désigné par le sort" if sort else " est éliminé") \
        + " : " + UC_NOMS_ROLES[role] + " !"
    ev.append(_tous({"type": "uc_elimination", "elimination": etat["elimination"]}))
    if role == "white":
        etat["phase"] = "mrwhite"
        etat["message"] += " Il tente de deviner le mot."
        absent = etat["partis"][j]
        etat["echeance"] = maintenant + UC_DELAI_REVELATION + (UC_DELAI_ABSENT if absent else UC_DELAI_DEVINE)
        return
    _suite(etat, maintenant)


def _suite(etat, maintenant):
    """Apres une elimination : fin de partie, ou revelation puis tour suivant."""
    civils = [i for i in _vivants(etat) if etat["roles"][i] == "civil"]
    infiltres = [i for i in _vivants(etat) if etat["roles"][i] != "civil"]
    if not infiltres:
        _terminer(etat, "civils")
    elif len(civils) <= 1:
        _terminer(etat, "infiltres")
    else:
        etat["phase"] = "revelation"
        etat["echeance"] = maintenant + UC_DELAI_REVELATION


def _terminer(etat, vainqueur, devin=None):
    n = etat["nb_joueurs"]
    if vainqueur == "civils":
        gagnants = [i for i in range(n) if etat["roles"][i] == "civil"]
    elif vainqueur == "infiltres":
        gagnants = [i for i in range(n) if etat["roles"][i] != "civil"]
    else:
        gagnants = [devin]
    perdants = [i for i in range(n) if i not in gagnants]
    dose = UC_GORGEES_WHITE if vainqueur == "white" else UC_GORGEES_PERDANT
    bus = [{"joueur": i, "gorgees": dose} for i in perdants]
    for b in bus:
        etat["gorgees"][b["joueur"]] += b["gorgees"]
    gains = []
    for i in gagnants:
        p = UC_POINTS[etat["roles"][i]]
        nom = etat["noms"][i]
        etat["points"][nom] = etat["points"].get(nom, 0) + p
        gains.append({"joueur": i, "points": p})
    etat["resultat"] = {"vainqueur": vainqueur, "gagnants": gagnants, "gorgees": bus, "points": gains}
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["ordre"] = []
    etat["departage"] = None
    textes = {"civils": "Les Civils gagnent !", "infiltres": "Les infiltrés gagnent !",
              "white": (etat["noms"][devin] if devin is not None else "Mr White") + " a trouvé le mot : Mr White gagne !"}
    etat["message"] = textes[vainqueur] + " Le mot des Civils était « " + etat["mots"]["civil"] \
        + " », celui de l'Undercover « " + etat["mots"]["undercover"] + " »."


# =============================================================
# ACTIONS
# =============================================================
def _pret(etat, ev, i, message, maintenant):
    if etat["phase"] != "lecture":
        return _erreur(i, "La partie a déjà commencé.")
    etat["prets"][i] = True
    if all(etat["prets"][j] for j in _presents_vivants(etat)):
        _nouveau_tour(etat, maintenant)
    else:
        uc_armer(etat, maintenant)
    return ev


def _fini(etat, ev, i, message, maintenant):
    if etat["phase"] != "indices" or _orateur(etat) != i:
        return _erreur(i, "Ce n'est pas à toi de parler.")
    _avancer_parole(etat, maintenant)
    return ev


def _voter(etat, ev, i, message, maintenant):
    if etat["phase"] != "vote":
        return _erreur(i, "Ce n'est pas le moment de voter.")
    c = _entier(message.get("cible"))
    candidats = etat["departage"] or _vivants(etat)
    if c is None or c == i or c not in candidats:
        return _erreur(i, "Vote pour un autre joueur en lice.")
    etat["votes"][str(i)] = c
    if all(str(j) in etat["votes"] for j in _presents_vivants(etat)):
        _depouiller(etat, ev, maintenant)
    return ev


def _deviner(etat, ev, i, message, maintenant):
    elim = etat["elimination"]
    if etat["phase"] != "mrwhite" or elim is None or elim["joueur"] != i:
        return _erreur(i, "Ce n'est pas à toi de deviner.")
    mot = message.get("mot")
    if not isinstance(mot, str) or not mot.strip():
        return _erreur(i, "Écris le mot des Civils.")
    _resoudre_devinette(etat, ev, i, mot.strip()[:40], maintenant)
    return ev


def _resoudre_devinette(etat, ev, i, mot, maintenant):
    ok = mot is not None and mot_trouve(mot, etat["mots"]["civil"], etat["mots"]["undercover"])
    etat["devinette"] = {"joueur": i, "mot": mot, "correct": ok}
    ev.append(_tous({"type": "uc_devinette", "devinette": etat["devinette"]}))
    if ok:
        _terminer(etat, "white", devin=i)
        return
    nom = etat["noms"][i]
    etat["message"] = (nom + " propose « " + mot + " » : raté !") if mot else (nom + " n'a rien trouvé.")
    _suite(etat, maintenant)


UC_ACTIONS = {
    "uc_pret": _pret,
    "uc_fini": _fini,
    "uc_voter": _voter,
    "uc_deviner": _deviner,
}


def uc_action(etat, i, message, maintenant):
    fn = UC_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
        return []
    if not etat["vivants"][i] and fn is not _deviner:
        return _erreur(i, "Tu es éliminé : tu peux encore débattre, mais plus voter.")
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR
# =============================================================
def uc_armer(etat, maintenant):
    phase = etat["phase"]
    if phase == "lecture":
        attendus = [j for j in _vivants(etat) if not etat["prets"][j]]
        presents = [j for j in attendus if not etat["partis"][j]]
        if etat["echeance"] is None or not presents:
            etat["echeance"] = maintenant + (UC_DELAI_LECTURE if presents else UC_DELAI_ABSENT)
        return
    if phase == "indices":
        j = _orateur(etat)
        if j is None:
            etat["echeance"] = maintenant + UC_DELAI_ABSENT
        else:
            etat["echeance"] = maintenant + (UC_DELAI_ABSENT if etat["partis"][j] else UC_DELAI_INDICE)
        return
    if phase == "vote":
        attendus = [j for j in _presents_vivants(etat) if str(j) not in etat["votes"]]
        etat["echeance"] = maintenant + (UC_DELAI_VOTE if attendus else UC_DELAI_ABSENT)
        return
    if phase == "fin":
        etat["echeance"] = None


def uc_prochaine_echeance(etat):
    return etat["echeance"]


def uc_expirer(etat, maintenant):
    """L echeance est passee : on avance a la place de celui qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    phase = etat["phase"]
    if phase == "lecture":
        _nouveau_tour(etat, maintenant)
    elif phase == "indices":
        _avancer_parole(etat, maintenant)
    elif phase == "vote":
        _depouiller(etat, ev, maintenant)
    elif phase == "mrwhite":
        _resoudre_devinette(etat, ev, etat["elimination"]["joueur"], None, maintenant)
    elif phase == "revelation":
        _nouveau_tour(etat, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def uc_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    _rearmer(etat, index, maintenant)


def uc_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    _rearmer(etat, index, maintenant)


def _rearmer(etat, index, maintenant):
    phase = etat["phase"]
    if phase == "indices" and _orateur(etat) == index:
        uc_armer(etat, maintenant)
    elif phase == "lecture":
        uc_armer(etat, maintenant)
    elif phase == "vote":
        attendus = [j for j in _presents_vivants(etat) if str(j) not in etat["votes"]]
        if not attendus:
            etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + UC_DELAI_ABSENT)
        elif not etat["partis"][index]:
            etat["echeance"] = max(etat["echeance"] or 0, maintenant + UC_DELAI_RETOUR_VOTE)
    elif phase == "mrwhite" and etat["elimination"] and etat["elimination"]["joueur"] == index:
        if etat["partis"][index]:
            etat["echeance"] = min(etat["echeance"], maintenant + UC_DELAI_ABSENT)
        else:
            etat["echeance"] = max(etat["echeance"], maintenant + UC_DELAI_RETOUR_VOTE)
