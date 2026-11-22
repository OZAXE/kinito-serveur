"""
Moteur de regles de La Tournee (party game plateau + mini-jeux, prefixe tr_).

Meme principe que justepinte.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
tr_etat apres chaque action acceptee et arme le minuteur sur
tr_prochaine_echeance. Chiffres : tournee_config.json, mini-jeux :
tournee_minijeux.json.

Regles :
  - Un plateau en boucle de 24 cases (pieces, piege, evenement, tournee,
    depart). Chacun commence au depart avec 10 pieces.
  - Une partie = N tours (5, 10 ou 15). Un tour :
      1. « des » : tout le monde lance son de EN MEME TEMPS (le serveur tire
         la valeur). A l echeance, les retardataires lancent automatiquement.
      2. « deplacement » : on avance dans l ordre des des (le plus haut
         d abord). Passer par le depart rapporte 5 pieces. Passer ou s arreter
         sur la capsule d or l achete si on a 20 pieces (elle part alors sur
         une autre case). Puis l effet de la case d arrivee.
      3. « minijeu » : un mini-jeu tire au hasard (jamais deux fois de suite),
         joue en local sur chaque telephone avec la meme graine ; chacun
         envoie son score. Gains 10 / 6 / 3 pieces aux trois premiers rangs,
         le dernier boit 2.
      4. « resultats » : bilan du tour, « Suivant » ou echeance.
  - Fin : classement aux capsules, puis aux pieces. Le dernier boit 3.
"""
import json
import math
import os
import random

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "tournee_config.json"), encoding="utf-8") as _f:
    TR_CONFIG = json.load(_f)
with open(os.path.join(_ICI, "tournee_minijeux.json"), encoding="utf-8") as _f:
    TR_MINIJEUX = json.load(_f)["minijeux"]

TR_JOUEURS_MIN = TR_CONFIG["joueurs"]["min"]
TR_JOUEURS_MAX = TR_CONFIG["joueurs"]["max"]
PLATEAU = TR_CONFIG["plateau"]
_D = TR_CONFIG["delais"]
_C = TR_CONFIG["cases"]
_M = TR_CONFIG["minijeu"]
_PRIX = TR_CONFIG["capsule"]["prix"]


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


def _minijeu(id_):
    for m in TR_MINIJEUX:
        if m["id"] == id_:
            return m
    return None


def _nouvelle_case_capsule(etat, rng):
    """Une case pour la capsule : pas le depart, pas la case actuelle, loin si possible des pions."""
    n = len(PLATEAU)
    interdites = set(TR_CONFIG["capsule"]["interdites"])
    candidates = [c for c in range(n) if PLATEAU[c] not in interdites and c != etat.get("capsule_case")]
    loin = [c for c in candidates if all(min((c - p) % n, (p - c) % n) >= 3 for p in etat["positions"])]
    return rng.choice(loin or candidates)


# =============================================================
# CREATION
# =============================================================
def tr_initialiser(noms, maintenant, tours=None, deja=None):
    n = len(noms)
    try:
        tours = int(tours)
    except (TypeError, ValueError, OverflowError):
        tours = None
    if tours not in TR_CONFIG["tours"]["choix"]:
        tours = TR_CONFIG["tours"]["defaut"]
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "nb_tours": tours,
        "tour": 1,
        "phase": "des",
        "positions": [0] * n,
        "pieces": [TR_CONFIG["pieces_depart"]] * n,
        "capsules": [0] * n,
        "gorgees": [0] * n,
        "capsule_case": None,
        "des": {},                   # str(joueur) -> valeur du de (public)
        "deplacements": None,        # resultat du tour de plateau (public)
        "minijeu": None,             # {id, nom, regle, duree, sens, graine}
        "dernier_minijeu": (deja or {}).get("dernier_minijeu"),
        "scores": {},                # str(joueur) -> score : SECRET jusqu aux resultats
        "resultat_minijeu": None,
        "suivants": [False] * n,
        "historique": [],            # [{tour, minijeu, gagnants}]
        "final": None,
        "echeance": None,
        "message": "Lancez les dés !",
    }
    etat["capsule_case"] = _nouvelle_case_capsule(etat, random)
    _armer(etat, maintenant)
    return etat


# =============================================================
# ETAT PUBLIC
# =============================================================
def tr_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant, 1))
    phase = etat["phase"]
    return {
        "type": "tr_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "phase": phase,
        "tour": etat["tour"],
        "nb_tours": etat["nb_tours"],
        "plateau": PLATEAU,
        "positions": etat["positions"],
        "pieces": etat["pieces"],
        "capsules": etat["capsules"],
        "gorgees": etat["gorgees"],
        "capsule_case": etat["capsule_case"],
        "prix_capsule": _PRIX,
        "des": {k: v for k, v in etat["des"].items()},
        "deplacements": etat["deplacements"] if phase != "des" else None,
        "minijeu": etat["minijeu"],
        "a_joue": [str(i) in etat["scores"] for i in range(etat["nb_joueurs"])],
        "resultat_minijeu": etat["resultat_minijeu"] if phase in ("resultats", "fin") else None,
        "suivants": etat["suivants"],
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def tr_score(etat, i):
    """Le score deja envoye par le joueur i (pour une page rechargee)."""
    if etat["phase"] != "minijeu" or str(i) not in etat["scores"]:
        return None
    return {"type": "tr_score_recu", "index": i, "score": etat["scores"][str(i)]}


# =============================================================
# DEROULEMENT
# =============================================================
def _gagner(etat, j, pieces):
    avant = etat["pieces"][j]
    etat["pieces"][j] = max(0, avant + pieces)
    return etat["pieces"][j] - avant


def _effet_case(etat, j, case, effets, rng):
    type_ = PLATEAU[case]
    if type_ == "pieces":
        effets.append({"type": "pieces", "pieces": _gagner(etat, j, _C["pieces"]["pieces"])})
    elif type_ == "piege":
        effets.append({"type": "piege", "pieces": _gagner(etat, j, _C["piege"]["pieces"]), "boit": _C["piege"]["boit"]})
        etat["gorgees"][j] += _C["piege"]["boit"]
    elif type_ == "tournee":
        effets.append({"type": "tournee", "distribue": _C["tournee"]["distribue"]})
    elif type_ == "evenement":
        ev = rng.choice(TR_CONFIG["evenements"])
        e = {"type": "evenement", "id": ev["id"], "texte": ev["texte"]}
        if "pieces" in ev:
            e["pieces"] = _gagner(etat, j, ev["pieces"])
        if "boit" in ev:
            e["boit"] = ev["boit"]
            etat["gorgees"][j] += ev["boit"]
        if "tous_boivent" in ev:
            e["tous_boivent"] = ev["tous_boivent"]
            for k in _presents(etat):
                etat["gorgees"][k] += ev["tous_boivent"]
        if "vol" in ev:
            autres = [k for k in _presents(etat) if k != j]
            if autres:
                riche = max(autres, key=lambda k: (etat["pieces"][k], -k))
                pris = -_gagner(etat, riche, -ev["vol"])
                _gagner(etat, j, pris)
                e["victime"], e["pieces"] = riche, pris
        if "raccourci" in ev:
            n = len(PLATEAU)
            cible = (etat["capsule_case"] - ev["raccourci"]) % n
            e["vers"] = cible
            etat["positions"][j] = cible
        effets.append(e)


def _deplacer(etat, ev, maintenant, rng=random):
    """Tous les des sont lances : chacun avance, dans l ordre des des."""
    n = len(PLATEAU)
    ordre = sorted((-v, int(k)) for k, v in etat["des"].items())
    lignes = []
    for moins_de, j in ordre:
        de = -moins_de
        depart = etat["positions"][j]
        pos = depart
        chemin, effets = [], []
        for _ in range(de):
            pos = (pos + 1) % n
            chemin.append(pos)
            if pos == 0:
                effets.append({"type": "depart", "case": pos,
                               "pieces": _gagner(etat, j, _C["depart_passage"]["pieces"])})
            if pos == etat["capsule_case"]:
                if etat["pieces"][j] >= _PRIX:
                    etat["pieces"][j] -= _PRIX
                    etat["capsules"][j] += 1
                    ancienne = etat["capsule_case"]
                    etat["positions"][j] = pos
                    etat["capsule_case"] = _nouvelle_case_capsule(etat, rng)
                    effets.append({"type": "capsule", "case": ancienne, "pieces": -_PRIX,
                                   "nouvelle_case": etat["capsule_case"]})
                else:
                    effets.append({"type": "capsule_ratee", "case": pos, "manque": _PRIX - etat["pieces"][j]})
        etat["positions"][j] = pos
        _effet_case(etat, j, pos, effets, rng)
        lignes.append({"joueur": j, "de": de, "depart": depart, "chemin": chemin,
                       "arrivee": etat["positions"][j], "effets": effets})
    etat["deplacements"] = {"tour": etat["tour"], "lignes": lignes}
    etat["phase"] = "deplacement"
    noms = etat["noms"]
    achats = [l["joueur"] for l in lignes if any(e["type"] == "capsule" for e in l["effets"])]
    etat["message"] = ("Capsule d'or pour " + ", ".join(noms[j] for j in achats) + " !") if achats else "En route !"
    ev.append(_tous({"type": "tr_deplacements", "deplacements": etat["deplacements"]}))
    _armer(etat, maintenant)


def _lancer_minijeu(etat, ev, maintenant, rng=random):
    choix = [m for m in TR_MINIJEUX if m["id"] != etat.get("dernier_minijeu")] or TR_MINIJEUX
    m = rng.choice(choix)
    etat["minijeu"] = {"id": m["id"], "nom": m["nom"], "regle": m["regle"], "duree": m["duree"],
                       "sens": m["sens"], "consignes": _M["consignes"], "graine": rng.randrange(1, 2 ** 31)}
    etat["dernier_minijeu"] = m["id"]
    etat["scores"] = {}
    etat["phase"] = "minijeu"
    etat["message"] = "Mini-jeu : " + m["nom"] + " !"
    ev.append(_tous({"type": "tr_minijeu", "minijeu": etat["minijeu"]}))
    _armer(etat, maintenant)


def _classer_minijeu(etat, ev, maintenant):
    m = _minijeu(etat["minijeu"]["id"])
    grand = m["sens"] == "grand"
    joueurs = [j for j in range(etat["nb_joueurs"]) if str(j) in etat["scores"] or not etat["partis"][j]]
    avec = sorted((j for j in joueurs if str(j) in etat["scores"]),
                  key=lambda j: (-etat["scores"][str(j)] if grand else etat["scores"][str(j)], j))
    sans = [j for j in joueurs if str(j) not in etat["scores"]]
    lignes = []
    rang, precedent = 0, None
    for k, j in enumerate(avec):
        s = etat["scores"][str(j)]
        if s != precedent:
            rang, precedent = k, s
        gain = _M["gains"][rang] if rang < len(_M["gains"]) else 0
        lignes.append({"joueur": j, "score": s, "rang": rang + 1, "pieces": gain, "boit": 0})
    for j in sans:
        lignes.append({"joueur": j, "score": None, "rang": len(avec) + 1, "pieces": 0, "boit": 0})
    rangs = sorted({l["rang"] for l in lignes})
    if len(lignes) >= 2 and len(rangs) >= 2:
        for l in lignes:
            if l["rang"] == rangs[-1]:
                l["boit"] = _M["dernier_boit"]
    for l in lignes:
        _gagner(etat, l["joueur"], l["pieces"])
        etat["gorgees"][l["joueur"]] += l["boit"]
    gagnants = [l["joueur"] for l in lignes if l["rang"] == 1 and l["score"] is not None]
    etat["resultat_minijeu"] = {"tour": etat["tour"], "minijeu": etat["minijeu"]["id"], "nom": etat["minijeu"]["nom"],
                                "sens": m["sens"], "lignes": lignes}
    etat["historique"].append({"tour": etat["tour"], "minijeu": etat["minijeu"]["id"], "gagnants": gagnants})
    etat["phase"] = "resultats"
    etat["suivants"] = [False] * etat["nb_joueurs"]
    noms = etat["noms"]
    etat["message"] = (", ".join(noms[j] for j in gagnants) + " gagne" + ("nt" if len(gagnants) > 1 else "")
                       + " " + etat["minijeu"]["nom"] + " !") if gagnants else "Personne n'a joué !"
    ev.append(_tous({"type": "tr_resultats", "resultat": etat["resultat_minijeu"]}))
    _armer(etat, maintenant)


def _tour_suivant(etat, ev, maintenant):
    if etat["tour"] >= etat["nb_tours"]:
        _terminer(etat, ev)
        return
    etat["tour"] += 1
    etat["phase"] = "des"
    etat["des"] = {}
    etat["deplacements"] = None
    etat["minijeu"] = None
    etat["scores"] = {}
    etat["resultat_minijeu"] = None
    etat["suivants"] = [False] * etat["nb_joueurs"]
    reste = etat["nb_tours"] - etat["tour"]
    etat["message"] = "Dernier tour !" if reste == 0 else f"Tour {etat['tour']} : lancez les dés !"
    _armer(etat, maintenant)


def _terminer(etat, ev):
    n = etat["nb_joueurs"]
    cle = lambda j: (etat["capsules"][j], etat["pieces"][j])
    classement = sorted(range(n), key=lambda j: (-etat["capsules"][j], -etat["pieces"][j], etat["gorgees"][j], j))
    meilleur = max(cle(j) for j in range(n))
    gagnants = [j for j in range(n) if cle(j) == meilleur]
    pire = min(cle(j) for j in range(n))
    derniers = [j for j in range(n) if cle(j) == pire and pire != meilleur]
    for j in derniers:
        etat["gorgees"][j] += TR_CONFIG["fin"]["dernier_boit"]
    etat["final"] = {
        "classement": classement,
        "gagnants": gagnants,
        "derniers": [{"joueur": j, "boit": TR_CONFIG["fin"]["dernier_boit"]} for j in derniers],
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["message"] = "Victoire de " + ", ".join(etat["noms"][j] for j in gagnants) + " !"
    ev.append(_tous({"type": "tr_fin", "final": etat["final"]}))


def _lancer(etat, ev, i, message, maintenant):
    if etat["phase"] != "des":
        return []
    if str(i) in etat["des"]:
        return []
    etat["des"][str(i)] = random.randint(1, 6)
    ev.append(_tous({"type": "tr_de", "index": i, "valeur": etat["des"][str(i)]}))
    if all(str(j) in etat["des"] for j in _presents(etat)):
        _deplacer(etat, ev, maintenant)
    return ev


def _envoyer_score(etat, ev, i, message, maintenant):
    if etat["phase"] != "minijeu":
        return _erreur(i, "Le mini-jeu est terminé.")
    if str(i) in etat["scores"]:
        return []
    v = message.get("score")
    if isinstance(v, bool):
        return _erreur(i, "Score invalide.")
    try:
        v = float(v)
    except (TypeError, ValueError, OverflowError):
        return _erreur(i, "Score invalide.")
    if not math.isfinite(v):
        return _erreur(i, "Score invalide.")
    m = _minijeu(etat["minijeu"]["id"])
    v = max(0.0, min(float(m["max"]), v))
    etat["scores"][str(i)] = int(v) if v == int(v) else round(v, 2)
    ev.append(_prive(i, {"type": "tr_score_recu", "index": i, "score": etat["scores"][str(i)]}))
    if all(str(j) in etat["scores"] for j in _presents(etat)):
        _classer_minijeu(etat, ev, maintenant)
    return ev


def _suivant(etat, ev, i, message, maintenant):
    if etat["phase"] != "resultats" or etat["suivants"][i]:
        return []
    etat["suivants"][i] = True
    if all(etat["suivants"][j] for j in _presents(etat)):
        _tour_suivant(etat, ev, maintenant)
    return ev


TR_ACTIONS = {
    "tr_lancer": _lancer,
    "tr_score": _envoyer_score,
    "tr_suivant": _suivant,
}


def tr_action(etat, i, message, maintenant):
    fn = TR_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"] or etat["partis"][i]:
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
    if etat["phase"] == "des":
        return [j for j in presents if str(j) not in etat["des"]]
    if etat["phase"] == "minijeu":
        return [j for j in presents if str(j) not in etat["scores"]]
    if etat["phase"] == "resultats":
        return [j for j in presents if not etat["suivants"][j]]
    return []


def _armer(etat, maintenant):
    phase = etat["phase"]
    if phase == "des":
        etat["echeance"] = maintenant + (_D["des"] if _attendus(etat) else _D["absent"])
    elif phase == "deplacement":
        nb = len((etat["deplacements"] or {}).get("lignes", []))
        etat["echeance"] = maintenant + _D["deplacement_base"] + _D["deplacement_par_joueur"] * nb
    elif phase == "minijeu":
        m = etat["minijeu"]
        etat["echeance"] = maintenant + m["consignes"] + m["duree"] + _M["marge"]
    elif phase == "resultats":
        etat["echeance"] = maintenant + (_D["resultats"] if _attendus(etat) else _D["absent"])
    else:
        etat["echeance"] = None


def tr_prochaine_echeance(etat):
    return etat["echeance"]


def tr_expirer(etat, maintenant):
    """L echeance est passee : on avance sans ceux qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    phase = etat["phase"]
    if phase == "des":
        for j in _presents(etat):
            if str(j) not in etat["des"]:
                etat["des"][str(j)] = random.randint(1, 6)
        if not etat["des"]:
            # Personne n est la : on ne bouge pas, on attend.
            etat["echeance"] = maintenant + _D["des"]
            return ev
        _deplacer(etat, ev, maintenant)
    elif phase == "deplacement":
        _lancer_minijeu(etat, ev, maintenant)
    elif phase == "minijeu":
        _classer_minijeu(etat, ev, maintenant)
    elif phase == "resultats":
        _tour_suivant(etat, ev, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def tr_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    if etat["phase"] in ("des", "minijeu", "resultats") and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + _D["absent"])


def tr_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "des" and str(index) not in etat["des"]:
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
