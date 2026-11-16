"""
Moteur de l'Enquete (jeu fil conducteur d une soiree). Pur : ni reseau ni
asyncio, testable hors ligne (tests/enquete_essai.py).

Un meurtre a eu lieu ; la solution secrete est un triplet (tueur, arme,
lieu). Le tueur est l un des joueurs. Les autres enquetent en parallele des
autres jeux de la soiree.

Le moteur ne connait pas l univers : tout le contenu (lieux, armes,
portraits, textes du narrateur) vient d un pack JSON (enquetes/*.json), et
tous les chiffres de enquete_config.json.

Tranche 1 : salle d attente, reglages de l hote, choix des portraits,
tirage du tueur et de la solution, scene d ouverture, fin a l heure butoir.

SECURITE : le tueur ne doit jamais etre deductible techniquement.
- vue_publique ne contient JAMAIS la solution ni le tueur avant la fin ;
- vue_privee a exactement les memes cles pour tous les joueurs.

Les actions renvoient une liste d evenements {"a": None | nom, "msg": {...}} :
a None = a tous, sinon au seul joueur nomme. Un refus ne change pas l etat
(la version ne bouge pas) et n envoie qu une erreur privee.
"""
import json
import os
import random

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PACKS = os.path.join(DOSSIER, "enquetes")
FICHIER_CONFIG = os.path.join(DOSSIER, "enquete_config.json")
NOMBRE_ELEMENTS_PACK = 8      # lieux et armes par univers


def charger_config():
    with open(FICHIER_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def valider_pack(pack, joueurs_max):
    """Un pack mal forme doit etre refuse au chargement, pas en pleine soiree."""
    for cle in ("id", "nom", "victime", "narrateur", "vocabulaire", "lieux", "armes", "portraits", "textes"):
        if cle not in pack:
            raise ValueError(f"pack {pack.get('id')} : champ {cle} manquant")
    for cle in ("lieux", "armes"):
        if len(pack[cle]) != NOMBRE_ELEMENTS_PACK:
            raise ValueError(f"pack {pack['id']} : {NOMBRE_ELEMENTS_PACK} {cle} attendus")
    if len(pack["portraits"]) < joueurs_max:
        raise ValueError(f"pack {pack['id']} : au moins {joueurs_max} portraits attendus")
    for cle in ("lieux", "armes", "portraits"):
        ids = [e["id"] for e in pack[cle]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"pack {pack['id']} : identifiants de {cle} en double")
    return pack


def charger_packs(config):
    packs = {}
    for nom in sorted(os.listdir(DOSSIER_PACKS)):
        if nom.endswith(".json"):
            with open(os.path.join(DOSSIER_PACKS, nom), encoding="utf-8") as f:
                pack = valider_pack(json.load(f), config["joueurs_max"])
            packs[pack["id"]] = pack
    return packs


CONFIG = charger_config()
PACKS = charger_packs(CONFIG)


def cle_nom(nom):
    return " ".join(str(nom).split()).casefold()


def _tous(msg):
    return {"a": None, "msg": msg}


def _a(nom, msg):
    return {"a": nom, "msg": msg}


def _erreur(nom, texte):
    return [_a(nom, {"type": "enq_erreur", "message": texte})]


def pack_public(pack):
    """Ce que les clients affichent de l univers (aucun secret dedans)."""
    return {
        "type": "enq_pack",
        "id": pack["id"], "nom": pack["nom"], "epoque": pack.get("epoque", ""),
        "ambiance": pack.get("ambiance", ""), "palette": pack.get("palette", {}),
        "police": pack.get("police"), "victime": pack["victime"],
        "narrateur": pack["narrateur"], "vocabulaire": pack["vocabulaire"],
        "lieux": pack["lieux"], "armes": pack["armes"], "portraits": pack["portraits"],
        "ouverture": pack["textes"]["ouverture"],
    }


def univers_disponibles():
    return [{"id": p["id"], "nom": p["nom"], "epoque": p.get("epoque", "")} for p in PACKS.values()]


# =============================================================
# CREATION ET SALLE D ATTENTE
# =============================================================

def nouvelle_partie(code, hote, maintenant, config=None):
    config = config or CONFIG
    vitesse = config["vitesse_defaut"]
    v = config["vitesses"][vitesse]
    return {
        "code": code,
        "hote": hote,
        "phase": "lobby",           # lobby | ouverture | enquete | fin | annulee
        "version": 0,
        "cree_a": maintenant,
        "univers": next(iter(PACKS)),
        "vitesse": vitesse,
        "jeux": list(v["jeux"]),
        "duree": v["duree"],
        "unite": config["gorgees"]["unite_defaut"],
        "joueurs": [],              # [{"nom", "portrait"}], ordre d arrivee
        # --- secret jusqu a la fin ---
        "tueur": None,
        "solution": None,           # {"tueur", "arme", "lieu"}
        "lieux": [],                # ids retenus pour cette partie
        "armes": [],
        "debut_a": None,
        "ouverture_fin_a": None,
        "fin_a": None,
        "vainqueur": None,          # "enqueteurs" | "tueur"
        "detective": None,
        "raison_fin": None,
    }


def _joueur(etat, nom):
    k = cle_nom(nom)
    for j in etat["joueurs"]:
        if cle_nom(j["nom"]) == k:
            return j
    return None


def _est_hote(etat, nom):
    return cle_nom(nom) == cle_nom(etat["hote"])


def action(etat, nom, message, maintenant, rng=random, config=None):
    config = config or CONFIG
    t = message.get("type")
    f = {
        "enq_portrait": _portrait,
        "enq_quitter": _quitter,
        "enq_config": _configurer,
        "enq_lancer": _lancer,
        "enq_annuler": _annuler,
    }.get(t)
    if f is None:
        return []
    return f(etat, nom, message, maintenant, rng, config)


def _portrait(etat, nom, message, maintenant, rng, config):
    if etat["phase"] != "lobby":
        return _erreur(nom, "L'enquête a déjà commencé.")
    pack = PACKS[etat["univers"]]
    portrait = message.get("portrait")
    if portrait not in {p["id"] for p in pack["portraits"]}:
        return _erreur(nom, "Portrait inconnu.")
    moi = _joueur(etat, nom)
    for j in etat["joueurs"]:
        if j["portrait"] == portrait and j is not moi:
            return _erreur(nom, j["nom"] + " a déjà pris ce portrait.")
    if moi is None:
        if len(etat["joueurs"]) >= config["joueurs_max"]:
            return _erreur(nom, f"L'enquête accueille {config['joueurs_max']} joueurs au plus.")
        etat["joueurs"].append({"nom": nom, "portrait": portrait})
    else:
        moi["portrait"] = portrait
    etat["version"] += 1
    return []


def _quitter(etat, nom, message, maintenant, rng, config):
    moi = _joueur(etat, nom)
    if etat["phase"] != "lobby" or moi is None:
        return []
    etat["joueurs"].remove(moi)
    etat["version"] += 1
    return []


def _configurer(etat, nom, message, maintenant, rng, config):
    if not _est_hote(etat, nom):
        return _erreur(nom, "Seul l'hôte règle l'enquête.")
    if etat["phase"] != "lobby":
        return _erreur(nom, "L'enquête a déjà commencé.")
    ev = []
    univers = message.get("univers", etat["univers"])
    if univers not in PACKS:
        return _erreur(nom, "Univers inconnu.")
    vitesse = message.get("vitesse", etat["vitesse"])
    if vitesse not in config["vitesses"]:
        return _erreur(nom, "Vitesse inconnue.")
    if univers != etat["univers"]:
        # Les portraits changent d un univers a l autre : chacun rechoisit.
        etat["univers"] = univers
        for j in etat["joueurs"]:
            j["portrait"] = None
        ev.append(_tous(pack_public(PACKS[univers])))
    if vitesse != etat["vitesse"]:
        etat["vitesse"] = vitesse
        etat["jeux"] = list(config["vitesses"][vitesse]["jeux"])
        etat["duree"] = config["vitesses"][vitesse]["duree"]
    autorises = config["vitesses"][etat["vitesse"]]["jeux"]
    if "jeux" in message:
        jeux = [j for j in message["jeux"] if j in autorises] if isinstance(message["jeux"], list) else []
        if not jeux:
            return _erreur(nom, "Il faut au moins un jeu qui compte pour l'enquête.")
        etat["jeux"] = jeux
    if "duree" in message:
        d = message["duree"]
        if not isinstance(d, (int, float)) or not 600 <= d <= 8 * 3600:
            return _erreur(nom, "Durée entre 10 minutes et 8 heures.")
        etat["duree"] = int(d)
    if "unite" in message:
        u = " ".join(str(message["unite"]).split())[:20]
        if not u:
            return _erreur(nom, "Unité de pénalité vide.")
        etat["unite"] = u
    etat["version"] += 1
    return ev


def _lancer(etat, nom, message, maintenant, rng, config):
    if not _est_hote(etat, nom):
        return _erreur(nom, "Seul l'hôte lance l'enquête.")
    if etat["phase"] != "lobby":
        return []
    n = len(etat["joueurs"])
    if not config["joueurs_min"] <= n <= config["joueurs_max"]:
        return _erreur(nom, f"L'enquête se joue de {config['joueurs_min']} à {config['joueurs_max']} joueurs ({n} inscrits).")
    sans = [j["nom"] for j in etat["joueurs"] if not j["portrait"]]
    if sans:
        return _erreur(nom, "Portrait à choisir : " + ", ".join(sans) + ".")
    pack = PACKS[etat["univers"]]
    k = config["vitesses"][etat["vitesse"]]["lieux_armes"]
    etat["lieux"] = rng.sample([l["id"] for l in pack["lieux"]], k)
    etat["armes"] = rng.sample([a["id"] for a in pack["armes"]], k)
    tueur = rng.choice(etat["joueurs"])["nom"]
    etat["tueur"] = tueur
    etat["solution"] = {"tueur": tueur, "arme": rng.choice(etat["armes"]), "lieu": rng.choice(etat["lieux"])}
    etat["phase"] = "ouverture"
    etat["debut_a"] = maintenant
    etat["ouverture_fin_a"] = maintenant + config["duree_ouverture"]
    etat["fin_a"] = maintenant + config["duree_ouverture"] + etat["duree"]
    etat["version"] += 1
    return []


def _annuler(etat, nom, message, maintenant, rng, config):
    if not _est_hote(etat, nom):
        return _erreur(nom, "Seul l'hôte peut arrêter l'enquête.")
    etat["phase"] = "annulee"
    etat["version"] += 1
    return [_tous({"type": "enq_annulee"})]


# =============================================================
# TEMPS
# =============================================================

def prochaine_echeance(etat):
    if etat["phase"] == "ouverture":
        return etat["ouverture_fin_a"]
    if etat["phase"] == "enquete":
        return etat["fin_a"]
    return None


def expirer(etat, maintenant, config=None):
    ev = []
    if etat["phase"] == "ouverture" and maintenant >= etat["ouverture_fin_a"]:
        etat["phase"] = "enquete"
        etat["version"] += 1
    if etat["phase"] == "enquete" and maintenant >= etat["fin_a"]:
        _terminer(etat, "tueur", "heure", None)
    return ev


def _terminer(etat, vainqueur, raison, detective):
    etat["phase"] = "fin"
    etat["vainqueur"] = vainqueur
    etat["raison_fin"] = raison
    etat["detective"] = detective
    etat["version"] += 1


# =============================================================
# VUES
# =============================================================

def vue_publique(etat, maintenant):
    """A tous. JAMAIS la solution ni le tueur avant la fin."""
    lancee = etat["phase"] in ("ouverture", "enquete", "fin")
    v = {
        "type": "enq_etat",
        "version": etat["version"],
        "phase": etat["phase"],
        "hote": etat["hote"],
        "univers": etat["univers"],
        "univers_disponibles": univers_disponibles(),
        "vitesse": etat["vitesse"],
        "vitesses": {k: {"titre": x["titre"], "duree": x["duree"], "jeux": x["jeux"]}
                     for k, x in CONFIG["vitesses"].items()},
        "jeux": etat["jeux"],
        "duree": etat["duree"],
        "unite": etat["unite"],
        "joueurs": [{"nom": j["nom"], "portrait": j["portrait"]} for j in etat["joueurs"]],
        "joueurs_min": CONFIG["joueurs_min"],
        "joueurs_max": CONFIG["joueurs_max"],
        "lieux": etat["lieux"] if lancee else [],
        "armes": etat["armes"] if lancee else [],
        "ouverture_fin_a": etat["ouverture_fin_a"],
        "fin_a": etat["fin_a"],
        "maintenant": maintenant,
        "fin": None,
    }
    if etat["phase"] == "fin":
        v["fin"] = {
            "solution": etat["solution"],
            "vainqueur": etat["vainqueur"],
            "detective": etat["detective"],
            "raison": etat["raison_fin"],
            "recit": recit_de_fin(etat),
        }
    return v


def vue_privee(etat, nom):
    """
    A un seul joueur. Memes cles pour tout le monde (enqueteur, tueur ou
    simple spectateur) : rien dans la forme du message ne trahit le role.
    """
    moi = _joueur(etat, nom)
    lancee = etat["phase"] in ("ouverture", "enquete", "fin")
    role = None
    solution = None
    if moi is not None and lancee:
        if cle_nom(moi["nom"]) == cle_nom(etat["tueur"]):
            role = "tueur"
            solution = dict(etat["solution"])
        else:
            role = "enqueteur"
    return {
        "type": "enq_moi",
        "nom": nom,
        "inscrit": moi is not None,
        "portrait": moi["portrait"] if moi else None,
        "hote": _est_hote(etat, nom),
        "role": role,
        "solution": solution,
    }


def _nom_element(pack, cle, ident):
    for e in pack[cle]:
        if e["id"] == ident:
            return e.get("le", e["nom"])
    return ident


def recit_de_fin(etat):
    pack = PACKS[etat["univers"]]
    t = pack["textes"]
    s = etat["solution"]
    if etat["vainqueur"] == "enqueteurs":
        verdict = t["verdict_enqueteurs"].format(detective=etat["detective"], tueur=s["tueur"])
    else:
        verdict = t["verdict_tueur"].format(tueur=s["tueur"])
    return t["fin"].format(tueur=s["tueur"], lieu=_nom_element(pack, "lieux", s["lieu"]),
                           arme=_nom_element(pack, "armes", s["arme"]), verdict=verdict)
