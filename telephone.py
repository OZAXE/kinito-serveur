"""
Moteur de regles du Telephone Dessine (on ecrit, on dessine, on devine).

Meme principe que undercover.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
td_etat et la tache de chacun (td_tache) apres chaque action acceptee, et
arme le minuteur sur td_prochaine_echeance. Chiffres : telephone_config.json.

Deroule (mode Normal) :
  - Etape 0, « phrase » : chacun ecrit une phrase de depart (source libre ou
    mixte), ou la banque la fournit (source banque : l etape est sautee).
    Il y a autant d albums que de joueurs ; l album i commence par la phrase
    du joueur i.
  - Etape k >= 1 : le joueur i travaille sur l album (i + k) % N. Etapes
    impaires : il dessine la phrase precedente ; paires : il decrit le
    dessin precedent. Il ne voit QUE la contribution precedente.
  - Une etape finit quand tous les presents ont envoye, ou a l echeance
    (+ un delai de grace pour l envoi automatique des pages). Qui n a rien
    envoye : son brouillon, sinon une page vide ou "...".
  - Revelation (lancee par l hote) : album par album, contribution par
    contribution ; l hote avance (ou active l automatique). Fin d album :
    vote anonyme du « meilleur moment » (pas pour soi), puis resultat.
  - Vote final : « le dessin le plus mal compris ». Puis fin de partie.

Knock-Off : une seule phrase de la banque ouvre tous les albums ; on ne fait
que dessiner. A partir de la 2e etape, le modele (dessin precedent) n est
montre que pendant le temps de visionnage, puis on le recopie de memoire,
avec un peu moins de temps a chaque etape. Jusqu a N etapes.
Speedrun : comme Normal, minuteurs tres courts et moins d etapes par defaut ;
a chaque etape le serveur tire au hasard l album de chacun (jamais un album
deja touche), au lieu de la rotation (i + k) % N.

Gorgees (x multiplicateur de la vitesse, pas de plafond) :
  - contribution la plus votee d un album : son auteur distribue ;
  - la plus votee de toute la partie : son auteur distribue ;
  - dessin le plus mal compris : son auteur boit ;
  - contribution pas envoyee a temps (joueur present) : il boit.

Secret : td_etat n a jamais le contenu d un album avant sa revelation (seul
l album courant, jusqu a la contribution devoilee) ; td_tache ne donne a
chacun que la contribution precedente de son album du moment.
"""
import json
import os
import random
import re

from telephone_phrases import PHRASES

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "telephone_config.json"), encoding="utf-8") as _f:
    TD_CONFIG = json.load(_f)

TD_JOUEURS_MIN = TD_CONFIG["joueurs"]["min"]
TD_JOUEURS_MAX = TD_CONFIG["joueurs"]["max"]
GOMME = TD_CONFIG["dessin"]["couleurs"]      # indice de couleur de la gomme

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_VAL = {c: i for i, c in enumerate(_B64)}
_RE_POINTS = re.compile(r"^[A-Za-z0-9_-]+$")
_RE_CONTROLE = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")


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


def _presents(etat):
    return [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]


def _arrondi(x):
    return int(x + 0.5)


def _dose(etat, base):
    if base <= 0:
        return 0
    return max(1, _arrondi(base * TD_CONFIG["vitesses"][etat["vitesse"]]["multiplicateur"]))


def nettoyer_texte(v):
    """Texte d une phrase : une ligne, sans caracteres de controle, 80 max."""
    if not isinstance(v, str):
        return None
    t = _RE_CONTROLE.sub(" ", v)
    t = " ".join(t.split())
    return t[:TD_CONFIG["texte_max"]].strip()


def decoder_points(p):
    """'AbCd...' -> [(x, y), ...] : chaque coordonnee tient sur 2 caracteres."""
    vals = [_VAL[p[k]] * 64 + _VAL[p[k + 1]] for k in range(0, len(p), 2)]
    return list(zip(vals[0::2], vals[1::2]))


def valider_dessin(v):
    """
    Un dessin = une liste de traits {"c": couleur (0..7, 8 = gomme),
    "e": epaisseur (0..2), "p": points encodes}. Renvoie le dessin propre,
    ou None s il est invalide (trop gros, hors du cadre, mal forme).
    """
    d = TD_CONFIG["dessin"]
    if not isinstance(v, list) or len(v) > d["traits_max"]:
        return None
    total = 0
    propre = []
    for t in v:
        if not isinstance(t, dict):
            return None
        c, e, p = _entier(t.get("c")), _entier(t.get("e")), t.get("p")
        if c is None or not 0 <= c <= GOMME or e is None or not 0 <= e < len(d["epaisseurs"]):
            return None
        if not isinstance(p, str) or not p or len(p) % 4 or not _RE_POINTS.match(p):
            return None
        total += len(p) // 4
        if total > d["points_max"]:
            return None
        for x, y in decoder_points(p):
            if x > d["largeur"] or y > d["hauteur"]:
                return None
        propre.append({"c": c, "e": e, "p": p})
    return propre


def _contenu_valide(nature, v):
    if nature == "dessin":
        return valider_dessin(v)
    return nettoyer_texte(v)


def _defaut(nature):
    return [] if nature == "dessin" else TD_CONFIG["texte_vide"]


def nature_etape(k, mode="normal"):
    if k == 0:
        return "phrase"
    if mode == "knockoff":
        return "dessin"              # on ne fait que recopier des dessins
    return "dessin" if k % 2 else "ecriture"


def _nature(etat, k=None):
    return nature_etape(etat["etape"] if k is None else k, etat["mode"])


def album_de(etat, i, k=None):
    k = etat["etape"] if k is None else k
    aff = etat.get("affectations") or []
    if k < len(aff) and aff[k] is not None:
        return aff[k][i]              # Speedrun : repartition tiree au hasard
    return (i + k) % etat["nb_joueurs"]


def _repartir(etat, rng=random):
    """
    Speedrun : chaque joueur sur un album different, jamais un album qu il a
    deja touche. Un tel choix existe toujours (rectangle latin : chaque
    joueur et chaque album ont encore N - k partenaires possibles) ; on le
    cherche au hasard, avec retour en arriere (8 joueurs au plus).
    """
    n = etat["nb_joueurs"]
    deja = [{c["auteur"] for c in album if c["auteur"] is not None} for album in etat["albums"]]
    ordre = list(range(n))
    rng.shuffle(ordre)
    choix = [None] * n
    pris = set()

    def placer(r):
        if r == n:
            return True
        i = ordre[r]
        albums = [a for a in range(n) if a not in pris and i not in deja[a]]
        rng.shuffle(albums)
        for a in albums:
            choix[i] = a
            pris.add(a)
            if placer(r + 1):
                return True
            pris.discard(a)
        return False

    if not placer(0):                 # impossible en theorie : rotation classique
        k = etat["etape"]
        return [(i + k) % n for i in range(n)]
    return choix


def _tirer_phrases(etat, n, rng=random):
    """n phrases de la banque, en evitant celles deja sorties dans le salon."""
    deja = set(etat["deja"])
    libres = [p for p in PHRASES if p not in deja]
    if len(libres) < n:
        etat["deja"] = []
        libres = list(PHRASES)
    choix = rng.sample(libres, n)
    etat["deja"] = (etat["deja"] + choix)[-len(PHRASES) // 2:]
    return choix


# =============================================================
# INITIALISATION
# =============================================================
def td_initialiser(noms, maintenant, hote=0, vitesse=None, source=None, etapes=None,
                   mode=None, deja=None, rng=random):
    n = len(noms)
    vitesse = vitesse if vitesse in TD_CONFIG["vitesses"] else TD_CONFIG["vitesse_defaut"]
    source = source if source in TD_CONFIG["sources"] else TD_CONFIG["source_defaut"]
    mode = mode if mode in TD_CONFIG["modes"] else TD_CONFIG["modes"][0]
    if mode == "knockoff":
        source = "banque"            # tout le monde part de la MEME phrase de la banque
    # Knock-Off : la phrase vient de la banque, chacun peut toucher chaque album (N copies).
    maxi = n if mode == "knockoff" else n - 1
    if mode == "speedrun":
        defaut = min(maxi, TD_CONFIG["speedrun"]["etapes_defaut"])
    else:
        defaut = maxi
    nb = _entier(etapes)
    nb = defaut if nb is None or not 1 <= nb <= maxi else nb
    hote = hote if isinstance(hote, int) and 0 <= hote < n else 0
    etat = {
        "version": 0,
        "mode": mode,
        "vitesse": vitesse,
        "source": source,
        "nb_joueurs": n,
        "noms": list(noms),
        "hote": hote,
        "partis": [False] * n,
        "nb_etapes": nb,
        "phase": "etape",
        "etape": 0,
        "fin_etape": None,       # echeance affichee aux joueurs
        "echeance": None,        # echeance du serveur (avec la grace)
        "albums": [[] for _ in range(n)],
        "affectations": [],      # Speedrun : album de chaque joueur, par etape
        "fin_vision": None,      # Knock-Off : le modele disparait a cet instant
        "envoyes": [False] * n,
        "brouillons": [None] * n,
        "suggestions": [None] * n,
        "retards": [0] * n,
        "deja": list(deja or []),
        "revelation": None,      # {"album", "montre", "auto"}
        "votes": {},
        "resultats": [],         # un par album : {"album", "votes", "gagnants"}
        "votes_final": {},
        "final": None,
        "gorgees": [],           # {"joueur", "n", "sens", "raison"}
        "derniere_reaction": [0.0] * n,
        "fin_signalee": False,
    }
    if mode == "knockoff":
        phrase = _tirer_phrases(etat, 1, rng)[0]
        for a in range(n):
            etat["albums"][a].append({"auteur": None, "type": "texte", "contenu": phrase, "auto": False})
        _ouvrir_etape(etat, 1, maintenant, rng)
    elif source == "banque":
        for a, phrase in enumerate(_tirer_phrases(etat, n, rng)):
            etat["albums"][a].append({"auteur": None, "type": "texte", "contenu": phrase, "auto": False})
        _ouvrir_etape(etat, 1, maintenant, rng)
    else:
        _ouvrir_etape(etat, 0, maintenant, rng)
    return etat


def _duree(etat, nature, k=None):
    v = TD_CONFIG["vitesses"][etat["vitesse"]]
    if etat["mode"] == "speedrun":
        return v["speedrun_" + nature]
    if etat["mode"] == "knockoff" and k is not None and k >= 2:
        ko = TD_CONFIG["knockoff"]
        return max(ko["minimum"], round(v["dessin"] * ko["decroissance"] ** (k - 1)))
    return v[nature]


def _vision(etat, k):
    """Knock-Off, a partir de la 2e etape : temps pour regarder le modele."""
    if etat["mode"] == "knockoff" and k >= 2:
        return TD_CONFIG["vitesses"][etat["vitesse"]]["visionnage"]
    return 0


def _ouvrir_etape(etat, k, maintenant, rng=random):
    n = etat["nb_joueurs"]
    etat["phase"] = "etape"
    etat["etape"] = k
    etat["envoyes"] = [False] * n
    etat["brouillons"] = [None] * n
    etat["suggestions"] = [None] * n
    if k == 0:
        etat["suggestions"] = _tirer_phrases(etat, n, rng)
    if etat["mode"] == "speedrun":
        etat["affectations"] += [None] * (k + 1 - len(etat["affectations"]))
        etat["affectations"][k] = _repartir(etat, rng)
    vision = _vision(etat, k)
    etat["fin_vision"] = maintenant + vision if vision else None
    etat["fin_etape"] = maintenant + vision + _duree(etat, _nature(etat, k), k)
    etat["echeance"] = etat["fin_etape"] + TD_CONFIG["delai_grace"]
    _accelerer_si_complet(etat, maintenant)


def _accelerer_si_complet(etat, maintenant):
    """Plus personne de present a attendre : on n attend pas l echeance."""
    if etat["phase"] == "etape":
        attendus = [i for i in _presents(etat) if not etat["envoyes"][i]]
    elif etat["phase"] in ("vote_album", "vote_final"):
        attendus = _votants_attendus(etat)
    else:
        return
    if not attendus:
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + TD_CONFIG["delai_absent"])


# =============================================================
# ETAT PUBLIC ET TACHES PRIVEES
# =============================================================
def _total_gorgees(etat):
    n = etat["nb_joueurs"]
    boit, distribue = [0] * n, [0] * n
    for g in etat["gorgees"]:
        (boit if g["sens"] == "boit" else distribue)[g["joueur"]] += g["n"]
    return {"boit": boit, "distribue": distribue}


def td_etat_public(etat, maintenant):
    phase = etat["phase"]
    reste = None
    if phase == "etape" and etat["fin_etape"] is not None:
        reste = max(0, round(etat["fin_etape"] - maintenant))
    elif etat["echeance"] is not None and phase in ("vote_album", "vote_final"):
        reste = max(0, round(etat["echeance"] - maintenant))
    rev = None
    if phase in ("revelation", "vote_album", "resultat_album") and etat["revelation"]:
        r = etat["revelation"]
        album = etat["albums"][r["album"]]
        rev = {
            "album": r["album"],
            "nb_albums": etat["nb_joueurs"],
            "longueur": len(album),
            "montre": r["montre"],
            "auto": r["auto"],
            # Seulement ce qui est deja devoile : jamais la suite de l album.
            "contributions": [dict(c, index=k) for k, c in enumerate(album[:r["montre"]])],
        }
    a_vote = None
    if phase == "vote_album":
        a_vote = [str(i) in etat["votes"] for i in range(etat["nb_joueurs"])]
    elif phase == "vote_final":
        a_vote = [str(i) in etat["votes_final"] for i in range(etat["nb_joueurs"])]
    return {
        "type": "td_etat",
        "version": etat["version"],
        "mode": etat["mode"],
        "vitesse": etat["vitesse"],
        "source": etat["source"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "hote": etat["hote"],
        "phase": phase,
        "etape": etat["etape"],
        "nb_etapes": etat["nb_etapes"],
        "nature": _nature(etat) if phase == "etape" else None,
        "prets": etat["envoyes"] if phase == "etape" else None,
        "reste": reste,
        "revelation": rev,
        "a_vote": a_vote,
        "resultat": etat["resultats"][-1] if phase == "resultat_album" and etat["resultats"] else None,
        "resultats": etat["resultats"] if phase == "fin" else None,
        "final": etat["final"] if phase == "fin" else None,
        "candidats": _candidats_final(etat) if phase == "vote_final" else None,
        "gorgees": _total_gorgees(etat),
        "regles": {
            "dessin": TD_CONFIG["dessin"],
            "texte_max": TD_CONFIG["texte_max"],
            "reactions": TD_CONFIG["reactions"],
        },
    }


def td_tache(etat, i, maintenant=None):
    """
    Ce que le joueur i doit faire a l etape en cours (message prive).
    Knock-Off : le modele n est donne que pendant le temps de visionnage
    (une page qui revient apres ne le recoit plus : on copie de memoire).
    """
    if etat["phase"] != "etape" or not 0 <= i < etat["nb_joueurs"]:
        return None
    k = etat["etape"]
    nature = _nature(etat, k)
    a = album_de(etat, i)
    modele = etat["albums"][a][k - 1]["contenu"] if k >= 1 else None
    vision = None
    if etat.get("fin_vision") is not None:
        reste = etat["fin_vision"] - (maintenant if maintenant is not None else 0)
        vision = max(0, round(reste, 1)) if maintenant is not None else _vision(etat, k)
        if maintenant is not None and reste <= 0:
            modele = None
    return {
        "type": "td_tache",
        "version": etat["version"],
        "etape": k,
        "nature": nature,
        "modele": modele,
        "copie": etat["mode"] == "knockoff" and k >= 2,
        "vision": vision,
        "suggestion": etat["suggestions"][i] if k == 0 else None,
        "prerempli": k == 0 and etat["source"] == "mixte",
        "envoye": etat["envoyes"][i],
        "brouillon": etat["brouillons"][i],
    }


def td_galerie(etat):
    """Tous les albums : seulement une fois la revelation entierement passee."""
    if etat["phase"] not in ("vote_final", "fin"):
        return None
    return {"type": "td_galerie", "albums": etat["albums"]}


# =============================================================
# ETAPES
# =============================================================
def _contribuer(etat, ev, i, message, maintenant):
    if etat["phase"] != "etape":
        return _erreur(i, "Ce n'est pas le moment d'envoyer.")
    if etat["envoyes"][i]:
        return _erreur(i, "Tu as déjà envoyé ta contribution pour cette étape.")
    nature = _nature(etat)
    contenu = _contenu_valide(nature, message.get("contenu"))
    if contenu is None:
        return _erreur(i, "Contribution refusée (dessin trop chargé ou texte invalide).")
    if nature != "dessin" and not contenu:
        contenu = TD_CONFIG["texte_vide"]
    en_retard = message.get("auto") is True
    _enregistrer(etat, ev, i, contenu, en_retard)
    if all(etat["envoyes"][j] or etat["partis"][j] for j in range(etat["nb_joueurs"])):
        _clore_etape(etat, ev, maintenant)
    return ev


def _enregistrer(etat, ev, i, contenu, en_retard):
    k = etat["etape"]
    a = album_de(etat, i)
    album = etat["albums"][a]
    if len(album) != k:          # garde-fou : une seule contribution par etape et par album
        return
    nature = _nature(etat, k)
    album.append({"auteur": i, "type": "dessin" if nature == "dessin" else "texte",
                  "contenu": contenu, "auto": en_retard})
    etat["envoyes"][i] = True
    etat["brouillons"][i] = None
    if en_retard and not etat["partis"][i]:
        etat["retards"][i] += 1
        n = _dose(etat, TD_CONFIG["gorgees"]["retard"])
        if n:
            g = {"joueur": i, "n": n, "sens": "boit", "raison": "retard"}
            etat["gorgees"].append(g)
            ev.append(_tous({"type": "td_gorgees", "liste": [g]}))


def _brouillon(etat, ev, i, message, maintenant):
    if etat["phase"] != "etape" or etat["envoyes"][i]:
        return []
    contenu = _contenu_valide(_nature(etat), message.get("contenu"))
    if contenu is not None:
        etat["brouillons"][i] = contenu
    return []                    # rien a diffuser


def _suggestion(etat, ev, i, message, maintenant):
    if etat["phase"] != "etape" or etat["etape"] != 0 or etat["envoyes"][i]:
        return []
    etat["suggestions"][i] = random.choice(PHRASES)
    return [_prive(i, {"type": "td_suggestion", "suggestion": etat["suggestions"][i]})]


def _clore_etape(etat, ev, maintenant):
    """Remplit ce qui manque (brouillon, sinon vide), puis etape suivante."""
    k = etat["etape"]
    nature = _nature(etat, k)
    for i in range(etat["nb_joueurs"]):
        if etat["envoyes"][i]:
            continue
        contenu = etat["brouillons"][i]
        if contenu is None or (nature != "dessin" and not contenu):
            if k == 0 and etat["source"] == "mixte" and etat["suggestions"][i]:
                contenu = etat["suggestions"][i]
            else:
                contenu = _defaut(nature)
        _enregistrer(etat, ev, i, contenu, True)
    if k >= etat["nb_etapes"]:
        etat["phase"] = "pret"
        etat["fin_etape"] = None
        etat["echeance"] = None
        etat["brouillons"] = [None] * etat["nb_joueurs"]
        ev.append(_tous({"type": "td_albums_prets"}))
    else:
        _ouvrir_etape(etat, k + 1, maintenant)


# =============================================================
# REVELATION ET VOTES
# =============================================================
def _hote_seul(etat, i):
    return i == etat["hote"]


def _armer_auto(etat, maintenant):
    r = etat["revelation"]
    if etat["phase"] == "revelation" and r["auto"]:
        album = etat["albums"][r["album"]]
        dernier = album[r["montre"] - 1] if r["montre"] else None
        delai = TD_CONFIG["revelation"]["auto_dessin" if dernier and dernier["type"] == "dessin" else "auto_texte"]
        etat["echeance"] = maintenant + delai
    elif etat["phase"] == "resultat_album" and r["auto"]:
        etat["echeance"] = maintenant + TD_CONFIG["revelation"]["auto_resultat"]
    elif etat["phase"] in ("revelation", "resultat_album"):
        etat["echeance"] = None


def _reveler(etat, ev, i, message, maintenant):
    if etat["phase"] != "pret":
        return []
    if not _hote_seul(etat, i):
        return _erreur(i, "C'est l'hôte qui lance la révélation.")
    _ouvrir_album(etat, 0, maintenant, auto=message.get("auto") is True)
    return ev


def _ouvrir_album(etat, a, maintenant, auto=None):
    ancien = etat["revelation"]
    etat["phase"] = "revelation"
    etat["revelation"] = {"album": a, "montre": 1,
                          "auto": ancien["auto"] if auto is None and ancien else bool(auto)}
    etat["votes"] = {}
    _armer_auto(etat, maintenant)


def _avancer(etat, ev, maintenant):
    r = etat["revelation"]
    if etat["phase"] == "revelation":
        if r["montre"] < len(etat["albums"][r["album"]]):
            r["montre"] += 1
            _armer_auto(etat, maintenant)
        else:
            etat["phase"] = "vote_album"
            etat["votes"] = {}
            etat["echeance"] = maintenant + TD_CONFIG["revelation"]["vote_album"]
            _accelerer_si_complet(etat, maintenant)
    elif etat["phase"] == "resultat_album":
        if r["album"] + 1 < etat["nb_joueurs"]:
            _ouvrir_album(etat, r["album"] + 1, maintenant)
        else:
            etat["phase"] = "vote_final"
            etat["votes_final"] = {}
            etat["echeance"] = maintenant + TD_CONFIG["revelation"]["vote_final"]
            _accelerer_si_complet(etat, maintenant)


def _suivant(etat, ev, i, message, maintenant):
    if etat["phase"] not in ("revelation", "resultat_album"):
        return []
    if not _hote_seul(etat, i):
        return _erreur(i, "C'est l'hôte qui fait défiler la révélation.")
    _avancer(etat, ev, maintenant)
    return ev


def _auto(etat, ev, i, message, maintenant):
    if etat["phase"] not in ("revelation", "vote_album", "resultat_album") or not _hote_seul(etat, i):
        return []
    etat["revelation"]["auto"] = message.get("actif") is True
    _armer_auto(etat, maintenant)
    return ev


def _candidats_album(etat, i):
    """Contributions de l album courant pour lesquelles i peut voter."""
    album = etat["albums"][etat["revelation"]["album"]]
    return [k for k, c in enumerate(album) if c["auteur"] is not None and c["auteur"] != i]


def _candidats_final(etat):
    return [[a, k] for a, album in enumerate(etat["albums"])
            for k, c in enumerate(album) if c["type"] == "dessin" and c["auteur"] is not None]


def _votants_attendus(etat):
    if etat["phase"] == "vote_album":
        return [i for i in _presents(etat) if str(i) not in etat["votes"] and _candidats_album(etat, i)]
    if etat["phase"] == "vote_final":
        cands = _candidats_final(etat)
        return [i for i in _presents(etat) if str(i) not in etat["votes_final"]
                and any(etat["albums"][a][k]["auteur"] != i for a, k in cands)]
    return []


def _voter(etat, ev, i, message, maintenant):
    if etat["phase"] == "vote_album":
        k = _entier(message.get("cible"))
        if k is None or k not in _candidats_album(etat, i):
            return _erreur(i, "Vote impossible (on ne vote pas pour soi).")
        etat["votes"][str(i)] = k
    elif etat["phase"] == "vote_final":
        a, k = _entier(message.get("album")), _entier(message.get("cible"))
        if [a, k] not in _candidats_final(etat) or etat["albums"][a][k]["auteur"] == i:
            return _erreur(i, "Vote impossible (on ne vote pas pour son propre dessin).")
        etat["votes_final"][str(i)] = [a, k]
    else:
        return []
    if not _votants_attendus(etat):
        _depouiller(etat, ev, maintenant)
    return ev


def _gagnants(compte):
    if not compte:
        return []
    meilleur = max(compte.values())
    return sorted(c for c, n in compte.items() if n == meilleur)


def _donner(etat, joueur, base, sens, raison, liste):
    n = _dose(etat, base)
    if n and joueur is not None:
        g = {"joueur": joueur, "n": n, "sens": sens, "raison": raison}
        etat["gorgees"].append(g)
        liste.append(g)


def _depouiller(etat, ev, maintenant):
    liste = []
    if etat["phase"] == "vote_album":
        a = etat["revelation"]["album"]
        compte = {}
        for k in etat["votes"].values():
            compte[k] = compte.get(k, 0) + 1
        gagnants = _gagnants(compte)
        for k in gagnants:
            _donner(etat, etat["albums"][a][k]["auteur"], TD_CONFIG["gorgees"]["meilleur_album"],
                    "distribue", "meilleur_album", liste)
        # Anonyme : seulement le nombre de voix par contribution, jamais qui a vote quoi.
        etat["resultats"].append({"album": a, "votes": {str(k): n for k, n in sorted(compte.items())},
                                  "gagnants": gagnants})
        etat["votes"] = {}
        etat["phase"] = "resultat_album"
        _armer_auto(etat, maintenant)
    elif etat["phase"] == "vote_final":
        compte = {}
        for a, k in etat["votes_final"].values():
            compte[(a, k)] = compte.get((a, k), 0) + 1
        mal = _gagnants(compte)
        for a, k in mal:
            _donner(etat, etat["albums"][a][k]["auteur"], TD_CONFIG["gorgees"]["mal_compris"],
                    "boit", "mal_compris", liste)
        # La plus votee de la partie : sur l ensemble des votes d album.
        partie = {}
        for r in etat["resultats"]:
            for k, n in r["votes"].items():
                partie[(r["album"], int(k))] = n
        meilleurs = _gagnants(partie)
        for a, k in meilleurs:
            _donner(etat, etat["albums"][a][k]["auteur"], TD_CONFIG["gorgees"]["meilleur_soiree"],
                    "distribue", "meilleur_soiree", liste)
        etat["final"] = {
            "mal_compris": [list(x) for x in mal],
            "votes_mal_compris": [[a, k, n] for (a, k), n in sorted(compte.items())],
            "meilleurs": [list(x) for x in meilleurs],
            "voix_meilleurs": partie[meilleurs[0]] if meilleurs else 0,
            "retards": etat["retards"],
        }
        etat["votes_final"] = {}
        etat["phase"] = "fin"
        etat["echeance"] = None
    if liste:
        ev.append(_tous({"type": "td_gorgees", "liste": liste}))


def _reaction(etat, ev, i, message, maintenant):
    if etat["phase"] not in ("revelation", "vote_album", "resultat_album", "vote_final", "fin"):
        return []
    emoji = message.get("emoji")
    if emoji not in TD_CONFIG["reactions"]:
        return []
    if maintenant - etat["derniere_reaction"][i] < TD_CONFIG["reaction_intervalle"]:
        return []
    etat["derniere_reaction"][i] = maintenant
    # Pas de changement de version : la reaction passe, l etat ne bouge pas.
    return [_tous({"type": "td_reaction", "emoji": emoji, "joueur": i})]


TD_ACTIONS = {
    "td_contribuer": _contribuer,
    "td_brouillon": _brouillon,
    "td_suggestion": _suggestion,
    "td_reveler": _reveler,
    "td_suivant": _suivant,
    "td_auto": _auto,
    "td_voter": _voter,
    "td_reaction": _reaction,
}


def td_action(etat, i, message, maintenant):
    fn = TD_ACTIONS.get(message.get("type"))
    if fn is None or not isinstance(i, int) or not 0 <= i < etat["nb_joueurs"]:
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
def td_prochaine_echeance(etat):
    return etat["echeance"]


def td_expirer(etat, maintenant):
    """L echeance est passee : on avance a la place de ceux qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    phase = etat["phase"]
    if phase == "etape":
        _clore_etape(etat, ev, maintenant)
    elif phase in ("vote_album", "vote_final"):
        _depouiller(etat, ev, maintenant)
    elif phase in ("revelation", "resultat_album"):
        _avancer(etat, ev, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def td_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    if etat["hote"] == index:
        presents = _presents(etat)
        if presents:
            etat["hote"] = presents[0]
    etat["version"] += 1
    _accelerer_si_complet(etat, maintenant)


def td_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    if etat["partis"][etat["hote"]]:
        etat["hote"] = index
    etat["version"] += 1
    phase = etat["phase"]
    # Un revenant qui a encore quelque chose a faire a un minimum de temps.
    if phase == "etape" and not etat["envoyes"][index]:
        etat["fin_etape"] = max(etat["fin_etape"] or 0, maintenant + TD_CONFIG["revelation"]["retour_vote"])
        etat["echeance"] = etat["fin_etape"] + TD_CONFIG["delai_grace"]
    elif phase in ("vote_album", "vote_final") and index in _votants_attendus(etat):
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + TD_CONFIG["revelation"]["retour_vote"])
