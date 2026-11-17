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
Tranche 2 : points d enquete (PE) gagnes aux jeux, stock d indices vrais
plafonne, distribution, archive officielle (lecture gratuite une fois, puis
chrono et gorgee), achat d indice contre des gorgees, fil du narrateur.
Tranche 3 : accusations (delai double a chaque echec, gorgees, seuil de
patience), fin de partie et recapitulatif.
Tranche 4 : bonus aleatoires (« Temoin anonyme » : un indice offert de
temps en temps a un joueur tire au sort, tueur compris) et contre-expertise
(un indice de l archive recoit un tampon permanent Authentique ou Falsifie,
payee en PE a la place d un indice ou en gorgees ; elle compte comme une
consultation).
Tranche 5 : le tueur achete des charges avec ses PE et sabote (fausse piste
en remplacement ou en detournement d un Temoin, interception, ecoute,
fausse rumeur publique). Chaque sabotage a une chance d etre detecte, qui
baisse avec le temps ; l annonce « interference » arrive avec un retard
aleatoire. De vraies rumeurs ambiantes noient la fausse dans le bruit.
Aucune penalite visible n est liee au role : tout passe par les charges,
les delais et la detection.
Tranche 6 : evenements d ambiance, programmes en secret au lancement, a des
moments aleatoires, jamais deux a la fois ni deux fois de suite le meme :
coupure (archive verrouillee, plis illisibles, detection divisee par 2),
decouverte (un indice vrai publie a tous), alerte (delai d accusation
divise par 2, gorgees d accusation ratee doublees), brouhaha (rumeurs deux
fois plus frequentes, une fausse rumeur gratuite pour le tueur),
interrogatoire (2 joueurs designes ; refuser coute des gorgees), maree
(PE doubles).
Tranche 7 : vitesses detaillees pour l hote, dosage des gorgees (doux,
normal, corse) et plafond de gorgees par heure et par joueur : au-dela, la
penalite devient un handicap de temps (archive et accusations bloquees)
au lieu de faire boire. Unite de penalite libre (mode sans alcool).
Tranche 9 : joueurs fictifs (pour tester seul, et pour le simulateur
tests/simulation.py). Un joueur fictif ne voit que ce qu un vrai joueur
verrait : ses plis, les decouvertes publiques, le retour de ses accusations.

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
    plan = pack.get("plan", {})
    zones = plan.get("zones", {})
    if set(zones) != {l["id"] for l in pack["lieux"]}:
        raise ValueError(f"pack {pack['id']} : le plan doit avoir une zone par lieu")
    connues = set(zones) | set(plan.get("couloirs", {}))
    for a, b in plan.get("portes", []):
        if a not in connues or b not in connues:
            raise ValueError(f"pack {pack['id']} : porte vers une piece inconnue ({a}, {b})")
    for cle in ("lieux", "armes", "portraits"):
        ids = [e["id"] for e in pack[cle]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"pack {pack['id']} : identifiants de {cle} en double")
    # Textes : tout ce que le moteur va chercher en pleine partie, et des
    # modeles qui se remplissent sans erreur.
    t = pack["textes"]
    essai = {"nom": "Lea", "lieu": "le Salon", "arme": "le Vase", "tueur": "Lea", "detective": "Max", "verdict": "."}
    try:
        for genre in ("indices", "rumeurs"):
            for cat in ("suspect", "lieu", "arme"):
                if len(t[genre][cat]) < 3:
                    raise ValueError(f"pack {pack['id']} : 3 variantes de {genre}/{cat} attendues")
                for modele in t[genre][cat]:
                    modele.format(**essai)
        for cle in ("indice", "accusation_ratee", "interference", "dossier_epuise", "fin"):
            t["annonces"][cle]
        for cle in ("coupure", "decouverte", "alerte", "brouhaha", "interrogatoire", "maree"):
            for champ in ("nom", "debut", "fin"):
                t["evenements"][cle][champ]
        t["fin"].format(**essai)
        t["verdict_enqueteurs"].format(**essai)
        t["verdict_tueur"].format(**essai)
        if not t["ouverture"]:
            raise ValueError(f"pack {pack['id']} : scène d'ouverture vide")
    except KeyError as e:
        raise ValueError(f"pack {pack['id']} : texte manquant ou variable inconnue ({e})")
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
        "plan": pack["plan"],
        "ouverture": pack["textes"]["ouverture"],
        "evenements": {k: {"nom": v["nom"], "debut": v["debut"]} for k, v in pack["textes"].get("evenements", {}).items()},
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
        "univers": config.get("univers_defaut") if config.get("univers_defaut") in PACKS else next(iter(PACKS)),
        "vitesse": vitesse,
        "jeux": list(v["jeux"]),
        "duree": v["duree"],
        "unite": config["gorgees"]["unite_defaut"],
        "dosage": "normal",         # doux | normal | corse (multiplie les gorgees)
        "plafond": config["gorgees"]["plafond_par_heure"],   # gorgees max par heure et par joueur (0 : sans)
        "gorgees_publiques": True,  # chaque penalite s affiche chez tout le monde (anti-triche)
        "penalites": [],            # public si gorgees_publiques : [{id, t, nom, nombre, raison, bu}]
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
        # --- tranche 2 ---
        "pool": [],                 # SECRET : eliminations vraies restantes
        "perso": {},                # cle du prenom -> etat prive du joueur
        "journal": [],              # fil public du narrateur
        "prochain_id": 1,
        "dernier_tic": None,
        "dossier_epuise": False,
        # --- tranche 3 ---
        "accusations": [],          # SECRET jusqu a la fin : [{t, nom, suspect, arme, lieu, justes}]
        "echecs": 0,                # public : accusations ratees, tous joueurs
        # --- tranche 4 ---
        "prochain_bonus_a": None,   # SECRET (heure exacte) : prochain Temoin anonyme
        "dernier_bonus": None,      # SECRET : cle du dernier joueur servi
        "bonus": [],                # SECRET jusqu a la fin : [{t, nom}]
        # --- tranche 5 ---
        "sabotages": [],            # SECRET jusqu a la fin : [{t, type, cible, categorie, element, mode, detecte}]
        "differes": [],             # SECRET : [{t, genre: "annonce"|"rumeur", ...}] a publier plus tard
        "rumeurs": [],              # public : [{t, texte, lieu}] (vraies et fausses, meme format)
        "prochaine_rumeur_a": None, # SECRET
        "detournement": None,       # SECRET : {cible, categorie, element}
        "faux_emis": [],            # SECRET : [[categorie, element]] (deux faux identiques interdits)
        "prochain_tic_bots": None,  # joueurs fictifs et PE automatiques (vitesse Demo)
        # --- tranche 6 ---
        "programme": [],            # SECRET : [{t, type}] evenements a venir
        "evenement": None,          # public : {type, debut, fin, interroges}
        "coupure_jusqua": None,
        "decouvertes": [],          # public : [{t, texte, categorie, element}]
        "evenements_passes": [],    # [{type, debut}]
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
        "enq_indice": _demander_indice,
        "enq_acheter": _acheter_indice,
        "enq_lire": _lire,
        "enq_consulter": _consulter,
        "enq_accuser": _accuser,
        "enq_expertise": _expertiser,
        "enq_charge": _acheter_charge,
        "enq_sabotage": _saboter,
        "enq_refuser": _refuser_interrogatoire,
        "enq_repondu": _repondu_interrogatoire,
        "enq_bu": _c_est_bu,
        "enq_bot_ajouter": _ajouter_bot,
        "enq_bot_retirer": _retirer_bot,
    }.get(t)
    if f is None:
        return []
    return f(etat, nom, _nettoyer(message), maintenant, rng, config)


def _nettoyer(message):
    """
    Les champs viennent d un client : un vieil onglet ou un petit malin peut
    envoyer une liste ou un objet la ou on attend un texte ou un nombre. Les
    tests du moteur (« x in ensemble », « x == solution ») plantaient alors
    (TypeError) au milieu d une action. Un champ de travers compte comme
    absent ; seul « jeux » est une liste, de textes.
    """
    propre = {}
    for k, v in message.items():
        if k == "jeux":
            propre[k] = [j for j in v if isinstance(j, str)] if isinstance(v, list) else None
        elif isinstance(v, (list, dict)):
            propre[k] = None
        else:
            propre[k] = v
    return propre


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
    if "gorgees_publiques" in message:
        etat["gorgees_publiques"] = bool(message["gorgees_publiques"])
    if "dosage" in message:
        if message["dosage"] not in config["gorgees"]["dosages"]:
            return _erreur(nom, "Dosage inconnu.")
        etat["dosage"] = message["dosage"]
    if "plafond" in message:
        if message["plafond"] not in config["gorgees"]["plafonds"]:
            return _erreur(nom, "Plafond inconnu.")
        etat["plafond"] = message["plafond"]
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
    construire_pool(etat, config, rng)
    etat["perso"] = {cle_nom(j["nom"]): perso_vide() for j in etat["joueurs"]}
    etat["phase"] = "ouverture"
    etat["debut_a"] = maintenant
    etat["ouverture_fin_a"] = maintenant + config["duree_ouverture"]
    etat["fin_a"] = maintenant + config["duree_ouverture"] + etat["duree"]
    etat["prochain_bonus_a"] = etat["ouverture_fin_a"] + _intervalle_bonus(etat, config, rng)
    etat["prochaine_rumeur_a"] = etat["ouverture_fin_a"] + _intervalle(etat, config, rng, "rumeurs_toutes")
    etat["programme"] = programmer_evenements(etat, config, rng)
    etat["prochain_tic_bots"] = etat["ouverture_fin_a"]
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
        # Reveil regulier pour compter le temps passe aux jeux longs (paliers).
        tic = (etat["dernier_tic"] or etat["ouverture_fin_a"]) + TIC_PALIERS
        candidats = [etat["fin_a"], tic, etat.get("prochain_bonus_a"), etat.get("prochaine_rumeur_a")]
        candidats += [d["t"] for d in etat.get("differes", [])]
        candidats += [x["t"] for x in etat.get("programme", [])[:1]]
        if any(j.get("bot") for j in etat["joueurs"]) or CONFIG["vitesses"][etat["vitesse"]].get("pe_auto_heure"):
            candidats.append(etat.get("prochain_tic_bots"))
        if etat.get("evenement"):
            candidats.append(etat["evenement"]["fin"])
        candidats += [e["visible_a"] for p in etat["perso"].values() for e in p["archive"] if e.get("retenu")]
        return min(x for x in candidats if x is not None)
    return None


def expirer(etat, maintenant, config=None, rng=random):
    config = config or CONFIG
    ev = []
    if etat["phase"] == "ouverture" and maintenant >= etat["ouverture_fin_a"]:
        etat["phase"] = "enquete"
        etat["version"] += 1
    if etat["phase"] == "enquete" and maintenant >= etat["fin_a"]:
        _terminer(etat, "tueur", "heure", None)
    if etat["phase"] == "enquete" and etat.get("prochain_bonus_a") is not None and maintenant >= etat["prochain_bonus_a"]:
        ev += temoin_anonyme(etat, maintenant, rng, config)
    if etat["phase"] == "enquete":
        ev += _evenements(etat, maintenant, rng, config)
        ev += _liberer_retenus(etat, maintenant)
        ev += _publier_differes(etat, maintenant)
        if etat.get("prochaine_rumeur_a") is not None and maintenant >= etat["prochaine_rumeur_a"]:
            ev += rumeur_ambiante(etat, maintenant, rng, config)
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
        "vitesses": {k: {"titre": x["titre"], "duree": x["duree"], "jeux": x["jeux"],
                         "pe_par_indice": x["pe_par_indice"], "timer_consultation": x["timer_consultation"],
                         "bonus_toutes": x["bonus_toutes"], "delai_accusation": x["delai_accusation"],
                         "echecs_par_joueur": x["echecs_par_joueur"], "mult_gorgees": x["mult_gorgees"],
                         "evenements": x["evenements"], "duree_evenement": x["duree_evenement"],
                         "lieux_armes": x["lieux_armes"], "pe_auto_heure": x.get("pe_auto_heure", 0)}
                     for k, x in CONFIG["vitesses"].items()},
        "dosage": etat.get("dosage", "normal"),
        "dosages": CONFIG["gorgees"]["dosages"],
        "plafond": etat.get("plafond"),
        "gorgees_publiques": etat.get("gorgees_publiques", True),
        "penalites": etat.get("penalites", []) if etat.get("gorgees_publiques", True) else [],
        "plafonds": CONFIG["gorgees"]["plafonds"],
        "jeux": etat["jeux"],
        "duree": etat["duree"],
        "unite": etat["unite"],
        "joueurs": [{"nom": j["nom"], "portrait": j["portrait"], "bot": bool(j.get("bot"))} for j in etat["joueurs"]],
        "joueurs_min": CONFIG["joueurs_min"],
        "joueurs_max": CONFIG["joueurs_max"],
        "lieux": etat["lieux"] if lancee else [],
        "armes": etat["armes"] if lancee else [],
        "ouverture_fin_a": etat["ouverture_fin_a"],
        "fin_a": etat["fin_a"],
        "maintenant": maintenant,
        "journal": etat.get("journal", [])[-20:],
        "dossier_epuise": etat.get("dossier_epuise", False),
        "timer_consultation": CONFIG["vitesses"][etat["vitesse"]]["timer_consultation"],
        "gorgees_consultation": _gorgees(etat, CONFIG, CONFIG["gorgees"]["consultation"]),
        "gorgees_achat": _gorgees(etat, CONFIG, CONFIG["gorgees"]["indice_achete"]),
        "gorgees_expertise": _gorgees(etat, CONFIG, CONFIG["gorgees"]["contre_expertise_achetee"]),
        "bonus_toutes": CONFIG["vitesses"][etat["vitesse"]]["bonus_toutes"],
        "echecs": etat.get("echecs", 0),
        "rumeurs": etat.get("rumeurs", [])[-20:],
        "evenement": _evenement_public(etat),
        "gorgees_refus": _gorgees(etat, CONFIG, CONFIG["gorgees"]["refus_interrogatoire"]),
        "decouvertes": etat.get("decouvertes", []),
        "seuil_patience": seuil_patience(etat) if lancee else None,
        "delai_accusation": CONFIG["vitesses"][etat["vitesse"]]["delai_accusation"],
        "fin": None,
    }
    if etat["phase"] == "fin":
        v["fin"] = {
            "solution": etat["solution"],
            "vainqueur": etat["vainqueur"],
            "detective": etat["detective"],
            "raison": etat["raison_fin"],
            "recit": recit_de_fin(etat),
            "recap": recapitulatif(etat),
        }
    return v


def vue_privee(etat, nom, maintenant=0.0):
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
            solution = {"tueur": None, "arme": None, "lieu": None}   # equivalent factice, meme forme
    v = {
        "type": "enq_moi",
        "nom": nom,
        "inscrit": moi is not None,
        "portrait": moi["portrait"] if moi else None,
        "hote": _est_hote(etat, nom),
        "role": role,
        "solution": solution,
    }
    v.update(vue_privee_t2(etat, nom if lancee else "\0", maintenant))
    # Console : meme forme pour tout le monde pendant l enquete ; seuls les
    # champs du tueur sont remplis, les autres recoivent un equivalent factice.
    if role is not None and etat["phase"] == "enquete":
        v["console"] = console(etat, moi["nom"], maintenant) if role == "tueur" else console_factice(etat, maintenant)
    else:
        v["console"] = None
    return v


def contracter(texte):
    """Contractions francaises apres insertion d un nom : « de le Fumoir » -> « du Fumoir »."""
    import re
    for avant, apres in ((r"\bde le ", "du "), (r"\bà le ", "au "), (r"\bde les ", "des "), (r"\bà les ", "aux "),
                         (r"\bDe le ", "Du "), (r"\bÀ le ", "Au ")):
        texte = re.sub(avant, apres, texte)
    return texte


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
    return contracter(t["fin"].format(tueur=s["tueur"], lieu=_nom_element(pack, "lieux", s["lieu"]),
                                      arme=_nom_element(pack, "armes", s["arme"]), verdict=verdict))


# =============================================================
# TRANCHE 2 : POINTS D ENQUETE, INDICES, ARCHIVE
# =============================================================

TIC_PALIERS = 60          # secondes entre deux comptages du temps de jeu
TIC_MAX = 120             # un reveil en retard ne compte pas plus que ca
JOURNAL_MAX = 40


def perso_vide():
    return {
        "pe": 0,
        "archive": [],              # indices recus (texte compris : prive)
        "derniere_consultation": None,
        "obtenus": [],              # horodatages des indices obtenus (plafond horaire)
        "gorgees": [],              # [horodatage, nombre, raison]
        "manches_kinito": 0,
        "expertises": 0,
        # Sabotage : la meme structure pour tout le monde, seul le tueur s en sert.
        "charges": 0,
        "prochain_sabotage_a": None,
        "rumeur_utilisee": False,
        "faux_en_attente": None,    # SECRET : fausse piste qui remplacera le prochain indice
        "interception": False,      # SECRET : le prochain indice sera retenu
        "temps_palier": {},         # jeu -> secondes accumulees
        "derniere_source": None,
        "accusations": [],          # les siennes : [{t, suspect, arme, lieu, justes}]
        "echecs": 0,
        "prochaine_accusation_a": None,
    }


def _perso(etat, nom):
    return etat["perso"].get(cle_nom(nom))


def _vitesse(etat, config):
    return config["vitesses"][etat["vitesse"]]


def construire_pool(etat, config, rng):
    """
    Les eliminations VRAIES que le Commissaire pourra distribuer, sans
    doublon. Plafonnees pour qu il reste toujours au moins 2 candidats par
    categorie, meme si tout le monde met tout en commun :
    - suspects : K-3, car chaque enqueteur s elimine deja lui-meme ;
    - lieux et armes : K-2.
    Un indice vrai n elimine jamais un element de la solution.
    """
    sol = etat["solution"]
    pool = []
    suspects = [j["nom"] for j in etat["joueurs"] if cle_nom(j["nom"]) != cle_nom(sol["tueur"])]
    rng.shuffle(suspects)
    plafond = max(0, len(etat["joueurs"]) - config["plafond_suspects_marge"])
    pool += [{"categorie": "suspect", "element": x} for x in suspects[:plafond]]
    for cat, ids, bon in (("lieu", etat["lieux"], sol["lieu"]), ("arme", etat["armes"], sol["arme"])):
        autres = [i for i in ids if i != bon]
        rng.shuffle(autres)
        plafond = max(0, len(ids) - config["plafond_autres_marge"])
        pool += [{"categorie": cat, "element": x} for x in autres[:plafond]]
    rng.shuffle(pool)
    etat["pool"] = pool


def _annoncer(etat, maintenant, texte):
    etat["journal"].append({"t": maintenant, "texte": texte})
    del etat["journal"][:-JOURNAL_MAX]


def texte_indice(etat, indice, rng):
    """Meme format pour tous les indices (vrais ou, plus tard, faux)."""
    pack = PACKS[etat["univers"]]
    modele = rng.choice(pack["textes"]["indices"][indice["categorie"]])
    e = indice["element"]
    if indice["categorie"] == "suspect":
        return modele.format(nom=e)
    if indice["categorie"] == "lieu":
        return contracter(modele.format(lieu=_nom_element(pack, "lieux", e)))
    return contracter(modele.format(arme=_nom_element(pack, "armes", e)))


def _tirer(etat, nom):
    """Le prochain indice du stock, sans jamais donner a quelqu un sa propre innocence."""
    k = cle_nom(nom)
    for i, e in enumerate(etat["pool"]):
        if e["categorie"] == "suspect" and cle_nom(e["element"]) == k:
            continue
        return etat["pool"].pop(i)
    return None


def donner_indice(etat, nom, source, maintenant, rng, config, faux=None):
    """
    Ajoute un indice a l archive du joueur. Renvoie les evenements, ou None
    si le stock est vide pour lui. Le texte n est montre qu a l ouverture.

    Sabotages : une fausse piste en attente (remplacement) prend la place de
    l indice vrai, qui reste dans le stock ; une interception retient le pli
    (invisible pour le joueur) jusqu a visible_a.
    """
    p = _perso(etat, nom)
    if faux is None and p is not None and p.get("faux_en_attente"):
        faux, p["faux_en_attente"] = p["faux_en_attente"], None
    indice = faux or _tirer(etat, nom)
    if p is None or indice is None:
        if not etat["dossier_epuise"] and not etat["pool"]:
            etat["dossier_epuise"] = True
            _annoncer(etat, maintenant, PACKS[etat["univers"]]["textes"]["annonces"]["dossier_epuise"])
        return None
    entree = {
        "id": etat["prochain_id"],
        "texte": texte_indice(etat, indice, rng),
        "categorie": indice["categorie"],
        "element": indice["element"],
        "vrai": faux is None,       # SECRET : un faux a exactement le meme format
        "source": source,
        "recu_a": maintenant,
        "lu": False,
        "tampon": None,             # "authentique" | "falsifie" (contre-expertise)
    }
    etat["prochain_id"] += 1
    p["archive"].append(entree)
    if p.get("interception"):
        p["interception"] = False
        entree["retenu"] = True
        entree["visible_a"] = maintenant + config["sabotage"]["interception"]
        entree["recu_a"] = entree["visible_a"]
        return []
    _annoncer(etat, maintenant, PACKS[etat["univers"]]["textes"]["annonces"]["indice"])
    return [_a(nom, {"type": "enq_nouvel_indice", "id": entree["id"], "source": source})]


def _indices_derniere_heure(p, maintenant):
    return sum(1 for t in p["obtenus"] if maintenant - t < 3600)


def _gorgees(etat, config, base):
    """Nombre de gorgees selon la vitesse et le dosage choisi par l hote, arrondi, au moins 1."""
    dosage = config["gorgees"]["dosages"].get(etat.get("dosage", "normal"), 1)
    return max(1, int(base * _vitesse(etat, config)["mult_gorgees"] * dosage + 0.5))


def gorgees_heure(p, maintenant):
    return sum(n for t, n, _ in p["gorgees"] if maintenant - t < 3600)


def _imposer(etat, nom, nombre, raison, maintenant, config=None):
    """
    Fait boire un joueur, dans la limite du plafond horaire. Au-dela, le
    reste devient un handicap de temps : archive et accusations bloquees
    (handicap_par_gorgee secondes par gorgee non bue). Meme regle pour tous,
    tueur compris : rien ne le distingue.
    """
    config = config or CONFIG
    p = _perso(etat, nom)
    plafond = etat.get("plafond") or 0
    bu, exces = nombre, 0
    if plafond:
        bu = max(0, min(nombre, plafond - gorgees_heure(p, maintenant)))
        exces = nombre - bu
    if bu:
        p["gorgees"].append([maintenant, bu, raison])
        if etat.get("gorgees_publiques", True):
            # Affiche a tous jusqu a ce que le joueur confirme : pas de gorgee oubliee.
            etat.setdefault("penalites", []).append({"id": etat["prochain_id"], "t": maintenant, "nom": nom,
                                                     "nombre": bu, "raison": raison, "bu": False})
            etat["prochain_id"] += 1
            del etat["penalites"][:-PENALITES_MAX]
    secondes = 0
    if exces:
        secondes = exces * config["gorgees"]["handicap_par_gorgee"]
        p["handicap_jusqua"] = max(p.get("handicap_jusqua") or maintenant, maintenant) + secondes
        p.setdefault("handicaps", []).append([maintenant, exces, raison])
    return _a(nom, {"type": "enq_gorgee", "nombre": bu, "unite": etat["unite"], "raison": raison,
                    "handicap": secondes})


PENALITES_MAX = 40


def _c_est_bu(etat, nom, message, maintenant, rng, config):
    """Le joueur confirme devant tous qu il a bu sa penalite."""
    for x in etat.get("penalites", []):
        if x["id"] == message.get("id"):
            if cle_nom(x["nom"]) != cle_nom(nom):
                return _erreur(nom, "Ce n'est pas ta pénalité.")
            if not x["bu"]:
                x["bu"] = True
                etat["version"] += 1
            return []
    return _erreur(nom, "Pénalité introuvable.")


def _handicap(etat, nom, maintenant):
    """Message d erreur si le joueur purge un handicap de temps, sinon None."""
    h = (_perso(etat, nom) or {}).get("handicap_jusqua")
    if h and maintenant < h:
        reste = int(h - maintenant + 0.999)
        return f"Plafond de {etat['unite']}s atteint : pénalité de temps, encore {reste // 60} min {reste % 60:02d} s."
    return None


def _verifier_demande(etat, nom, maintenant, config):
    if etat["phase"] != "enquete":
        return None, "L'enquête n'est pas en cours."
    p = _perso(etat, nom)
    if p is None:
        return None, "Tu ne participes pas à cette enquête."
    if _indices_derniere_heure(p, maintenant) >= config["indices_par_heure_max"]:
        return None, f"{config['indices_par_heure_max']} indices par heure au plus : patience."
    if not etat["pool"] or all(e["categorie"] == "suspect" and cle_nom(e["element"]) == cle_nom(nom) for e in etat["pool"]):
        return None, "Le dossier est épuisé : plus aucun indice à distribuer."
    return p, None


def _demander_indice(etat, nom, message, maintenant, rng, config):
    p, refus = _verifier_demande(etat, nom, maintenant, config)
    if refus:
        return _erreur(nom, refus)
    cout = _vitesse(etat, config)["pe_par_indice"]
    if p["pe"] < cout:
        return _erreur(nom, f"Il faut {cout} PE pour un indice (tu en as {p['pe']}).")
    p["pe"] -= cout
    p["obtenus"].append(maintenant)
    ev = donner_indice(etat, nom, p["derniere_source"] or "Victoire", maintenant, rng, config)
    etat["version"] += 1
    return ev or []


def _acheter_indice(etat, nom, message, maintenant, rng, config):
    p, refus = _verifier_demande(etat, nom, maintenant, config)
    if refus:
        return _erreur(nom, refus)
    p["obtenus"].append(maintenant)
    ev = donner_indice(etat, nom, "Achat", maintenant, rng, config) or []
    ev.append(_imposer(etat, nom, _gorgees(etat, config, config["gorgees"]["indice_achete"]), "Indice acheté", maintenant))
    etat["version"] += 1
    return ev


def _entree(etat, nom, message):
    p = _perso(etat, nom)
    if p is None:
        return None
    for e in p["archive"]:
        if e["id"] == message.get("id") and not e.get("retenu"):
            return e
    return None


def _texte(entree, gratuit, expertise=False):
    return {"type": "enq_texte", "id": entree["id"], "texte": entree["texte"], "source": entree["source"],
            "recu_a": entree["recu_a"], "tampon": entree["tampon"], "gratuit": gratuit, "expertise": expertise,
            # Pour l illustration de la lettre : rien de plus que ce que dit deja le texte.
            "categorie": entree["categorie"], "element": entree["element"]}


def _lire(etat, nom, message, maintenant, rng, config):
    """Premiere lecture d un indice recu : libre, une seule fois."""
    e = _entree(etat, nom, message)
    if e is None or etat["phase"] not in ("enquete", "fin"):
        return []
    if e["lu"]:
        return _erreur(nom, "Déjà lu : il est rangé dans ton archive.")
    if _actif(etat, "coupure", maintenant):
        return _erreur(nom, "Le courant est coupé : impossible de lire ce pli pour l'instant.")
    e["lu"] = True
    etat["version"] += 1
    return [_a(nom, _texte(e, True))]


def _consulter(etat, nom, message, maintenant, rng, config):
    """Revoir un indice de l archive : apres le chrono, et contre une gorgee."""
    e = _entree(etat, nom, message)
    if e is None or not e["lu"]:
        return []
    if etat["phase"] != "enquete":
        return _erreur(nom, "L'archive est close.")
    if _actif(etat, "coupure", maintenant):
        return _erreur(nom, "Le courant est coupé : l'archive est verrouillée.")
    p = _perso(etat, nom)
    timer = _vitesse(etat, config)["timer_consultation"]
    if p["derniere_consultation"] is not None and maintenant < p["derniere_consultation"] + timer:
        reste = int(p["derniere_consultation"] + timer - maintenant + 0.999)
        return _erreur(nom, f"Archive verrouillée encore {reste} s.")
    refus = _handicap(etat, nom, maintenant)
    if refus:
        return _erreur(nom, refus)
    p["derniere_consultation"] = maintenant
    etat["version"] += 1
    return [_a(nom, _texte(e, False)),
            _imposer(etat, nom, _gorgees(etat, config, config["gorgees"]["consultation"]), "Consultation de l'archive", maintenant)]


# --- Points d enquete gagnes aux jeux ---

def crediter_pe(etat, nom, n, jeu, raison, config=None):
    """Point d integration unique : un joueur gagne n PE grace a un jeu."""
    p = _perso(etat, nom)
    if p is None or n <= 0:
        return []
    if (etat.get("evenement") or {}).get("type") == "maree":
        n *= 2                       # les victoires rapportent le double
    p["pe"] += n
    titre = {"kinito": "Kinito", "buckshot": "Buckshot", "menteur": "Cal 12", "beerbattle": "BeerBattle",
             "picolopoly": "Picopoly", "casino": "Casino", "undercover": "Undercover"}.get(jeu, jeu)
    p["derniere_source"] = f"Victoire ({titre})"
    etat["version"] += 1
    return [_a(nom, {"type": "enq_pe", "gain": n, "total": p["pe"], "raison": raison})]


def evenement_jeu(etat, ev, maintenant, config=None):
    """
    Un jeu de la soiree signale un resultat (voir couches.py). Seuls comptent
    les jeux choisis par l hote, pendant l enquete, pour les inscrits.
    """
    config = config or CONFIG
    jeu = ev.get("jeu")
    if etat["phase"] != "enquete" or jeu not in etat["jeux"]:
        return []
    regle = config["pe"].get(jeu)
    if not regle:
        return []
    out = []
    if regle["type"] == "victoire" and ev["type"] == "victoire":
        for nom in ev["gagnants"]:
            out += crediter_pe(etat, nom, regle["pe"], jeu, "victoire", config)
    elif jeu == "kinito" and ev["type"] == "manche":
        # 1 PE toutes les N manches non perdues (compteur par joueur).
        perdants = {cle_nom(n) for n in ev["perdants"]}
        for nom in ev["joueurs"]:
            p = _perso(etat, nom)
            if p is None or cle_nom(nom) in perdants:
                continue
            p["manches_kinito"] += 1
            if p["manches_kinito"] >= regle.get("toutes_les", 1):
                p["manches_kinito"] = 0
                out += crediter_pe(etat, nom, regle["pe"], jeu, "manches gagnées", config)
        etat["version"] += 1
    elif jeu == "buckshot" and ev["type"] == "manche":
        g = {n: v for n, v in ev["details"].get("gorgees", {}).items() if _perso(etat, n) is not None}
        if g:
            mini = min(g.values())
            for nom, v in g.items():
                if v == mini:
                    out += crediter_pe(etat, nom, regle["pe"], jeu, "manche la plus sobre", config)
    elif regle["type"] == "palier" and ev["type"] == "victoire":
        for nom in ev["gagnants"]:
            out += crediter_pe(etat, nom, regle.get("bonus_victoire", 0), jeu, "victoire finale", config)
    return out


def tic_paliers(etat, tables, maintenant, config=None):
    """
    Jeux longs (Picopoly, Casino) : 1 PE par palier de temps passe a la
    table, compte par joueur. Appele regulierement (TIC_PALIERS).
    """
    config = config or CONFIG
    if etat["phase"] != "enquete":
        return []
    dt = 0 if etat["dernier_tic"] is None else min(TIC_MAX, max(0, maintenant - etat["dernier_tic"]))
    etat["dernier_tic"] = maintenant
    out = []
    for t in tables:
        jeu = t.get("jeu")
        regle = config["pe"].get(jeu)
        if not regle or regle["type"] != "palier" or jeu not in etat["jeux"]:
            continue
        for nom in t["joueurs"]:
            p = _perso(etat, nom)
            if p is None:
                continue
            p["temps_palier"][jeu] = p["temps_palier"].get(jeu, 0) + dt
            while p["temps_palier"][jeu] >= regle["palier"]:
                p["temps_palier"][jeu] -= regle["palier"]
                out += crediter_pe(etat, nom, regle["pe"], jeu, "palier de jeu", config)
    return out


def _consultation_possible(p, timer):
    """Fin du chrono de l archive, ou du handicap de temps s il dure plus longtemps."""
    bornes = [x for x in ((p["derniere_consultation"] + timer) if p["derniere_consultation"] else None,
                          p.get("handicap_jusqua")) if x]
    return max(bornes) if bornes else None


def vue_privee_t2(etat, nom, maintenant, config=None):
    """Champs prives de la tranche 2 : memes cles pour tout le monde."""
    config = config or CONFIG
    p = _perso(etat, nom)
    v = _vitesse(etat, config)
    timer = v["timer_consultation"]
    if p is None:
        return {"pe": 0, "cout_indice": v["pe_par_indice"], "indices_heure": 0,
                "indices_heure_max": config["indices_par_heure_max"], "archive": [],
                "consultation_possible_a": None, "accusations": [], "prochaine_accusation_a": None,
                "gorgees_heure": 0, "handicap_jusqua": None}
    return {
        "pe": p["pe"],
        "cout_indice": v["pe_par_indice"],
        "indices_heure": _indices_derniere_heure(p, maintenant),
        "indices_heure_max": config["indices_par_heure_max"],
        # Sans le texte : il ne s affiche qu a la lecture (gratuite une fois,
        # puis consultation payante).
        "archive": [{"id": e["id"], "source": e["source"], "recu_a": e["recu_a"], "lu": e["lu"],
                     "tampon": e["tampon"]} for e in p["archive"] if not e.get("retenu")],
        "consultation_possible_a": _consultation_possible(p, timer),
        "gorgees_heure": gorgees_heure(p, maintenant),
        "handicap_jusqua": p.get("handicap_jusqua"),
        "accusations": p.get("accusations", []),
        "prochaine_accusation_a": prochaine_accusation(etat, p, maintenant),
    }


# =============================================================
# TRANCHE 3 : ACCUSATIONS ET FIN
# =============================================================

def seuil_patience(etat, config=None):
    """Accusations ratees (tous joueurs confondus) au-dela desquelles le tueur gagne."""
    config = config or CONFIG
    return _vitesse(etat, config)["echecs_par_joueur"] * len(etat["joueurs"])


def _accuser(etat, nom, message, maintenant, rng, config):
    """
    Un joueur accuse (personnage, arme, lieu). Il apprend combien d elements
    sont justes, sans savoir lesquels ; 3 sur 3 : il devient le Detective.
    A tous : seulement « une accusation a echoue », sans auteur ni contenu.
    Le tueur peut accuser aussi (pour bluffer) : pour tout le monde, son
    accusation est une accusation ratee comme les autres.
    """
    if etat["phase"] != "enquete":
        return _erreur(nom, "L'enquête n'est pas en cours.")
    p = _perso(etat, nom)
    if p is None:
        return _erreur(nom, "Tu ne participes pas à cette enquête.")
    suspect, arme, lieu = message.get("suspect"), message.get("arme"), message.get("lieu")
    if _joueur(etat, suspect or "") is None or arme not in etat["armes"] or lieu not in etat["lieux"]:
        return _erreur(nom, "Accusation incomplète : un personnage, une arme et un lieu.")
    prochaine = prochaine_accusation(etat, p, maintenant)
    if prochaine is not None and maintenant < prochaine:
        reste = int(prochaine - maintenant + 0.999)
        return _erreur(nom, f"Prochaine accusation possible dans {reste // 60} min {reste % 60:02d} s.")
    sol = etat["solution"]
    suspect = _joueur(etat, suspect)["nom"]
    justes = (int(cle_nom(suspect) == cle_nom(sol["tueur"])) + int(arme == sol["arme"]) + int(lieu == sol["lieu"]))
    tueur = cle_nom(nom) == cle_nom(sol["tueur"])
    etat["accusations"].append({"t": maintenant, "nom": nom, "suspect": suspect, "arme": arme, "lieu": lieu,
                                "justes": justes, "par_le_tueur": tueur})
    pack = PACKS[etat["univers"]]
    ev = []
    if justes == 3 and not tueur:
        p["accusations"].append({"t": maintenant, "suspect": suspect, "arme": arme, "lieu": lieu, "justes": 3})
        _terminer(etat, "enqueteurs", "accusation", nom)
        _annoncer(etat, maintenant, pack["textes"]["annonces"]["fin"])
        return ev
    # Accusation ratee (ou bluff du tueur : il ne peut pas se denoncer).
    vue_justes = min(justes, 2)
    p["accusations"].append({"t": maintenant, "suspect": suspect, "arme": arme, "lieu": lieu, "justes": vue_justes})
    gorgees_echecs = config["gorgees"]["accusation_ratee"]
    base = gorgees_echecs[min(p["echecs"], len(gorgees_echecs) - 1)]
    if _actif(etat, "alerte", maintenant):
        base *= 2                    # l inspecteur presse : les erreurs coutent double
    p["echecs"] += 1
    etat["echecs"] += 1
    delai = _vitesse(etat, config)["delai_accusation"] * (2 ** p["echecs"])
    p["prochaine_accusation_a"] = maintenant + delai
    _annoncer(etat, maintenant, pack["textes"]["annonces"]["accusation_ratee"])
    ev.append(_a(nom, {"type": "enq_verdict", "justes": vue_justes, "suspect": suspect, "arme": arme, "lieu": lieu,
                       "prochaine_a": p["prochaine_accusation_a"]}))
    ev.append(_imposer(etat, nom, _gorgees(etat, config, base), "Accusation ratée", maintenant))
    etat["version"] += 1
    if etat["echecs"] >= seuil_patience(etat, config):
        _terminer(etat, "tueur", "patience", None)
        _annoncer(etat, maintenant, pack["textes"]["annonces"]["fin"])
    return ev


# =============================================================
# TRANCHE 4 : TEMOIN ANONYME ET CONTRE-EXPERTISE
# =============================================================

def _intervalle(etat, config, rng, cle):
    """Toutes les X minutes selon la vitesse, a +/- bonus_alea pres (pas d horloge previsible)."""
    base = _vitesse(etat, config)[cle]
    alea = config.get("bonus_alea", 0.25)
    return base * (1 + rng.uniform(-alea, alea))


def _intervalle_bonus(etat, config, rng):
    return _intervalle(etat, config, rng, "bonus_toutes")


def _peut_recevoir(etat, nom):
    k = cle_nom(nom)
    return any(not (e["categorie"] == "suspect" and cle_nom(e["element"]) == k) for e in etat["pool"])


def temoin_anonyme(etat, maintenant, rng=random, config=None):
    """
    Un indice offert a un joueur tire au sort, tueur compris (pour lui,
    c est un indice de couverture, toujours vrai). Source « Temoin anonyme »
    : un indice qui arrive sans victoire est donc normal. Hors plafond
    horaire. Plus de stock pour personne : les bonus s arretent.
    """
    config = config or CONFIG
    d = etat.get("detournement")
    if d is not None:
        # Le bonus prevu pour un autre est redirige vers la cible, et il est
        # faux : aucun indice de plus n est cree.
        etat["detournement"] = None
        ev = donner_indice(etat, d["cible"], "Témoin anonyme", maintenant, rng, config,
                           faux={"categorie": d["categorie"], "element": d["element"]}) or []
        etat["dernier_bonus"] = cle_nom(d["cible"])
        etat.setdefault("bonus", []).append({"t": maintenant, "nom": d["cible"]})
        etat["prochain_bonus_a"] = maintenant + _intervalle_bonus(etat, config, rng)
        etat["version"] += 1
        return ev
    candidats = [j["nom"] for j in etat["joueurs"] if _peut_recevoir(etat, j["nom"])]
    if not candidats:
        etat["prochain_bonus_a"] = None
        donner_indice(etat, etat["joueurs"][0]["nom"], "Témoin anonyme", maintenant, rng, config)   # annonce « dossier epuise »
        etat["version"] += 1
        return []
    # Pas deux fois de suite le meme, quand c est possible.
    autres = [n for n in candidats if cle_nom(n) != etat.get("dernier_bonus")]
    nom = rng.choice(autres or candidats)
    ev = donner_indice(etat, nom, "Témoin anonyme", maintenant, rng, config) or []
    etat["dernier_bonus"] = cle_nom(nom)
    etat.setdefault("bonus", []).append({"t": maintenant, "nom": nom})
    etat["prochain_bonus_a"] = maintenant + _intervalle_bonus(etat, config, rng)
    etat["version"] += 1
    return ev


def _expertiser(etat, nom, message, maintenant, rng, config):
    """
    Contre-expertise d un indice de son archive : tampon permanent
    « authentique » ou « falsifie ». Payee en PE (a la place d un indice) ou
    en gorgees. Compte comme une consultation : meme chrono, pas de gorgee
    de consultation en plus. Aucune annonce publique.
    """
    if etat["phase"] != "enquete":
        return _erreur(nom, "L'enquête n'est pas en cours.")
    e = _entree(etat, nom, message)
    if e is None or not e["lu"]:
        return _erreur(nom, "Cet indice n'est pas dans ton archive.")
    if e["tampon"]:
        return _erreur(nom, "Cet indice porte déjà un tampon d'expertise.")
    if _actif(etat, "coupure", maintenant):
        return _erreur(nom, "Le courant est coupé : l'archive est verrouillée.")
    p = _perso(etat, nom)
    timer = _vitesse(etat, config)["timer_consultation"]
    if p["derniere_consultation"] is not None and maintenant < p["derniere_consultation"] + timer:
        reste = int(p["derniere_consultation"] + timer - maintenant + 0.999)
        return _erreur(nom, f"Archive verrouillée encore {reste} s.")
    refus = _handicap(etat, nom, maintenant)
    if refus:
        return _erreur(nom, refus)
    paiement = message.get("paiement")
    cout = _vitesse(etat, config)["pe_par_indice"]
    if paiement == "pe":
        if p["pe"] < cout:
            return _erreur(nom, f"Il faut {cout} PE pour une contre-expertise (tu en as {p['pe']}).")
        p["pe"] -= cout
    elif paiement != "gorgee":
        return _erreur(nom, "Paiement inconnu.")
    e["tampon"] = "authentique" if e["vrai"] else "falsifie"
    p["derniere_consultation"] = maintenant
    p["expertises"] = p.get("expertises", 0) + 1
    etat["version"] += 1
    ev = [_a(nom, _texte(e, False, expertise=True))]
    if paiement == "gorgee":
        ev.append(_imposer(etat, nom, _gorgees(etat, config, config["gorgees"]["contre_expertise_achetee"]),
                           "Contre-expertise", maintenant))
    return ev


# =============================================================
# TRANCHE 5 : SABOTAGES, DETECTION, RUMEURS
# =============================================================

SABOTAGES = ("fausse_piste", "interception", "ecoute", "rumeur")


def _est_tueur(etat, nom):
    return etat.get("tueur") is not None and cle_nom(nom) == cle_nom(etat["tueur"])


def detection_actuelle(etat, maintenant, config=None):
    """Chance qu un sabotage soit remarque : baisse lineairement du debut a la fin de l enquete."""
    config = config or CONFIG
    d0, d1 = _vitesse(etat, config)["detection"]
    debut, fin = etat["ouverture_fin_a"], etat["fin_a"]
    f = min(1.0, max(0.0, (maintenant - debut) / max(1.0, fin - debut)))
    d = d0 + (d1 - d0) * f
    if etat.get("coupure_jusqua") and maintenant < etat["coupure_jusqua"]:
        d /= 2                       # tranche 6 : pendant une coupure de courant
    return d


def _candidats(etat, categorie, pour=None):
    """Les elements d une categorie ; pour un suspect, sans le joueur lui-meme (il se sait innocent)."""
    if categorie == "lieu":
        return list(etat["lieux"])
    if categorie == "arme":
        return list(etat["armes"])
    return [j["nom"] for j in etat["joueurs"] if pour is None or cle_nom(j["nom"]) != cle_nom(pour)]


def _element_solution(etat, categorie):
    return etat["solution"]["tueur" if categorie == "suspect" else categorie]


def _fausse_piste(etat, cible, categorie, config):
    """
    (element, risque) si une fausse piste est possible pour cette cible dans
    cette categorie, sinon (None, raison). Le faux elimine l element de la
    solution. Interdit s il a deja ete emis, ou s il ne laisserait plus aucun
    candidat a la cible (contradiction directe avec son archive).
    Risque selon les eliminations deja distribuees dans la categorie.
    """
    element = _element_solution(etat, categorie)
    if [categorie, element] in etat["faux_emis"]:
        return None, "Ce faux a déjà servi : deux faux identiques se repéreraient."
    p = _perso(etat, cible)
    elim = {e["element"] for e in p["archive"] if e["categorie"] == categorie}
    reste = [x for x in _candidats(etat, categorie, cible) if x not in elim and x != element]
    if not reste:
        return None, "Ce faux contredirait directement son archive."
    tous = _candidats(etat, categorie)
    distribues = {e["element"] for q in etat["perso"].values() for e in q["archive"] if e["categorie"] == categorie}
    marge = config["plafond_suspects_marge"] if categorie == "suspect" else config["plafond_autres_marge"]
    plafond = max(1, len(tous) - marge)
    ratio = len(distribues) / plafond
    if len(reste) == 1 or ratio >= 0.67:
        risque = "eleve"
    elif ratio >= 0.34:
        risque = "moyen"
    else:
        risque = "faible"
    return element, risque


def console(etat, nom, maintenant, config=None):
    """Ce que voit le tueur dans sa console (jamais envoye a un autre joueur)."""
    config = config or CONFIG
    p = _perso(etat, nom)
    cibles = [j["nom"] for j in etat["joueurs"] if cle_nom(j["nom"]) != cle_nom(nom)]
    risques = {}
    for c in cibles:
        risques[c] = {}
        for cat in ("suspect", "lieu", "arme"):
            el, r = _fausse_piste(etat, c, cat, config)
            risques[c][cat] = r if el is not None else None
    pack = PACKS[etat["univers"]]
    return {
        "charges": p["charges"],
        "charges_max": config["sabotage"]["charges_max"],
        "cout_charge": _vitesse(etat, config)["pe_par_indice"],
        "prochain_sabotage_a": p["prochain_sabotage_a"],
        "detection": round(detection_actuelle(etat, maintenant, config), 3),
        "detection_fin": _vitesse(etat, config)["detection"][1],
        "rumeur_gratuite": _rumeur_gratuite(etat, maintenant),
        "rumeur_utilisee": p["rumeur_utilisee"],
        "detournement_possible": etat.get("detournement") is None and etat.get("prochain_bonus_a") is not None,
        "interception_minutes": round(config["sabotage"]["interception"] / 60),
        "cibles": cibles,
        "risques": risques,
        # Rumeur : uniquement des innocents
        "innocents": {
            "suspect": [c for c in cibles],
            "lieu": [x for x in etat["lieux"] if x != etat["solution"]["lieu"]],
            "arme": [x for x in etat["armes"] if x != etat["solution"]["arme"]],
        },
        "faux_en_attente": [c for c in cibles if _perso(etat, c).get("faux_en_attente")],
        "interceptions": [c for c in cibles if _perso(etat, c).get("interception")],
        "historique": [{"t": x["t"], "type": x["type"], "cible": x["cible"],
                        "element": _nom_element(pack, "lieux", x["element"]) if x.get("categorie") == "lieu"
                        else _nom_element(pack, "armes", x["element"]) if x.get("categorie") == "arme" else x.get("element")}
                       for x in etat["sabotages"]],
    }


def console_factice(etat, maintenant, config=None):
    """Meme cles que console(), valeurs neutres : rien dans la forme ne distingue le tueur."""
    config = config or CONFIG
    return {
        "charges": 0, "charges_max": config["sabotage"]["charges_max"], "cout_charge": _vitesse(etat, config)["pe_par_indice"],
        "prochain_sabotage_a": None, "detection": 0, "detection_fin": 0, "rumeur_gratuite": False, "rumeur_utilisee": False,
        "detournement_possible": False, "interception_minutes": round(config["sabotage"]["interception"] / 60),
        "cibles": [], "risques": {}, "innocents": {"suspect": [], "lieu": [], "arme": []},
        "faux_en_attente": [], "interceptions": [], "historique": [],
    }


def _acheter_charge(etat, nom, message, maintenant, rng, config):
    if etat["phase"] != "enquete" or not _est_tueur(etat, nom):
        return _erreur(nom, "Action impossible.")
    p = _perso(etat, nom)
    cout = _vitesse(etat, config)["pe_par_indice"]
    if p["charges"] >= config["sabotage"]["charges_max"]:
        return _erreur(nom, f"{config['sabotage']['charges_max']} charges au plus en réserve.")
    if p["pe"] < cout:
        return _erreur(nom, f"Il faut {cout} PE pour une charge (tu en as {p['pe']}).")
    p["pe"] -= cout
    p["charges"] += 1
    etat["version"] += 1
    return []


def _saboter(etat, nom, message, maintenant, rng, config):
    if etat["phase"] != "enquete" or not _est_tueur(etat, nom):
        return _erreur(nom, "Action impossible.")
    p = _perso(etat, nom)
    genre = message.get("sabotage")
    if genre not in SABOTAGES:
        return _erreur(nom, "Sabotage inconnu.")
    gratuite = genre == "rumeur" and _rumeur_gratuite(etat, maintenant)
    if p["charges"] < 1 and not gratuite:
        return _erreur(nom, "Plus de charge : achète-en une avec tes PE.")
    if not gratuite and p["prochain_sabotage_a"] is not None and maintenant < p["prochain_sabotage_a"]:
        reste = int(p["prochain_sabotage_a"] - maintenant + 0.999)
        return _erreur(nom, f"Prochain sabotage possible dans {reste} s.")
    pack = PACKS[etat["univers"]]
    trace = {"t": maintenant, "type": genre, "cible": None, "categorie": None, "element": None, "mode": None}
    ev = []
    if genre == "rumeur":
        if p["rumeur_utilisee"] and not gratuite:
            return _erreur(nom, "Une seule fausse rumeur par partie.")
        cat, elem = message.get("categorie"), message.get("element")
        if cat not in ("suspect", "lieu", "arme") or elem not in _candidats(etat, cat) or elem == _element_solution(etat, cat) \
                or (cat == "suspect" and cle_nom(elem) == cle_nom(nom)):
            return _erreur(nom, "La rumeur doit viser un innocent.")
        if gratuite:
            etat["evenement"]["rumeur_gratuite_utilisee"] = True
        else:
            p["rumeur_utilisee"] = True
        trace.update(categorie=cat, element=elem)
        # Publiee apres un petit delai aleatoire, comme une rumeur ambiante.
        a, b = config["sabotage"]["delai_publication"]
        etat["differes"].append({"t": maintenant + rng.uniform(a, b), "genre": "rumeur",
                                 "texte": _texte_rumeur(etat, cat, elem, rng), "lieu": elem if cat == "lieu" else None})
    else:
        cible = message.get("cible")
        cj = _joueur(etat, cible or "")
        if cj is None or cle_nom(cj["nom"]) == cle_nom(nom):
            return _erreur(nom, "Choisis une cible.")
        cible = cj["nom"]
        q = _perso(etat, cible)
        trace["cible"] = cible
        if genre == "ecoute":
            visibles = [e for e in q["archive"] if not e.get("retenu")]
            if not visibles:
                return _erreur(nom, f"{cible} n'a encore reçu aucun indice.")
            e = visibles[-1]
            ev.append(_a(nom, {"type": "enq_ecoute", "cible": cible, "texte": e["texte"], "source": e["source"],
                               "recu_a": e["recu_a"], "categorie": e["categorie"], "element": e["element"]}))
        elif genre == "interception":
            if q["interception"]:
                return _erreur(nom, f"Le prochain pli de {cible} est déjà guetté.")
            q["interception"] = True
        else:
            cat, mode = message.get("categorie"), message.get("mode")
            if cat not in ("suspect", "lieu", "arme") or mode not in ("remplacement", "detournement"):
                return _erreur(nom, "Choisis une catégorie et un mode.")
            element, risque = _fausse_piste(etat, cible, cat, config)
            if element is None:
                return _erreur(nom, risque)
            if mode == "remplacement":
                if q["faux_en_attente"]:
                    return _erreur(nom, f"Un faux attend déjà {cible}.")
                q["faux_en_attente"] = {"categorie": cat, "element": element}
            else:
                if etat.get("detournement") is not None or etat.get("prochain_bonus_a") is None:
                    return _erreur(nom, "Aucun témoin à détourner pour l'instant.")
                etat["detournement"] = {"cible": cible, "categorie": cat, "element": element}
            etat["faux_emis"].append([cat, element])
            trace.update(categorie=cat, element=element, mode=mode)
    detecte = rng.random() < detection_actuelle(etat, maintenant, config)
    trace["detecte"] = detecte
    if detecte:
        a, b = config["sabotage"]["delai_annonce"]
        etat["differes"].append({"t": maintenant + rng.uniform(a, b), "genre": "annonce",
                                 "texte": pack["textes"]["annonces"]["interference"]})
    if not gratuite:
        p["charges"] -= 1
        p["prochain_sabotage_a"] = maintenant + _vitesse(etat, config)["delai_sabotage"]
    trace["gratuite"] = gratuite
    etat["sabotages"].append(trace)
    etat["version"] += 1
    return ev


def _texte_rumeur(etat, categorie, element, rng):
    pack = PACKS[etat["univers"]]
    modele = rng.choice(pack["textes"]["rumeurs"][categorie])
    if categorie == "suspect":
        return modele.format(nom=element)
    if categorie == "lieu":
        return contracter(modele.format(lieu=_nom_element(pack, "lieux", element)))
    return contracter(modele.format(arme=_nom_element(pack, "armes", element)))


def _ajouter_rumeur(etat, maintenant, texte, lieu):
    etat["rumeurs"].append({"t": maintenant, "texte": texte, "lieu": lieu})
    del etat["rumeurs"][:-JOURNAL_MAX]
    return [_tous({"type": "enq_rumeur", "texte": texte, "lieu": lieu})]


def rumeur_ambiante(etat, maintenant, rng=random, config=None):
    """
    Bruit de fond : une rumeur sur un element tire au hasard, solution
    comprise ou non, sans aucune valeur de preuve. Meme format que la
    fausse rumeur du tueur.
    """
    config = config or CONFIG
    cat = rng.choice(("suspect", "lieu", "arme"))
    elem = rng.choice(_candidats(etat, cat))
    etat["prochaine_rumeur_a"] = maintenant + _intervalle(etat, config, rng, "rumeurs_toutes") / (2 if _actif(etat, "brouhaha", maintenant) else 1)
    etat["version"] += 1
    return _ajouter_rumeur(etat, maintenant, _texte_rumeur(etat, cat, elem, rng), elem if cat == "lieu" else None)


def _publier_differes(etat, maintenant):
    ev = []
    restants = []
    for d in sorted(etat.get("differes", []), key=lambda x: x["t"]):
        if d["t"] > maintenant:
            restants.append(d)
        elif d["genre"] == "annonce":
            _annoncer(etat, maintenant, d["texte"])
            etat["version"] += 1
        else:
            ev += _ajouter_rumeur(etat, maintenant, d["texte"], d["lieu"])
            etat["version"] += 1
    etat["differes"] = restants
    return ev


def _liberer_retenus(etat, maintenant):
    """Plis interceptes : ils arrivent enfin, comme s ils venaient d etre remis."""
    ev = []
    for k, p in etat["perso"].items():
        for e in p["archive"]:
            if e.get("retenu") and e["visible_a"] <= maintenant:
                e["retenu"] = False
                nom = next((j["nom"] for j in etat["joueurs"] if cle_nom(j["nom"]) == k), k)
                _annoncer(etat, maintenant, PACKS[etat["univers"]]["textes"]["annonces"]["indice"])
                ev.append(_a(nom, {"type": "enq_nouvel_indice", "id": e["id"], "source": e["source"]}))
                etat["version"] += 1
    return ev


# =============================================================
# TRANCHE 6 : EVENEMENTS D AMBIANCE
# =============================================================

EVENEMENTS = ("coupure", "decouverte", "alerte", "brouhaha", "interrogatoire", "maree")


def programmer_evenements(etat, config, rng):
    """
    N evenements (selon la vitesse) de duree D, places au hasard dans
    l enquete : un depart au plus tot apres 10 % du temps, jamais deux a la
    fois, au moins 2 D entre la fin de l un et le debut du suivant, jamais
    deux fois de suite le meme type. SECRET (personne ne sait quand).
    """
    v = _vitesse(etat, config)
    n, d = v["evenements"], v["duree_evenement"]
    debut, fin = etat["ouverture_fin_a"] + 0.1 * etat["duree"], etat["fin_a"] - d - 0.05 * etat["duree"]
    pas = 3 * d                       # D d evenement + 2 D d espacement
    while n > 0 and (fin - debut) < (n - 1) * pas:
        n -= 1                        # partie trop courte : moins d evenements
    if n <= 0:
        return []
    # Positions : on tire n points dans l espace libre, puis on ajoute l espacement.
    libre = (fin - debut) - (n - 1) * pas
    points = sorted(rng.uniform(0, libre) for _ in range(n))
    types, dernier = [], None
    for _ in range(n):
        choix = [t for t in EVENEMENTS if t != dernier]
        dernier = rng.choice(choix)
        types.append(dernier)
    return [{"t": debut + x + i * pas, "type": types[i]} for i, x in enumerate(points)]


def _actif(etat, type_, maintenant):
    e = etat.get("evenement")
    return bool(e and e["type"] == type_ and e["debut"] <= maintenant < e["fin"])


def _rumeur_gratuite(etat, maintenant):
    e = etat.get("evenement")
    return _actif(etat, "brouhaha", maintenant) and not e.get("rumeur_gratuite_utilisee")


def prochaine_accusation(etat, p, maintenant):
    """Heure de la prochaine accusation ; pendant une alerte, l attente restante est divisee par 2."""
    prochaine = p.get("prochaine_accusation_a")
    if prochaine is not None and _actif(etat, "alerte", maintenant):
        derniere = p["accusations"][-1]["t"] if p.get("accusations") else maintenant
        prochaine = derniere + (prochaine - derniere) / 2
    h = p.get("handicap_jusqua")
    if h and h > maintenant:
        prochaine = max(prochaine or 0, h)
    return prochaine


def _evenement_public(etat):
    e = etat.get("evenement")
    if not e:
        return None
    return {"type": e["type"], "debut": e["debut"], "fin": e["fin"], "interroges": e.get("interroges", []),
            "refus": e.get("refus", []), "repondu": e.get("repondu", [])}


def _textes_evenement(etat, type_):
    return PACKS[etat["univers"]]["textes"]["evenements"][type_]


def _evenements(etat, maintenant, rng, config):
    ev = []
    e = etat.get("evenement")
    if e and maintenant >= e["fin"]:
        if e["type"] == "interrogatoire":
            # Silence jusqu au bout : compte comme un refus (sinon il suffirait d ignorer).
            for nom in e.get("interroges", []):
                if not _a_traite(e, nom):
                    e["refus"].append(nom)
                    ev.append(_imposer(etat, nom, _gorgees(etat, config, config["gorgees"]["refus_interrogatoire"]),
                                       "Silence à l'interrogatoire", maintenant))
        textes = _textes_evenement(etat, e["type"])
        if textes.get("fin"):
            _annoncer(etat, maintenant, textes["fin"])
        etat["evenement"] = None
        etat["coupure_jusqua"] = None
        etat["version"] += 1
        ev.append(_tous({"type": "enq_evenement_fin", "evenement": e["type"]}))
    if etat.get("evenement") is None and etat.get("programme") and maintenant >= etat["programme"][0]["t"]:
        prevu = etat["programme"].pop(0)
        ev += demarrer_evenement(etat, prevu["type"], maintenant, rng, config)
    return ev


def demarrer_evenement(etat, type_, maintenant, rng=random, config=None):
    config = config or CONFIG
    d = _vitesse(etat, config)["duree_evenement"]
    textes = _textes_evenement(etat, type_)
    e = {"type": type_, "debut": maintenant, "fin": maintenant + d}
    etat["evenement"] = e
    etat.setdefault("evenements_passes", []).append({"type": type_, "debut": maintenant})
    _annoncer(etat, maintenant, textes["nom"] + " : " + textes["debut"])
    ev = []
    if type_ == "coupure":
        etat["coupure_jusqua"] = e["fin"]
    elif type_ == "decouverte":
        if etat["pool"]:
            indice = etat["pool"].pop(rng.randrange(len(etat["pool"])))
            texte = texte_indice(etat, indice, rng)
            etat["decouvertes"].append({"t": maintenant, "texte": texte, "categorie": indice["categorie"], "element": indice["element"]})
            _annoncer(etat, maintenant, texte)
            ev.append(_tous({"type": "enq_decouverte", "texte": texte, "categorie": indice["categorie"],
                             "element": indice["element"], "source": textes["nom"], "recu_a": maintenant}))
            if not etat["pool"] and not etat["dossier_epuise"]:
                etat["dossier_epuise"] = True
                _annoncer(etat, maintenant, PACKS[etat["univers"]]["textes"]["annonces"]["dossier_epuise"])
    elif type_ == "brouhaha":
        etat["prochaine_rumeur_a"] = min(etat.get("prochaine_rumeur_a") or e["fin"],
                                         maintenant + _intervalle(etat, config, rng, "rumeurs_toutes") / 4)
    elif type_ == "interrogatoire":
        e["interroges"] = rng.sample([j["nom"] for j in etat["joueurs"]], min(2, len(etat["joueurs"])))
        e["refus"] = []
        e["repondu"] = []
    etat["version"] += 1
    ev.insert(0, _tous({"type": "enq_evenement", "evenement": type_, "nom": textes["nom"], "texte": textes["debut"],
                        "fin": e["fin"], "interroges": e.get("interroges", [])}))
    return ev


def _a_traite(e, nom):
    return any(cle_nom(x) == cle_nom(nom) for x in e.get("refus", []) + e.get("repondu", []))


def _verifier_interroge(etat, nom, maintenant):
    e = etat.get("evenement")
    if not _actif(etat, "interrogatoire", maintenant) or not any(cle_nom(x) == cle_nom(nom) for x in e["interroges"]):
        return None, "Personne ne t'interroge en ce moment."
    if _a_traite(e, nom):
        return None, "Tu as déjà répondu ou refusé."
    return e, None


def _repondu_interrogatoire(etat, nom, message, maintenant, rng, config):
    """Un joueur designe signale devant tous qu il a livre son indice a voix haute."""
    e, refus = _verifier_interroge(etat, nom, maintenant)
    if refus:
        return _erreur(nom, refus)
    e["repondu"].append(nom)
    etat["version"] += 1
    return []


def _refuser_interrogatoire(etat, nom, message, maintenant, rng, config):
    """Un joueur designe refuse de livrer un indice a voix haute : il boit."""
    e, refus = _verifier_interroge(etat, nom, maintenant)
    if refus:
        return _erreur(nom, refus)
    e["refus"].append(nom)
    etat["version"] += 1
    return [_imposer(etat, nom, _gorgees(etat, config, config["gorgees"]["refus_interrogatoire"]), "Refus de répondre", maintenant)]


# =============================================================
# TRANCHE 9 : JOUEURS FICTIFS
# =============================================================

BOT_NOMS = ["Alfred", "Berthe", "Corentin", "Daphné", "Edgar", "Fanny", "Gustave", "Hortense",
            "Isidore", "Joséphine", "Léon", "Mathilde"]


def _ajouter_bot(etat, nom, message, maintenant, rng, config):
    """L hote ajoute un joueur fictif (pour tester l enquete sans reunir tout le monde)."""
    if not _est_hote(etat, nom):
        return _erreur(nom, "Seul l'hôte ajoute des joueurs fictifs.")
    if etat["phase"] != "lobby":
        return _erreur(nom, "L'enquête a déjà commencé.")
    if len(etat["joueurs"]) >= config["joueurs_max"]:
        return _erreur(nom, f"{config['joueurs_max']} joueurs au plus.")
    pris = {cle_nom(j["nom"]) for j in etat["joueurs"]}
    libre = next((b for b in BOT_NOMS if cle_nom(b) not in pris), None)
    portraits = [x["id"] for x in PACKS[etat["univers"]]["portraits"] if x["id"] not in {j["portrait"] for j in etat["joueurs"]}]
    if libre is None or not portraits:
        return _erreur(nom, "Plus de place pour un joueur fictif.")
    etat["joueurs"].append({"nom": libre, "portrait": rng.choice(portraits), "bot": True})
    etat["version"] += 1
    return []


def _retirer_bot(etat, nom, message, maintenant, rng, config):
    if not _est_hote(etat, nom) or etat["phase"] != "lobby":
        return _erreur(nom, "Action impossible.")
    avant = len(etat["joueurs"])
    etat["joueurs"] = [j for j in etat["joueurs"] if not (j.get("bot") and cle_nom(j["nom"]) == cle_nom(message.get("nom", "")))]
    if len(etat["joueurs"]) != avant:
        etat["version"] += 1
    return []


def est_bot(etat, nom):
    j = _joueur(etat, nom)
    return bool(j and j.get("bot"))


def connaissance(etat, nom, partage=()):
    """
    Ce que le joueur sait LEGITIMEMENT : ses plis lus (hors tampon falsifie),
    ceux qu on lui a montres (partage), les decouvertes publiques, et les
    accusations a 0 juste. Renvoie les candidats restants par categorie et
    les triplets deja tentes.
    """
    p = _perso(etat, nom)
    elim = {"suspect": set(), "lieu": set(), "arme": set()}
    for q in [p] + [_perso(etat, x) for x in partage if _perso(etat, x)]:
        for e in q["archive"]:
            if e["lu"] and not e.get("retenu") and e["tampon"] != "falsifie":
                elim[e["categorie"]].add(e["element"])
    for d in etat.get("decouvertes", []):
        elim[d["categorie"]].add(d["element"])
    for a in p["accusations"]:
        if a["justes"] == 0:
            elim["suspect"].add(a["suspect"]); elim["arme"].add(a["arme"]); elim["lieu"].add(a["lieu"])
    cands = {cat: [x for x in _candidats(etat, cat, nom) if x not in elim[cat]] for cat in elim}
    for cat in cands:
        if not cands[cat]:          # contradiction (un faux non demasque) : on repart de zero
            cands[cat] = _candidats(etat, cat, nom)
    tentes = {(a["suspect"], a["arme"], a["lieu"]) for a in p["accusations"]}
    return cands, tentes


def jouer_bot(etat, nom, maintenant, rng=random, config=None, partage=(), audace=1.0):
    """Un tour de jeu d un joueur fictif. Renvoie les evenements produits."""
    config = config or CONFIG
    if etat["phase"] != "enquete":
        return []
    p = _perso(etat, nom)
    ev = []

    def agir(msg):
        if etat["phase"] == "enquete":
            ev.extend(action(etat, nom, msg, maintenant, rng, config))

    for x in etat.get("penalites", []):
        if cle_nom(x["nom"]) == cle_nom(nom) and not x["bu"]:
            x["bu"] = True
            etat["version"] += 1
    evt = etat.get("evenement")
    if _actif(etat, "interrogatoire", maintenant) and any(cle_nom(x) == cle_nom(nom) for x in evt["interroges"]) and not _a_traite(evt, nom):
        agir({"type": "enq_repondu" if rng.random() < 0.8 else "enq_refuser"})
    for e in list(p["archive"]):
        if not e["lu"] and not e.get("retenu"):
            agir({"type": "enq_lire", "id": e["id"]})
    cout = _vitesse(etat, config)["pe_par_indice"]
    autres = [j["nom"] for j in etat["joueurs"] if cle_nom(j["nom"]) != cle_nom(nom)]
    if _est_tueur(etat, nom):
        if p["pe"] >= cout:
            agir({"type": "enq_charge"} if p["charges"] < 2 and rng.random() < 0.6 else {"type": "enq_indice"})
        pret = p["prochain_sabotage_a"] is None or maintenant >= p["prochain_sabotage_a"]
        if (p["charges"] > 0 or _rumeur_gratuite(etat, maintenant)) and pret and rng.random() < 0.5:
            cible = rng.choice(autres)
            genre = rng.choice(["fausse_piste", "fausse_piste", "interception", "ecoute"] + ([] if p["rumeur_utilisee"] else ["rumeur"]))
            if _rumeur_gratuite(etat, maintenant):
                genre = "rumeur"
            msg = {"type": "enq_sabotage", "sabotage": genre, "cible": cible}
            if genre == "fausse_piste":
                possibles = [(cat, r) for cat in ("suspect", "lieu", "arme") for el, r in [_fausse_piste(etat, cible, cat, config)] if el]
                if not possibles:
                    return ev
                doux = [c for c, r in possibles if r != "eleve"] or [c for c, _ in possibles]
                mode = "detournement" if etat.get("detournement") is None and etat.get("prochain_bonus_a") and rng.random() < 0.3 else "remplacement"
                msg.update(categorie=rng.choice(doux), mode=mode)
            elif genre == "rumeur":
                cat = rng.choice(["suspect", "lieu", "arme"])
                innocents = [x for x in _candidats(etat, cat) if x != _element_solution(etat, cat) and cle_nom(x) != cle_nom(nom)]
                msg.update(categorie=cat, element=rng.choice(innocents))
            agir(msg)
        return ev
    # Enqueteur
    if p["pe"] >= cout:
        a_verifier = [e for e in p["archive"] if e["lu"] and not e["tampon"] and not e.get("retenu")]
        if a_verifier and rng.random() < 0.2:
            agir({"type": "enq_expertise", "id": rng.choice(a_verifier)["id"], "paiement": "pe"})
        else:
            agir({"type": "enq_indice"})
    cands, tentes = connaissance(etat, nom, partage)
    combos = [(s, a, l) for s in cands["suspect"] for a in cands["arme"] for l in cands["lieu"] if (s, a, l) not in tentes]
    # Deduction facon Mastermind : un triplet reste possible s il donne, pour
    # chaque accusation passee, le meme nombre d elements justes.
    for acc in p["accusations"]:
        combos = [c for c in combos if min(2, (cle_nom(c[0]) == cle_nom(acc["suspect"])) + (c[1] == acc["arme"]) + (c[2] == acc["lieu"])) == acc["justes"]]
    prochaine = prochaine_accusation(etat, p, maintenant)
    avance = (maintenant - etat["ouverture_fin_a"]) / max(1, etat["fin_a"] - etat["ouverture_fin_a"])
    # On accuse quand le champ s est assez resserre, de plus en plus tot a
    # mesure que l heure tourne (une accusation ratee renseigne aussi).
    seuil = 3 + int(60 * avance ** 2)
    if combos and (prochaine is None or maintenant >= prochaine) and len(combos) <= seuil * audace and rng.random() < 0.5:
        s_, a_, l_ = rng.choice(combos)
        agir({"type": "enq_accuser", "suspect": s_, "arme": a_, "lieu": l_})
    return ev


def tic_bots(etat, maintenant, rng=random, config=None):
    """
    A intervalle regulier (tic_bots secondes selon la vitesse, 60 par defaut) :
    - vitesse Demo : tout le monde, vrais joueurs compris, gagne des PE
      automatiquement (pe_auto_heure), pour tester seul sans jouer aux jeux ;
    - les joueurs fictifs gagnent des PE comme s ils jouaient, puis jouent leur tour.
    """
    config = config or CONFIG
    if etat["phase"] != "enquete":
        return []
    prochain = etat.get("prochain_tic_bots")
    if prochain is not None and maintenant < prochain:
        return []
    v = _vitesse(etat, config)
    pas = v.get("tic_bots", 60)
    etat["prochain_tic_bots"] = maintenant + pas
    ev = []
    auto = v.get("pe_auto_heure", 0) * pas / 3600
    taux_bot = max(auto, config.get("bot_pe_heure", 4) * pas / 3600)
    for j in etat["joueurs"]:
        if rng.random() < (taux_bot if j.get("bot") else auto):
            ev += crediter_pe(etat, j["nom"], 1, "menteur", "entraînement" if not j.get("bot") else "victoire", config)
        if j.get("bot") and rng.random() < 0.5:
            ev += jouer_bot(etat, j["nom"], maintenant, rng, config)
    return ev


def recapitulatif(etat):
    """Ecran de fin : tout est revele (accusations, indices recus, solution)."""
    return {
        "accusations": [{"nom": a["nom"], "suspect": a["suspect"], "arme": a["arme"], "lieu": a["lieu"],
                         "justes": a["justes"], "par_le_tueur": a["par_le_tueur"]} for a in etat["accusations"]],
        "indices": [{"nom": j["nom"], "recus": len(etat["perso"].get(cle_nom(j["nom"]), {}).get("archive", [])),
                     "faux": sum(1 for e in etat["perso"].get(cle_nom(j["nom"]), {}).get("archive", []) if not e["vrai"]),
                     "temoins": sum(1 for e in etat["perso"].get(cle_nom(j["nom"]), {}).get("archive", [])
                                    if e["source"] == "Témoin anonyme"),
                     "expertises": etat["perso"].get(cle_nom(j["nom"]), {}).get("expertises", 0)}
                    for j in etat["joueurs"]],
        "gorgees": gorgees_de_fin(etat),
        "bues": [{"nom": j["nom"],
                  "total": sum(n for _, n, _ in etat["perso"].get(cle_nom(j["nom"]), {}).get("gorgees", [])),
                  "handicap": sum(n for _, n, _ in etat["perso"].get(cle_nom(j["nom"]), {}).get("handicaps", []))}
                 for j in etat["joueurs"]],
        "sabotages": [{"type": x["type"], "cible": x["cible"], "detecte": x["detecte"]} for x in etat.get("sabotages", [])],
    }


def gorgees_de_fin(etat, config=None):
    config = config or CONFIG
    n = _gorgees(etat, config, config["gorgees"]["victoire_distribuee"])
    sol = etat["solution"]
    if etat["vainqueur"] == "enqueteurs":
        return [{"qui": etat["detective"], "action": "distribue", "nombre": n},
                {"qui": sol["tueur"], "action": "boit", "nombre": n}]
    return [{"qui": sol["tueur"], "action": "distribue aux enquêteurs", "nombre": n}]
