"""
Moteur de regles de La Tournee (party game plateau + mini-jeux, prefixe tr_).

Meme principe que justepinte.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
tr_etat apres chaque action acceptee et arme le minuteur sur
tr_prochaine_echeance. Chiffres : tournee_config.json, plateaux :
tournee_plateaux.json (fabrique par tournee_plateaux.py), mini-jeux :
tournee_minijeux.json.

Regles :
  - Trois plateaux (La Terrasse facile, Le Pub moyen, La Cave difficile),
    construits comme les cartes de Mario Party : une grande boucle en
    quartiers, des embranchements (le joueur choisit sa route en y arrivant),
    un peage, un gadget (bateau, tram, geyser, trappes), deux boutiques, des
    cases objet. Chaque plateau a ses gains, ses gorgees et ses evenements.
    Chacun commence au depart avec 10 pieces et les poches vides (3 objets
    au plus).
  - Une partie = N tours (5, 10 ou 15). Un tour :
      0. selon le plateau, un evenement de plateau (happy hour, tournee du
         patron...) ; dans la Cave, le videur se deplace.
      1. « des » : chacun peut d abord utiliser un objet (« tr_objet » :
         double ou triple de, cafe +3, de pipe, taxi, echange, vol...), puis
         tout le monde lance son de de 10 EN MEME TEMPS (le serveur tire la
         valeur). A chaque embranchement sur sa route, le joueur choisit
         (« tr_chemin »). A l echeance, de et chemins au hasard.
      2. « deplacement » : on avance dans l ordre des des (le plus haut
         d abord). Passer par le depart rapporte des pieces. Passer ou
         s arreter sur la capsule d or l achete si on a de quoi (elle part
         alors ailleurs). Passer devant le videur coute des pieces, s arreter
         sur lui renvoie au depart. Passer un peage coute des pieces. Puis
         l effet de la case d arrivee (objet, transport, piege pose...).
      2 bis. « boutique » : ceux qui sont passes devant une boutique peuvent
         acheter un objet (« tr_acheter »).
      3. « minijeu » : un mini-jeu tire au hasard (jamais deux fois de suite),
         joue en local sur chaque telephone avec la meme graine ; chacun
         envoie son score. Gains 10 / 6 / 3 pieces aux trois premiers rangs,
         le dernier boit (selon le plateau).
      4. « resultats » : bilan du tour, « Suivant » ou echeance.
  - Fin : classement aux capsules, puis aux pieces. Le dernier boit.
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
with open(os.path.join(_ICI, "tournee_plateaux.json"), encoding="utf-8") as _f:
    _PL = json.load(_f)
TR_PLATEAUX = {p["id"]: p for p in _PL["plateaux"]}
TR_PLATEAU_DEFAUT = _PL["defaut"]

TR_JOUEURS_MIN = TR_CONFIG["joueurs"]["min"]
TR_JOUEURS_MAX = TR_CONFIG["joueurs"]["max"]
_D = TR_CONFIG["delais"]
_M = TR_CONFIG["minijeu"]
_EVENEMENTS = {e["id"]: e for e in TR_CONFIG["evenements"]}
_EV_PLATEAU = {e["id"]: e for e in TR_CONFIG["evenements_plateau"]}
_CHANCE = {e["id"]: e for e in TR_CONFIG["chance"]}
_OBJETS = {o["id"]: o for o in TR_CONFIG["objets"]}
_FACES = TR_CONFIG["de"]["faces"]
_POCHE = TR_CONFIG["poche_max"]


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


def _plateau(etat):
    return TR_PLATEAUX[etat["plateau"]]


def _cases(etat):
    return _plateau(etat)["cases"]


def _prix(etat):
    return etat["bonus"].get("prix_capsule") or _plateau(etat)["prix_capsule"]


def _boire(etat, cle):
    return _plateau(etat)["boire"].get(cle, 0)


def _distances(cases, depart, limite):
    """Distances en avancant depuis depart, jusqu a limite pas."""
    dist, front = {depart: 0}, [depart]
    for d in range(1, limite + 1):
        suivant = []
        for c in front:
            for s in cases[c]["suivants"]:
                if s not in dist:
                    dist[s] = d
                    suivant.append(s)
        front = suivant
    return dist


def _nouvelle_case_capsule(etat, rng):
    """Une case pour la capsule : un de ses emplacements (comme les etoiles de Mario Party),
    pas la case actuelle, pas juste devant un pion."""
    cases = _cases(etat)
    interdites = set(TR_CONFIG["capsule"]["interdites"])
    possibles = _plateau(etat).get("capsule_possibles") or [c["id"] for c in cases if c["type"] not in interdites]
    candidates = [c for c in possibles if c != etat.get("capsule_case")]
    # Ni sous le nez d un joueur, ni hors d atteinte : a 1 a 3 tours de de du plus proche.
    dist = {}
    for p in etat["positions"]:
        for c, d in _distances(cases, p, 24).items():
            dist[c] = min(d, dist.get(c, 99))
    bien = [c for c in candidates if 7 <= dist.get(c, 99) <= 18]
    loin = [c for c in candidates if dist.get(c, 99) > 5]
    return rng.choice(bien or loin or candidates)


def _chemin_hasard(cases, depart, de, rng):
    pos, ch = depart, []
    for _ in range(de):
        pos = rng.choice(cases[pos]["suivants"])
        ch.append(pos)
    return ch


def _options_carrefour(etat, j, pos, reste):
    """A l embranchement pos, les routes proposees au joueur j : chacune jusqu au prochain
    embranchement ou jusqu au bout du de. Un peage trop cher n est pas propose."""
    cases = _cases(etat)
    prix_peage = _plateau(etat).get("peage", 0)
    ici = cases[pos].get("zone")
    options = []
    for s in cases[pos]["suivants"]:
        seg, p = [s], s
        while len(seg) < reste and len(cases[p]["suivants"]) == 1:
            p = cases[p]["suivants"][0]
            seg.append(p)
        zone = cases[s].get("zone")
        libelle = "tout droit" if not zone or zone == ici else "par " + zone
        o = {"chemin": seg, "arrivee": seg[-1], "libelle": libelle, "type_arrivee": cases[seg[-1]]["type"]}
        if cases[s]["type"] == "peage" and prix_peage:
            o["peage"] = prix_peage
            o["libelle"] += " (péage " + str(prix_peage) + " pièces)"
        options.append(o)
    payables = [o for o in options if o.get("peage", 0) <= etat["pieces"][j]]
    options = payables or options
    vus = {}
    for o in options:
        vus[o["libelle"]] = vus.get(o["libelle"], 0) + 1
    for o in options:
        if vus[o["libelle"]] > 1:
            o["libelle"] += " (case " + str(o["arrivee"]) + ")"
    return options


def _planifier(etat, j):
    """Avance la route du joueur j jusqu au bout de son de, ou jusqu au prochain embranchement
    (il doit alors choisir : etat["options"])."""
    cases = _cases(etat)
    de = etat["des"][str(j)]
    ch = etat["trajets"].setdefault(str(j), [])
    pos = ch[-1] if ch else etat["positions"][j]
    etat["options"].pop(str(j), None)
    while len(ch) < de:
        suivants = cases[pos]["suivants"]
        if len(suivants) > 1:
            options = _options_carrefour(etat, j, pos, de - len(ch))
            if len(options) > 1:
                etat["options"][str(j)] = options
                etat["choix"].pop(str(j), None)
                return
            suivants = [options[0]["chemin"][0]]
        pos = suivants[0]
        ch.append(pos)


def _replanifier(etat, j):
    """La position du joueur j a change avant les deplacements : sa route repart de zero."""
    if str(j) in etat["des"]:
        etat["trajets"][str(j)] = []
        etat["choix"].pop(str(j), None)
        _planifier(etat, j)


def _choisir(etat, j, k):
    o = etat["options"][str(j)][k]
    etat["trajets"].setdefault(str(j), []).extend(o["chemin"])
    etat["choix"][str(j)] = k
    _planifier(etat, j)


def _precedents(cases):
    prec = {c["id"]: [] for c in cases}
    for c in cases:
        for s in c["suivants"]:
            prec[s].append(c["id"])
    return prec


def _types(etat):
    return [c["type"] for c in _cases(etat)]


def _carte(p):
    return {"id": p["id"], "nom": p["nom"], "difficulte": p["difficulte"], "icone": p["icone"],
            "description": p["description"], "cases": p["cases"], "videur": p["videur"] is not None,
            "boire": p["boire"], "pieces": p["pieces"], "peage": p.get("peage", 0),
            "capsule_possibles": p.get("capsule_possibles", []), "faces": _FACES, "poche_max": _POCHE,
            "objets": TR_CONFIG["objets"]}


def tr_plateaux():
    """La liste des plateaux pour la salle d attente."""
    return [{"id": p["id"], "nom": p["nom"], "difficulte": p["difficulte"], "icone": p["icone"],
             "description": p["description"]} for p in _PL["plateaux"]]


# =============================================================
# CREATION
# =============================================================
def tr_initialiser(noms, maintenant, tours=None, deja=None, plateau=None):
    n = len(noms)
    deja = deja or {}
    try:
        tours = int(tours)
    except (TypeError, ValueError, OverflowError):
        tours = None
    if tours not in TR_CONFIG["tours"]["choix"]:
        tours = TR_CONFIG["tours"]["defaut"]
    if plateau not in TR_PLATEAUX:
        plateau = deja.get("plateau") if deja.get("plateau") in TR_PLATEAUX else TR_PLATEAU_DEFAUT
    p = TR_PLATEAUX[plateau]
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "plateau": plateau,
        "nb_tours": tours,
        "tour": 1,
        "phase": "des",
        "positions": [0] * n,
        "pieces": [TR_CONFIG["pieces_depart"]] * n,
        "capsules": [0] * n,
        "gorgees": [0] * n,
        "capsule_case": None,
        "videur": p["videur"]["depart"] if p["videur"] else None,
        "bonus": {},                 # effets de l evenement de plateau du tour
        "evenement_plateau": None,   # {tour, id, texte, ...} (public)
        "des": {},                   # str(joueur) -> valeur du de (public)
        "options": {},               # str(joueur) -> routes a l embranchement ou il doit choisir (public)
        "choix": {},                 # str(joueur) -> indice du dernier chemin choisi
        "trajets": {},               # str(joueur) -> la route deja decidee pour ce tour
        "des_detail": {},            # str(joueur) -> les des lances (double, triple...)
        "objets": [[] for _ in range(n)],   # les poches (3 objets au plus, public comme dans Mario Party)
        "objet_tour": {},            # str(joueur) -> objet utilise ce tour (un seul)
        "modif_de": {},              # str(joueur) -> {"des": 2} ou {"bonus": 3} pour le prochain lancer
        "utilisations": [],          # les objets utilises ce tour (public)
        "pieges_poses": {},          # str(case) -> joueur qui y a pose un verre renverse
        "boutiques": {},             # str(joueur) -> la boutique devant laquelle il est passe
        "achats": {},                # str(joueur) -> objet achete (ou None : rien)
        "deplacements": None,        # resultat du tour de plateau (public)
        "minijeu": None,             # {id, nom, regle, duree, sens, graine}
        "dernier_minijeu": deja.get("dernier_minijeu"),
        "minijeux_recents": list(deja.get("minijeux_recents") or [])[-len(TR_MINIJEUX):],
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
        "plateau": _types(etat),     # ancien format (liste des types), garde pour les pages d avant
        "carte": _carte(_plateau(etat)),
        "positions": etat["positions"],
        "pieces": etat["pieces"],
        "capsules": etat["capsules"],
        "gorgees": etat["gorgees"],
        "capsule_case": etat["capsule_case"],
        "prix_capsule": _prix(etat),
        "videur": etat["videur"],
        "bonus": etat["bonus"],
        "evenement_plateau": etat["evenement_plateau"],
        "des": {k: v for k, v in etat["des"].items()},
        "options": etat["options"] if phase == "des" else {},
        "choix": etat["choix"],
        "trajets": etat.get("trajets", {}) if phase == "des" else {},
        "des_detail": etat.get("des_detail", {}),
        "objets": etat.get("objets"),
        "objet_tour": etat.get("objet_tour", {}),
        "utilisations": etat.get("utilisations", []),
        "pieges_poses": etat.get("pieges_poses", {}),
        "boutiques": etat.get("boutiques", {}) if phase == "boutique" else {},
        "achats": etat.get("achats", {}) if phase == "boutique" else {},
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


def _boit(etat, j, n):
    etat["gorgees"][j] += n
    return n


def _protege(etat, k):
    """L ange gardien de k bloque un vol (il s en va ensuite)."""
    poche = etat.get("objets", [[]] * etat["nb_joueurs"])[k]
    if "bouclier" in poche:
        poche.remove("bouclier")
        return True
    return False


def _donner_objet(etat, j, objet):
    if len(etat["objets"][j]) >= _POCHE:
        return False
    etat["objets"][j].append(objet)
    return True


def _echanger(etat, j, rng):
    autres = [k for k in _presents(etat) if k != j]
    if not autres:
        return None
    k = rng.choice(autres)
    etat["positions"][j], etat["positions"][k] = etat["positions"][k], etat["positions"][j]
    return k


def _avancer(etat, j, pas, rng):
    cases = _cases(etat)
    pos = etat["positions"][j]
    for _ in range(pas):
        pos = rng.choice(cases[pos]["suivants"])
    etat["positions"][j] = pos
    return pos


def _appliquer(etat, j, ev, e, rng):
    """Les effets d une carte (evenement de case ou chance) sur le joueur j."""
    if "pieces" in ev:
        e["pieces"] = _gagner(etat, j, ev["pieces"])
    if "boit" in ev:
        e["boit"] = _boit(etat, j, ev["boit"])
    if "tous_boivent" in ev:
        e["tous_boivent"] = ev["tous_boivent"]
        for k in _presents(etat):
            _boit(etat, k, ev["tous_boivent"])
    if "vol" in ev:
        autres = [k for k in _presents(etat) if k != j]
        if autres:
            riche = max(autres, key=lambda k: (etat["pieces"][k], -k))
            if _protege(etat, riche):
                e["victime"], e["protege"] = riche, True
            else:
                pris = -_gagner(etat, riche, -ev["vol"])
                _gagner(etat, j, pris)
                e["victime"], e["pieces"] = riche, pris
    if "raccourci" in ev:
        # Juste avant la capsule : on remonte de quelques cases a partir d elle.
        prec = _precedents(_cases(etat))
        cible = etat["capsule_case"]
        for _ in range(ev["raccourci"]):
            if prec[cible]:
                cible = rng.choice(prec[cible])
        e["vers"] = cible
        etat["positions"][j] = cible
    if ev.get("echange"):
        k = _echanger(etat, j, rng)
        if k is not None:
            e["avec"], e["vers"] = k, etat["positions"][j]
    if "avance" in ev:
        e["vers"] = _avancer(etat, j, ev["avance"], rng)
    if "vol_capsule" in ev:
        autres = [k for k in _presents(etat) if k != j and etat["capsules"][k] > 0]
        if autres:
            leader = max(autres, key=lambda k: (etat["capsules"][k], etat["pieces"][k], -k))
            if _protege(etat, leader):
                e["victime"], e["protege"] = leader, True
            else:
                etat["capsules"][leader] -= 1
                etat["capsules"][j] += 1
                e["victime"], e["capsules"] = leader, 1
        else:
            e["rate"] = True


def _effet_case(etat, j, case, effets, rng):
    p = _plateau(etat)
    type_ = p["cases"][case]["type"]
    if type_ == "pieces":
        gain = p["pieces"]["pieces"] * etat["bonus"].get("multiplicateur", 1)
        effets.append({"type": "pieces", "pieces": _gagner(etat, j, gain)})
    elif type_ == "piege":
        effets.append({"type": "piege", "pieces": _gagner(etat, j, p["pieces"]["piege"]),
                       "boit": _boit(etat, j, _boire(etat, "piege"))})
    elif type_ == "tournee":
        effets.append({"type": "tournee", "distribue": _boire(etat, "tournee")})
    elif type_ == "evenement":
        ev = _EVENEMENTS[rng.choice(p["evenements"])]
        e = {"type": "evenement", "id": ev["id"], "texte": ev["texte"]}
        _appliquer(etat, j, ev, e, rng)
        effets.append(e)
    elif type_ == "chance":
        ev = _CHANCE[rng.choice(p.get("chance") or list(_CHANCE))]
        e = {"type": "chance", "id": ev["id"], "texte": ev["texte"]}
        _appliquer(etat, j, ev, e, rng)
        effets.append(e)
    elif type_ == "duel":
        autres = [k for k in _presents(etat) if k != j]
        if autres:
            k = rng.choice(autres)
            gagnant, perdant = (j, k) if rng.random() < 0.5 else (k, j)
            e = {"type": "duel", "adversaire": k, "gagnant": gagnant, "perdant": perdant}
            if p.get("duel_capsule") and etat["capsules"][perdant] > 0:
                # Dans la Cave, le duel se joue pour une capsule.
                etat["capsules"][perdant] -= 1
                etat["capsules"][gagnant] += 1
                e["capsules"] = 1
            else:
                pris = -_gagner(etat, perdant, -TR_CONFIG["duel"]["pieces"])
                _gagner(etat, gagnant, pris)
                e["pieces"] = pris
            e["boit"] = _boit(etat, perdant, _boire(etat, "duel"))
            effets.append(e)
    elif type_ == "trappe":
        vers = p["cases"][case]["vers"]
        etat["positions"][j] = vers
        effets.append({"type": "trappe", "vers": vers})
    elif type_ == "transport":
        vers = p["cases"][case]["vers"]
        etat["positions"][j] = vers
        effets.append({"type": "transport", "moyen": p["cases"][case].get("moyen"), "vers": vers})
    elif type_ == "objet":
        objet = rng.choice(p.get("objets_case") or list(_OBJETS))
        if _donner_objet(etat, j, objet):
            effets.append({"type": "objet", "objet": objet})
        else:
            effets.append({"type": "objet", "plein": True, "pieces": _gagner(etat, j, TR_CONFIG["objet_plein_pieces"])})


def _deplacer(etat, ev, maintenant, rng=random):
    """Tous les des sont lances : chacun avance, dans l ordre des des."""
    p = _plateau(etat)
    cases = p["cases"]
    ordre = sorted((-v, int(k)) for k, v in etat["des"].items())
    lignes = []
    boutiques = {}
    for moins_de, j in ordre:
        de = -moins_de
        depart = etat["positions"][j]
        etat.setdefault("trajets", {})
        while str(j) in etat["options"]:
            _choisir(etat, j, rng.randrange(len(etat["options"][str(j)])))
        prevu = etat["trajets"].get(str(j)) or []
        if len(prevu) != de or prevu[0] not in cases[depart]["suivants"]:
            # Un joueur passe avant a echange sa place avec lui : il repart de sa nouvelle case.
            prevu = _chemin_hasard(cases, depart, de, rng)
        chemin, effets = [], []
        renvoye = False
        for n, pos in enumerate(prevu):
            chemin.append(pos)
            if cases[pos]["type"] == "peage" and p.get("peage"):
                effets.append({"type": "peage", "case": pos, "pieces": _gagner(etat, j, -p["peage"])})
            if cases[pos]["type"] == "boutique":
                boutiques[j] = pos
            if pos == 0:
                effets.append({"type": "depart", "case": pos,
                               "pieces": _gagner(etat, j, p["pieces"]["depart"])})
            if pos == etat["capsule_case"]:
                prix = _prix(etat)
                if etat["pieces"][j] >= prix:
                    etat["pieces"][j] -= prix
                    etat["capsules"][j] += 1
                    ancienne = etat["capsule_case"]
                    etat["positions"][j] = pos
                    etat["capsule_case"] = _nouvelle_case_capsule(etat, rng)
                    effets.append({"type": "capsule", "case": ancienne, "pieces": -prix,
                                   "nouvelle_case": etat["capsule_case"]})
                else:
                    effets.append({"type": "capsule_ratee", "case": pos, "manque": prix - etat["pieces"][j]})
            if etat["videur"] is not None and pos == etat["videur"]:
                if n == len(prevu) - 1:
                    # S arreter sur le videur : dehors, retour au depart.
                    effets.append({"type": "videur", "case": pos, "vers": 0,
                                   "boit": _boit(etat, j, _boire(etat, "videur"))})
                    renvoye = True
                else:
                    effets.append({"type": "fouille", "case": pos,
                                   "pieces": _gagner(etat, j, p["videur"]["fouille"])})
        etat["positions"][j] = 0 if renvoye else chemin[-1]
        if not renvoye:
            pose = etat.setdefault("pieges_poses", {}).get(str(chemin[-1]))
            if pose is not None and pose != j:
                # Un verre renverse : on paie celui qui l a pose.
                pris = -_gagner(etat, j, -TR_CONFIG["piege_pose"])
                _gagner(etat, pose, pris)
                del etat["pieges_poses"][str(chemin[-1])]
                effets.append({"type": "piege_pose", "case": chemin[-1], "proprio": pose, "pieces": -pris})
            _effet_case(etat, j, chemin[-1], effets, rng)
        lignes.append({"joueur": j, "de": de, "depart": depart, "chemin": chemin,
                       "arrivee": etat["positions"][j], "effets": effets})
    # Un echange de place deplace aussi l autre joueur : les arrivees suivent les positions finales.
    for l in lignes:
        l["arrivee"] = etat["positions"][l["joueur"]]
    etat["deplacements"] = {"tour": etat["tour"], "lignes": lignes, "positions": list(etat["positions"])}
    etat["boutiques"] = {}
    etat["achats"] = {}
    for j, c in boutiques.items():
        offre = _offre(etat, j, c)
        if offre:
            etat["boutiques"][str(j)] = offre
    etat["phase"] = "deplacement"
    noms = etat["noms"]
    achats = [l["joueur"] for l in lignes if any(e["type"] == "capsule" for e in l["effets"])]
    etat["message"] = ("Capsule d'or pour " + ", ".join(noms[j] for j in achats) + " !") if achats else "En route !"
    ev.append(_tous({"type": "tr_deplacements", "deplacements": etat["deplacements"]}))
    _armer(etat, maintenant)


def _prix_objet(etat, objet):
    return max(1, round(_OBJETS[objet]["prix"] * etat["bonus"].get("soldes", 1)))


def _offre(etat, j, c):
    """Ce que la boutique c propose au joueur j (rien s il a les poches pleines ou pas un sou)."""
    case = _cases(etat)[c]
    if etat["partis"][j] or len(etat["objets"][j]) >= _POCHE:
        return None
    stock = [{"id": o, "prix": _prix_objet(etat, o)} for o in case.get("stock", []) if o in _OBJETS]
    if not stock or etat["pieces"][j] < min(s["prix"] for s in stock):
        return None
    return {"case": c, "nom": case.get("nom", "La boutique"), "stock": stock}


def _apres_deplacement(etat, ev, maintenant, rng=random):
    clients = [j for j in etat.get("boutiques", {}) if not etat["partis"][int(j)]]
    if clients:
        etat["phase"] = "boutique"
        noms = [etat["noms"][int(j)] for j in clients]
        etat["message"] = "Boutique : " + ", ".join(noms) + (" font" if len(noms) > 1 else " fait") + " ses courses."
        ev.append(_tous({"type": "tr_boutique", "boutiques": etat["boutiques"]}))
        _armer(etat, maintenant)
    else:
        _lancer_minijeu(etat, ev, maintenant, rng)


def _tirer_equipes(etat, m, rng):
    """Parfois, le mini-jeu se joue en equipes (2 contre 2, 1 contre 3, 3 contre 5...)."""
    presents = _presents(etat)
    n = len(presents)
    if n < 3 or not m.get("equipes", True) or rng.random() >= _M["proba_equipes"]:
        return None
    tailles = [k for k in range(1, n // 2 + 1)]
    # Plus souvent des equipes equilibrees, parfois un seul contre tous.
    poids = [3 if k == n // 2 else (2 if k == 1 else 1) for k in tailles]
    k = rng.choices(tailles, weights=poids)[0]
    melange = list(presents)
    rng.shuffle(melange)
    return [sorted(melange[:k]), sorted(melange[k:])]


def _lancer_minijeu(etat, ev, maintenant, rng=random):
    # Pas de redite tant que possible : on evite les mini-jeux joues recemment
    # (la moitie du catalogue), et toujours le dernier.
    recents = etat.setdefault("minijeux_recents", [])
    garde = max(1, len(TR_MINIJEUX) // 2)
    exclus = set(recents[-garde:]) | {etat.get("dernier_minijeu")}
    choix = [m for m in TR_MINIJEUX if m["id"] not in exclus] or [m for m in TR_MINIJEUX if m["id"] != etat.get("dernier_minijeu")] or TR_MINIJEUX
    m = rng.choice(choix)
    recents.append(m["id"])
    del recents[:-len(TR_MINIJEUX)]
    etat["minijeu"] = {"id": m["id"], "nom": m["nom"], "regle": m["regle"], "duree": m["duree"],
                       "sens": m["sens"], "consignes": _M["consignes"], "graine": rng.randrange(1, 2 ** 31),
                       "equipes": _tirer_equipes(etat, m, rng), "gorgees_max": m.get("gorgees_max", 0)}
    etat["dernier_minijeu"] = m["id"]
    etat["scores"] = {}
    etat["gorgees_jeu"] = {}
    etat["phase"] = "minijeu"
    eq = etat["minijeu"]["equipes"]
    etat["message"] = "Mini-jeu : " + m["nom"] + (" en équipes, " + str(len(eq[0])) + " contre " + str(len(eq[1])) if eq else "") + " !"
    ev.append(_tous({"type": "tr_minijeu", "minijeu": etat["minijeu"]}))
    _armer(etat, maintenant)


def _lignes_chacun(etat, m, joueurs):
    grand = m["sens"] == "grand"
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
                l["boit"] = _boire(etat, "minijeu_dernier")
    return lignes, None


def _lignes_equipes(etat, m, joueurs, equipes):
    """Score d equipe = moyenne de ceux qui ont joue. L equipe gagnante se partage la cagnotte, l autre boit."""
    grand = m["sens"] == "grand"
    moyennes = []
    for eq in equipes:
        notes = [etat["scores"][str(j)] for j in eq if str(j) in etat["scores"]]
        moyennes.append(round(sum(notes) / len(notes), 2) if notes else None)
    a, b = moyennes
    if a is None and b is None:
        gagnante = None
    elif a is None or b is None:
        gagnante = 1 if a is None else 0
    elif a == b:
        gagnante = None
    else:
        gagnante = 0 if (a > b) == grand else 1
    lignes = []
    for k, eq in enumerate(equipes):
        for j in eq:
            if j not in joueurs:
                continue
            if gagnante is None:
                gain, boit, rang = _M["egalite_equipes"], 0, 1
            elif k == gagnante:
                gain, boit, rang = _M["cagnotte_equipe"] // len(eq), 0, 1
            else:
                gain, boit, rang = 0, _boire(etat, "minijeu_dernier"), 2
            lignes.append({"joueur": j, "score": etat["scores"].get(str(j)), "rang": rang, "pieces": gain,
                           "boit": boit, "equipe": k})
    return lignes, {"moyennes": moyennes, "gagnante": gagnante}


def _classer_minijeu(etat, ev, maintenant):
    m = _minijeu(etat["minijeu"]["id"])
    joueurs = [j for j in range(etat["nb_joueurs"]) if str(j) in etat["scores"] or not etat["partis"][j]]
    equipes = etat["minijeu"].get("equipes")
    if equipes:
        lignes, bilan = _lignes_equipes(etat, m, joueurs, equipes)
    else:
        lignes, bilan = _lignes_chacun(etat, m, joueurs)
    for l in lignes:
        l["boit_jeu"] = etat.get("gorgees_jeu", {}).get(str(l["joueur"]), 0)
        _gagner(etat, l["joueur"], l["pieces"])
        etat["gorgees"][l["joueur"]] += l["boit"] + l["boit_jeu"]
    if equipes:
        gagnants = equipes[bilan["gagnante"]] if bilan["gagnante"] is not None else []
    else:
        gagnants = [l["joueur"] for l in lignes if l["rang"] == 1 and l["score"] is not None]
    etat["resultat_minijeu"] = {"tour": etat["tour"], "minijeu": etat["minijeu"]["id"], "nom": etat["minijeu"]["nom"],
                                "sens": m["sens"], "lignes": lignes, "equipes": equipes, "bilan_equipes": bilan}
    etat["historique"].append({"tour": etat["tour"], "minijeu": etat["minijeu"]["id"], "gagnants": gagnants})
    etat["phase"] = "resultats"
    etat["suivants"] = [False] * etat["nb_joueurs"]
    noms = etat["noms"]
    if equipes and bilan["gagnante"] is None:
        etat["message"] = "Égalité entre les équipes !"
    elif gagnants:
        etat["message"] = (", ".join(noms[j] for j in gagnants) + " gagne" + ("nt" if len(gagnants) > 1 else "")
                           + " " + etat["minijeu"]["nom"] + " !")
    else:
        etat["message"] = "Personne n'a joué !"
    ev.append(_tous({"type": "tr_resultats", "resultat": etat["resultat_minijeu"]}))
    _armer(etat, maintenant)


def _evenement_plateau(etat, rng):
    """Debut de tour : le videur bouge, et parfois un evenement de plateau."""
    p = _plateau(etat)
    etat["bonus"] = {}
    etat["evenement_plateau"] = None
    pas_videur = 0
    if p["videur"]:
        pas_videur = rng.randint(p["videur"]["pas_min"], p["videur"]["pas_max"])
    ep = p["evenements_plateau"]
    if ep and (etat["tour"] - 1) % ep["tous_les"] == 0:
        ev = _EV_PLATEAU[rng.choice(ep["liste"])]
        e = {"tour": etat["tour"], "id": ev["id"], "texte": ev["texte"]}
        if "pieces_tous" in ev:
            e["pieces"] = {k: _gagner(etat, k, ev["pieces_tous"]) for k in _presents(etat)}
        if "tous_boivent" in ev:
            e["tous_boivent"] = ev["tous_boivent"]
            for k in _presents(etat):
                _boit(etat, k, ev["tous_boivent"])
        if "prix_capsule" in ev:
            etat["bonus"]["prix_capsule"] = ev["prix_capsule"]
        if "multiplicateur" in ev:
            etat["bonus"]["multiplicateur"] = ev["multiplicateur"]
        if "soldes" in ev:
            etat["bonus"]["soldes"] = ev["soldes"]
        if ev.get("capsule_bouge"):
            etat["capsule_case"] = _nouvelle_case_capsule(etat, rng)
            e["capsule_case"] = etat["capsule_case"]
        if "videur_pas" in ev and p["videur"]:
            pas_videur = ev["videur_pas"]
        if ev.get("partage"):
            presents = _presents(etat)
            total = sum(etat["pieces"][k] for k in presents)
            part, reste = divmod(total, len(presents))
            e["pieces"] = {}
            for n, k in enumerate(sorted(presents, key=lambda k: (etat["capsules"][k], etat["pieces"][k], k))):
                nouveau = part + (1 if n < reste else 0)
                e["pieces"][k] = nouveau - etat["pieces"][k]
                etat["pieces"][k] = nouveau
        if "coup_de_pouce" in ev:
            presents = _presents(etat)
            pire = min((etat["capsules"][k], etat["pieces"][k]) for k in presents)
            derniers = [k for k in presents if (etat["capsules"][k], etat["pieces"][k]) == pire]
            e["pieces"] = {k: _gagner(etat, k, ev["coup_de_pouce"]) for k in derniers}
        if ev.get("echange"):
            presents = _presents(etat)
            if len(presents) >= 2:
                a, b = rng.sample(presents, 2)
                etat["positions"][a], etat["positions"][b] = etat["positions"][b], etat["positions"][a]
                e["echange"] = [a, b]
        etat["evenement_plateau"] = e
    if pas_videur and etat["videur"] is not None:
        cases = p["cases"]
        pos = etat["videur"]
        for _ in range(pas_videur):
            pos = rng.choice(cases[pos]["suivants"])
            if pos == 0:
                pos = cases[0]["suivants"][0]   # le videur ne garde pas le depart
        etat["videur"] = pos


def _tour_suivant(etat, ev, maintenant, rng=random):
    if etat["tour"] >= etat["nb_tours"]:
        _terminer(etat, ev)
        return
    etat["tour"] += 1
    etat["phase"] = "des"
    etat["des"] = {}
    etat["options"] = {}
    etat["choix"] = {}
    etat["trajets"] = {}
    etat["des_detail"] = {}
    etat["objet_tour"] = {}
    etat["modif_de"] = {}
    etat["utilisations"] = []
    etat["boutiques"] = {}
    etat["achats"] = {}
    etat["deplacements"] = None
    etat["minijeu"] = None
    etat["scores"] = {}
    etat["resultat_minijeu"] = None
    etat["suivants"] = [False] * etat["nb_joueurs"]
    _evenement_plateau(etat, rng)
    reste = etat["nb_tours"] - etat["tour"]
    if etat["evenement_plateau"]:
        etat["message"] = etat["evenement_plateau"]["texte"]
        ev.append(_tous({"type": "tr_evenement_plateau", "evenement": etat["evenement_plateau"]}))
    else:
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
        etat["gorgees"][j] += _boire(etat, "fin_dernier")
    etat["final"] = {
        "classement": classement,
        "gagnants": gagnants,
        "derniers": [{"joueur": j, "boit": _boire(etat, "fin_dernier")} for j in derniers],
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["message"] = "Victoire de " + ", ".join(etat["noms"][j] for j in gagnants) + " !"
    ev.append(_tous({"type": "tr_fin", "final": etat["final"]}))


def _pret_a_partir(etat):
    return all(str(j) in etat["des"] and not etat["options"].get(str(j)) for j in _presents(etat))


def _rouler(etat, j, rng=random):
    """Le lancer du joueur j : un de de 10, ou plus avec un objet."""
    m = etat.setdefault("modif_de", {}).get(str(j), {})
    detail = [rng.randint(1, _FACES) for _ in range(m.get("des", 1))]
    return sum(detail) + m.get("bonus", 0), detail


def _poser_de(etat, ev, j, total, detail):
    etat["des"][str(j)] = total
    etat.setdefault("des_detail", {})[str(j)] = detail
    etat.setdefault("trajets", {})[str(j)] = []
    _planifier(etat, j)
    ev.append(_tous({"type": "tr_de", "index": j, "valeur": total, "detail": detail}))


def _lancer(etat, ev, i, message, maintenant):
    if etat["phase"] != "des":
        return []
    if str(i) in etat["des"]:
        return []
    total, detail = _rouler(etat, i)
    _poser_de(etat, ev, i, total, detail)
    if _pret_a_partir(etat):
        _deplacer(etat, ev, maintenant)
    return ev


def _choisir_chemin(etat, ev, i, message, maintenant):
    if etat["phase"] != "des" or not etat["options"].get(str(i)):
        return []
    k = message.get("choix")
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < len(etat["options"][str(i)]):
        return _erreur(i, "Chemin inconnu.")
    _choisir(etat, i, k)
    if _pret_a_partir(etat):
        _deplacer(etat, ev, maintenant)
    return ev


def _utiliser_objet(etat, ev, i, message, maintenant, rng=random):
    """Avant de lancer son de, on peut utiliser un objet de sa poche (un par tour)."""
    if etat["phase"] != "des":
        return _erreur(i, "Les objets s'utilisent avant de lancer le dé.")
    if str(i) in etat["des"]:
        return _erreur(i, "Tu as déjà lancé ton dé.")
    if str(i) in etat.setdefault("objet_tour", {}):
        return _erreur(i, "Un seul objet par tour.")
    objet = message.get("objet")
    poche = etat["objets"][i]
    if not isinstance(objet, str) or objet not in poche or objet not in _OBJETS:
        return _erreur(i, "Tu n'as pas cet objet.")
    o = _OBJETS[objet]
    if o.get("passif"):
        return _erreur(i, o["nom"] + " agit tout seul.")
    cible = message.get("cible")
    if o.get("cible"):
        if isinstance(cible, bool) or not isinstance(cible, int) or cible == i or cible not in _presents(etat):
            return _erreur(i, "Choisis un autre joueur.")
        if o.get("vol_objet") and not etat["objets"][cible]:
            return _erreur(i, etat["noms"][cible] + " n'a rien dans les poches.")
    u = {"joueur": i, "objet": objet}
    if o.get("choix_de"):
        v = message.get("valeur")
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= _FACES:
            return _erreur(i, "Choisis un chiffre de 1 à " + str(_FACES) + ".")
        u["valeur"] = v
    if o.get("piege"):
        case = etat["positions"][i]
        if _cases(etat)[case]["type"] == "depart" or str(case) in etat.setdefault("pieges_poses", {}):
            return _erreur(i, "Pas de piège possible sur cette case.")
    poche.remove(objet)
    etat["objet_tour"][str(i)] = objet
    if "des" in o or "bonus" in o:
        etat.setdefault("modif_de", {})[str(i)] = {"des": o.get("des", 1), "bonus": o.get("bonus", 0)}
    elif o.get("taxi"):
        prec = _precedents(_cases(etat))[etat["capsule_case"]]
        vers = rng.choice(prec)
        etat["positions"][i] = vers
        u["vers"] = vers
    elif o.get("echange"):
        etat["positions"][i], etat["positions"][cible] = etat["positions"][cible], etat["positions"][i]
        u["cible"], u["vers"] = cible, etat["positions"][i]
        _replanifier(etat, cible)
    elif "vol" in o:
        u["cible"] = cible
        if _protege(etat, cible):
            u["protege"] = True
        else:
            pris = -_gagner(etat, cible, -rng.randint(*o["vol"]))
            _gagner(etat, i, pris)
            u["pieces"] = pris
    elif o.get("vol_objet"):
        u["cible"] = cible
        if _protege(etat, cible):
            u["protege"] = True
        else:
            pris = rng.choice(etat["objets"][cible])
            etat["objets"][cible].remove(pris)
            etat["objets"][i].append(pris)
            u["vole"] = pris
    elif o.get("piege"):
        etat["pieges_poses"][str(etat["positions"][i])] = i
        u["case"] = etat["positions"][i]
    elif "gorgees" in o:
        u["cible"], u["boit"] = cible, _boit(etat, cible, o["gorgees"])
    etat.setdefault("utilisations", []).append(u)
    etat["message"] = etat["noms"][i] + " utilise " + o["nom"] + (" sur " + etat["noms"][cible] if "cible" in u else "") + " !"
    ev.append(_tous({"type": "tr_objet_utilise", "utilisation": u}))
    if o.get("choix_de"):
        _poser_de(etat, ev, i, u["valeur"], [u["valeur"]])
        if _pret_a_partir(etat):
            _deplacer(etat, ev, maintenant)
    return ev


def _acheter(etat, ev, i, message, maintenant):
    if etat["phase"] != "boutique" or str(i) not in etat["boutiques"] or str(i) in etat["achats"]:
        return []
    objet = message.get("objet")
    if objet is not None:
        offre = {s["id"]: s["prix"] for s in etat["boutiques"][str(i)]["stock"]}
        if not isinstance(objet, str) or objet not in offre:
            return _erreur(i, "Pas en rayon.")
        if etat["pieces"][i] < offre[objet]:
            return _erreur(i, "Pas assez de pièces.")
        if len(etat["objets"][i]) >= _POCHE:
            return _erreur(i, "Tes poches sont pleines.")
        etat["pieces"][i] -= offre[objet]
        etat["objets"][i].append(objet)
    etat["achats"][str(i)] = objet
    ev.append(_tous({"type": "tr_achat", "index": i, "objet": objet}))
    if not _attendus(etat):
        _lancer_minijeu(etat, ev, maintenant)
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
    # Gorgees bues pendant le mini-jeu (un rate, une erreur) : jamais plus que le plafond du mini-jeu.
    g = message.get("gorgees")
    if isinstance(g, int) and not isinstance(g, bool) and g > 0:
        etat.setdefault("gorgees_jeu", {})[str(i)] = min(g, m.get("gorgees_max", 0))
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
    "tr_chemin": _choisir_chemin,
    "tr_objet": _utiliser_objet,
    "tr_acheter": _acheter,
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
        return [j for j in presents if str(j) not in etat["des"]
                or (etat["options"].get(str(j)) and str(j) not in etat["choix"])]
    if etat["phase"] == "boutique":
        return [j for j in presents if str(j) in etat["boutiques"] and str(j) not in etat["achats"]]
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
        lignes = (etat["deplacements"] or {}).get("lignes", [])
        # Le temps que les pions avancent case par case sur le site (dé de 10, effets en route).
        cases = sum(len(l["chemin"]) for l in lignes)
        effets = sum(len(l["effets"]) for l in lignes)
        etat["echeance"] = (maintenant + _D["deplacement_base"] + _D["deplacement_par_joueur"] * len(lignes)
                            + _D.get("deplacement_par_case", 0) * cases + _D.get("deplacement_par_effet", 0) * effets)
    elif phase == "boutique":
        etat["echeance"] = maintenant + (_D["boutique"] if _attendus(etat) else _D["absent"])
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
                total, detail = _rouler(etat, j)
                _poser_de(etat, ev, j, total, detail)
        if not etat["des"]:
            # Personne n est la : on ne bouge pas, on attend.
            etat["echeance"] = maintenant + _D["des"]
            return ev
        _deplacer(etat, ev, maintenant)
    elif phase == "deplacement":
        _apres_deplacement(etat, ev, maintenant)
    elif phase == "boutique":
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
    if etat["phase"] in ("des", "boutique", "minijeu", "resultats") and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + _D["absent"])


def tr_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "des" and str(index) not in etat["des"]:
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
