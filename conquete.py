"""
Moteur de regles de Royaumes (jeu de conquete, prefixe sw_).

Meme principe que menteur.py et undercover.py : moteur PUR, sans reseau ni
horloge implicite (le temps est passe en parametre). Chaque action prend
l etat (un dict serialisable en JSON) et renvoie la liste des messages a
envoyer, au format {"a": index ou None pour tous, "msg": {...}}. serveur.py
diffuse sw_etat apres chaque action acceptee (etat["version"] change) et arme
le minuteur sur sw_prochaine_echeance.

Toutes les valeurs chiffrees sont dans conquete_config.json, les cartes dans
conquete_cartes.json (fabriquees par conquete_cartes.py).

Regles (resume, detail dans le README) :
  - Chaque joueur dirige un peuple = une race + un pouvoir tires au hasard,
    choisi dans une file de 6 (la premiere gratuite, 1 piece par combinaison
    sautee, posee sur celle-ci).
  - A son tour : choisir une combinaison s il n a pas de peuple actif, puis
    ETENDRE (conquetes, puis redeploiement) ou passer son peuple en DECLIN.
  - Conquete : 2 pions + 1 par pion adverse + 1 en montagne + effets, jamais
    moins de 1. Premiere conquete d un peuple sur une region de bord, les
    suivantes sur une voisine. Le defenseur actif perd 1 pion et recupere les
    autres en main ; un peuple en declin perd tout.
  - De de renfort pour une derniere conquete (tire ici, jamais par le client).
  - Fin de tour : 1 piece par region occupee (actif + declin) + bonus.
  - Partie en N tours par joueur ; le plus de pieces gagne (egalite : le
    plus de regions, puis le sort).
  - Le serveur est l arbitre : tout coup illegal est refuse avec un message.

Gorgees (x multiplicateur de la vitesse, sans plafond) :
  - region prise : l ancien occupant boit 1 par region, au plus 3 par tour
    d attaquant ;
  - declin : le joueur distribue 2 ;
  - fin : le dernier boit 3, le vainqueur distribue 5 ;
  - option vengeance : 3 tours adverses d affilee a perdre des regions, on
    distribue 1.
"""
import json
import os
import random

_ICI = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_ICI, "conquete_config.json"), encoding="utf-8") as _f:
    SW_CONFIG = json.load(_f)
with open(os.path.join(_ICI, "conquete_cartes.json"), encoding="utf-8") as _f:
    SW_CARTES = json.load(_f)

SW_JOUEURS_MIN = SW_CONFIG["joueurs_min"]
SW_JOUEURS_MAX = SW_CONFIG["joueurs_max"]
SW_RACES = SW_CONFIG["races"]
SW_POUVOIRS = SW_CONFIG["pouvoirs"]
SW_REACTIONS = ["👏", "😂", "😱", "🔥", "💀", "🍻", "😡", "🙏"]
SW_DELAI_REACTION = 1.2
SW_DELAI_RETOUR = 20       # un joueur revenu pendant son tour a au moins ce temps
# Pour le journal : le terrain avec son article
SW_TERRAINS = {"plaine": "une plaine", "foret": "une forêt", "colline": "une colline",
               "marais": "un marais", "montagne": "une montagne"}


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


def _arrondi(x):
    return int(x + 0.5)


def _gorgees(etat, base):
    return max(1, _arrondi(base * etat["mult"])) if base > 0 else 0


def _carte_pour(n):
    """La carte faite pour ce nombre de joueurs (la plus proche sinon)."""
    if str(n) in SW_CARTES:
        return SW_CARTES[str(n)]
    proche = min(SW_CARTES, key=lambda k: abs(int(k) - n))
    return SW_CARTES[proche]


def _region(etat, r):
    return etat["carte"]["regions"][r]


def _peuple(etat, pid):
    return etat["peuples"].get(str(pid)) if pid is not None else None


def _actif(etat, i):
    return _peuple(etat, etat["j"][i]["actif"])


def _regions_de(etat, pid):
    return [r for r, reg in enumerate(etat["regions"]) if reg["p"] == pid]


def _nom(etat, i):
    return etat["joueurs"][i]


def _nom_peuple(p):
    return SW_RACES[p["race"]]["nom"] + " " + SW_POUVOIRS[p["pouvoir"]]["nom"].lower()


def _journal(etat, texte, joueur=None, genre="info"):
    etat["journal_n"] += 1
    etat["journal"].append({"n": etat["journal_n"], "texte": texte, "j": joueur, "genre": genre})
    del etat["journal"][:-SW_CONFIG["journal_max"]]


def _donner_gorgees(etat, ev, liste, raison):
    """liste : [(joueur, "boit"|"distribue", base)]. Applique le multiplicateur."""
    sortie = []
    for k, sens, base in liste:
        n = _gorgees(etat, base)
        if n <= 0:
            continue
        if sens == "boit":
            etat["j"][k]["bu"] += n
        else:
            etat["j"][k]["distribue"] += n
        sortie.append({"joueur": k, sens: n})
    if sortie:
        ev.append(_tous({"type": "sw_gorgees", "liste": sortie, "raison": raison}))
    return sortie


# =============================================================
# COMBINAISONS
# =============================================================
def _banni(etat, race, pouvoir):
    for r, p in SW_CONFIG["bannis"]:
        if (r is None or r == race) and (p is None or p == pouvoir):
            return True
    return False


def _bonus_depart(race, pouvoir):
    total = 0
    for b in SW_CONFIG["bonus_depart"]:
        if (b["race"] is None or b["race"] == race) and (b["pouvoir"] is None or b["pouvoir"] == pouvoir):
            total += b["pieces"]
    return total


def _pions(race, pouvoir):
    return max(SW_CONFIG["pions_min"], SW_RACES[race]["pions"] + SW_POUVOIRS[pouvoir]["pions"])


def _tirer(etat):
    """Une nouvelle combinaison pour la fin de la file (jamais bannie, jamais en double dans la file)."""
    deja = {(c["race"], c["pouvoir"]) for c in etat["file"]}
    races_vues = {c["race"] for c in etat["file"]}
    pouvoirs_vus = {c["pouvoir"] for c in etat["file"]}
    for essai in range(400):
        race = random.choice(etat["races"])
        pouvoir = random.choice(etat["pouvoirs"])
        if _banni(etat, race, pouvoir) or (race, pouvoir) in deja:
            continue
        # De la variete : pas deux fois la meme race ou le meme pouvoir visibles, si possible
        if essai < 200 and (race in races_vues or pouvoir in pouvoirs_vus):
            continue
        return {"race": race, "pouvoir": pouvoir, "pions": _pions(race, pouvoir),
                "pieces": _bonus_depart(race, pouvoir)}
    race, pouvoir = etat["races"][0], etat["pouvoirs"][0]
    return {"race": race, "pouvoir": pouvoir, "pions": _pions(race, pouvoir), "pieces": 0}


def _remplir_file(etat):
    while len(etat["file"]) < SW_CONFIG["file_visible"]:
        etat["file"].append(_tirer(etat))


# =============================================================
# CREATION
# =============================================================
def sw_vitesse_conseillee(n):
    return SW_CONFIG["vitesse_defaut_nombreux"] if n >= SW_CONFIG["joueurs_nombreux"] else SW_CONFIG["vitesse_defaut"]


def sw_initialiser(noms, maintenant, options=None):
    options = options or {}
    n = len(noms)
    vitesse = options.get("vitesse")
    if vitesse not in SW_CONFIG["vitesses"]:
        vitesse = sw_vitesse_conseillee(n)
    v = SW_CONFIG["vitesses"][vitesse]
    tours = _entier(options.get("tours"))
    if tours not in SW_CONFIG["tours_choix"]:
        tours = v["tours"]
    aleatoire = options.get("aleatoire") is True
    races = list(SW_RACES)
    pouvoirs = list(SW_POUVOIRS)
    if aleatoire:
        races = random.sample(races, min(len(races), SW_CONFIG["aleatoire"]["races"]))
        pouvoirs = random.sample(pouvoirs, min(len(pouvoirs), SW_CONFIG["aleatoire"]["pouvoirs"]))
    carte = _carte_pour(n)
    premier = random.randrange(n)
    etat = {
        "version": 0,
        "phase": "jeu",
        "nb_joueurs": n,
        "joueurs": list(noms),
        "partis": [False] * n,
        "vitesse": vitesse,
        "tours_max": tours,
        "minuteur": v["minuteur"],
        "mult": v["mult_gorgees"],
        "aleatoire": aleatoire,
        "vengeance": options.get("vengeance") is True,
        "races": races,
        "pouvoirs": pouvoirs,
        "carte": carte,
        "regions": [{"j": None, "p": None, "pions": 0, "fortin": False, "camp": False, "gardien": False}
                    for _ in carte["regions"]],
        "peuples": {},
        "prochain_peuple": 1,
        "file": [],
        "ordre": [(premier + k) % n for k in range(n)],
        "pos": 0,
        "manche": 1,
        "joueur": premier,
        "etape": "choix",
        "echeance": None,
        "debut_tour": maintenant,
        "j": [{"pieces": 0, "actif": None, "declins": [], "bu": 0, "distribue": 0,
               "suite_pertes": 0, "interdits": [], "prises": 0, "pertes": 0}
              for _ in range(n)],
        "tour": {},
        "journal": [],
        "journal_n": 0,
        "dernier_score": None,
        "resultat": None,
        "fin_signalee": False,
        "reactions": {},
    }
    _remplir_file(etat)
    _journal(etat, f"Partie lancée sur la carte à {carte['joueurs'][0]} joueurs ({len(carte['regions'])} régions), "
                   f"{tours} tours chacun. {noms[premier]} commence.", genre="debut")
    _debut_tour(etat, maintenant)
    return etat


# =============================================================
# TOUR
# =============================================================
def _debut_tour(etat, maintenant):
    i = etat["joueur"]
    etat["tour"] = {"rassemble": False, "conquetes": 0, "de_utilise": False, "marin": False,
                    "vol": False, "pillages": 0, "victimes": {}, "choisi": False}
    etat["etape"] = "choix" if etat["j"][i]["actif"] is None else "action"
    etat["debut_tour"] = maintenant
    _armer(etat, maintenant)


def _armer(etat, maintenant):
    if etat["phase"] != "jeu":
        etat["echeance"] = None
        return
    i = etat["joueur"]
    etat["echeance"] = maintenant + (SW_CONFIG["delai_absent"] if etat["partis"][i] else etat["minuteur"])


def _rassembler(etat, i):
    """Reprise en main : tous les pions du peuple actif sauf 1 par region."""
    if etat["tour"]["rassemble"]:
        return
    etat["tour"]["rassemble"] = True
    p = _actif(etat, i)
    if p is None:
        return
    for r in _regions_de(etat, p["id"]):
        reg = etat["regions"][r]
        if reg["pions"] > 1:
            p["main"] += reg["pions"] - 1
            reg["pions"] = 1


def _en_tete(etat):
    m = max(j["pieces"] for j in etat["j"])
    return {k for k, j in enumerate(etat["j"]) if j["pieces"] == m}


def _cout(etat, i, r):
    """Cout de conquete de la region r par le peuple actif de i (sans le de)."""
    p = _actif(etat, i)
    reg = etat["regions"][r]
    terrain = _region(etat, r)["terrain"]
    c = SW_CONFIG["cout_base"] + reg["pions"]
    if terrain == "montagne":
        c += SW_CONFIG["cout_montagne"]
    if reg["fortin"]:
        c += 1
    defenseur = _peuple(etat, reg["p"])
    if defenseur is not None and defenseur["etat"] == "actif" and defenseur["race"] == "titans":
        c += 1
    if p["race"] == "colosses" and terrain in ("montagne", "colline"):
        c -= 1
    if p["pouvoir"] == "chevaucheur" and terrain in ("plaine", "colline"):
        c -= 1
    if p["race"] == "spectres" and defenseur is not None and defenseur["etat"] == "declin":
        c -= 1
    if p["pouvoir"] == "eclaireur":
        c -= 1
    return max(SW_CONFIG["cout_min"], c)


def _verifier_cible(etat, i, r, dragon=False):
    """(cout, voie, None) si la region r est conquerable par i, sinon (None, None, raison)."""
    p = _actif(etat, i)
    if p is None:
        return None, None, "Tu n'as pas de peuple actif."
    r = _entier(r)
    if r is None or not 0 <= r < len(etat["regions"]):
        return None, None, "Région inconnue."
    reg = etat["regions"][r]
    if reg["j"] == i:
        return None, None, "Cette région est déjà à toi."
    if reg["gardien"]:
        return None, None, "Un gardien protège cette région."
    if reg["j"] is not None and reg["j"] in etat["j"][i]["interdits"]:
        return None, None, f"{_nom(etat, reg['j'])} t'a envoyé un émissaire : pas d'attaque contre lui ce tour."
    info = _region(etat, r)
    if dragon or p["pouvoir"] == "aerien":
        voie = "dragon" if dragon else "aerien"
    else:
        miennes = _regions_de(etat, p["id"])
        if not miennes:
            if not info["bord"]:
                return None, None, "Un peuple entre sur la carte par une région de bord."
            voie = "entree"
        elif any(r in _region(etat, m)["voisins"] for m in miennes):
            voie = "voisine"
        elif (p["pouvoir"] == "souterrain" and info["terrain"] == "montagne"
              and any(_region(etat, m)["terrain"] == "montagne" for m in miennes)):
            voie = "tunnel"
        elif p["race"] == "marins" and info["bord"] and not etat["tour"]["marin"]:
            voie = "marin"
        else:
            return None, None, "Cette région n'est pas voisine des tiennes."
    cout = 1 if dragon else _cout(etat, i, r)
    return cout, voie, None


def sw_possibles(etat):
    """Pour le joueur dont c est le tour : {region: {cout, voie}} conquerables."""
    if etat["phase"] != "jeu" or etat["etape"] not in ("action", "conquete"):
        return {}
    i = etat["joueur"]
    if _actif(etat, i) is None:
        return {}
    res = {}
    for r in range(len(etat["regions"])):
        cout, voie, err = _verifier_cible(etat, i, r)
        if err is None:
            res[str(r)] = {"cout": cout, "voie": voie}
    return res


def _conquerir(etat, ev, i, r, poses, cout, voie, de=None):
    p = _actif(etat, i)
    reg = etat["regions"][r]
    ancien_j, ancien_p = reg["j"], _peuple(etat, reg["p"])
    perdus = 0
    texte_bonus = []
    if ancien_j is not None:
        if ancien_p["etat"] == "actif":
            perte = 0 if ancien_p["race"] == "intouchables" else 1
            ancien_p["main"] += max(0, reg["pions"] - perte)
            perdus = min(perte, reg["pions"])
        else:
            perdus = reg["pions"]
        t = etat["tour"]
        t["victimes"][str(ancien_j)] = t["victimes"].get(str(ancien_j), 0) + 1
        etat["j"][ancien_j]["pertes"] += 1
        if p["race"] == "pillards":
            t["pillages"] += 1
        if p["race"] == "nuees":
            p["main"] += 1
            texte_bonus.append("+1 pion (Nuées)")
        if (p["race"] == "voleurs" and ancien_p["etat"] == "actif" and not t["vol"]
                and etat["j"][ancien_j]["pieces"] > 0):
            t["vol"] = True
            etat["j"][ancien_j]["pieces"] -= 1
            etat["j"][i]["pieces"] += 1
            texte_bonus.append(f"1 pièce volée à {_nom(etat, ancien_j)}")
        if p["pouvoir"] == "chasseur" and ancien_j in _en_tete(etat):
            etat["j"][i]["pieces"] += SW_CONFIG["bonus"]["chasseur"]
            texte_bonus.append(f"+{SW_CONFIG['bonus']['chasseur']} pièces (prime sur le premier)")
    reg.update({"j": i, "p": p["id"], "pions": poses, "fortin": False, "camp": False, "gardien": False})
    p["main"] -= poses
    etat["tour"]["conquetes"] += 1
    etat["j"][i]["prises"] += 1
    if voie == "marin":
        etat["tour"]["marin"] = True
    if voie == "dragon":
        p["dragon"] = True
    terrain = SW_TERRAINS[_region(etat, r)["terrain"]]
    pl = "s" if poses > 1 else ""
    if ancien_j is None:
        texte = f"{_nom(etat, i)} prend {terrain} libre ({poses} pion{pl})."
    else:
        texte = f"{_nom(etat, i)} prend {terrain} à {_nom(etat, ancien_j)} ({poses} pion{pl})."
    if voie == "dragon":
        texte += " Le dragon frappe !"
    if texte_bonus:
        texte += " " + ", ".join(texte_bonus) + "."
    _journal(etat, texte, i, "conquete")
    ev.append(_tous({"type": "sw_conquete", "joueur": i, "region": r, "ancien": ancien_j,
                     "pions": poses, "cout": cout, "voie": voie, "perdus": perdus, "de": de}))
    # Gorgees : 1 par region perdue, au plus "plafond_par_attaque" par tour d attaquant
    if ancien_j is not None:
        g = SW_CONFIG["gorgees"]
        n = etat["tour"]["victimes"][str(ancien_j)]
        if n <= g["plafond_par_attaque"]:
            _donner_gorgees(etat, ev, [(ancien_j, "boit", g["region_perdue"])],
                            f"{_nom(etat, ancien_j)} perd une région")


# =============================================================
# ACTIONS
# =============================================================
def _choisir(etat, ev, i, message, maintenant):
    if etat["etape"] != "choix":
        return _erreur(i, "Tu as déjà un peuple.")
    rang = _entier(message.get("rang"))
    if rang is None or not 0 <= rang < len(etat["file"]):
        return _erreur(i, "Combinaison inconnue.")
    j = etat["j"][i]
    if j["pieces"] < rang:
        return _erreur(i, f"Il te faut {rang} pièce{'s' if rang > 1 else ''} pour prendre celle-ci.")
    for k in range(rang):
        etat["file"][k]["pieces"] += 1
    j["pieces"] -= rang
    combi = etat["file"].pop(rang)
    j["pieces"] += combi["pieces"]
    pid = etat["prochain_peuple"]
    etat["prochain_peuple"] += 1
    p = {"id": pid, "j": i, "race": combi["race"], "pouvoir": combi["pouvoir"], "etat": "actif",
         "main": combi["pions"], "pions": combi["pions"], "tours": 0, "dragon": False}
    etat["peuples"][str(pid)] = p
    j["actif"] = pid
    _remplir_file(etat)
    etat["etape"] = "conquete"
    etat["tour"]["choisi"] = True
    etat["tour"]["rassemble"] = True
    gain = f", et ramasse {combi['pieces']} pièce{'s' if combi['pieces'] > 1 else ''}" if combi["pieces"] else ""
    paye = f" (paie {rang})" if rang else ""
    _journal(etat, f"{_nom(etat, i)} prend les {_nom_peuple(p)}{paye}{gain} : {p['main']} pions.", i, "choix")
    ev.append(_tous({"type": "sw_choix", "joueur": i, "race": p["race"], "pouvoir": p["pouvoir"],
                     "rang": rang, "pions": p["main"]}))
    return ev


def _declin(etat, ev, i, message, maintenant):
    p = _actif(etat, i)
    if p is None:
        return _erreur(i, "Tu n'as pas de peuple actif.")
    endurant = p["pouvoir"] == "endurant"
    if etat["etape"] == "action" and not etat["tour"]["choisi"]:
        _passer_en_declin(etat, ev, i)
        _fin_tour(etat, ev, maintenant)
        return ev
    if endurant and etat["etape"] in ("conquete", "redeploiement") and etat["tour"]["conquetes"] > 0:
        # Endurant : on finit le tour de conquete (redeploiement sur, score), puis le declin.
        _rassembler(etat, i)
        _redeploiement_sur(etat, i)
        _fin_tour(etat, ev, maintenant, declin_apres=True)
        return ev
    if etat["tour"]["choisi"]:
        return _erreur(i, "Un peuple tout juste choisi ne peut pas passer en déclin ce tour-ci.")
    return _erreur(i, "Le déclin se décide en début de tour, avant toute conquête.")


def _passer_en_declin(etat, ev, i):
    j = etat["j"][i]
    p = _actif(etat, i)
    # L ancien peuple en declin disparait (sauf Immortel)
    restants = []
    for pid in j["declins"]:
        ancien = _peuple(etat, pid)
        if ancien["pouvoir"] == "immortel":
            restants.append(pid)
            continue
        ancien["etat"] = "disparu"
        for r in _regions_de(etat, pid):
            etat["regions"][r].update({"j": None, "p": None, "pions": 0, "fortin": False, "camp": False, "gardien": False})
    for r in _regions_de(etat, p["id"]):
        etat["regions"][r].update({"pions": 1, "fortin": False, "camp": False, "gardien": False})
    p["etat"] = "declin"
    p["main"] = 0
    restants.append(p["id"])
    j["declins"] = restants
    j["actif"] = None
    _journal(etat, f"{_nom(etat, i)} met les {_nom_peuple(p)} en déclin.", i, "declin")
    ev.append(_tous({"type": "sw_declin", "joueur": i, "peuple": p["id"]}))
    _donner_gorgees(etat, ev, [(i, "distribue", SW_CONFIG["gorgees"]["declin_distribue"])],
                    f"Déclin de {_nom(etat, i)}")


def _preparer_extension(etat, i):
    if etat["etape"] not in ("action", "conquete"):
        return "Ce n'est plus le moment de conquérir."
    if _actif(etat, i) is None:
        return "Choisis d'abord un peuple."
    _rassembler(etat, i)
    etat["etape"] = "conquete"
    return None


def _abandonner(etat, ev, i, message, maintenant):
    err = _preparer_extension(etat, i)
    if err:
        return _erreur(i, err)
    if etat["tour"]["conquetes"] > 0:
        return _erreur(i, "On abandonne une région avant de conquérir, pas après.")
    p = _actif(etat, i)
    r = _entier(message.get("region"))
    if r is None or not 0 <= r < len(etat["regions"]) or etat["regions"][r]["p"] != p["id"]:
        return _erreur(i, "Tu ne peux abandonner qu'une région de ton peuple actif.")
    reg = etat["regions"][r]
    p["main"] += reg["pions"]
    reg.update({"j": None, "p": None, "pions": 0, "fortin": False, "camp": False, "gardien": False})
    _journal(etat, f"{_nom(etat, i)} abandonne {SW_TERRAINS[_region(etat, r)['terrain']]}.", i, "abandon")
    ev.append(_tous({"type": "sw_abandon", "joueur": i, "region": r}))
    return ev


def _conquete(etat, ev, i, message, maintenant):
    err = _preparer_extension(etat, i)
    if err:
        return _erreur(i, err)
    p = _actif(etat, i)
    cout, voie, err = _verifier_cible(etat, i, message.get("region"))
    if err:
        return _erreur(i, err)
    r = _entier(message.get("region"))
    if p["main"] < cout:
        if etat["tour"]["de_utilise"] or p["main"] < 1:
            return _erreur(i, f"Il faut {cout} pions, tu en as {p['main']}.")
        return _erreur(i, f"Il faut {cout} pions, tu en as {p['main']} : tente le dé de renfort.")
    _conquerir(etat, ev, i, r, cout, cout, voie)
    return ev


def _tenter(etat, ev, i, message, maintenant):
    err = _preparer_extension(etat, i)
    if err:
        return _erreur(i, err)
    p = _actif(etat, i)
    if etat["tour"]["de_utilise"]:
        return _erreur(i, "Le dé de renfort a déjà servi ce tour.")
    cout, voie, err = _verifier_cible(etat, i, message.get("region"))
    if err:
        return _erreur(i, err)
    if p["main"] < 1:
        return _erreur(i, "Il te faut au moins 1 pion en main pour tenter le dé.")
    if p["main"] >= cout:
        return _erreur(i, "Tu as assez de pions : conquiers directement.")
    r = _entier(message.get("region"))
    faces = [random.choice(SW_CONFIG["de_renfort"])]
    if p["main"] + faces[0] < cout and p["race"] == "mages":
        faces.append(random.choice(SW_CONFIG["de_renfort"]))   # les Mages relancent un echec
    d = faces[-1]
    reussi = p["main"] + d >= cout
    etat["tour"]["de_utilise"] = True
    ev.append(_tous({"type": "sw_de", "joueur": i, "region": r, "faces": faces, "reussi": reussi,
                     "cout": cout, "main": p["main"]}))
    if reussi:
        _conquerir(etat, ev, i, r, max(1, cout - d), cout, voie, de=d)
    else:
        _journal(etat, f"{_nom(etat, i)} tente le dé ({' puis '.join(map(str, faces))}) : raté.", i, "de")
    if p["pouvoir"] != "furie":
        etat["etape"] = "redeploiement"
    return ev


def _dragon(etat, ev, i, message, maintenant):
    err = _preparer_extension(etat, i)
    if err:
        return _erreur(i, err)
    p = _actif(etat, i)
    if p["pouvoir"] != "dompteur":
        return _erreur(i, "Seul un peuple Dompteur a un dragon.")
    if p["dragon"]:
        return _erreur(i, "Le dragon a déjà servi.")
    if p["main"] < 1:
        return _erreur(i, "Il te faut 1 pion en main.")
    cout, voie, err = _verifier_cible(etat, i, message.get("region"), dragon=True)
    if err:
        return _erreur(i, err)
    _conquerir(etat, ev, i, _entier(message.get("region")), 1, 1, "dragon")
    return ev


def _fin_conquete(etat, ev, i, message, maintenant):
    if etat["etape"] not in ("action", "conquete"):
        return _erreur(i, "Tu as déjà fini tes conquêtes.")
    if _actif(etat, i) is None:
        return _erreur(i, "Choisis d'abord un peuple.")
    _rassembler(etat, i)
    etat["etape"] = "redeploiement"
    return ev


def _menaces(etat, i, r):
    """Nombre de voisines qui ne sont pas au peuple actif de i (pour le redeploiement automatique)."""
    p = _actif(etat, i)
    return sum(1 for v in _region(etat, r)["voisins"] if etat["regions"][v]["p"] != p["id"]) + \
        (1 if _region(etat, r)["bord"] else 0)


def _redeploiement_sur(etat, i):
    """Le minuteur a parle (ou declin Endurant) : les pions en main vont aux regions les plus exposees."""
    p = _actif(etat, i)
    if p is None:
        return
    miennes = _regions_de(etat, p["id"])
    if not miennes:
        return
    for r in miennes:
        reg = etat["regions"][r]
        if reg["pions"] < 1 and not reg["camp"]:
            if p["main"] > 0:
                reg["pions"] = 1
                p["main"] -= 1
    ordre = sorted(miennes, key=lambda r: (-_menaces(etat, i, r), r))
    k = 0
    while p["main"] > 0:
        etat["regions"][ordre[k % len(ordre)]]["pions"] += 1
        p["main"] -= 1
        k += 1
    # Pouvoirs de fin de tour poses d office, la ou ils protegent le mieux
    lim = SW_CONFIG["limites"]
    if p["pouvoir"] == "batisseur":
        if sum(1 for r in miennes if etat["regions"][r]["fortin"]) < lim["fortins"]:
            libres = [r for r in ordre if not etat["regions"][r]["fortin"]]
            if libres:
                etat["regions"][libres[0]]["fortin"] = True
    if p["pouvoir"] == "gardien" and not any(etat["regions"][r]["gardien"] for r in miennes):
        for r in ordre[:lim["gardiens"]]:
            etat["regions"][r]["gardien"] = True


def _redeployer(etat, ev, i, message, maintenant):
    if etat["etape"] != "redeploiement":
        return _erreur(i, "Termine d'abord tes conquêtes.")
    p = _actif(etat, i)
    if p is None:
        return _erreur(i, "Tu n'as pas de peuple actif.")
    miennes = _regions_de(etat, p["id"])
    lim = SW_CONFIG["limites"]
    total = sum(etat["regions"][r]["pions"] for r in miennes) + p["main"]
    repartition = message.get("pions")
    if miennes:
        if not isinstance(repartition, dict):
            return _erreur(i, "Répartition des pions manquante.")
        nouv = {}
        for r in miennes:
            v = _entier(repartition.get(str(r), 0))
            if v is None or v < 0:
                return _erreur(i, "Nombre de pions invalide.")
            nouv[r] = v
        for cle in repartition:
            if _entier(cle) not in nouv:
                return _erreur(i, "Tu ne peux placer des pions que sur tes régions.")
        if sum(nouv.values()) != total:
            return _erreur(i, f"Place tous tes pions : {total} au total.")
    else:
        nouv = {}

    # Choix de fin de tour (pouvoirs), verifies avant de toucher a l etat
    fortin = message.get("fortin")
    if fortin is not None:
        fortin = _entier(fortin)
        if p["pouvoir"] != "batisseur" or fortin not in nouv or etat["regions"][fortin]["fortin"]:
            return _erreur(i, "Fortin impossible ici.")
        if sum(1 for r in miennes if etat["regions"][r]["fortin"]) >= lim["fortins"]:
            return _erreur(i, f"{lim['fortins']} fortins au maximum.")
    camp = message.get("camp")
    if camp is not None:
        camp = _entier(camp)
        if p["pouvoir"] != "campeur" or camp not in nouv or etat["regions"][camp]["camp"]:
            return _erreur(i, "Camp impossible ici.")
        if sum(1 for r in miennes if etat["regions"][r]["camp"]) >= lim["camps"]:
            return _erreur(i, f"{lim['camps']} camps au maximum.")
    gardiens = message.get("gardiens")
    if gardiens is not None:
        if p["pouvoir"] != "gardien" or not isinstance(gardiens, list):
            return _erreur(i, "Gardiens impossibles.")
        gardiens = [_entier(g) for g in gardiens]
        if len(set(gardiens)) != len(gardiens) or len(gardiens) > lim["gardiens"] or \
                any(g not in nouv for g in gardiens):
            return _erreur(i, f"Au plus {lim['gardiens']} gardiens, sur tes régions.")
    sabotage = message.get("sabotage")
    if sabotage is not None:
        sabotage = _entier(sabotage)
        if p["pouvoir"] != "saboteur" or sabotage is None or not 0 <= sabotage < len(etat["regions"]):
            return _erreur(i, "Sabotage impossible.")
        cible = etat["regions"][sabotage]
        if cible["j"] is None or cible["j"] == i or cible["pions"] < 1 or \
                not any(sabotage in _region(etat, m)["voisins"] for m in miennes):
            return _erreur(i, "Sabote une région adverse voisine des tiennes, avec au moins 1 pion.")
    emissaire = message.get("emissaire")
    if emissaire is not None:
        emissaire = _entier(emissaire)
        if p["pouvoir"] != "emissaire" or emissaire is None or not 0 <= emissaire < etat["nb_joueurs"] \
                or emissaire == i:
            return _erreur(i, "Émissaire impossible.")
        if str(emissaire) in etat["tour"]["victimes"]:
            return _erreur(i, "L'émissaire va chez un adversaire que tu n'as pas attaqué ce tour.")

    # Regions laissees sans pion : camp, ou Nomades (2 au plus)
    vides = [r for r, v in nouv.items() if v == 0 and not etat["regions"][r]["camp"] and r != camp]
    if vides:
        if p["race"] != "nomades" or len(vides) > lim["nomades_vides"]:
            return _erreur(i, "Chaque région garde au moins 1 pion"
                              + (f" (les Nomades peuvent en vider {lim['nomades_vides']})." if p["race"] == "nomades" else "."))

    for r, v in nouv.items():
        etat["regions"][r]["pions"] = v
    if miennes:
        p["main"] = 0
    if fortin is not None:
        etat["regions"][fortin]["fortin"] = True
        _journal(etat, f"{_nom(etat, i)} bâtit un fortin.", i, "pouvoir")
    if camp is not None:
        etat["regions"][camp]["camp"] = True
        _journal(etat, f"{_nom(etat, i)} installe un camp.", i, "pouvoir")
    if gardiens is not None:
        for r in miennes:
            etat["regions"][r]["gardien"] = r in gardiens
    if sabotage is not None:
        cible = etat["regions"][sabotage]
        victime = cible["j"]
        cp = _peuple(etat, cible["p"])
        cible["pions"] -= 1
        if cible["pions"] == 0 and not cible["camp"] and not (cp["etat"] == "actif" and cp["race"] == "nomades"):
            cible.update({"j": None, "p": None, "fortin": False, "gardien": False})
            _journal(etat, f"{_nom(etat, i)} sabote une région de {_nom(etat, victime)} : elle tombe.", i, "pouvoir")
        else:
            _journal(etat, f"{_nom(etat, i)} sabote une région de {_nom(etat, victime)} (-1 pion).", i, "pouvoir")
        ev.append(_tous({"type": "sw_sabotage", "joueur": i, "region": sabotage, "victime": victime}))
    if emissaire is not None:
        etat["j"][emissaire]["interdits"].append(i)
        _journal(etat, f"{_nom(etat, i)} envoie un émissaire à {_nom(etat, emissaire)}.", i, "pouvoir")
    if message.get("declin") is True and p["pouvoir"] == "endurant" and etat["tour"]["conquetes"] > 0:
        _fin_tour(etat, ev, maintenant, declin_apres=True)
    else:
        _fin_tour(etat, ev, maintenant)
    return ev


def _reaction(etat, ev, i, message, maintenant):
    emoji = message.get("emoji")
    if emoji not in SW_REACTIONS:
        return []
    if maintenant - etat["reactions"].get(str(i), 0) < SW_DELAI_REACTION:
        return []
    etat["reactions"][str(i)] = maintenant
    return [_tous({"type": "sw_reaction", "joueur": i, "emoji": emoji})]


# =============================================================
# FIN DE TOUR, SCORE, FIN DE PARTIE
# =============================================================
def _bonus_pouvoir(etat, p, regions, detail):
    pv = p["pouvoir"]
    terr = [_region(etat, r)["terrain"] for r in regions]
    n = 0
    for pouvoir, terrain in (("sylvestre", "foret"), ("alpin", "colline"), ("fangeux", "marais")):
        if pv == pouvoir:
            k = terr.count(terrain)
            if k:
                detail.append([SW_POUVOIRS[pv]["nom"], k])
                n += k
    if pv == "batisseur":
        k = sum(1 for r in regions if etat["regions"][r]["fortin"])
        if k:
            detail.append(["Fortins", k])
            n += k
    if pv == "negociant" and regions:
        detail.append(["Négociant", len(regions)])
        n += len(regions)
    if pv == "transmuteur":
        detail.append(["Transmuteur", SW_CONFIG["bonus"]["transmuteur"]])
        n += SW_CONFIG["bonus"]["transmuteur"]
    return n


def sw_score(etat, i):
    """(total, detail) des pieces de fin de tour du joueur i."""
    j = etat["j"][i]
    detail = []
    total = 0
    p = _actif(etat, i)
    if p is not None:
        reg = _regions_de(etat, p["id"])
        if reg:
            detail.append(["Régions (actif)", len(reg)])
            total += len(reg)
        terr = [_region(etat, r)["terrain"] for r in reg]
        race = p["race"]
        bonus = 0
        if race == "taupiers":
            bonus = sum(1 for r in reg if _region(etat, r)["marque"] == "mine")
        elif race == "cultivateurs":
            bonus = terr.count("plaine")
        elif race == "pillards":
            bonus = etat["tour"].get("pillages", 0) if etat["joueur"] == i else 0
        elif race == "pecheurs":
            bonus = sum(1 for r in reg if _region(etat, r)["bord"])
        elif race == "forgerons":
            bonus = SW_CONFIG["bonus"]["forgerons"] * terr.count("montagne")
        elif race == "mages":
            bonus = sum(1 for r in reg if _region(etat, r)["marque"] == "source")
        if bonus:
            detail.append([SW_RACES[race]["nom"], bonus])
            total += bonus
        total += _bonus_pouvoir(etat, p, reg, detail)
        if p["pouvoir"] == "mecene" and p["tours"] == 0:
            detail.append(["Mécène", SW_CONFIG["bonus"]["mecene"]])
            total += SW_CONFIG["bonus"]["mecene"]
    for pid in j["declins"]:
        d = _peuple(etat, pid)
        reg = _regions_de(etat, pid)
        if reg:
            detail.append(["Régions (déclin)", len(reg)])
            total += len(reg)
        if d["race"] == "revenants":
            total += _bonus_pouvoir(etat, d, reg, detail)
    return total, detail


def _fin_tour(etat, ev, maintenant, declin_apres=False):
    i = etat["joueur"]
    j = etat["j"][i]
    total, detail = sw_score(etat, i)
    j["pieces"] += total
    p = _actif(etat, i)
    if p is not None:
        p["tours"] += 1
    etat["dernier_score"] = {"joueur": i, "total": total, "detail": detail}
    _journal(etat, f"{_nom(etat, i)} gagne {total} pièce{'s' if total > 1 else ''}.", i, "score")
    ev.append(_tous({"type": "sw_score", "joueur": i, "total": total, "detail": detail}))
    if declin_apres and p is not None:
        _passer_en_declin(etat, ev, i)
    # Vengeance (option) : trois tours adverses d affilee a perdre des regions
    if etat["vengeance"]:
        g = SW_CONFIG["gorgees"]
        for k in range(etat["nb_joueurs"]):
            if k == i:
                continue
            if str(k) in etat["tour"]["victimes"]:
                etat["j"][k]["suite_pertes"] += 1
                if etat["j"][k]["suite_pertes"] >= g["vengeance_seuil"]:
                    etat["j"][k]["suite_pertes"] = 0
                    _donner_gorgees(etat, ev, [(k, "distribue", g["vengeance_distribue"])],
                                    f"Vengeance de {_nom(etat, k)}")
            else:
                etat["j"][k]["suite_pertes"] = 0
    j["interdits"] = []
    # Joueur suivant
    etat["pos"] += 1
    if etat["pos"] >= etat["nb_joueurs"]:
        etat["pos"] = 0
        etat["manche"] += 1
        if etat["manche"] > etat["tours_max"]:
            _terminer(etat, ev)
            return
    etat["joueur"] = etat["ordre"][etat["pos"]]
    _debut_tour(etat, maintenant)


def _nb_regions(etat, k):
    return sum(1 for reg in etat["regions"] if reg["j"] == k)


def _terminer(etat, ev):
    n = etat["nb_joueurs"]
    sort = list(range(n))
    random.shuffle(sort)
    tirage = {k: rang for rang, k in enumerate(sort)}
    classement = sorted(range(n), key=lambda k: (-etat["j"][k]["pieces"], -_nb_regions(etat, k), tirage[k]))
    etat["phase"] = "fin"
    etat["etape"] = "fin"
    etat["echeance"] = None
    g = SW_CONFIG["gorgees"]
    gagnant, dernier = classement[0], classement[-1]
    gorgees = _donner_gorgees(etat, ev, [(dernier, "boit", g["dernier_boit"]),
                                         (gagnant, "distribue", g["vainqueur_distribue"])], "Fin de partie")
    etat["resultat"] = {
        "classement": [{"joueur": k, "pieces": etat["j"][k]["pieces"], "regions": _nb_regions(etat, k),
                        "prises": etat["j"][k]["prises"], "pertes": etat["j"][k]["pertes"],
                        "bu": etat["j"][k]["bu"], "distribue": etat["j"][k]["distribue"]}
                       for k in classement],
        "gagnant": gagnant,
        "dernier": dernier,
        "gorgees": gorgees,
    }
    _journal(etat, f"Fin de partie : {_nom(etat, gagnant)} l'emporte avec {etat['j'][gagnant]['pieces']} pièces.",
             gagnant, "fin")
    ev.append(_tous({"type": "sw_fin", "resultat": etat["resultat"]}))


# =============================================================
# POINT D ENTREE
# =============================================================
SW_ACTIONS = {
    "sw_choisir": _choisir,
    "sw_declin": _declin,
    "sw_abandonner": _abandonner,
    "sw_conquerir": _conquete,
    "sw_tenter": _tenter,
    "sw_dragon": _dragon,
    "sw_fin_conquete": _fin_conquete,
    "sw_redeployer": _redeployer,
}


def sw_action(etat, i, message, maintenant):
    t = message.get("type")
    if t == "sw_reaction":
        if not 0 <= i < etat["nb_joueurs"]:
            return []
        return _reaction(etat, [], i, message, maintenant)
    fn = SW_ACTIONS.get(t)
    if fn is None:
        return []
    if not 0 <= i < etat["nb_joueurs"]:
        return _erreur(i, "Tu regardes cette partie : tu joueras à la suivante.")
    if etat["phase"] != "jeu":
        return _erreur(i, "La partie est finie.")
    if i != etat["joueur"]:
        return _erreur(i, f"Ce n'est pas ton tour : c'est à {_nom(etat, etat['joueur'])}.")
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR, DEPARTS ET RETOURS
# =============================================================
def sw_prochaine_echeance(etat):
    return etat["echeance"]


def sw_expirer(etat, maintenant):
    """Le minuteur du tour est ecoule : le serveur finit le tour de facon sure."""
    if etat["phase"] != "jeu" or etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    i = etat["joueur"]
    avant = etat["version"]
    if etat["etape"] == "choix":
        _choisir(etat, ev, i, {"rang": 0}, maintenant)
    _rassembler(etat, i)
    _redeploiement_sur(etat, i)
    _journal(etat, f"Temps écoulé pour {_nom(etat, i)} : ses pions sont redéployés d'office.", i, "temps")
    _fin_tour(etat, ev, maintenant)
    etat["version"] = avant + 1
    return ev


def sw_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    if etat["phase"] == "jeu" and etat["joueur"] == index and etat["echeance"] is not None:
        etat["echeance"] = min(etat["echeance"], maintenant + SW_CONFIG["delai_absent"])


def sw_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "jeu" and etat["joueur"] == index and etat["echeance"] is not None:
        etat["echeance"] = max(etat["echeance"], maintenant + min(SW_DELAI_RETOUR, etat["minuteur"]))


# =============================================================
# CE QUE VOIENT LES JOUEURS
# =============================================================
def sw_catalogue(etat):
    """Partie fixe (envoyee au debut et a la reconnexion) : carte, races, pouvoirs, reglages."""
    return {
        "type": "sw_demarree",
        "joueurs": etat["joueurs"],
        "carte": etat["carte"],
        "races": {k: SW_RACES[k] for k in etat["races"]},
        "pouvoirs": {k: SW_POUVOIRS[k] for k in etat["pouvoirs"]},
        "de": SW_CONFIG["de_renfort"],
        "reactions": SW_REACTIONS,
        "reglage": {"vitesse": etat["vitesse"], "titre_vitesse": SW_CONFIG["vitesses"][etat["vitesse"]]["titre"],
                    "tours": etat["tours_max"], "minuteur": etat["minuteur"], "mult": etat["mult"],
                    "aleatoire": etat["aleatoire"], "vengeance": etat["vengeance"]},
    }


def sw_etat_public(etat, maintenant):
    """Tout est public dans ce jeu : seule la suite des combinaisons (tiree a la volee) reste cachee."""
    i = etat["joueur"]
    p = _actif(etat, i) if etat["phase"] == "jeu" else None
    t = etat["tour"]
    return {
        "type": "sw_etat",
        "version": etat["version"],
        "phase": etat["phase"],
        "joueurs": etat["joueurs"],
        "partis": etat["partis"],
        "joueur": i,
        "etape": etat["etape"],
        "manche": min(etat["manche"], etat["tours_max"]),
        "tours_max": etat["tours_max"],
        "ordre": etat["ordre"],
        "pos": etat["pos"],
        "reste": None if etat["echeance"] is None else max(0.0, round(etat["echeance"] - maintenant, 1)),
        "minuteur": etat["minuteur"],
        "regions": etat["regions"],
        "peuples": {k: v for k, v in etat["peuples"].items() if v["etat"] != "disparu"},
        "file": etat["file"],
        "scores": [{"pieces": j["pieces"], "actif": j["actif"], "declins": j["declins"], "bu": j["bu"],
                    "distribue": j["distribue"], "interdits": j["interdits"], "regions": _nb_regions(etat, k)}
                   for k, j in enumerate(etat["j"])],
        "tour": {"conquetes": t.get("conquetes", 0), "de_utilise": t.get("de_utilise", False),
                 "marin": t.get("marin", False), "choisi": t.get("choisi", False),
                 "rassemble": t.get("rassemble", False), "victimes": t.get("victimes", {})},
        "main": p["main"] if p else 0,
        # pions qu il aura en main pour conquerir (reprise en main pas encore faite)
        "main_dispo": (p["main"] + (0 if t.get("rassemble") else
                                    sum(max(0, etat["regions"][r]["pions"] - 1) for r in _regions_de(etat, p["id"]))))
        if p else 0,
        "possibles": sw_possibles(etat),
        "dernier_score": etat["dernier_score"],
        "journal": etat["journal"][-15:],
        "resultat": etat["resultat"],
    }
