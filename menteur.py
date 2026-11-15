"""
Moteur de regles de Menteur (bluff aux cartes + roulette des shots).

Meme principe que picolopoly.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
mt_etat et la main privee de chacun apres chaque action acceptee, et arme le
minuteur sur mt_prochaine_echeance.

Regles (le jeu de cartes classique du « menteur », a la roulette) :
  - Chaque manche, on distribue 5 cartes a chaque joueur encore en vie et on
    tire la « carte de la table » (Roi, Dame ou As).
  - A son tour, on pose 1 a 3 cartes face cachee en annoncant qu elles sont
    toutes la carte de la table. Le Joker remplace n importe quelle carte.
  - Le joueur suivant pose a son tour (il accepte l annonce) ou crie
    « Menteur ! ». On retourne alors la derniere pose :
        une carte n est pas celle de la table -> le poseur tire ;
        tout est vrai                          -> celui qui a crie tire.
  - Un joueur sans carte est saute. Si plus personne d autre n a de carte, le
    suivant est oblige de crier « Menteur ».
  - Tirer : chaque joueur a son barillet de 6 coups, une seule balle placee au
    hasard, qui avance d un cran a chaque tir. « Clic » : une gorgee. La
    balle : cul sec, et le joueur est elimine. Dernier en vie : gagnant.

Modes (cartes speciales) :
  classique  6 Rois, 6 Dames, 6 As, 2 Jokers (par tranche de 4 joueurs)
  diable     classique, mais une carte devient le Diable. Il se pose SEUL.
             S il est retourne sur un « Menteur », tous les autres joueurs
             en vie tirent (celui qui l a pose est epargne).
  chaos      5 Rois, 5 Dames, 1 Chaos, 1 Maitre, 3 cartes chacun. Ni l un ni
             l autre n est vrai ou faux : s il est retourne,
               Maitre -> celui qui l a pose choisit qui tire ;
               Chaos  -> chaque joueur en vie choisit quelqu un sur qui tirer
                         (une balle par vote, le Chaos l emporte sur le Maitre).
"""
import random

# =============================================================
# CONSTANTES
# =============================================================
MT_JOUEURS_MIN = 2
MT_JOUEURS_MAX = 8
MT_MODES = ("classique", "diable", "chaos")
MT_MODE_DEFAUT = "classique"

MT_CHAMBRES = 6               # barillet
MT_GORGEES_CLIC = 1           # un tir a vide
MT_POSE_MAX = 3

MT_DELAI_TOUR = 45            # pour poser ou crier
MT_DELAI_CIBLE = 25           # pour choisir une cible (Maitre, Chaos)
MT_DELAI_ABSENT = 3           # quand on attend un joueur parti
MT_DELAI_REVELATION = 5.0     # temps de lecture du retournement...
MT_DELAI_PAR_TIR = 2.4        # ... plus l animation de chaque tir

# Composition d un « jeu » de cartes, pour 4 joueurs au plus. Au-dela, on en
# melange deux : il faut 5 cartes par joueur (3 en chaos).
MT_PAQUETS = {
    "classique": {"R": 6, "D": 6, "A": 6, "J": 2},
    "diable": {"R": 6, "D": 6, "A": 6, "J": 2},
    "chaos": {"R": 5, "D": 5, "C": 1, "M": 1},
}
MT_MAIN = {"classique": 5, "diable": 5, "chaos": 3}
MT_CARTES_TABLE = {"classique": "RDA", "diable": "RDA", "chaos": "RD"}

MT_NOMS_CARTES = {"R": "Roi", "D": "Dame", "A": "As", "J": "Joker",
                  "X": "Diable", "C": "Chaos", "M": "Maître"}


# =============================================================
# OUTILS
# =============================================================
def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _erreur(i, texte):
    return [_prive(i, {"type": "erreur", "message": texte})]


def _vivants(etat):
    return [i for i in range(etat["nb_joueurs"]) if etat["vivants"][i]]


def _entier(v):
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _suivant_vivant(etat, i):
    n = etat["nb_joueurs"]
    for k in range(1, n + 1):
        j = (i + k) % n
        if etat["vivants"][j]:
            return j
    return i


def mt_repondant(etat, i):
    """Qui parle apres la pose de i : le prochain en vie qui a des cartes ;
    s il n y en a pas, le prochain en vie, oblige de crier."""
    n = etat["nb_joueurs"]
    for k in range(1, n):
        j = (i + k) % n
        if etat["vivants"][j] and etat["mains"][j]:
            return j
    return _suivant_vivant(etat, i)


def _creer_paquet(mode, nb_vivants):
    jeux = 1 if nb_vivants <= 4 else 2
    paquet = []
    for carte, nb in MT_PAQUETS[mode].items():
        paquet += [carte] * (nb * jeux)
    if mode == "diable":
        # Une seule carte du Diable, quel que soit le nombre de joueurs.
        k = random.choice([x for x, c in enumerate(paquet) if c != "J"])
        paquet[k] = "X"
    random.shuffle(paquet)
    return paquet


# =============================================================
# INITIALISATION
# =============================================================
def mt_initialiser(noms, maintenant, mode=None):
    if mode not in MT_MODES:
        mode = MT_MODE_DEFAUT
    n = len(noms)
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "mode": mode,
        "vivants": [True] * n,
        "tirs": [0] * n,
        # Position de la balle dans chaque barillet : SECRET, jamais diffuse.
        "balles": [random.randrange(MT_CHAMBRES) for _ in range(n)],
        "gorgees": [0] * n,
        "mains": [[] for _ in range(n)],
        "phase": "jeu",
        "manche": 0,
        "carte_table": None,
        "courant": random.randrange(n),
        "pose": None,          # {"joueur": i, "cartes": [...]} : cartes SECRETES
        "pile": 0,             # cartes posees cette manche
        "revelation": None,    # dernier retournement, public
        "num_revelation": 0,
        "cibles": None,        # {"raison": "maitre"|"chaos", "choix": {str(i): cible|None}}
        "suite": None,         # le prochain a commencer apres la revelation
        "echeance": None,
        "gagnant": None,
        "message": "",
    }
    _nouvelle_manche(etat, etat["courant"], maintenant)
    return etat


def _nouvelle_manche(etat, premier, maintenant):
    vivants = _vivants(etat)
    etat["manche"] += 1
    paquet = _creer_paquet(etat["mode"], len(vivants))
    taille = MT_MAIN[etat["mode"]]
    etat["mains"] = [[] for _ in range(etat["nb_joueurs"])]
    for i in vivants:
        etat["mains"][i] = sorted(paquet[:taille], key="RDAJXCM".index)
        paquet = paquet[taille:]
    etat["carte_table"] = random.choice(MT_CARTES_TABLE[etat["mode"]])
    etat["pose"] = None
    etat["pile"] = 0
    etat["cibles"] = None
    etat["phase"] = "jeu"
    if not etat["vivants"][premier]:
        premier = _suivant_vivant(etat, premier)
    etat["courant"] = premier
    etat["message"] = "Manche " + str(etat["manche"]) + " : carte de la table, " \
        + MT_NOMS_CARTES[etat["carte_table"]] + "."
    mt_armer(etat, maintenant)


# =============================================================
# ETAT PUBLIC
# =============================================================
def mt_etat_public(etat, maintenant):
    pose = etat["pose"]
    cibles = etat["cibles"]
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    return {
        "type": "mt_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "mode": etat["mode"],
        "vivants": etat["vivants"],
        "tirs": etat["tirs"],
        "chambres": MT_CHAMBRES,
        "gorgees": etat["gorgees"],
        "nb_cartes": [len(m) for m in etat["mains"]],
        "phase": etat["phase"],
        "manche": etat["manche"],
        "carte_table": etat["carte_table"],
        "courant": etat["courant"],
        "pose": None if pose is None else {"joueur": pose["joueur"], "nb": len(pose["cartes"])},
        "pile": etat["pile"],
        "revelation": etat["revelation"],
        "cibles": None if cibles is None else {
            "raison": cibles["raison"],
            "tireurs": [int(k) for k in cibles["choix"]],
            "choisis": [int(k) for k, v in cibles["choix"].items() if v is not None],
        },
        "delai": delai,
        "gagnant": etat["gagnant"],
        "message": etat["message"],
    }


def mt_main(etat, i):
    if i >= etat["nb_joueurs"]:
        return None
    return {"type": "mt_main", "index": i, "main": etat["mains"][i]}


# =============================================================
# ACTIONS
# =============================================================
def _poser(etat, ev, i, message, maintenant):
    if etat["phase"] != "jeu" or etat["courant"] != i:
        return _erreur(i, "Ce n'est pas à toi de jouer.")
    main = etat["mains"][i]
    if not main:
        return _erreur(i, "Tu n'as plus de carte : tu dois crier Menteur.")
    brut = message.get("cartes")
    if not isinstance(brut, list):
        return _erreur(i, "Choisis tes cartes.")
    indices = [_entier(x) for x in brut]
    if (not 1 <= len(indices) <= MT_POSE_MAX or None in indices
            or len(set(indices)) != len(indices)
            or any(not 0 <= k < len(main) for k in indices)):
        return _erreur(i, "Pose de 1 à 3 cartes.")
    cartes = [main[k] for k in indices]
    if "X" in cartes and len(cartes) > 1:
        return _erreur(i, "Le Diable se pose seul.")
    for k in sorted(indices, reverse=True):
        main.pop(k)
    etat["pose"] = {"joueur": i, "cartes": cartes}
    etat["pile"] += len(cartes)
    etat["courant"] = mt_repondant(etat, i)
    nom = etat["noms"][i]
    etat["message"] = nom + " pose " + str(len(cartes)) + " " \
        + MT_NOMS_CARTES[etat["carte_table"]] + ("s" if len(cartes) > 1 else "") + "."
    ev.append(_tous({"type": "mt_pose", "joueur": i, "nb": len(cartes)}))
    mt_armer(etat, maintenant)
    return ev


def _tirer(etat, j):
    """Un coup de barillet pour j. Renvoie le resultat public."""
    numero = etat["tirs"][j]
    etat["tirs"][j] += 1
    if numero == etat["balles"][j] or etat["tirs"][j] >= MT_CHAMBRES:
        etat["vivants"][j] = False
        etat["mains"][j] = []
        return {"joueur": j, "numero": numero + 1, "resultat": "balle"}
    etat["gorgees"][j] += MT_GORGEES_CLIC
    return {"joueur": j, "numero": numero + 1, "resultat": "clic",
            "gorgees": MT_GORGEES_CLIC}


def _executer_tirs(etat, victimes):
    """Tire sur chacun dans l ordre ; on s arrete quand il ne reste qu un vivant
    (il faut un gagnant, meme sous le Chaos)."""
    resultats = []
    for j in victimes:
        if len(_vivants(etat)) <= 1:
            break
        if etat["vivants"][j]:
            resultats.append(_tirer(etat, j))
    return resultats


def _crier(etat, ev, i, message, maintenant):
    pose = etat["pose"]
    if etat["phase"] != "jeu" or etat["courant"] != i:
        return _erreur(i, "Ce n'est pas à toi de jouer.")
    if pose is None or pose["joueur"] == i:
        return _erreur(i, "Il n'y a rien à contester.")
    cartes = pose["cartes"]
    poseur = pose["joueur"]
    table = etat["carte_table"]
    rev = {
        "num": etat["num_revelation"] + 1,
        "appelant": i,
        "poseur": poseur,
        "cartes": list(cartes),
        "carte_table": table,
        "special": None,
        "menteur": None,
        "votes": [],
        "tirs": [],
    }
    etat["num_revelation"] += 1
    etat["revelation"] = rev
    nom_a, nom_p = etat["noms"][i], etat["noms"][poseur]

    if "C" in cartes or "M" in cartes:
        # Ni vrai ni faux : on passe au choix des cibles.
        raison = "chaos" if "C" in cartes else "maitre"
        rev["special"] = raison
        tireurs = _vivants(etat) if raison == "chaos" else [poseur]
        etat["cibles"] = {"raison": raison, "choix": {str(t): None for t in tireurs}}
        etat["phase"] = "cible"
        etat["suite"] = poseur
        if raison == "chaos":
            etat["message"] = "CHAOS ! Chacun choisit sur qui tirer."
        else:
            etat["message"] = "Le Maître ! " + nom_p + " choisit qui tire."
        ev.append(_tous({"type": "mt_revelation", "revelation": rev}))
        mt_armer(etat, maintenant)
        return ev

    if "X" in cartes:
        rev["special"] = "diable"
        victimes = [j for j in _vivants(etat) if j != poseur]
        etat["message"] = "Le Diable ! Tout le monde tire, sauf " + nom_p + "."
        suite = poseur
    else:
        menteur = any(c not in (table, "J") for c in cartes)
        rev["menteur"] = menteur
        perdant = poseur if menteur else i
        victimes = [perdant]
        if menteur:
            etat["message"] = nom_p + " mentait ! " + nom_p + " tire."
        else:
            etat["message"] = nom_p + " disait vrai. " + nom_a + " tire."
        suite = perdant
    rev["tirs"] = _executer_tirs(etat, victimes)
    _apres_tirs(etat, suite, maintenant)
    ev.append(_tous({"type": "mt_revelation", "revelation": rev}))
    return ev


def _apres_tirs(etat, suite, maintenant):
    vivants = _vivants(etat)
    rev = etat["revelation"]
    morts = [t["joueur"] for t in rev["tirs"] if t["resultat"] == "balle"]
    if morts:
        etat["message"] += " " + ", ".join(etat["noms"][j] for j in morts) \
            + (" tombent." if len(morts) > 1 else " tombe : cul sec !")
    etat["cibles"] = None
    etat["pose"] = None
    etat["suite"] = suite
    if len(vivants) <= 1:
        etat["gagnant"] = vivants[0] if vivants else None
        etat["phase"] = "fin"
        etat["echeance"] = None
        etat["courant"] = None
        if vivants:
            etat["message"] += " " + etat["noms"][vivants[0]] + " gagne la partie !"
        return
    etat["phase"] = "revelation"
    etat["echeance"] = maintenant + MT_DELAI_REVELATION + MT_DELAI_PAR_TIR * len(rev["tirs"])


def _choisir_cible(etat, ev, i, message, maintenant):
    cibles = etat["cibles"]
    if etat["phase"] != "cible" or cibles is None or str(i) not in cibles["choix"]:
        return _erreur(i, "Tu n'as pas de cible à choisir.")
    if cibles["choix"][str(i)] is not None:
        return _erreur(i, "Ton choix est déjà fait.")
    c = _entier(message.get("cible"))
    if c is None or not 0 <= c < etat["nb_joueurs"] or not etat["vivants"][c] or c == i:
        return _erreur(i, "Choisis un autre joueur en vie.")
    cibles["choix"][str(i)] = c
    if all(v is not None for v in cibles["choix"].values()):
        _resoudre_cibles(etat, ev, maintenant)
    return ev


def _resoudre_cibles(etat, ev, maintenant):
    rev = etat["revelation"]
    choix = etat["cibles"]["choix"]
    votes = [{"tireur": int(k), "cible": v} for k, v in choix.items()]
    rev["votes"] = votes
    rev["tirs"] = _executer_tirs(etat, [v["cible"] for v in votes])
    # Le premier vise commence la manche suivante.
    suite = votes[0]["cible"] if votes else etat["suite"]
    if rev["special"] == "maitre":
        etat["message"] = etat["noms"][rev["poseur"]] + " désigne " + etat["noms"][suite] + "."
    else:
        etat["message"] = "Le Chaos a parlé."
    _apres_tirs(etat, suite, maintenant)
    ev.append(_tous({"type": "mt_tirs", "revelation": rev}))


MT_ACTIONS = {
    "mt_poser": _poser,
    "mt_menteur": _crier,
    "mt_cible": _choisir_cible,
}


def mt_action(etat, i, message, maintenant):
    fn = MT_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
        return []
    if not etat["vivants"][i]:
        return _erreur(i, "Tu es éliminé : tu regardes la fin de la partie.")
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR : choix par defaut d un joueur absent ou trop lent
# =============================================================
def mt_attendu(etat):
    """(index, nature) du joueur que la table attend, ou None."""
    if etat["phase"] == "jeu" and etat["courant"] is not None:
        return etat["courant"], "tour"
    if etat["phase"] == "cible" and etat["cibles"]:
        for k, v in etat["cibles"]["choix"].items():
            if v is None and not etat["partis"][int(k)]:
                return int(k), "cible"
        for k, v in etat["cibles"]["choix"].items():
            if v is None:
                return int(k), "cible"
    return None


def mt_armer(etat, maintenant):
    att = mt_attendu(etat)
    if att is None:
        etat["echeance"] = None
        return
    i, nature = att
    if etat["phase"] == "cible":
        # Un absent ne fait pas attendre les presents : son choix part vite.
        absents = [int(k) for k, v in etat["cibles"]["choix"].items()
                   if v is None and etat["partis"][int(k)]]
        presents = [int(k) for k, v in etat["cibles"]["choix"].items()
                    if v is None and not etat["partis"][int(k)]]
        if absents and not presents:
            etat["echeance"] = maintenant + MT_DELAI_ABSENT
        else:
            etat["echeance"] = maintenant + MT_DELAI_CIBLE
        return
    if etat["partis"][i]:
        etat["echeance"] = maintenant + MT_DELAI_ABSENT
    else:
        etat["echeance"] = maintenant + (MT_DELAI_TOUR if nature == "tour" else MT_DELAI_CIBLE)


def mt_prochaine_echeance(etat):
    return etat["echeance"]


def mt_expirer(etat, maintenant):
    """L echeance est passee : on joue a la place de celui qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    if etat["phase"] == "revelation":
        _nouvelle_manche(etat, etat["suite"] if etat["suite"] is not None else 0, maintenant)
        etat["version"] = avant + 1
        return ev
    if etat["phase"] == "jeu":
        i = etat["courant"]
        if not etat["mains"][i]:
            _crier(etat, ev, i, {}, maintenant)
        else:
            # Par defaut on pose une seule carte, de preference une vraie.
            main = etat["mains"][i]
            vraies = [k for k, c in enumerate(main) if c in (etat["carte_table"], "J")]
            k = vraies[0] if vraies else random.randrange(len(main))
            _poser(etat, ev, i, {"cartes": [k]}, maintenant)
        etat["version"] = avant + 1
        return ev
    if etat["phase"] == "cible":
        # Ceux qui n ont pas choisi a temps visent au hasard.
        cibles = etat["cibles"]
        for k, v in list(cibles["choix"].items()):
            if v is None:
                t = int(k)
                cibles["choix"][k] = random.choice([j for j in _vivants(etat) if j != t])
        _resoudre_cibles(etat, ev, maintenant)
        etat["version"] = avant + 1
        return ev
    etat["echeance"] = None
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def mt_joueur_parti(etat, index, maintenant):
    if index >= etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    _rearmer_si_attendu(etat, index, maintenant)


def mt_joueur_revenu(etat, index, maintenant):
    if index >= etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    _rearmer_si_attendu(etat, index, maintenant)


def _rearmer_si_attendu(etat, index, maintenant):
    att = mt_attendu(etat)
    if etat["phase"] == "cible" and etat["cibles"] and str(index) in etat["cibles"]["choix"]:
        mt_armer(etat, maintenant)
    elif att is not None and att[0] == index:
        mt_armer(etat, maintenant)
