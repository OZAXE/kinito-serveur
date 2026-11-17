"""
Bingo de soiree : moteur de regles pur (sans reseau), couche parallele « bng ».

Chaque joueur recoit une grille PRIVEE de petits evenements de soiree. Il
coche ce qu il voit ; une ligne complete lui permet d annoncer un BINGO,
verifie par un petit jury de joueurs (votes anonymes « Vu » / « Pas vu »).

- Toutes les valeurs chiffrees sont dans bingo_config.json, la banque de
  cases dans bingo_cases.json.
- Les joueurs sont connus par leur PRENOM (cle insensible a la casse).
- Chaque fonction qui change l etat incremente etat["version"] et renvoie
  des evenements {"a": nom | None, "msg": {...}} (None : tout le monde).
- Ce qui est partage avec le reste de la soiree (plafond de gorgees,
  unite, heure de fin, jeux actifs) arrive par l argument « commun » de
  synchroniser() : le moteur ne connait pas les autres couches.

Etat d une case cochee : {"t": horodatage, "fige": bool, "valide": bool}.
- provisoire : moins de « annulation » secondes, ni figee ni validee ;
  un deuxieme appui l annule ;
- definitive : passe ce delai, ou figee par une annonce de BINGO ;
- validee : acceptee par un audit, jamais reauditee.
"""
import json
import os
import random

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "bingo_config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)
with open(os.path.join(_ICI, "bingo_cases.json"), encoding="utf-8") as f:
    BANQUE = json.load(f)

CATEGORIES = list(BANQUE["categories"])


def cle_nom(nom):
    return " ".join(str(nom).split()).casefold()


def _v(etat, config):
    return config["vitesses"][etat["vitesse"]]


def _a(nom, msg):
    return {"a": nom, "msg": msg}


def _erreur(nom, texte):
    return [_a(nom, {"type": "bng_erreur", "message": texte})]


def lignes_de(n):
    """Les alignements d une grille n x n : lignes, colonnes, puis les deux diagonales."""
    out = [[r * n + c for c in range(n)] for r in range(n)]
    out += [[r * n + c for r in range(n)] for c in range(n)]
    out.append([i * n + i for i in range(n)])
    out.append([i * n + (n - 1 - i) for i in range(n)])
    return out


# =============================================================
# CREATION, SALLE D ATTENTE
# =============================================================

def nouvelle_partie(code, hote, maintenant, config=None):
    config = config or CONFIG
    vitesse = config["vitesse_defaut"]
    return {
        "code": code,
        "hote": hote,
        "phase": "lobby",           # lobby | jeu | fin | fermee
        "version": 0,
        "cree_a": maintenant,
        "vitesse": vitesse,
        "duree": config["vitesses"][vitesse]["duree"],
        "unite": config["gorgees"]["unite_defaut"],
        "plafond": config["gorgees"]["plafond_par_heure"],
        "perso": [],                # cases personnalisees de l hote (textes)
        "commun": {},               # dernier etat partage recu (voir synchroniser)
        "debut_a": None,
        "fin_a": None,
        "fin_calee": False,         # heure de fin reprise de l Enquete
        "joueurs": [],              # prenoms, ordre d arrivee
        "j": {},                    # cle du prenom -> etat prive (grille, cases cochees...)
        "file": [],                 # annonces en attente d audit
        "audit": None,              # audit en cours
        "dernier_jury": [],         # cles des jures du dernier audit
        "prochain_id": 1,
        "journal": [],              # notifications publiques discretes [{id, t, texte}]
        "resultat": None,           # fin de partie
    }


def synchroniser(etat, commun):
    """
    Ce que la soiree partage avec le Bingo, relu avant chaque action :
    {"enquete": bool, "jeux": [jeux en cours aux tables], "plafond": int|None,
     "unite": str|None, "fin_a": float|None, "vitesse": str|None}
    Si l Enquete tourne, son plafond et son unite font foi (reglage commun).
    """
    etat["commun"] = dict(commun or {})


def plafond_effectif(etat):
    c = etat.get("commun") or {}
    return c["plafond"] if c.get("enquete") and c.get("plafond") is not None else (etat.get("plafond") or 0)


def unite_effective(etat):
    c = etat.get("commun") or {}
    return c["unite"] if c.get("enquete") and c.get("unite") else etat["unite"]


def _journal(etat, texte, maintenant, config):
    etat["journal"].append({"id": etat["prochain_id"], "t": maintenant, "texte": texte})
    etat["prochain_id"] += 1
    del etat["journal"][:-config["journal_max"]]


def _inscrire(etat, nom, maintenant, rng, config):
    k = cle_nom(nom)
    if k in etat["j"]:
        return False
    etat["joueurs"].append(nom)
    etat["j"][k] = {
        "nom": nom,
        "grille": [],
        "coches": {},               # index (str) -> {"t", "fige", "valide"}
        "lignes": [],               # index des alignements valides
        "grille_validee": False,
        "points": 0,
        "score_a": None,            # quand le score actuel a ete atteint (departage)
        "dernier_cochage": None,
        "refus_jusqua": None,       # pas de BINGO avant (refus ou handicap)
        "gorgees": [],              # [[t, nombre bu]] : compte dans le plafond commun
        "refus": 0,
        "inscrit_a": maintenant,
    }
    if etat["phase"] == "jeu":
        etat["j"][k]["grille"] = tirer_grille(etat, rng, config)
    return True


def action(etat, nom, message, maintenant, rng=random, config=None, connectes=None, ailleurs=None):
    """
    Un message « bng_... » d un joueur.
    connectes : prenoms ayant la page ouverte (pour choisir un jury present).
    ailleurs : (nom, maintenant) -> gorgees bues dans l heure dans les AUTRES jeux.
    """
    config = config or CONFIG
    t = message.get("type")
    if t == "bng_rejoindre":
        if etat["phase"] not in ("lobby", "jeu"):
            return _erreur(nom, "La partie de bingo est terminée.")
        if _inscrire(etat, nom, maintenant, rng, config):
            etat["version"] += 1
        return []
    if t == "bng_quitter":
        return _quitter(etat, nom, maintenant, config)
    hote = cle_nom(nom) == cle_nom(etat["hote"])
    if t == "bng_config":
        if not hote:
            return _erreur(nom, "Seul l'hôte règle la partie.")
        if etat["phase"] != "lobby":
            return _erreur(nom, "La partie est déjà lancée.")
        return _configurer(etat, nom, message, config)
    if t == "bng_lancer":
        if not hote:
            return _erreur(nom, "Seul l'hôte lance la partie.")
        return lancer(etat, nom, maintenant, rng, config)
    if t == "bng_terminer":
        if not hote:
            return _erreur(nom, "Seul l'hôte termine la partie.")
        if etat["phase"] != "jeu":
            return _erreur(nom, "La partie n'est pas en cours.")
        return terminer(etat, maintenant, rng, config, connectes, ailleurs, "hote")
    if t in ("bng_fermer", "bng_annuler"):
        if not hote:
            return _erreur(nom, "Seul l'hôte peut fermer la partie.")
        if etat["phase"] == "jeu":
            return _erreur(nom, "Termine d'abord la partie.")
        etat["phase"] = "fermee"
        etat["version"] += 1
        return [_a(None, {"type": "bng_ferme"})]
    if t == "bng_cocher":
        return cocher(etat, nom, message.get("index"), maintenant, config)
    if t == "bng_bingo":
        return annoncer(etat, nom, maintenant, rng, config, connectes, ailleurs)
    if t == "bng_voter":
        return voter(etat, nom, message, maintenant, rng, config, connectes, ailleurs)
    return _erreur(nom, "Action inconnue.")


def _configurer(etat, nom, message, config):
    if "vitesse" in message:
        if message["vitesse"] not in config["vitesses"]:
            return _erreur(nom, "Vitesse inconnue.")
        if message["vitesse"] != etat["vitesse"]:
            etat["vitesse"] = message["vitesse"]
            etat["duree"] = config["vitesses"][etat["vitesse"]]["duree"]
    if "duree" in message:
        if message["duree"] not in config["durees"] + [config["vitesses"][etat["vitesse"]]["duree"]]:
            return _erreur(nom, "Durée inconnue.")
        etat["duree"] = message["duree"]
    if "plafond" in message:
        if message["plafond"] not in config["gorgees"]["plafonds"]:
            return _erreur(nom, "Plafond inconnu.")
        etat["plafond"] = message["plafond"]
    if "unite" in message:
        u = " ".join(str(message["unite"]).split())[:20]
        if not u:
            return _erreur(nom, "Unité vide.")
        etat["unite"] = u
    if "perso" in message:
        p = config["perso"]
        if not isinstance(message["perso"], list):
            return _erreur(nom, "Cases personnalisées illisibles.")
        textes, vus = [], set()
        for x in message["perso"]:
            x = " ".join(str(x).split())[:p["longueur"]]
            if x and x.casefold() not in vus:
                vus.add(x.casefold())
                textes.append(x)
        etat["perso"] = textes[:p["max"]]
    etat["version"] += 1
    return []


def _quitter(etat, nom, maintenant, config):
    """Ne plus jouer au bingo (sa grille est oubliee, ses annonces retirees)."""
    k = cle_nom(nom)
    if k not in etat["j"] or etat["phase"] == "fin":
        return []
    etat["file"] = [x for x in etat["file"] if x["joueur"] != k]
    if etat["audit"] and etat["audit"]["joueur"] == k:
        etat["audit"] = None
    del etat["j"][k]
    etat["joueurs"] = [n for n in etat["joueurs"] if cle_nom(n) != k]
    etat["version"] += 1
    return []


# =============================================================
# TIRAGE DES GRILLES
# =============================================================

def _actif(etat, condition):
    if not condition:
        return True
    c = etat.get("commun") or {}
    actifs = set(c.get("jeux") or [])
    if c.get("enquete"):
        actifs.add("enq")
    return any(x in actifs for x in condition)


def tirer_grille(etat, rng=random, config=None):
    """
    Une grille privee, sans doublon :
    - jusqu a perso.par_grille cases personnalisees de l hote d abord ;
    - au moins min_par_categorie cases de chaque categorie (moins si la
      grille est trop petite pour en mettre autant partout) ;
    - au plus part_rares de cases rares ;
    - les cases conditionnelles seulement si leur jeu est actif.
    """
    config = config or CONFIG
    v = _v(etat, config)
    taille = v["taille"] * v["taille"]
    max_rares = int(v["part_rares"] * taille)
    grille = []
    perso = list(etat["perso"])
    rng.shuffle(perso)
    for i, texte in enumerate(perso[:config["perso"]["par_grille"]]):
        grille.append({"id": "p" + str(etat["perso"].index(texte)), "texte": texte, "categorie": "perso",
                       "rarete": "courant"})
    pool = [dict(c) for c in BANQUE["cases"] if _actif(etat, c.get("condition"))]
    rng.shuffle(pool)
    rares = 0

    def prendre(case):
        nonlocal rares
        grille.append(case)
        pool.remove(case)
        if case["rarete"] == "rare":
            rares += 1

    place = taille - len(grille)
    mini = min(config["min_par_categorie"], place // len(CATEGORIES))
    for cat in CATEGORIES:
        for _ in range(mini):
            choix = [c for c in pool if c["categorie"] == cat and (c["rarete"] != "rare" or rares < max_rares)]
            if choix:
                prendre(choix[0])
    while len(grille) < taille:
        choix = [c for c in pool if c["rarete"] != "rare" or rares < max_rares] or pool
        if not choix:
            break
        prendre(choix[0])
    rng.shuffle(grille)
    return [{k: c[k] for k in ("id", "texte", "categorie", "rarete")} for c in grille]


# =============================================================
# LANCEMENT ET FIN
# =============================================================

def lancer(etat, nom, maintenant, rng=random, config=None):
    config = config or CONFIG
    if etat["phase"] != "lobby":
        return _erreur(nom, "La partie est déjà lancée.")
    if len(etat["joueurs"]) < config["joueurs_min"]:
        return _erreur(nom, f"Il faut au moins {config['joueurs_min']} joueurs.")
    etat["phase"] = "jeu"
    etat["debut_a"] = maintenant
    c = etat.get("commun") or {}
    if c.get("enquete") and c.get("fin_a") and c["fin_a"] > maintenant + 60:
        etat["fin_a"] = c["fin_a"]          # meme heure de fin que l Enquete
        etat["fin_calee"] = True
    else:
        etat["fin_a"] = maintenant + etat["duree"]
    for k in etat["j"]:
        etat["j"][k]["grille"] = tirer_grille(etat, rng, config)
    _journal(etat, "Le bingo commence : regarde ta grille !", maintenant, config)
    etat["version"] += 1
    return [_a(None, {"type": "bng_lance"})]


def classement(etat):
    js = list(etat["j"].values())
    ordre = {cle_nom(n): i for i, n in enumerate(etat["joueurs"])}
    js.sort(key=lambda p: (-p["points"], p["score_a"] if p["score_a"] is not None else float("inf"),
                           ordre.get(cle_nom(p["nom"]), 0)))
    return [{"nom": p["nom"], "points": p["points"], "lignes": len(p["lignes"]),
             "grille": p["grille_validee"]} for p in js]


def terminer(etat, maintenant, rng=random, config=None, connectes=None, ailleurs=None, raison="heure"):
    config = config or CONFIG
    if etat["phase"] != "jeu":
        return []
    out = []
    if etat["audit"]:
        out += cloturer_audit(etat, maintenant, rng, config, connectes, ailleurs, suivant=False)
    etat["file"] = []
    etat["phase"] = "fin"
    cl = classement(etat)
    vainqueur = cl[0] if cl and cl[0]["points"] > 0 else None
    g = _g(etat, config, config["gorgees"]["vainqueur_distribue"])
    grille_v = None
    if vainqueur:
        p = etat["j"][cle_nom(vainqueur["nom"])]
        grille_v = {"taille": _taille(p), "cases": [c["texte"] for c in p["grille"]],
                    "coches": sorted(int(i) for i in p["coches"]),
                    "valides": sorted(int(i) for i, c in p["coches"].items() if c["valide"])}
        out.append(_a(vainqueur["nom"], {"type": "bng_distribue", "nombre": g, "unite": unite_effective(etat),
                                         "raison": "Tu gagnes le bingo"}))
    etat["resultat"] = {
        "raison": raison,
        "classement": cl,
        "vainqueur": vainqueur["nom"] if vainqueur else None,
        "distribue": g if vainqueur else 0,
        "grille_vainqueur": grille_v,
        "lignes": sum(len(p["lignes"]) for p in etat["j"].values()),
        "refus": sum(p["refus"] for p in etat["j"].values()),
        "cases": sum(len(p["coches"]) for p in etat["j"].values()),
    }
    _journal(etat, (vainqueur["nom"] + " gagne le bingo !") if vainqueur else "Bingo terminé, sans vainqueur.",
             maintenant, config)
    etat["version"] += 1
    out.append(_a(None, {"type": "bng_fin"}))
    return out


# =============================================================
# COCHER
# =============================================================

def _statut(c, maintenant, config):
    if c["valide"]:
        return "validee"
    if c["fige"]:
        return "figee"
    return "provisoire" if maintenant - c["t"] < config["annulation"] else "definitive"


def _taille(p):
    n = 1
    while n * n < len(p["grille"]):
        n += 1
    return n


def cocher(etat, nom, index, maintenant, config=None):
    config = config or CONFIG
    p = etat["j"].get(cle_nom(nom))
    if etat["phase"] != "jeu":
        return _erreur(nom, "Le bingo n'est pas en cours.")
    if p is None:
        return _erreur(nom, "Tu ne joues pas au bingo.")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(p["grille"]):
        return _erreur(nom, "Case inconnue.")
    c = p["coches"].get(str(index))
    if c is not None:
        if _statut(c, maintenant, config) == "provisoire":
            del p["coches"][str(index)]     # erreur de doigt
            etat["version"] += 1
            return []
        return _erreur(nom, "Case déjà enregistrée : elle ne se décoche plus.")
    delai = _v(etat, config)["delai_cochage"]
    if p["dernier_cochage"] is not None and maintenant - p["dernier_cochage"] < delai:
        reste = int(delai - (maintenant - p["dernier_cochage"]) + 0.999)
        return _erreur(nom, f"Doucement : encore {reste} s avant de cocher une autre case.")
    p["coches"][str(index)] = {"t": maintenant, "fige": False, "valide": False}
    p["dernier_cochage"] = maintenant
    etat["version"] += 1
    return []


# =============================================================
# BINGO ET AUDIT
# =============================================================

def _en_attente(etat, k):
    return (etat["audit"] and etat["audit"]["joueur"] == k) or any(x["joueur"] == k for x in etat["file"])


def nouvelles_lignes(p):
    n = _taille(p)
    return [i for i, l in enumerate(lignes_de(n))
            if i not in p["lignes"] and all(str(x) in p["coches"] for x in l)]


def annonce_possible(etat, p):
    """'grille', 'lignes' ou None : ce que le bouton BINGO annoncerait."""
    if etat["phase"] != "jeu" or not p["grille"] or _en_attente(etat, cle_nom(p["nom"])):
        return None
    if not p["grille_validee"] and len(p["coches"]) == len(p["grille"]):
        return "grille"
    return "lignes" if nouvelles_lignes(p) else None


def annoncer(etat, nom, maintenant, rng=random, config=None, connectes=None, ailleurs=None):
    config = config or CONFIG
    k = cle_nom(nom)
    p = etat["j"].get(k)
    if etat["phase"] != "jeu" or p is None:
        return _erreur(nom, "Le bingo n'est pas en cours.")
    if _en_attente(etat, k):
        return _erreur(nom, "Ton BINGO est déjà en vérification.")
    if p["refus_jusqua"] and maintenant < p["refus_jusqua"]:
        reste = int(p["refus_jusqua"] - maintenant + 0.999)
        return _erreur(nom, f"Pas de BINGO pendant encore {reste // 60} min {reste % 60:02d} s.")
    genre = annonce_possible(etat, p)
    if genre is None:
        return _erreur(nom, "Aucune nouvelle ligne complète.")
    lignes = nouvelles_lignes(p)
    if genre == "grille":
        cases = list(range(len(p["grille"])))
    else:
        n = _taille(p)
        cases = sorted({x for i in lignes for x in lignes_de(n)[i]})
    for x in cases:
        p["coches"][str(x)]["fige"] = True
    a_verifier = [x for x in cases if not p["coches"][str(x)]["valide"]]
    annonce = {"joueur": k, "lignes": lignes, "cases": a_verifier, "grille": genre == "grille", "t": maintenant}
    etat["version"] += 1
    if not a_verifier:
        return _valider(etat, annonce, maintenant, config)
    etat["file"].append(annonce)
    out = [_a(nom, {"type": "bng_annonce", "attente": len(etat["file"]) - (0 if etat["audit"] else 1)})]
    if etat["audit"] is None:
        out += demarrer_audit(etat, maintenant, rng, config, connectes, ailleurs)
    return out


def _choisir_jury(etat, k, rng, config, connectes):
    autres = [x for x in etat["j"] if x != k]
    if len(autres) <= config["jury"]["tous_jusqua"]:
        return autres
    presents = {cle_nom(n) for n in (connectes or [])}
    precedents = set(etat["dernier_jury"])
    rng.shuffle(autres)
    # D abord les presents qui n etaient pas au dernier jury, puis les presents, puis les autres.
    autres.sort(key=lambda x: (x not in presents if connectes is not None else False, x in precedents))
    return autres[:config["jury"]["taille"]]


def demarrer_audit(etat, maintenant, rng=random, config=None, connectes=None, ailleurs=None):
    config = config or CONFIG
    out = []
    while etat["audit"] is None and etat["file"]:
        annonce = etat["file"].pop(0)
        if annonce["joueur"] not in etat["j"]:
            continue
        jures = _choisir_jury(etat, annonce["joueur"], rng, config, connectes)
        if not jures:
            out += _valider(etat, annonce, maintenant, config)
            continue
        etat["audit"] = dict(annonce, id=etat["prochain_id"], jures=jures, votes={},
                             echeance=maintenant + _v(etat, config)["duree_vote"])
        etat["prochain_id"] += 1
        etat["dernier_jury"] = list(jures)
        etat["version"] += 1
        for j in jures:
            out.append(_a(etat["j"][j]["nom"], {"type": "bng_jury", "id": etat["audit"]["id"]}))
    return out


def voter(etat, nom, message, maintenant, rng=random, config=None, connectes=None, ailleurs=None):
    """{"id": audit, "votes": {"<index>": "vu" | "pas_vu"}} : un ou plusieurs votes a la fois."""
    config = config or CONFIG
    a = etat["audit"]
    k = cle_nom(nom)
    if a is None or a["id"] != message.get("id"):
        return _erreur(nom, "Ce vote est terminé.")
    if k not in a["jures"]:
        return _erreur(nom, "Tu n'es pas dans ce jury.")
    votes = message.get("votes")
    if not isinstance(votes, dict):
        return _erreur(nom, "Vote illisible.")
    mien = a["votes"].setdefault(k, {})
    for i, v in votes.items():
        if v in ("vu", "pas_vu") and str(i).isdigit() and int(i) in a["cases"]:
            mien[str(int(i))] = v
    etat["version"] += 1
    if all(len(a["votes"].get(j, {})) == len(a["cases"]) for j in a["jures"]):
        return cloturer_audit(etat, maintenant, rng, config, connectes, ailleurs)
    return []


def cloturer_audit(etat, maintenant, rng=random, config=None, connectes=None, ailleurs=None, suivant=True):
    """Fin du vote (tous ont vote, ou temps ecoule : sans vote, c est « Vu »)."""
    config = config or CONFIG
    a = etat["audit"]
    if a is None:
        return []
    etat["audit"] = None
    etat["version"] += 1
    refusees = [x for x in a["cases"]
                if 2 * sum(1 for j in a["jures"] if a["votes"].get(j, {}).get(str(x)) == "pas_vu") > len(a["jures"])]
    out = _refuser(etat, a, refusees, maintenant, config, ailleurs) if refusees else _valider(etat, a, maintenant, config)
    if suivant:
        out += demarrer_audit(etat, maintenant, rng, config, connectes, ailleurs)
    return out


def _g(etat, config, base):
    return max(1, int(base * _v(etat, config)["mult_gorgees"] + 0.5))


def gorgees_heure(p, maintenant):
    return sum(n for t, n in p["gorgees"] if maintenant - t < 3600)


def _valider(etat, annonce, maintenant, config):
    p = etat["j"].get(annonce["joueur"])
    if p is None:
        return []
    for x in annonce["cases"]:
        c = p["coches"].get(str(x))
        if c:
            c["valide"] = True
    gagne = 0
    for i in annonce["lignes"]:
        if i not in p["lignes"]:
            p["lignes"].append(i)
            gagne += config["points"]["ligne"]
    bonus = annonce["grille"] and not p["grille_validee"]
    if bonus:
        # La grille pleine valide aussi tous ses alignements.
        p["grille_validee"] = True
        gagne += config["points"]["grille"]
        for i in range(len(lignes_de(_taille(p)))):
            if i not in p["lignes"]:
                p["lignes"].append(i)
    if gagne:
        p["points"] += gagne
        p["score_a"] = maintenant
    base = config["gorgees"]["grille_distribue"] if bonus else config["gorgees"]["ligne_distribue"] * max(1, len(annonce["lignes"]))
    g = _g(etat, config, base)
    unite = unite_effective(etat)
    quoi = "GRILLE PLEINE" if bonus else ("BINGO" if len(annonce["lignes"]) <= 1 else f"BINGO ({len(annonce['lignes'])} lignes)")
    _journal(etat, f"{quoi} validé pour {p['nom']} : {g} {unite}{'s' if g > 1 else ''} à distribuer.", maintenant, config)
    etat["version"] += 1
    return [_a(p["nom"], {"type": "bng_resultat", "ok": True, "grille": bonus, "points": gagne,
                          "distribue": g, "unite": unite})]


def _refuser(etat, annonce, refusees, maintenant, config, ailleurs):
    p = etat["j"].get(annonce["joueur"])
    if p is None:
        return []
    textes = []
    for x in refusees:
        p["coches"].pop(str(x), None)
        textes.append(p["grille"][x]["texte"])
    for x in annonce["cases"]:
        c = p["coches"].get(str(x))
        if c:
            c["fige"] = False
    p["refus"] += 1
    nombre = _g(etat, config, config["gorgees"]["refus_boit"])
    # Plafond commun : les gorgees du bingo et des autres jeux (Enquete) s additionnent.
    plafond = plafond_effectif(etat)
    bu = nombre
    if plafond:
        deja = gorgees_heure(p, maintenant) + (ailleurs(p["nom"], maintenant) if ailleurs else 0)
        bu = max(0, min(nombre, plafond - deja))
    if bu:
        p["gorgees"].append([maintenant, bu])
    handicap = (nombre - bu) * config["gorgees"]["handicap_par_gorgee"]
    p["refus_jusqua"] = maintenant + _v(etat, config)["delai_refus"] + handicap
    _journal(etat, f"BINGO de {p['nom']} refusé par le jury.", maintenant, config)
    etat["version"] += 1
    return [_a(p["nom"], {"type": "bng_resultat", "ok": False, "refusees": textes, "boit": bu,
                          "unite": unite_effective(etat), "handicap": handicap,
                          "jusqua": p["refus_jusqua"]})]


# =============================================================
# TEMPS
# =============================================================

def prochaine_echeance(etat):
    if etat["phase"] != "jeu":
        return None
    candidats = [etat["fin_a"]]
    if etat["audit"]:
        candidats.append(etat["audit"]["echeance"])
    return min(x for x in candidats if x is not None)


def expirer(etat, maintenant, rng=random, config=None, connectes=None, ailleurs=None):
    config = config or CONFIG
    out = []
    if etat["phase"] != "jeu":
        return out
    if etat["audit"] and maintenant >= etat["audit"]["echeance"]:
        out += cloturer_audit(etat, maintenant, rng, config, connectes, ailleurs)
    if etat["fin_a"] and maintenant >= etat["fin_a"]:
        out += terminer(etat, maintenant, rng, config, connectes, ailleurs, "heure")
    return out


# =============================================================
# VUES (jamais la grille d un autre joueur)
# =============================================================

def vue_publique(etat, maintenant, config=None):
    config = config or CONFIG
    a = etat["audit"]
    c = etat.get("commun") or {}
    return {
        "type": "bng_etat",
        "phase": etat["phase"],
        "hote": etat["hote"],
        "maintenant": maintenant,
        "vitesse": etat["vitesse"],
        "vitesses": {k: {"titre": x["titre"], "duree": x["duree"], "taille": x["taille"]}
                     for k, x in config["vitesses"].items()},
        "durees": config["durees"],
        "duree": etat["duree"],
        "taille": _v(etat, config)["taille"],
        "unite": unite_effective(etat),
        "plafond": plafond_effectif(etat),
        "plafonds": config["gorgees"]["plafonds"],
        "commun_enquete": bool(c.get("enquete")),
        "enquete_fin_a": c.get("fin_a") if c.get("enquete") else None,
        "nb_perso": len(etat["perso"]),
        "perso_max": config["perso"]["max"],
        "perso_longueur": config["perso"]["longueur"],
        "joueurs_min": config["joueurs_min"],
        "debut_a": etat["debut_a"],
        "fin_a": etat["fin_a"],
        "fin_calee": etat["fin_calee"],
        "joueurs": list(etat["joueurs"]),
        "classement": classement(etat),
        "audit": {"joueur": etat["j"][a["joueur"]]["nom"], "echeance": a["echeance"]} if a and a["joueur"] in etat["j"] else None,
        "file": len(etat["file"]),
        "journal": etat["journal"][-8:],
        "resultat": etat["resultat"],
    }


def vue_privee(etat, nom, maintenant, config=None):
    """Meme forme pour tous ; la grille et les votes ne vont qu a leur proprietaire."""
    config = config or CONFIG
    k = cle_nom(nom)
    p = etat["j"].get(k)
    a = etat["audit"]
    hote = k == cle_nom(etat["hote"])
    jury = None
    if a and k in a["jures"] and a["joueur"] in etat["j"]:
        cible = etat["j"][a["joueur"]]
        jury = {"id": a["id"], "joueur": cible["nom"], "echeance": a["echeance"],
                "cases": [{"index": x, "texte": cible["grille"][x]["texte"]} for x in a["cases"]],
                "votes": dict(a["votes"].get(k, {}))}
    mon_audit = None
    if p is not None:
        if a and a["joueur"] == k:
            mon_audit = {"etat": "en_cours", "echeance": a["echeance"], "position": 0}
        else:
            pos = next((i + 1 for i, x in enumerate(etat["file"]) if x["joueur"] == k), None)
            if pos:
                mon_audit = {"etat": "file", "echeance": None, "position": pos}
    delai = _v(etat, config)["delai_cochage"]
    return {
        "type": "bng_moi",
        "inscrit": p is not None,
        "hote": hote,
        "perso": list(etat["perso"]) if hote else [],
        "taille": _taille(p) if p and p["grille"] else _v(etat, config)["taille"],
        "grille": [{"texte": c["texte"], "categorie": c["categorie"], "rare": c["rarete"] == "rare"}
                   for c in p["grille"]] if p else [],
        "coches": [{"index": int(i), "t": c["t"], "statut": _statut(c, maintenant, config)}
                   for i, c in sorted(p["coches"].items(), key=lambda x: int(x[0]))] if p else [],
        "lignes": list(p["lignes"]) if p else [],
        "annonce": annonce_possible(etat, p) if p else None,
        "points": p["points"] if p else 0,
        "refus_jusqua": p["refus_jusqua"] if p else None,
        "prochain_cochage_a": (p["dernier_cochage"] + delai) if p and p["dernier_cochage"] else None,
        "annulation": config["annulation"],
        "gorgees_heure": gorgees_heure(p, maintenant) if p else 0,
        "jury": jury,
        "mon_audit": mon_audit,
    }
