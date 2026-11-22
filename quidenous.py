"""
Moteur de regles de Qui de nous ? (vote social, prefixe qn_).

Meme principe que justepinte.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
qn_etat apres chaque action acceptee et arme le minuteur sur
qn_prochaine_echeance. Chiffres : quidenous_config.json, cartes :
quidenous_cartes.py.

Regles :
  - Une partie = N manches (5, 10 ou 15). Chaque manche tire une carte
    « Qui de nous... » du paquet choisi (gentil, epice ou melange), jamais
    deja tiree ce soir dans ce salon si possible.
  - Phase « vote » : chacun designe en secret UN autre joueur (cible), et
    parie sur celui qui sera le plus designe (pari, soi-meme compris).
    Modifiable. La phase finit quand tous les presents ont vote, ou a
    l echeance.
  - Phase « revelation » : le ou les plus designes (ex aequo) boivent 2.
    Un bon pari rapporte 1 point, un mauvais fait boire 1, un present sans
    vote boit 1. Votes « anonymes » (par defaut) : seul le compte par joueur
    est montre ; « a decouvert » : on voit qui a vote pour qui.
  - On passe a la manche suivante quand tous les presents ont touche
    « Suivant », ou a l echeance. Fin : classement aux points, le titre de
    la soiree au plus designe, et le dernier au classement boit 2.
"""
import json
import os
import random

from quidenous_cartes import QN_PAQUETS, cartes, cle

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "quidenous_config.json"), encoding="utf-8") as _f:
    QN_CONFIG = json.load(_f)

QN_JOUEURS_MIN = QN_CONFIG["joueurs"]["min"]
QN_JOUEURS_MAX = QN_CONFIG["joueurs"]["max"]
_D = QN_CONFIG["delais"]
_G = QN_CONFIG["gorgees"]


# =============================================================
# OUTILS
# =============================================================
def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _erreur(i, texte):
    return [_prive(i, {"type": "erreur", "message": texte})]


def _presents(etat):
    return [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]


def _indice(v, n):
    if isinstance(v, bool):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if 0 <= v < n else None


# =============================================================
# CREATION
# =============================================================
def qn_initialiser(noms, maintenant, manches=None, paquet=None, anonyme=True, deja=None):
    n = len(noms)
    choix = QN_CONFIG["manches"]["choix"]
    try:
        manches = int(manches)
    except (TypeError, ValueError, OverflowError):
        manches = None
    if manches not in choix:
        manches = QN_CONFIG["manches"]["defaut"]
    if paquet not in QN_PAQUETS:
        paquet = QN_CONFIG["paquet_defaut"]

    # Des cartes pas encore tirees ce soir, si possible.
    deja = [d for d in (deja or []) if isinstance(d, str)]
    banque = cartes(paquet)
    neuves = [c for c in banque if cle(c) not in deja]
    if len(neuves) < manches:
        deja = [d for d in deja if d not in {cle(c) for c in banque}]
        neuves = banque
    tirage = random.sample(neuves, min(manches, len(neuves)))
    deja += [cle(c) for c in tirage]

    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "paquet": paquet,
        "anonyme": anonyme is not False,
        "nb_manches": len(tirage),
        "tirage": [list(c) for c in tirage],
        "deja": deja,
        "manche": 0,
        "phase": "vote",
        "votes": {},                 # str(joueur) -> {"cible", "pari"} : SECRET jusqu a la revelation
        "suivants": [False] * n,
        "points": [0] * n,
        "gorgees": [0] * n,
        "designations": [0] * n,     # total des votes recus dans la partie
        "resultat": None,
        "historique": [],            # [{carte, designes: [joueurs]}]
        "final": None,
        "echeance": None,
        "message": "Première carte !",
    }
    _armer(etat, maintenant)
    return etat


# =============================================================
# ETAT PUBLIC
# =============================================================
def qn_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    phase = etat["phase"]
    carte = None
    if phase in ("vote", "revelation") and etat["manche"] < etat["nb_manches"]:
        p, texte = etat["tirage"][etat["manche"]]
        carte = {"paquet": p, "texte": texte}
    return {
        "type": "qn_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "phase": phase,
        "manche": etat["manche"] + (0 if phase == "fin" else 1),
        "nb_manches": etat["nb_manches"],
        "paquet": etat["paquet"],
        "anonyme": etat["anonyme"],
        "carte": carte,
        "a_vote": [str(i) in etat["votes"] for i in range(etat["nb_joueurs"])],
        "suivants": etat["suivants"],
        "points": etat["points"],
        "gorgees": etat["gorgees"],
        "designations": etat["designations"],
        # Les votes ne sont publics qu une fois la manche revelee.
        "resultat": etat["resultat"] if phase in ("revelation", "fin") else None,
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def qn_vote(etat, i):
    """Le vote deja envoye par le joueur i (pour une page rechargee)."""
    if etat["phase"] != "vote" or not 0 <= i < etat["nb_joueurs"]:
        return None
    v = etat["votes"].get(str(i))
    if v is None:
        return None
    return {"type": "qn_vote", "index": i, "cible": v["cible"], "pari": v["pari"]}


# =============================================================
# DEROULEMENT
# =============================================================
def _reveler(etat, ev, maintenant):
    """Fin du vote : les plus designes, les paris, les gorgees."""
    n = etat["nb_joueurs"]
    compte = [0] * n
    for v in etat["votes"].values():
        compte[v["cible"]] += 1
    haut = max(compte) if n else 0
    designes = [j for j in range(n) if haut > 0 and compte[j] == haut]
    boit = [0] * n
    for j in designes:
        boit[j] += _G["designe"]
    paris = []
    for cle_j, v in sorted(etat["votes"].items(), key=lambda kv: int(kv[0])):
        j = int(cle_j)
        bon = v["pari"] in designes
        if bon:
            etat["points"][j] += QN_CONFIG["points"]["bon_pari"]
        else:
            boit[j] += _G["mauvais_pari"]
        paris.append({"joueur": j, "pari": v["pari"], "bon": bon})
    sans = [j for j in _presents(etat) if str(j) not in etat["votes"]]
    for j in sans:
        boit[j] += _G["sans_vote"]
    for j in range(n):
        etat["gorgees"][j] += boit[j]
        etat["designations"][j] += compte[j]
    resultat = {
        "manche": etat["manche"] + 1,
        "compte": compte,
        "designes": designes,
        "paris": paris,
        "sans_vote": sans,
        "boit": boit,
    }
    if not etat["anonyme"]:
        resultat["votes"] = [{"joueur": int(k), "cible": v["cible"]}
                             for k, v in sorted(etat["votes"].items(), key=lambda kv: int(kv[0]))]
    etat["resultat"] = resultat
    etat["historique"].append({"carte": etat["tirage"][etat["manche"]][1], "designes": designes})
    etat["phase"] = "revelation"
    etat["suivants"] = [False] * n
    noms = etat["noms"]
    if designes:
        qui = ", ".join(noms[j] for j in designes)
        etat["message"] = (f"C'est {qui} !" if len(designes) == 1 else f"Égalité : {qui} !")
    else:
        etat["message"] = "Personne n'a voté !"
    ev.append(_tous({"type": "qn_revelation", "resultat": resultat}))
    _armer(etat, maintenant)


def _manche_suivante(etat, ev, maintenant):
    etat["manche"] += 1
    etat["votes"] = {}
    etat["suivants"] = [False] * etat["nb_joueurs"]
    if etat["manche"] >= etat["nb_manches"]:
        _terminer(etat, ev)
        return
    etat["phase"] = "vote"
    etat["resultat"] = None
    reste = etat["nb_manches"] - etat["manche"]
    etat["message"] = "Dernière carte !" if reste == 1 else f"Carte {etat['manche'] + 1}."
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
    haut = max(etat["designations"]) if n else 0
    titre = [j for j in range(n) if haut > 0 and etat["designations"][j] == haut]
    etat["final"] = {
        "classement": classement,
        "gagnants": gagnants,
        "derniers": [{"joueur": j, "boit": _G["fin_dernier"]} for j in derniers],
        "titre": titre,
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["message"] = "Meilleur devin : " + ", ".join(etat["noms"][j] for j in gagnants) + " !"
    ev.append(_tous({"type": "qn_fin", "final": etat["final"]}))


def _voter(etat, ev, i, message, maintenant):
    if etat["phase"] != "vote":
        return _erreur(i, "Trop tard, les votes sont révélés.")
    n = etat["nb_joueurs"]
    cible = _indice(message.get("cible"), n)
    pari = _indice(message.get("pari"), n)
    if cible is None or cible == i:
        return _erreur(i, "Désigne un autre joueur.")
    if etat["partis"][cible]:
        return _erreur(i, "Ce joueur a quitté la table.")
    if pari is None:
        return _erreur(i, "Parie sur le joueur le plus désigné.")
    etat["votes"][str(i)] = {"cible": cible, "pari": pari}
    ev.append(_prive(i, {"type": "qn_vote", "index": i, "cible": cible, "pari": pari}))
    if all(str(j) in etat["votes"] for j in _presents(etat)):
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


QN_ACTIONS = {
    "qn_voter": _voter,
    "qn_suivant": _suivant,
}


def qn_action(etat, i, message, maintenant):
    fn = QN_ACTIONS.get(message.get("type"))
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
def _attendus(etat):
    presents = _presents(etat)
    if etat["phase"] == "vote":
        return [j for j in presents if str(j) not in etat["votes"]]
    if etat["phase"] == "revelation":
        return [j for j in presents if not etat["suivants"][j]]
    return []


def _armer(etat, maintenant):
    phase = etat["phase"]
    if phase == "vote":
        etat["echeance"] = maintenant + (_D["vote"] if _attendus(etat) else _D["absent"])
    elif phase == "revelation":
        etat["echeance"] = maintenant + (_D["revelation"] if _attendus(etat) else _D["absent"])
    else:
        etat["echeance"] = None


def qn_prochaine_echeance(etat):
    return etat["echeance"]


def qn_expirer(etat, maintenant):
    """L echeance est passee : on avance sans ceux qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    if etat["phase"] == "vote":
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
def qn_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    if etat["phase"] in ("vote", "revelation") and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + _D["absent"])


def qn_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "vote" and str(index) not in etat["votes"]:
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
