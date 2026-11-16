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
    construire_pool(etat, config, rng)
    etat["perso"] = {cle_nom(j["nom"]): perso_vide() for j in etat["joueurs"]}
    etat["phase"] = "ouverture"
    etat["debut_a"] = maintenant
    etat["ouverture_fin_a"] = maintenant + config["duree_ouverture"]
    etat["fin_a"] = maintenant + config["duree_ouverture"] + etat["duree"]
    etat["prochain_bonus_a"] = etat["ouverture_fin_a"] + _intervalle_bonus(etat, config, rng)
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
        return min(x for x in (etat["fin_a"], tic, etat.get("prochain_bonus_a")) if x is not None)
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
        "journal": etat.get("journal", [])[-20:],
        "dossier_epuise": etat.get("dossier_epuise", False),
        "timer_consultation": CONFIG["vitesses"][etat["vitesse"]]["timer_consultation"],
        "gorgees_consultation": max(1, int(CONFIG["gorgees"]["consultation"]
                                           * CONFIG["vitesses"][etat["vitesse"]]["mult_gorgees"] + 0.5)),
        "gorgees_achat": max(1, int(CONFIG["gorgees"]["indice_achete"]
                                    * CONFIG["vitesses"][etat["vitesse"]]["mult_gorgees"] + 0.5)),
        "gorgees_expertise": max(1, int(CONFIG["gorgees"]["contre_expertise_achetee"]
                                        * CONFIG["vitesses"][etat["vitesse"]]["mult_gorgees"] + 0.5)),
        "bonus_toutes": CONFIG["vitesses"][etat["vitesse"]]["bonus_toutes"],
        "echecs": etat.get("echecs", 0),
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
    return v


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
        return modele.format(lieu=_nom_element(pack, "lieux", e))
    return modele.format(arme=_nom_element(pack, "armes", e))


def _tirer(etat, nom):
    """Le prochain indice du stock, sans jamais donner a quelqu un sa propre innocence."""
    k = cle_nom(nom)
    for i, e in enumerate(etat["pool"]):
        if e["categorie"] == "suspect" and cle_nom(e["element"]) == k:
            continue
        return etat["pool"].pop(i)
    return None


def donner_indice(etat, nom, source, maintenant, rng, config):
    """
    Ajoute un indice a l archive du joueur. Renvoie les evenements, ou None
    si le stock est vide pour lui. Le texte n est montre qu a l ouverture.
    """
    p = _perso(etat, nom)
    indice = _tirer(etat, nom)
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
        "vrai": True,               # SECRET (les faux viendront avec les sabotages)
        "source": source,
        "recu_a": maintenant,
        "lu": False,
        "tampon": None,             # "authentique" | "falsifie" (contre-expertise)
    }
    etat["prochain_id"] += 1
    p["archive"].append(entree)
    _annoncer(etat, maintenant, PACKS[etat["univers"]]["textes"]["annonces"]["indice"])
    return [_a(nom, {"type": "enq_nouvel_indice", "id": entree["id"], "source": source})]


def _indices_derniere_heure(p, maintenant):
    return sum(1 for t in p["obtenus"] if maintenant - t < 3600)


def _gorgees(etat, config, base):
    """Nombre de gorgees selon la vitesse (multiplicateur), arrondi, au moins 1."""
    return max(1, int(base * _vitesse(etat, config)["mult_gorgees"] + 0.5))


def _imposer(etat, nom, nombre, raison, maintenant):
    p = _perso(etat, nom)
    p["gorgees"].append([maintenant, nombre, raison])
    return _a(nom, {"type": "enq_gorgee", "nombre": nombre, "unite": etat["unite"], "raison": raison})


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
        if e["id"] == message.get("id"):
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
    p = _perso(etat, nom)
    timer = _vitesse(etat, config)["timer_consultation"]
    if p["derniere_consultation"] is not None and maintenant < p["derniere_consultation"] + timer:
        reste = int(p["derniere_consultation"] + timer - maintenant + 0.999)
        return _erreur(nom, f"Archive verrouillée encore {reste} s.")
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
    p["pe"] += n
    titre = {"kinito": "Kinito", "buckshot": "Buckshot", "menteur": "Menteur", "beerbattle": "BeerBattle",
             "picolopoly": "Picopoly", "casino": "Casino"}.get(jeu, jeu)
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


def vue_privee_t2(etat, nom, maintenant, config=None):
    """Champs prives de la tranche 2 : memes cles pour tout le monde."""
    config = config or CONFIG
    p = _perso(etat, nom)
    v = _vitesse(etat, config)
    timer = v["timer_consultation"]
    if p is None:
        return {"pe": 0, "cout_indice": v["pe_par_indice"], "indices_heure": 0,
                "indices_heure_max": config["indices_par_heure_max"], "archive": [],
                "consultation_possible_a": None, "accusations": [], "prochaine_accusation_a": None}
    return {
        "pe": p["pe"],
        "cout_indice": v["pe_par_indice"],
        "indices_heure": _indices_derniere_heure(p, maintenant),
        "indices_heure_max": config["indices_par_heure_max"],
        # Sans le texte : il ne s affiche qu a la lecture (gratuite une fois,
        # puis consultation payante).
        "archive": [{"id": e["id"], "source": e["source"], "recu_a": e["recu_a"], "lu": e["lu"],
                     "tampon": e["tampon"]} for e in p["archive"]],
        "consultation_possible_a": (p["derniere_consultation"] + timer) if p["derniere_consultation"] else None,
        "accusations": p.get("accusations", []),
        "prochaine_accusation_a": p.get("prochaine_accusation_a"),
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
    if p["prochaine_accusation_a"] is not None and maintenant < p["prochaine_accusation_a"]:
        reste = int(p["prochaine_accusation_a"] - maintenant + 0.999)
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

def _intervalle_bonus(etat, config, rng):
    """Toutes les X minutes selon la vitesse, a +/- bonus_alea pres (pas d horloge previsible)."""
    base = _vitesse(etat, config)["bonus_toutes"]
    alea = config.get("bonus_alea", 0.25)
    return base * (1 + rng.uniform(-alea, alea))


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
    p = _perso(etat, nom)
    timer = _vitesse(etat, config)["timer_consultation"]
    if p["derniere_consultation"] is not None and maintenant < p["derniere_consultation"] + timer:
        reste = int(p["derniere_consultation"] + timer - maintenant + 0.999)
        return _erreur(nom, f"Archive verrouillée encore {reste} s.")
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
    }


def gorgees_de_fin(etat, config=None):
    config = config or CONFIG
    n = _gorgees(etat, config, config["gorgees"]["victoire_distribuee"])
    sol = etat["solution"]
    if etat["vainqueur"] == "enqueteurs":
        return [{"qui": etat["detective"], "action": "distribue", "nombre": n},
                {"qui": sol["tueur"], "action": "boit", "nombre": n}]
    return [{"qui": sol["tueur"], "action": "distribue aux enquêteurs", "nombre": n}]
