"""
Moteur de regles du Loup-Garou (deduction sociale, roles caches, prefixe lg_).

Meme principe que quidenous.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
lg_etat (public) apres chaque action acceptee, puis a chacun sa vue secrete
(lg_secret), et arme le minuteur sur lg_prochaine_echeance. Chiffres :
loupgarou_config.json.

Version jouee sur les telephones, sans meneur : le serveur fait le conteur.

Roles :
  - Loup-Garou : chaque nuit, la meute choisit une victime. Les loups voient
    leurs complices et les choix de la meute en direct.
  - Villageois : aucun pouvoir, son vote de jour.
  - Cartomancienne : chaque nuit, decouvre le role d un joueur.
  - Videur : chaque nuit, protege un joueur des loups (lui compris), jamais
    le meme deux nuits de suite.
  - Herboriste : une potion de soin (sauve la victime des loups) et une
    potion de poison (tue un joueur), chacune une fois par partie.
  - Braconnier : quand il meurt, il emporte un joueur de son choix.

Deroulement :
  - « role » : chacun decouvre son role en secret et touche « Pret ».
  - « nuit » : TOUT LE MONDE en vie touche un joueur, pour que personne ne
    se trahisse a la table. Les loups votent leur victime, la
    Cartomancienne sonde, le Videur protege, les autres designent leur
    suspect (le « flair », compte en fin de partie). La nuit finit quand
    tous ont agi (les loups d accord entre eux) ou a l echeance : la
    victime est le choix majoritaire de la meute (egalite : le sort).
  - « potion » : seulement si l Herboriste est dans la partie. Elle voit la
    victime et decide. Les autres attendent (« la nuit continue »), aussi
    longtemps si elle est morte : on ne devine rien a la duree.
  - « aube » : les morts de la nuit et leur role. Un Braconnier mort tire
    (« tir »). Puis « debat » : chacun vote en secret (ou blanc), modifiable
    tant que tous n ont pas vote. Le plus designe est elimine (egalite ou
    blanc majoritaire : personne), « verdict » montre qui a vote quoi.
  - Le village gagne quand il n y a plus de loup ; les loups gagnent des
    qu ils sont au moins aussi nombreux que les autres vivants.

Gorgees : mort la nuit 2, abattu par le Braconnier 2, elimine au vote 2
(loup demasque 3), chaque accusateur d un innocent elimine 1, et en fin de
partie chaque joueur du camp perdant 2 (seules celles de la fin partent dans
signaler). Les morts voient tous les roles (et se taisent).
"""
import json
import os
import random

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "loupgarou_config.json"), encoding="utf-8") as _f:
    LG_CONFIG = json.load(_f)

LG_JOUEURS_MIN = LG_CONFIG["joueurs"]["min"]
LG_JOUEURS_MAX = LG_CONFIG["joueurs"]["max"]
LG_SPECIAUX = list(LG_CONFIG["speciaux"]["ordre"])
LG_ROLES = ["loup", "villageois"] + LG_SPECIAUX
LG_NOMS_ROLES = {
    "loup": "Loup-Garou",
    "villageois": "Villageois",
    "cartomancienne": "Cartomancienne",
    "videur": "Videur",
    "herboriste": "Herboriste",
    "braconnier": "Braconnier",
}
_D = LG_CONFIG["delais"]
_G = LG_CONFIG["gorgees"]


# =============================================================
# OUTILS
# =============================================================
def _tous(msg):
    return {"a": None, "msg": msg}


def _prive(i, msg):
    return {"a": i, "msg": msg}


def _erreur(i, texte):
    return [_prive(i, {"type": "erreur", "message": texte})]


def _indice(v, n):
    if isinstance(v, bool):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if 0 <= v < n else None


def _vivants(etat):
    return [i for i in range(etat["nb_joueurs"]) if etat["vivants"][i]]


def _presents_vivants(etat):
    return [i for i in _vivants(etat) if not etat["partis"][i]]


def _loups_vivants(etat):
    return [i for i in _vivants(etat) if etat["roles"][i] == "loup"]


def _role_vivant(etat, role):
    for i in _vivants(etat):
        if etat["roles"][i] == role:
            return i
    return None


def lg_nb_loups_auto(n):
    return 1 + sum(1 for s in LG_CONFIG["loups"]["seuils"] if n > s)


def lg_loups_max(n):
    """Au plus un loup pour deux autres joueurs : sinon la partie est jouee d avance."""
    return max(1, (n - 1) // 2)


def lg_speciaux_auto(n):
    seuils = LG_CONFIG["speciaux"]["a_partir_de"]
    return [r for r in LG_SPECIAUX if n >= seuils.get(r, 99)]


def lg_composition(n, loups=None, speciaux=None):
    """
    Les roles de la partie (liste, pas encore melangee). `loups` : nombre ou
    None (automatique). `speciaux` : liste de roles speciaux ou None
    (automatique selon le nombre de joueurs). Les valeurs absurdes sont
    ramenees a quelque chose de jouable.
    """
    try:
        nb_loups = int(loups) if loups is not None and not isinstance(loups, bool) else None
    except (TypeError, ValueError, OverflowError):
        nb_loups = None
    if nb_loups is None:
        nb_loups = lg_nb_loups_auto(n)
    nb_loups = max(1, min(nb_loups, lg_loups_max(n)))
    if isinstance(speciaux, (list, tuple)):
        choisis = [r for r in LG_SPECIAUX if r in speciaux]
    else:
        choisis = lg_speciaux_auto(n)
    choisis = choisis[:max(0, n - nb_loups)]
    roles = ["loup"] * nb_loups + choisis
    roles += ["villageois"] * (n - len(roles))
    return roles


def _compte_roles(roles):
    compte = {}
    for r in LG_ROLES:
        k = roles.count(r)
        if k:
            compte[r] = k
    return compte


# =============================================================
# CREATION
# =============================================================
def lg_initialiser(noms, maintenant, loups=None, speciaux=None, alea=None):
    alea = alea or random
    n = len(noms)
    roles = lg_composition(n, loups, speciaux)
    alea.shuffle(roles)
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "roles": roles,                      # SECRET : jamais dans lg_etat tant que le joueur vit
        "composition": _compte_roles(roles),  # public, comme autour d une vraie table
        "vivants": [True] * n,
        "phase": "role",
        "nuit": 0,
        "jour": 0,
        "prets": [False] * n,
        # Nuit en cours (SECRET)
        "meute": {},                         # str(loup) -> cible
        "agi": [False] * n,
        "actions": {},                       # str(joueur) -> cible (sonde, protection, suspect)
        "visions": [],                       # [{nuit, cible, role}] de la Cartomancienne
        "protege": None,
        "dernier_protege": None,
        "victime_loups": None,               # choix de la meute, avant protection
        "victime": None,                     # victime des loups apres protection (vue par l Herboriste)
        "potions": {"soin": True, "poison": True},
        "potion": None,                      # choix de cette nuit : {"soin", "poison"}
        "suspects": [0] * n,                 # flair : nuits passees a soupconner un loup
        # Jour
        "votes": {},                         # str(joueur) -> cible ou None (blanc) : SECRET jusqu au verdict
        "suivants": [False] * n,
        "annonce": None,                     # aube ou verdict : morts, votes
        "tirs": [],                          # Braconniers morts qui n ont pas encore tire
        "tireur": None,
        "retour": None,                      # phase a reprendre apres un tir
        "suite": None,                       # "debat" ou "nuit" apres l annonce
        "gorgees": [0] * n,
        "morts": [],                         # [{joueur, role, cause, nuit, jour}]
        "final": None,
        "echeance": None,
        "message": "Découvre ton rôle en secret.",
    }
    _armer(etat, maintenant, alea)
    return etat


# =============================================================
# ETAT PUBLIC ET VUES SECRETES
# =============================================================
def lg_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    n = etat["nb_joueurs"]
    phase = etat["phase"]
    fin = phase == "fin"
    attendus = _attendus(etat)
    concernes = _concernes(etat)
    return {
        "type": "lg_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "vivants": etat["vivants"],
        "phase": phase,
        "nuit": etat["nuit"],
        "jour": etat["jour"],
        "composition": etat["composition"],
        # Le role d un vivant reste secret jusqu a la fin.
        "roles": [etat["roles"][i] if (fin or not etat["vivants"][i]) else None for i in range(n)],
        "prets": etat["prets"] if phase == "role" else [False] * n,
        # Pendant le debat, on sait qui a vote, jamais pour qui.
        "a_vote": [str(i) in etat["votes"] for i in range(n)] if phase == "debat" else [False] * n,
        # La nuit, un simple compte : tout le monde agit, rien ne se devine.
        "attente": {"fait": len([i for i in concernes if i not in attendus]), "total": len(concernes)},
        "suivants": etat["suivants"],
        "annonce": etat["annonce"] if phase in ("aube", "verdict", "tir") else None,
        "tireur": etat["tireur"] if phase == "tir" else None,
        "potion_en_jeu": "herboriste" in etat["composition"],
        "gorgees": etat["gorgees"],
        "morts": etat["morts"],
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def lg_vue(etat, i):
    """Ce que le joueur i est seul a voir (son role, sa meute, ses visions...)."""
    n = etat["nb_joueurs"]
    if not 0 <= i < n:
        return None
    role = etat["roles"][i]
    vivant = etat["vivants"][i]
    phase = etat["phase"]
    fantome = (not vivant) or phase == "fin"
    vue = {
        "type": "lg_secret",
        "index": i,
        "role": role,
        "vivant": vivant,
        "loups": None,
        "meute": None,
        "action": None,
        "vote": None,
        "visions": None,
        "dernier_protege": None,
        "potions": None,
        "victime": None,
        "potion_faite": None,
        "tous_roles": list(etat["roles"]) if fantome else None,
    }
    if role == "loup" or fantome:
        vue["loups"] = [j for j in range(n) if etat["roles"][j] == "loup"]
    if role == "loup" and vivant and phase == "nuit":
        vue["meute"] = {k: v for k, v in etat["meute"].items()}
    if vivant and phase == "nuit":
        if role == "loup":
            vue["action"] = etat["meute"].get(str(i))
        else:
            vue["action"] = etat["actions"].get(str(i))
    if vivant and phase == "debat" and str(i) in etat["votes"]:
        vue["vote"] = {"cible": etat["votes"][str(i)]}
    if role == "cartomancienne":
        vue["visions"] = list(etat["visions"])
    if role == "videur":
        vue["dernier_protege"] = etat["dernier_protege"]
    if role == "herboriste":
        vue["potions"] = dict(etat["potions"])
        if phase == "potion" and vivant:
            vue["victime"] = etat["victime"]
            vue["potion_faite"] = etat["potion"] is not None
    return vue


# =============================================================
# DEROULEMENT
# =============================================================
def _camp(role):
    return "loups" if role == "loup" else "village"


def _vainqueur(etat):
    loups = len(_loups_vivants(etat))
    autres = len(_vivants(etat)) - loups
    if loups == 0:
        return "village"
    if loups >= autres:
        return "loups"
    return None


def _mourir(etat, j, cause, morts):
    if not etat["vivants"][j]:
        return
    etat["vivants"][j] = False
    gorgees = {"devore": _G["devore"], "empoisonne": _G["empoisonne"], "abattu": _G["abattu"],
               "elimine": _G["loup_demasque"] if etat["roles"][j] == "loup" else _G["elimine"]}.get(cause, 0)
    etat["gorgees"][j] += gorgees
    mort = {"joueur": j, "role": etat["roles"][j], "cause": cause, "boit": gorgees,
            "nuit": etat["nuit"], "jour": etat["jour"]}
    etat["morts"].append(mort)
    morts.append(mort)
    if etat["roles"][j] == "braconnier":
        etat["tirs"].append(j)


def _nouvelle_nuit(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    etat["nuit"] += 1
    etat["phase"] = "nuit"
    etat["meute"] = {}
    etat["actions"] = {}
    etat["agi"] = [False] * n
    etat["protege"] = None
    etat["victime"] = None
    etat["potion"] = None
    etat["votes"] = {}
    etat["suivants"] = [False] * n
    etat["annonce"] = None
    etat["message"] = f"Nuit {etat['nuit']} : le village s'endort, chacun touche un joueur."
    ev.append(_tous({"type": "lg_nuit", "nuit": etat["nuit"]}))
    _armer(etat, maintenant, alea)


def _choix_meute(etat, alea):
    """La victime : le choix majoritaire des loups vivants (egalite : le sort)."""
    compte = {}
    for k, cible in etat["meute"].items():
        j = int(k)
        if etat["vivants"][j] and etat["roles"][j] == "loup" and cible is not None and etat["vivants"][cible] \
                and etat["roles"][cible] != "loup":
            compte[cible] = compte.get(cible, 0) + 1
    if not compte:
        return None
    haut = max(compte.values())
    tete = sorted(c for c, v in compte.items() if v == haut)
    return tete[0] if len(tete) == 1 else alea.choice(tete)


def _fin_nuit(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    # Le flair : ceux qui soupconnaient un loup cette nuit.
    for k, cible in etat["actions"].items():
        j = int(k)
        if etat["roles"][j] in ("villageois", "herboriste", "braconnier") and cible is not None \
                and etat["roles"][cible] == "loup":
            etat["suspects"][j] += 1
    victime = _choix_meute(etat, alea)
    etat["victime_loups"] = victime
    if victime is not None and victime == etat["protege"]:
        victime = None
    etat["victime"] = victime
    etat["dernier_protege"] = etat["protege"]
    etat["agi"] = [False] * n
    if "herboriste" in etat["composition"]:
        etat["phase"] = "potion"
        etat["message"] = "La nuit continue…"
        _armer(etat, maintenant, alea)
    else:
        _aube(etat, ev, maintenant, alea)


def _aube(etat, ev, maintenant, alea):
    morts = []
    victime = etat["victime"]
    potion = etat["potion"] or {}
    if victime is not None and not potion.get("soin"):
        _mourir(etat, victime, "devore", morts)
    if potion.get("poison") is not None:
        _mourir(etat, potion["poison"], "empoisonne", morts)
    etat["jour"] += 1
    etat["phase"] = "aube"
    etat["suite"] = "debat"
    etat["suivants"] = [False] * etat["nb_joueurs"]
    etat["annonce"] = {"type": "aube", "jour": etat["jour"], "morts": morts}
    noms = etat["noms"]
    if not morts:
        etat["message"] = "Le village se réveille : personne n'est mort cette nuit !"
    else:
        etat["message"] = "Le village se réveille. " + ", ".join(
            f"{noms[m['joueur']]} ({LG_NOMS_ROLES[m['role']]})" for m in morts) + (" est mort." if len(morts) == 1 else " sont morts.")
    ev.append(_tous({"type": "lg_aube", "annonce": etat["annonce"]}))
    _armer(etat, maintenant, alea)


def _continuer(etat, ev, maintenant, alea):
    """Fin d une annonce : tir en attente, victoire, ou la suite."""
    # Les tireurs partis (ou deja servis) ne bloquent pas.
    while etat["tirs"]:
        tireur = etat["tirs"].pop(0)
        if len(_vivants(etat)) > 0:
            etat["retour"] = etat["phase"]
            etat["tireur"] = tireur
            etat["phase"] = "tir"
            etat["suivants"] = [False] * etat["nb_joueurs"]
            etat["message"] = f"{etat['noms'][tireur]}, le Braconnier, emporte quelqu'un avec lui…"
            ev.append(_tous({"type": "lg_tir_attente", "tireur": tireur}))
            _armer(etat, maintenant, alea)
            return
    gagnant = _vainqueur(etat)
    if gagnant:
        _terminer(etat, ev, gagnant)
        return
    if etat["suite"] == "debat":
        _ouvrir_debat(etat, ev, maintenant, alea)
    else:
        _nouvelle_nuit(etat, ev, maintenant, alea)


def _ouvrir_debat(etat, ev, maintenant, alea):
    etat["phase"] = "debat"
    etat["votes"] = {}
    etat["suivants"] = [False] * etat["nb_joueurs"]
    etat["annonce"] = None
    etat["message"] = f"Jour {etat['jour']} : débattez à voix haute, puis votez."
    _armer(etat, maintenant, alea)


def _depouiller(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    compte = [0] * n
    blancs = 0
    votes = []
    for k, cible in sorted(etat["votes"].items(), key=lambda kv: int(kv[0])):
        j = int(k)
        if not etat["vivants"][j]:
            continue
        votes.append({"joueur": j, "cible": cible})
        if cible is None or not etat["vivants"][cible]:
            blancs += 1
        else:
            compte[cible] += 1
    haut = max(compte) if n else 0
    tete = [j for j in range(n) if haut > 0 and compte[j] == haut]
    elimine = tete[0] if len(tete) == 1 and haut > blancs else None
    morts = []
    accusateurs = []
    if elimine is not None:
        _mourir(etat, elimine, "elimine", morts)
        if etat["roles"][elimine] != "loup":
            for v in votes:
                if v["cible"] == elimine:
                    etat["gorgees"][v["joueur"]] += _G["accusateur_innocent"]
                    accusateurs.append({"joueur": v["joueur"], "boit": _G["accusateur_innocent"]})
    sans_vote = [j for j in _vivants(etat) + [m["joueur"] for m in morts]
                 if str(j) not in etat["votes"] and not etat["partis"][j]]
    etat["phase"] = "verdict"
    etat["suite"] = "nuit"
    etat["suivants"] = [False] * n
    etat["annonce"] = {
        "type": "verdict", "jour": etat["jour"], "compte": compte, "blancs": blancs, "votes": votes,
        "elimine": elimine, "egalite": len(tete) > 1, "morts": morts, "accusateurs": accusateurs,
        "sans_vote": sans_vote,
    }
    noms = etat["noms"]
    if elimine is not None:
        etat["message"] = f"Le village élimine {noms[elimine]} : {LG_NOMS_ROLES[etat['roles'][elimine]]} !"
    elif len(tete) > 1:
        etat["message"] = "Égalité : le village n'arrive pas à trancher, personne n'est éliminé."
    else:
        etat["message"] = "Pas de majorité : personne n'est éliminé."
    etat["votes"] = {}
    ev.append(_tous({"type": "lg_verdict", "annonce": etat["annonce"]}))
    _armer(etat, maintenant, alea)


def _terminer(etat, ev, gagnant):
    n = etat["nb_joueurs"]
    gagnants = [j for j in range(n) if _camp(etat["roles"][j]) == gagnant]
    perdants = [j for j in range(n) if j not in gagnants]
    for j in perdants:
        etat["gorgees"][j] += _G["perdant"]
    haut = max([etat["suspects"][j] for j in range(n)] + [0])
    etat["final"] = {
        "vainqueur": gagnant,
        "gagnants": gagnants,
        "perdants": [{"joueur": j, "boit": _G["perdant"]} for j in perdants],
        "roles": list(etat["roles"]),
        "flair": list(etat["suspects"]),
        "meilleur_flair": [j for j in range(n) if haut > 0 and etat["suspects"][j] == haut],
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["tireur"] = None
    etat["annonce"] = None
    etat["message"] = ("Le village est sauvé : plus aucun loup !" if gagnant == "village"
                       else "Les loups ont dévoré le village !")
    ev.append(_tous({"type": "lg_fin", "final": etat["final"]}))


# =============================================================
# ACTIONS
# =============================================================
def _pret(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "role" or etat["prets"][i]:
        return []
    etat["prets"][i] = True
    if all(etat["prets"][j] for j in _presents_vivants(etat)):
        _nouvelle_nuit(etat, ev, maintenant, alea)
    return ev


def _meute_d_accord(etat):
    loups = [j for j in _loups_vivants(etat) if not etat["partis"][j]]
    choix = {etat["meute"].get(str(j)) for j in loups}
    return bool(loups) and len(choix) == 1 and None not in choix


def _nuit(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "nuit":
        return _erreur(i, "Ce n'est pas la nuit.")
    if not etat["vivants"][i]:
        return _erreur(i, "Les fantômes ne jouent plus.")
    n = etat["nb_joueurs"]
    cible = _indice(message.get("cible"), n)
    if cible is None or not etat["vivants"][cible]:
        return _erreur(i, "Touche un joueur encore en vie.")
    role = etat["roles"][i]
    if role == "loup":
        if etat["roles"][cible] == "loup":
            return _erreur(i, "Un loup ne dévore pas un loup.")
        etat["meute"][str(i)] = cible
        # Toute la meute voit le choix en direct.
        for j in _loups_vivants(etat):
            ev.append(_prive(j, {"type": "lg_meute", "loup": i, "cible": cible}))
        d_accord = _meute_d_accord(etat)
        for j in _loups_vivants(etat):
            etat["agi"][j] = d_accord
    else:
        if cible == i and role != "videur":
            return _erreur(i, "Touche un autre joueur.")
        if role == "cartomancienne":
            if str(i) in etat["actions"]:
                return _erreur(i, "Une seule vision par nuit.")
            vision = {"nuit": etat["nuit"], "cible": cible, "role": etat["roles"][cible]}
            etat["visions"].append(vision)
            ev.append(_prive(i, {"type": "lg_vision", **vision}))
        elif role == "videur":
            if cible == etat["dernier_protege"]:
                return _erreur(i, "Pas deux nuits de suite le même joueur.")
            etat["protege"] = cible
        etat["actions"][str(i)] = cible
        etat["agi"][i] = True
    if not _attendus(etat):
        _fin_nuit(etat, ev, maintenant, alea)
    return ev


def _potion(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "potion" or etat["roles"][i] != "herboriste" or not etat["vivants"][i]:
        return _erreur(i, "Rien à faire pour l'instant.")
    if etat["potion"] is not None:
        return []
    n = etat["nb_joueurs"]
    soin = message.get("soin") is True
    poison = message.get("poison")
    poison = None if poison is None else _indice(poison, n)
    if soin and (not etat["potions"]["soin"] or etat["victime"] is None):
        return _erreur(i, "Pas de soin possible.")
    if message.get("poison") is not None:
        if poison is None or not etat["vivants"][poison] or poison == i:
            return _erreur(i, "Empoisonne un autre joueur en vie.")
        if not etat["potions"]["poison"]:
            return _erreur(i, "Ta potion de poison est déjà utilisée.")
    etat["potion"] = {"soin": soin, "poison": poison}
    if soin:
        etat["potions"]["soin"] = False
    if poison is not None:
        etat["potions"]["poison"] = False
    _aube(etat, ev, maintenant, alea)
    return ev


def _tir(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "tir" or etat["tireur"] != i:
        return _erreur(i, "Ce n'est pas à toi de tirer.")
    cible = _indice(message.get("cible"), etat["nb_joueurs"])
    if cible is None or not etat["vivants"][cible] or cible == i:
        return _erreur(i, "Vise un joueur encore en vie.")
    _resoudre_tir(etat, ev, cible, maintenant, alea)
    return ev


def _resoudre_tir(etat, ev, cible, maintenant, alea):
    tireur = etat["tireur"]
    morts = []
    if cible is not None:
        _mourir(etat, cible, "abattu", morts)
    annonce = dict(etat["annonce"] or {"type": etat["retour"], "morts": []})
    annonce["morts"] = list(annonce.get("morts", [])) + morts
    annonce["tir"] = {"tireur": tireur, "cible": cible}
    etat["annonce"] = annonce
    etat["phase"] = etat["retour"] or "aube"
    etat["tireur"] = None
    etat["suivants"] = [False] * etat["nb_joueurs"]
    noms = etat["noms"]
    if cible is None:
        etat["message"] = f"{noms[tireur]} n'a pas tiré."
    else:
        etat["message"] = f"{noms[tireur]} abat {noms[cible]} : {LG_NOMS_ROLES[etat['roles'][cible]]} !"
    ev.append(_tous({"type": "lg_tir", "tireur": tireur, "cible": cible, "morts": morts}))
    _armer(etat, maintenant, alea)


def _voter(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "debat":
        return _erreur(i, "Ce n'est pas l'heure du vote.")
    if not etat["vivants"][i]:
        return _erreur(i, "Les fantômes ne votent pas.")
    brut = message.get("cible")
    if brut is None:
        cible = None
    else:
        cible = _indice(brut, etat["nb_joueurs"])
        if cible is None or not etat["vivants"][cible] or cible == i:
            return _erreur(i, "Vote pour un autre joueur en vie (ou vote blanc).")
    etat["votes"][str(i)] = cible
    ev.append(_prive(i, {"type": "lg_vote", "index": i, "cible": cible}))
    if not _attendus(etat):
        _depouiller(etat, ev, maintenant, alea)
    return ev


def _suivant(etat, ev, i, message, maintenant, alea):
    if etat["phase"] not in ("aube", "verdict") or etat["suivants"][i]:
        return []
    etat["suivants"][i] = True
    if not _attendus(etat):
        _continuer(etat, ev, maintenant, alea)
    return ev


LG_ACTIONS = {
    "lg_pret": _pret,
    "lg_nuit": _nuit,
    "lg_potion": _potion,
    "lg_tir": _tir,
    "lg_voter": _voter,
    "lg_suivant": _suivant,
}


def lg_action(etat, i, message, maintenant, alea=None):
    fn = LG_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
        return []
    ev = []
    avant = etat["version"]
    res = fn(etat, ev, i, message, maintenant, alea or random)
    if res is ev:
        etat["version"] = avant + 1
    return res


# =============================================================
# MINUTEUR
# =============================================================
def _concernes(etat):
    """Ceux dont on attend quelque chose dans la phase (presents ou non)."""
    phase = etat["phase"]
    if phase in ("role", "aube", "verdict"):
        return list(range(etat["nb_joueurs"]))
    if phase in ("nuit", "debat"):
        return _vivants(etat)
    return []


def _attendus(etat):
    phase = etat["phase"]
    presents = [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]
    if phase == "role":
        return [j for j in _presents_vivants(etat) if not etat["prets"][j]]
    if phase == "nuit":
        return [j for j in _presents_vivants(etat) if not etat["agi"][j]]
    if phase == "debat":
        return [j for j in _presents_vivants(etat) if str(j) not in etat["votes"]]
    if phase in ("aube", "verdict"):
        return [j for j in presents if not etat["suivants"][j]]
    if phase == "potion":
        h = _role_vivant(etat, "herboriste")
        if h is None or etat["partis"][h] or etat["potion"] is not None:
            return []
        return [h]
    if phase == "tir":
        t = etat["tireur"]
        return [] if t is None or etat["partis"][t] else [t]
    return []


def _peut_soigner_ou_empoisonner(etat):
    h = _role_vivant(etat, "herboriste")
    if h is None:
        return False
    p = etat["potions"]
    return p["poison"] or (p["soin"] and etat["victime"] is not None)


def _armer(etat, maintenant, alea=None):
    alea = alea or random
    phase = etat["phase"]
    if phase == "fin":
        etat["echeance"] = None
        return
    if phase == "potion":
        h = _role_vivant(etat, "herboriste")
        if h is not None and not etat["partis"][h] and _peut_soigner_ou_empoisonner(etat):
            etat["echeance"] = maintenant + _D["potion"]
        else:
            # Herboriste morte, partie ou a sec : on attend quand meme un peu,
            # pour que la duree ne trahisse rien.
            etat["echeance"] = maintenant + alea.uniform(_D["potion_vide_min"], _D["potion_vide_max"])
        return
    delai = _D[phase] if _attendus(etat) else _D["absent"]
    etat["echeance"] = maintenant + delai


def lg_prochaine_echeance(etat):
    return etat["echeance"]


def lg_expirer(etat, maintenant, alea=None):
    """L echeance est passee : on avance sans ceux qu on attend."""
    alea = alea or random
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    phase = etat["phase"]
    if phase == "role":
        _nouvelle_nuit(etat, ev, maintenant, alea)
    elif phase == "nuit":
        _fin_nuit(etat, ev, maintenant, alea)
    elif phase == "potion":
        if etat["potion"] is None:
            etat["potion"] = {"soin": False, "poison": None}
        _aube(etat, ev, maintenant, alea)
    elif phase in ("aube", "verdict"):
        _continuer(etat, ev, maintenant, alea)
    elif phase == "tir":
        _resoudre_tir(etat, ev, None, maintenant, alea)
    elif phase == "debat":
        _depouiller(etat, ev, maintenant, alea)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def lg_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    if etat["phase"] == "nuit" and etat["roles"][index] == "loup":
        # La meute restante peut etre d accord sans lui.
        d_accord = _meute_d_accord(etat)
        for j in _loups_vivants(etat):
            etat["agi"][j] = d_accord
    if etat["phase"] not in ("fin", "potion") and etat["echeance"] is not None and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"], maintenant + _D["absent"])


def lg_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] in ("nuit", "debat", "role") and index in _attendus(etat):
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
