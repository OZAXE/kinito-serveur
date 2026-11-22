"""
Moteur de regles du Juste Pinte (estimation, prefixe jp_).

Meme principe qu undercover.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
jp_etat apres chaque action acceptee et arme le minuteur sur
jp_prochaine_echeance. Chiffres : justepinte_config.json, questions :
justepinte_questions.py.

Regles :
  - Une partie = N manches (5, 10 ou 15). Chaque manche pose une question a
    reponse chiffree, jamais deja posee ce soir dans ce salon si possible.
  - Phase « question » : chacun tape un nombre en secret (modifiable). La
    phase finit quand tous les presents ont repondu, ou a l echeance.
  - Phase « revelation » : les reponses et la bonne valeur sont devoilees.
    On classe par ecart a la bonne reponse (meme question pour tous : l unite
    ne compte pas). Ex aequo = meme rang.
  - Points : 3 / 2 / 1 aux trois premiers rangs, +1 « dans le mille »
    (a moins de 5 % de la reponse, ou d un an pour une annee, ou de la
    tolerance propre a la question).
  - Gorgees : le dernier boit 2, l avant-dernier 1 a partir de 5 repondants,
    un present sans reponse boit 2 ; « dans le mille » distribue 3.
  - On passe a la manche suivante quand tous les presents ont touche
    « Suivant », ou a l echeance. Fin : le dernier au classement boit 3.
"""
import json
import math
import os
import random

from justepinte_questions import JP_CATEGORIES, questions, cle

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "justepinte_config.json"), encoding="utf-8") as _f:
    JP_CONFIG = json.load(_f)

JP_JOUEURS_MIN = JP_CONFIG["joueurs"]["min"]
JP_JOUEURS_MAX = JP_CONFIG["joueurs"]["max"]
_D = JP_CONFIG["delais"]
_G = JP_CONFIG["gorgees"]


# =============================================================
# OUTILS
# =============================================================
def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _erreur(i, texte):
    return [_prive(i, {"type": "erreur", "message": texte})]


def _nombre(v):
    """Un nombre fini raisonnable, ou None. Accepte "12,5" et "1 000"."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        v = v.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
        if len(v) > 20:
            return None
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(x) or abs(x) > JP_CONFIG["valeur_max"]:
        return None
    return int(x) if x == int(x) else round(x, 6)


def _presents(etat):
    return [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]


def tolerance(q):
    """Ecart maximal pour etre « dans le mille »."""
    _, _, reponse, unite, tol, _ = q
    if tol is not None:
        return tol
    if unite == "année":
        return JP_CONFIG["mille"]["annee"]
    return abs(reponse) * JP_CONFIG["mille"]["pourcent"] / 100


# =============================================================
# CREATION
# =============================================================
def jp_initialiser(noms, maintenant, manches=None, categories=None, deja=None):
    n = len(noms)
    choix = JP_CONFIG["manches"]["choix"]
    try:
        manches = int(manches)
    except (TypeError, ValueError, OverflowError):
        manches = None
    if manches not in choix:
        manches = JP_CONFIG["manches"]["defaut"]
    cats = [c for c in (categories or []) if isinstance(c, str) and c in JP_CATEGORIES]
    if not cats:
        cats = list(JP_CATEGORIES)

    # Des questions pas encore posees ce soir, si possible.
    deja = [d for d in (deja or []) if isinstance(d, str)]
    banque = questions(cats)
    neuves = [q for q in banque if cle(q) not in deja]
    if len(neuves) < manches:
        deja = [d for d in deja if d not in {cle(q) for q in banque}]
        neuves = banque
    tirage = random.sample(neuves, min(manches, len(neuves)))
    deja += [cle(q) for q in tirage]

    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "categories": cats,
        "nb_manches": len(tirage),
        # SECRET : les reponses des questions a venir et de la question en cours.
        "tirage": [list(q) for q in tirage],
        "deja": deja,
        "manche": 0,                 # indice de la manche en cours
        "phase": "question",
        "reponses": {},              # str(joueur) -> nombre : SECRET jusqu a la revelation
        "suivants": [False] * n,
        "points": [0] * n,
        "gorgees": [0] * n,
        "resultat": None,            # resultat de la derniere manche (public)
        "historique": [],            # [{question, reponse, mille: [joueurs]}]
        "final": None,
        "echeance": None,
        "message": "Première question !",
    }
    _armer(etat, maintenant)
    return etat


# =============================================================
# ETAT PUBLIC
# =============================================================
def _question_publique(q, avec_reponse):
    categorie, texte, reponse, unite, _, info = q
    pub = {"categorie": categorie, "nom_categorie": JP_CATEGORIES.get(categorie, categorie),
           "texte": texte, "unite": unite}
    if avec_reponse:
        pub["reponse"] = reponse
        pub["info"] = info
        pub["tolerance"] = tolerance(q)
    return pub


def jp_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    phase = etat["phase"]
    question = None
    if phase in ("question", "revelation") and etat["manche"] < etat["nb_manches"]:
        question = _question_publique(etat["tirage"][etat["manche"]], phase == "revelation")
    return {
        "type": "jp_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "phase": phase,
        "manche": etat["manche"] + (0 if phase == "fin" else 1),
        "nb_manches": etat["nb_manches"],
        "categories": etat["categories"],
        "question": question,
        "a_repondu": [str(i) in etat["reponses"] for i in range(etat["nb_joueurs"])],
        "suivants": etat["suivants"],
        "points": etat["points"],
        "gorgees": etat["gorgees"],
        # Les reponses de chacun ne sont publiques qu une fois la manche revelee.
        "resultat": etat["resultat"] if phase in ("revelation", "fin") else None,
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def jp_reponse(etat, i):
    """La reponse deja envoyee par le joueur i (pour une page rechargee)."""
    if etat["phase"] != "question" or not 0 <= i < etat["nb_joueurs"]:
        return None
    if str(i) not in etat["reponses"]:
        return None
    return {"type": "jp_reponse", "index": i, "valeur": etat["reponses"][str(i)]}


# =============================================================
# DEROULEMENT
# =============================================================
def _reveler(etat, ev, maintenant):
    """Fin de la question : classement, points, gorgees."""
    q = etat["tirage"][etat["manche"]]
    reponse = q[2]
    tol = tolerance(q)
    pts = JP_CONFIG["points"]
    repondants = sorted((abs(v - reponse), int(j), v) for j, v in etat["reponses"].items())
    lignes = []
    rang, precedent = 0, None
    for k, (ecart, j, v) in enumerate(repondants):
        if ecart != precedent:
            rang, precedent = k, ecart
        mille = ecart <= tol + 1e-9
        gain = (pts[rang] if rang < len(pts) else 0) + (JP_CONFIG["points_mille"] if mille else 0)
        lignes.append({"joueur": j, "valeur": v, "ecart": round(ecart, 6), "rang": rang + 1,
                       "points": gain, "mille": mille, "boit": 0})
    # Gorgees : le pire rang des repondants (s ils sont au moins 2), puis
    # l avant-dernier rang a partir de 5 repondants.
    if len(lignes) >= 2:
        rangs = sorted({l["rang"] for l in lignes})
        if len(rangs) >= 2:
            for l in lignes:
                if l["rang"] == rangs[-1] and not l["mille"]:
                    l["boit"] = _G["dernier"]
            if len(lignes) >= _G["avant_dernier_des"] and len(rangs) >= 3:
                for l in lignes:
                    if l["rang"] == rangs[-2] and not l["mille"]:
                        l["boit"] = _G["avant_dernier"]
    sans = [j for j in _presents(etat) if str(j) not in etat["reponses"]]
    for l in lignes:
        etat["points"][l["joueur"]] += l["points"]
        etat["gorgees"][l["joueur"]] += l["boit"]
    for j in sans:
        etat["gorgees"][j] += _G["sans_reponse"]
    mille = [l["joueur"] for l in lignes if l["mille"]]
    etat["resultat"] = {
        "manche": etat["manche"] + 1,
        "lignes": lignes,
        "sans_reponse": [{"joueur": j, "boit": _G["sans_reponse"]} for j in sans],
        "mille": [{"joueur": j, "distribue": _G["mille_distribue"]} for j in mille],
    }
    etat["historique"].append({"question": q[1], "reponse": reponse, "unite": q[3], "mille": mille})
    etat["phase"] = "revelation"
    etat["suivants"] = [False] * etat["nb_joueurs"]
    noms = etat["noms"]
    if mille:
        etat["message"] = ("Dans le mille : " + ", ".join(noms[j] for j in mille)
                           + f" ! Distribue {_G['mille_distribue']} gorgées.")
    elif lignes:
        etat["message"] = f"Le plus proche : {noms[lignes[0]['joueur']]}."
    else:
        etat["message"] = "Personne n'a répondu !"
    ev.append(_tous({"type": "jp_revelation", "resultat": etat["resultat"]}))
    _armer(etat, maintenant)


def _manche_suivante(etat, ev, maintenant):
    etat["manche"] += 1
    etat["reponses"] = {}
    etat["suivants"] = [False] * etat["nb_joueurs"]
    if etat["manche"] >= etat["nb_manches"]:
        _terminer(etat, ev)
        return
    etat["phase"] = "question"
    etat["resultat"] = None
    reste = etat["nb_manches"] - etat["manche"]
    etat["message"] = "Dernière question !" if reste == 1 else f"Question {etat['manche'] + 1}."
    _armer(etat, maintenant)


def _terminer(etat, ev):
    n = etat["nb_joueurs"]
    pts = etat["points"]
    classement = sorted(range(n), key=lambda j: (-pts[j], etat["gorgees"][j], j))
    meilleur = max(pts) if n else 0
    gagnants = [j for j in range(n) if pts[j] == meilleur]
    pire = min(pts) if n else 0
    derniers = [j for j in range(n) if pts[j] == pire and pts[j] != meilleur]
    for j in derniers:
        etat["gorgees"][j] += _G["fin_dernier"]
    etat["final"] = {
        "classement": classement,
        "gagnants": gagnants,
        "derniers": [{"joueur": j, "boit": _G["fin_dernier"]} for j in derniers],
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["message"] = "Victoire de " + ", ".join(etat["noms"][j] for j in gagnants) + " !"
    ev.append(_tous({"type": "jp_fin", "final": etat["final"]}))


def _repondre(etat, ev, i, message, maintenant):
    if etat["phase"] != "question":
        return _erreur(i, "Trop tard, la réponse est déjà révélée.")
    v = _nombre(message.get("valeur"))
    if v is None:
        return _erreur(i, "Tape un nombre.")
    etat["reponses"][str(i)] = v
    ev.append(_prive(i, {"type": "jp_reponse", "index": i, "valeur": v}))
    if all(str(j) in etat["reponses"] for j in _presents(etat)):
        _reveler(etat, ev, maintenant)
    return ev


def _suivant(etat, ev, i, message, maintenant):
    if etat["phase"] != "revelation":
        return []
    if etat["suivants"][i]:
        return []
    etat["suivants"][i] = True
    if all(etat["suivants"][j] for j in _presents(etat)):
        _manche_suivante(etat, ev, maintenant)
    return ev


JP_ACTIONS = {
    "jp_repondre": _repondre,
    "jp_suivant": _suivant,
}


def jp_action(etat, i, message, maintenant):
    fn = JP_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
        return []
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR
# =============================================================
def _armer(etat, maintenant):
    phase = etat["phase"]
    presents = _presents(etat)
    if phase == "question":
        attendus = [j for j in presents if str(j) not in etat["reponses"]]
        etat["echeance"] = maintenant + (_D["question"] if attendus else _D["absent"])
    elif phase == "revelation":
        attendus = [j for j in presents if not etat["suivants"][j]]
        etat["echeance"] = maintenant + (_D["revelation"] if attendus else _D["absent"])
    else:
        etat["echeance"] = None


def jp_prochaine_echeance(etat):
    return etat["echeance"]


def jp_expirer(etat, maintenant):
    """L echeance est passee : on avance sans ceux qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    if etat["phase"] == "question":
        _reveler(etat, ev, maintenant)
    elif etat["phase"] == "revelation":
        _manche_suivante(etat, ev, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def jp_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    _rearmer(etat, maintenant)


def jp_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "question" and str(index) not in etat["reponses"]:
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])


def _rearmer(etat, maintenant):
    """Un depart : si plus personne n est attendu, on avance vite."""
    phase = etat["phase"]
    presents = _presents(etat)
    if phase == "question":
        attendus = [j for j in presents if str(j) not in etat["reponses"]]
    elif phase == "revelation":
        attendus = [j for j in presents if not etat["suivants"][j]]
    else:
        return
    if not attendus:
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + _D["absent"])
