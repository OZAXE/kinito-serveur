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
Les roles portent leurs noms officiels (LG_NOMS_ROLES). Quatre identifiants
internes datent de la premiere version et sont gardes pour la compatibilite :
cartomancienne = Voyante, videur = Salvateur, herboriste = Sorciere,
braconnier = Chasseur.

Roles du camp des loups (la meute vote une victime chaque nuit) :
  - loup : Loup-Garou.
  - infect : Infect Pere des Loups. Une fois par partie, il peut infecter la
    victime de la meute au lieu de la tuer : elle rejoint les loups en
    secret (elle garde sa carte, la Voyante voit toujours sa carte).
  - loup_blanc : Loup Blanc. Chasse avec la meute, mais gagne SEUL : une
    nuit sur deux (nuits paires), il peut devorer un loup.
Roles du village :
  - villageois : Simple Villageois, sans pouvoir.
  - cartomancienne (Voyante) : decouvre la carte d un joueur chaque nuit.
  - videur (Salvateur) : protege un joueur des loups chaque nuit (lui
    compris), jamais le meme deux nuits de suite.
  - herboriste (Sorciere) : potion de vie (sauve la victime des loups) et
    potion de mort, une fois chacune.
  - braconnier (Chasseur) : en mourant, il abat un joueur.
  - cupidon : la premiere nuit, lie deux amoureux. Si l un meurt, l autre
    meurt de chagrin. Un loup et un villageois amoureux forment leur propre
    camp : ils gagnent s ils restent seuls.
  - petite_fille (Petite Fille) : espionne la meute la nuit (elle voit ses
    choix, pas qui vote) ; chaque nuit, un risque d etre reperee par les loups.
  - ancien : resiste a la premiere attaque des loups. S il meurt de la main
    du village (vote, Chasseur, Sorciere), le village perd ses pouvoirs.
  - idiot (Idiot du Village) : elimine au vote, il est epargne et devoile,
    mais ne vote plus.
  - corbeau : chaque nuit, designe un joueur qui aura 2 voix contre lui au
    vote du lendemain (annonce publique).
  - renard : chaque nuit, flaire un joueur et ses deux voisins : apprend
    s il y a un loup parmi eux. S il n y en a pas, il perd son flair.
  - enfant_sauvage (Enfant Sauvage) : la premiere nuit, choisit un modele ;
    si son modele meurt, il devient loup.
  - montreur (Montreur d Ours) : chaque matin, l ours grogne si un loup est
    assis a cote du montreur.
  - chevalier (Chevalier a l Epee Rouillee) : devore, il infecte le premier
    loup a sa gauche, qui meurt la nuit suivante.
  - bouc (Bouc Emissaire) : en cas d egalite au vote, c est lui qui part.
  - ange : gagne seul s il meurt au premier tour (nuit 1 ou premier vote),
    sinon simple villageois.
  - chien_loup (Chien-Loup) : choisit son camp en decouvrant sa carte.
  - soeur (2 cartes) / frere (3 cartes) : se connaissent entre eux.
Capitaine (option) : elu au premier jour, sa voix compte double, il designe
son successeur en mourant.

Deroulement :
  - « role » : chacun decouvre son role en secret et touche « Pret ».
  - « nuit » : TOUT LE MONDE en vie touche un joueur, pour que personne ne
    se trahisse a la table. Chacun a son action (lg_secret.type_nuit) :
    meute, vision, protection, renard, corbeau, cupidon (deux joueurs),
    modele, ou un suspect (le « flair », compte en fin de partie). La nuit
    finit quand tous ont agi (la meute d accord) ou a l echeance.
  - « potion » : seulement si la Sorciere est dans la partie (duree au
    hasard si elle ne peut rien faire : on ne devine rien a la duree).
  - « aube » : les morts de la nuit et leur role. Puis les tirs du Chasseur
    (« tir »), la succession du Capitaine (« succession »), l election du
    Capitaine au premier jour (« election »), le « debat » (vote secret ou
    blanc), le « verdict » (qui a vote quoi).
  - Victoire : le village quand il n y a plus de loup ; les loups des qu ils
    sont au moins aussi nombreux que les autres vivants (si ni Loup Blanc ni
    couple mixte n est en vie, sinon il faut qu il ne reste qu eux) ; le Loup
    Blanc seul ; le couple mixte seul ; l Ange s il meurt au premier tour.

Gorgees : mort la nuit 2, abattu 2, elimine au vote 2 (loup demasque 3),
mort de chagrin 2, chaque accusateur d un innocent elimine 1, et en fin de
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
    "villageois": "Simple Villageois",
    "cartomancienne": "Voyante",
    "videur": "Salvateur",
    "herboriste": "Sorcière",
    "braconnier": "Chasseur",
    "cupidon": "Cupidon",
    "petite_fille": "Petite Fille",
    "ancien": "Ancien",
    "idiot": "Idiot du Village",
    "corbeau": "Corbeau",
    "renard": "Renard",
    "enfant_sauvage": "Enfant Sauvage",
    "montreur": "Montreur d'Ours",
    "chevalier": "Chevalier à l'Épée Rouillée",
    "bouc": "Bouc Émissaire",
    "ange": "Ange",
    "chien_loup": "Chien-Loup",
    "soeur": "Sœur",
    "frere": "Frère",
    "infect": "Infect Père des Loups",
    "loup_blanc": "Loup Blanc",
}
LG_ROLES_LOUPS = ("loup", "infect", "loup_blanc")
LG_LOUPS_SPECIAUX = ("infect", "loup_blanc")
LG_GROUPES = {"soeur": 2, "frere": 3}
# Pouvoirs perdus quand le village tue l Ancien.
_POUVOIRS_VILLAGE = ("cartomancienne", "videur", "herboriste", "braconnier", "petite_fille", "idiot",
                     "corbeau", "renard", "montreur", "chevalier")
_D = LG_CONFIG["delais"]
_G = LG_CONFIG["gorgees"]
_RISQUE_PF = LG_CONFIG.get("petite_fille_risque", 0.2)
_VOIX_CORBEAU = LG_CONFIG.get("corbeau_voix", 2)


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


def _est_loup(etat, j):
    return etat["roles"][j] in LG_ROLES_LOUPS or etat["convertis"][j]


def _loups(etat):
    return [j for j in range(etat["nb_joueurs"]) if _est_loup(etat, j)]


def _loups_vivants(etat):
    return [i for i in _vivants(etat) if _est_loup(etat, i)]


def _effectif(etat, j):
    """Le role qui compte pour les pouvoirs (l Ancien tue par le village les eteint)."""
    r = etat["roles"][j]
    if etat["pouvoirs_perdus"] and r in _POUVOIRS_VILLAGE:
        return "villageois"
    return r


def _role_vivant(etat, role):
    """Le joueur vivant qui a ce role ET son pouvoir (None sinon)."""
    for i in _vivants(etat):
        if etat["roles"][i] == role and _effectif(etat, i) == role:
            return i
    return None


def _voisins(etat, j):
    """Les plus proches vivants a gauche et a droite de j (sans j)."""
    n = etat["nb_joueurs"]
    res = []
    for pas in (1, -1):
        for k in range(1, n):
            v = (j + pas * k) % n
            if v != j and etat["vivants"][v]:
                if v not in res:
                    res.append(v)
                break
    return res


def lg_nb_loups_auto(n):
    return 1 + sum(1 for s in LG_CONFIG["loups"]["seuils"] if n > s)


def lg_loups_max(n):
    """Au plus un loup pour deux autres joueurs : sinon la partie est jouee d avance."""
    return max(1, (n - 1) // 2)


def lg_speciaux_auto(n):
    seuils = LG_CONFIG["speciaux"]["a_partir_de"]
    return [r for r in LG_SPECIAUX if n >= seuils.get(r, 99)]


def lg_capitaine_auto(n):
    return n >= LG_CONFIG["capitaine"]["a_partir_de"]


def lg_composition(n, loups=None, speciaux=None):
    """
    Les roles de la partie (liste, pas encore melangee). `loups` : nombre ou
    None (automatique). `speciaux` : liste de roles speciaux ou None
    (automatique selon le nombre de joueurs). Les valeurs absurdes sont
    ramenees a quelque chose de jouable. Infect et Loup Blanc prennent une
    place de loup (le Loup Blanc demande au moins 2 loups) ; Soeurs et Freres
    viennent par 2 et par 3, seulement s il reste la place.
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
    loups_sp = []
    for r in choisis:
        if r in LG_LOUPS_SPECIAUX and len(loups_sp) < nb_loups and not (r == "loup_blanc" and nb_loups < 2):
            loups_sp.append(r)
    places = n - nb_loups
    village = []
    for r in choisis:
        if r in LG_LOUPS_SPECIAUX:
            continue
        k = LG_GROUPES.get(r, 1)
        if len(village) + k <= places:
            village += [r] * k
    roles = loups_sp + ["loup"] * (nb_loups - len(loups_sp)) + village
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
def lg_initialiser(noms, maintenant, loups=None, speciaux=None, alea=None, capitaine=None):
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
        "convertis": [False] * n,            # SECRET : infectes, Enfant Sauvage orphelin, Chien-Loup loup
        "pouvoirs_perdus": False,            # public : l Ancien a ete tue par le village
        "phase": "role",
        "nuit": 0,
        "jour": 0,
        "prets": [False] * n,
        # Nuit en cours (SECRET)
        "meute": {},                         # str(loup) -> cible
        "agi": [False] * n,
        "actions": {},                       # str(joueur) -> cible (sonde, protection, suspect...)
        "visions": [],                       # [{nuit, cible, role}] de la Voyante
        "flairs": [],                        # [{nuit, cible, groupe, loup}] du Renard
        "renard_actif": True,
        "protege": None,
        "dernier_protege": None,
        "victime_loups": None,               # choix de la meute, avant protection
        "victime": None,                     # victime des loups apres protection (vue par la Sorciere)
        "potions": {"soin": True, "poison": True},
        "potion": None,                      # choix de cette nuit : {"soin", "poison"}
        "suspects": [0] * n,                 # flair : nuits passees a soupconner un loup
        "infection": "infect" in roles,      # l Infect peut encore infecter
        "infecter": False,                   # demande de cette nuit
        "solo": None,                        # proie du Loup Blanc cette nuit
        "couple": None,                      # choix de Cupidon cette nuit
        "amoureux": None,                    # [a, b] (SECRET, sauf pour eux et Cupidon)
        "modele_choix": None,
        "modele": None,                      # modele de l Enfant Sauvage
        "ancien_vie": True,
        "pf_reperee": False,
        "rouille": None,                     # loup qui mourra a la prochaine aube (Chevalier)
        "corbeau": None,                     # designe cette nuit, pour le vote de demain
        "corbeau_jour": None,                # designe pour le vote d aujourd hui (public)
        "idiot_revele": None,
        "ange_gagne": False,
        "chien": {},                         # str(chien) -> "loups" ou "village" (phase role)
        # Capitaine
        "capitaine_option": bool(lg_capitaine_auto(n) if capitaine is None else capitaine is True),
        "capitaine": None,
        "election_faite": False,
        "succession": None,                  # capitaine mort qui doit nommer son successeur
        # Jour
        "votes": {},                         # str(joueur) -> cible ou None (blanc) : SECRET jusqu au verdict
        "suivants": [False] * n,
        "annonce": None,                     # aube ou verdict : morts, votes
        "tirs": [],                          # Chasseurs morts qui n ont pas encore tire
        "tireur": None,                      # le Chasseur qui tire, ou le Capitaine qui nomme
        "retour": None,                      # phase a reprendre apres un tir ou une succession
        "suite": None,                       # "debat" ou "nuit" apres l annonce
        "gorgees": [0] * n,
        "morts": [],                         # [{joueur, role, cause, converti, nuit, jour}]
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
    visibles = [fin or not etat["vivants"][i] or etat["idiot_revele"] == i for i in range(n)]
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
        # Le role d un vivant reste secret jusqu a la fin (sauf l Idiot devoile).
        "roles": [etat["roles"][i] if visibles[i] else None for i in range(n)],
        "prets": etat["prets"] if phase == "role" else [False] * n,
        # Pendant un vote, on sait qui a vote, jamais pour qui.
        "a_vote": [str(i) in etat["votes"] for i in range(n)] if phase in ("debat", "election") else [False] * n,
        # La nuit, un simple compte : tout le monde agit, rien ne se devine.
        "attente": {"fait": len([i for i in concernes if i not in attendus]), "total": len(concernes)},
        "suivants": etat["suivants"],
        "annonce": etat["annonce"] if phase in ("aube", "verdict", "tir", "succession") else None,
        "tireur": etat["tireur"] if phase in ("tir", "succession") else None,
        "potion_en_jeu": "herboriste" in etat["composition"],
        "capitaine_option": etat["capitaine_option"],
        "capitaine": etat["capitaine"],
        "idiot_revele": etat["idiot_revele"],
        "pouvoirs_perdus": etat["pouvoirs_perdus"],
        "corbeau": etat["corbeau_jour"] if phase in ("aube", "election", "debat", "tir", "succession") else None,
        "gorgees": etat["gorgees"],
        "morts": etat["morts"],
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def _type_nuit(etat, j):
    """Ce que le joueur j fait cette nuit."""
    if _est_loup(etat, j):
        return "meute"
    r = _effectif(etat, j)
    if r == "cartomancienne":
        return "vision"
    if r == "videur":
        return "protection"
    if r == "renard" and etat["renard_actif"]:
        return "renard"
    if r == "corbeau":
        return "corbeau"
    if r == "cupidon" and etat["nuit"] == 1:
        return "cupidon"
    if r == "enfant_sauvage" and etat["nuit"] == 1:
        return "modele"
    return "suspect"


def _solo_possible(etat):
    """Le Loup Blanc peut devorer un loup : nuits paires, ou chaque nuit quand il ne reste que des loups."""
    lb = [j for j in _vivants(etat) if etat["roles"][j] == "loup_blanc"]
    if not lb:
        return False
    autres_loups = [j for j in _loups_vivants(etat) if j != lb[0]]
    if not autres_loups:
        return False
    village = [j for j in _vivants(etat) if not _est_loup(etat, j)]
    return etat["nuit"] % 2 == 0 or not village


def lg_vue(etat, i):
    """Ce que le joueur i est seul a voir (son role, sa meute, ses visions...)."""
    n = etat["nb_joueurs"]
    if not 0 <= i < n:
        return None
    role = etat["roles"][i]
    vivant = etat["vivants"][i]
    phase = etat["phase"]
    fantome = (not vivant) or phase == "fin"
    loup = _est_loup(etat, i)
    vue = {
        "type": "lg_secret",
        "index": i,
        "role": role,
        "vivant": vivant,
        "converti": etat["convertis"][i],
        "type_nuit": _type_nuit(etat, i) if vivant and phase == "nuit" else None,
        "loups": None,
        "meute": None,
        "espion": None,
        "petite_fille": None,
        "action": None,
        "couple": None,
        "vote": None,
        "visions": None,
        "flairs": None,
        "renard_actif": None,
        "dernier_protege": None,
        "potions": None,
        "victime": None,
        "potion_faite": None,
        "infection": None,
        "infecter": None,
        "solo_possible": None,
        "solo": None,
        "amoureux": None,
        "famille": None,
        "modele": None,
        "chien": None,
        "tous_roles": list(etat["roles"]) if fantome else None,
        "tous_convertis": list(etat["convertis"]) if fantome else None,
    }
    if loup or fantome:
        vue["loups"] = _loups(etat)
    if loup and vivant and phase == "nuit":
        vue["meute"] = {k: v for k, v in etat["meute"].items()}
    if loup and etat["pf_reperee"]:
        pf = [j for j in range(n) if etat["roles"][j] == "petite_fille"]
        vue["petite_fille"] = pf[0] if pf else None
    if vivant and phase == "nuit":
        if loup:
            vue["action"] = etat["meute"].get(str(i))
        else:
            vue["action"] = etat["actions"].get(str(i))
        if _effectif(etat, i) == "petite_fille" and not loup:
            compte = {}
            for c in etat["meute"].values():
                compte[str(c)] = compte.get(str(c), 0) + 1
            vue["espion"] = compte
        if role == "cupidon" and etat["nuit"] == 1:
            vue["couple"] = etat["couple"]
    if vivant and phase in ("debat", "election") and str(i) in etat["votes"]:
        vue["vote"] = {"cible": etat["votes"][str(i)]}
    if role == "cartomancienne":
        vue["visions"] = list(etat["visions"])
    if role == "renard":
        vue["flairs"] = list(etat["flairs"])
        vue["renard_actif"] = etat["renard_actif"] and not etat["pouvoirs_perdus"]
    if role == "videur":
        vue["dernier_protege"] = etat["dernier_protege"]
    if role == "herboriste":
        vue["potions"] = dict(etat["potions"])
        if phase == "potion" and vivant and _effectif(etat, i) == "herboriste":
            vue["victime"] = etat["victime"]
            vue["potion_faite"] = etat["potion"] is not None
    if role == "infect":
        vue["infection"] = etat["infection"]
        vue["infecter"] = etat["infecter"] if phase == "nuit" else None
    if role == "loup_blanc" and vivant and phase == "nuit":
        vue["solo_possible"] = _solo_possible(etat)
        vue["solo"] = etat["solo"]
    if etat["amoureux"] and (i in etat["amoureux"] or role == "cupidon"):
        vue["amoureux"] = list(etat["amoureux"])
    if role in LG_GROUPES:
        vue["famille"] = [j for j in range(n) if etat["roles"][j] == role]
    if role == "enfant_sauvage":
        vue["modele"] = etat["modele"]
    if role == "chien_loup":
        vue["chien"] = etat["chien"].get(str(i), "village")
    return vue


# =============================================================
# CAMPS ET VICTOIRE
# =============================================================
def _camp_base(etat, j):
    if etat["roles"][j] == "loup_blanc":
        return "loup_blanc"
    return "loups" if _est_loup(etat, j) else "village"


def _couple_mixte(etat):
    a = etat["amoureux"]
    return bool(a) and _camp_base(etat, a[0]) != _camp_base(etat, a[1])


def _camp(etat, j):
    if etat["amoureux"] and j in etat["amoureux"] and _couple_mixte(etat):
        return "amoureux"
    return _camp_base(etat, j)


def _vainqueur(etat):
    if etat["ange_gagne"]:
        return "ange"
    vivants = _vivants(etat)
    if not vivants:
        return "personne"
    camps = {_camp(etat, j) for j in vivants}
    if len(camps) == 1:
        return camps.pop()
    if camps == {"loups", "village"}:
        loups = len([j for j in vivants if _camp(etat, j) == "loups"])
        if loups >= len(vivants) - loups:
            return "loups"
    return None


# =============================================================
# DEROULEMENT
# =============================================================
def _mourir(etat, j, cause, morts):
    if not etat["vivants"][j]:
        return
    effectif = _effectif(etat, j)
    role = etat["roles"][j]
    etat["vivants"][j] = False
    gorgees = {"devore": _G["devore"], "rouille": _G["devore"], "empoisonne": _G["empoisonne"],
               "abattu": _G["abattu"], "chagrin": _G.get("chagrin", 2),
               "elimine": _G["loup_demasque"] if _est_loup(etat, j) else _G["elimine"],
               "bouc": _G["elimine"]}.get(cause, 0)
    etat["gorgees"][j] += gorgees
    mort = {"joueur": j, "role": role, "cause": cause, "boit": gorgees, "converti": etat["convertis"][j],
            "nuit": etat["nuit"], "jour": etat["jour"]}
    etat["morts"].append(mort)
    morts.append(mort)
    if effectif == "braconnier":
        etat["tirs"].append(j)
    if role == "ancien" and cause in ("elimine", "abattu", "empoisonne", "bouc"):
        etat["pouvoirs_perdus"] = True
    if effectif == "chevalier" and cause == "devore":
        n = etat["nb_joueurs"]
        for k in range(1, n):
            v = (j + k) % n
            if etat["vivants"][v] and _est_loup(etat, v):
                etat["rouille"] = v
                break
    if role == "ange" and etat["jour"] <= 1 and cause != "chagrin":
        etat["ange_gagne"] = True
    if etat["capitaine"] == j:
        etat["succession"] = j
    if etat["modele"] == j:
        for e in _vivants(etat):
            if etat["roles"][e] == "enfant_sauvage" and not etat["convertis"][e]:
                etat["convertis"][e] = True
    if etat["amoureux"] and j in etat["amoureux"]:
        autre = etat["amoureux"][0] if etat["amoureux"][1] == j else etat["amoureux"][1]
        _mourir(etat, autre, "chagrin", morts)


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
    etat["infecter"] = False
    etat["solo"] = None
    etat["couple"] = None
    etat["votes"] = {}
    etat["suivants"] = [False] * n
    etat["annonce"] = None
    etat["corbeau_jour"] = None
    etat["message"] = f"Nuit {etat['nuit']} : le village s'endort, chacun touche un joueur."
    ev.append(_tous({"type": "lg_nuit", "nuit": etat["nuit"]}))
    _armer(etat, maintenant, alea)


def _choix_meute(etat, alea):
    """La victime : le choix majoritaire des loups vivants (egalite : le sort)."""
    compte = {}
    for k, cible in etat["meute"].items():
        j = int(k)
        if etat["vivants"][j] and _est_loup(etat, j) and cible is not None and etat["vivants"][cible] \
                and not _est_loup(etat, cible):
            compte[cible] = compte.get(cible, 0) + 1
    if not compte:
        return None
    haut = max(compte.values())
    tete = sorted(c for c, v in compte.items() if v == haut)
    return tete[0] if len(tete) == 1 else alea.choice(tete)


def _fin_nuit(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    vivants = _vivants(etat)
    # Le flair : ceux qui soupconnaient un loup cette nuit.
    for k, cible in etat["actions"].items():
        j = int(k)
        if _type_nuit(etat, j) == "suspect" and isinstance(cible, int) and _est_loup(etat, cible):
            etat["suspects"][j] += 1
    # Cupidon et l Enfant Sauvage : premiere nuit, au hasard s ils n ont rien choisi.
    if etat["nuit"] == 1:
        cup = _role_vivant(etat, "cupidon")
        if cup is not None and etat["amoureux"] is None and len(vivants) >= 2:
            couple = etat["couple"] or sorted(alea.sample(vivants, 2))
            etat["amoureux"] = list(couple)
            for a in couple:
                ev.append(_prive(a, {"type": "lg_amoureux", "amoureux": list(couple)}))
            ev.append(_prive(cup, {"type": "lg_amoureux", "amoureux": list(couple)}))
        enf = _role_vivant(etat, "enfant_sauvage")
        if enf is not None and etat["modele"] is None and len(vivants) >= 2:
            m = etat["modele_choix"]
            etat["modele"] = m if m is not None else alea.choice([v for v in vivants if v != enf])
    # Le Corbeau, pour le vote de demain.
    corb = _role_vivant(etat, "corbeau")
    etat["corbeau"] = etat["actions"].get(str(corb)) if corb is not None else None
    # La Petite Fille espionne : les loups la reperent parfois.
    pf = _role_vivant(etat, "petite_fille")
    if pf is not None and not etat["pf_reperee"] and not _est_loup(etat, pf) and alea.random() < _RISQUE_PF:
        etat["pf_reperee"] = True
        for l in _loups_vivants(etat):
            ev.append(_prive(l, {"type": "lg_petite_fille", "joueur": pf}))
    victime = _choix_meute(etat, alea)
    etat["victime_loups"] = victime
    if victime is not None and victime == etat["protege"]:
        victime = None
    # L Infect : la victime rejoint la meute au lieu de mourir.
    infect = [j for j in _vivants(etat) if etat["roles"][j] == "infect"]
    if victime is not None and infect and etat["infection"] and etat["infecter"]:
        etat["convertis"][victime] = True
        etat["infection"] = False
        ev.append(_prive(victime, {"type": "lg_converti", "cause": "infect"}))
        victime = None
    # Le Loup Blanc devore un loup.
    solo = etat["solo"]
    if not (solo is not None and _solo_possible(etat) and etat["vivants"][solo] and _est_loup(etat, solo)
            and etat["roles"][solo] != "loup_blanc"):
        etat["solo"] = None
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
    rouille = etat["rouille"]
    etat["rouille"] = None
    convertis_avant = list(etat["convertis"])
    if victime is not None and not potion.get("soin"):
        if etat["roles"][victime] == "ancien" and etat["ancien_vie"]:
            etat["ancien_vie"] = False           # il resiste, en silence
        else:
            _mourir(etat, victime, "devore", morts)
    if potion.get("poison") is not None:
        _mourir(etat, potion["poison"], "empoisonne", morts)
    if etat["solo"] is not None:
        _mourir(etat, etat["solo"], "devore", morts)
        etat["solo"] = None
    if rouille is not None:
        _mourir(etat, rouille, "rouille", morts)
    for j in range(etat["nb_joueurs"]):
        if etat["convertis"][j] and not convertis_avant[j] and etat["vivants"][j]:
            ev.append(_prive(j, {"type": "lg_converti", "cause": "modele"}))
    etat["jour"] += 1
    etat["phase"] = "aube"
    etat["suite"] = "debat"
    etat["suivants"] = [False] * etat["nb_joueurs"]
    etat["corbeau_jour"] = etat["corbeau"] if etat["corbeau"] is not None and etat["vivants"][etat["corbeau"]] else None
    etat["corbeau"] = None
    grogne = None
    mo = _role_vivant(etat, "montreur")
    if mo is not None:
        grogne = any(_est_loup(etat, v) for v in _voisins(etat, mo))
    etat["annonce"] = {"type": "aube", "jour": etat["jour"], "morts": morts, "corbeau": etat["corbeau_jour"],
                       "grogne": grogne}
    noms = etat["noms"]
    if not morts:
        etat["message"] = "Le village se réveille : personne n'est mort cette nuit !"
    else:
        etat["message"] = "Le village se réveille. " + ", ".join(
            f"{noms[m['joueur']]} ({LG_NOMS_ROLES[m['role']]})" for m in morts) + (" est mort." if len(morts) == 1 else " sont morts.")
    if grogne:
        etat["message"] += " L'ours grogne !"
    ev.append(_tous({"type": "lg_aube", "annonce": etat["annonce"]}))
    _armer(etat, maintenant, alea)


def _continuer(etat, ev, maintenant, alea):
    """Fin d une annonce : tir en attente, victoire, succession, ou la suite."""
    if etat["phase"] in ("aube", "verdict"):
        etat["retour"] = etat["phase"]
    while etat["tirs"]:
        tireur = etat["tirs"].pop(0)
        if _vivants(etat):
            etat["tireur"] = tireur
            etat["phase"] = "tir"
            etat["suivants"] = [False] * etat["nb_joueurs"]
            etat["message"] = f"{etat['noms'][tireur]}, le Chasseur, emporte quelqu'un avec lui…"
            ev.append(_tous({"type": "lg_tir_attente", "tireur": tireur}))
            _armer(etat, maintenant, alea)
            return
    gagnant = _vainqueur(etat)
    if gagnant:
        _terminer(etat, ev, gagnant)
        return
    if etat["succession"] is not None:
        cap = etat["succession"]
        etat["succession"] = None
        etat["capitaine"] = None
        if _vivants(etat):
            etat["tireur"] = cap
            etat["phase"] = "succession"
            etat["suivants"] = [False] * etat["nb_joueurs"]
            etat["message"] = f"{etat['noms'][cap]}, le Capitaine, nomme son successeur…"
            ev.append(_tous({"type": "lg_succession_attente", "capitaine": cap}))
            _armer(etat, maintenant, alea)
            return
    if etat["suite"] == "debat":
        if etat["capitaine_option"] and not etat["election_faite"]:
            _ouvrir_election(etat, ev, maintenant, alea)
        else:
            _ouvrir_debat(etat, ev, maintenant, alea)
    else:
        _nouvelle_nuit(etat, ev, maintenant, alea)


def _ouvrir_election(etat, ev, maintenant, alea):
    etat["phase"] = "election"
    etat["votes"] = {}
    etat["suivants"] = [False] * etat["nb_joueurs"]
    etat["annonce"] = None
    etat["message"] = "Élisez votre Capitaine : sa voix comptera double."
    _armer(etat, maintenant, alea)


def _ouvrir_debat(etat, ev, maintenant, alea, intro=""):
    etat["phase"] = "debat"
    etat["votes"] = {}
    etat["suivants"] = [False] * etat["nb_joueurs"]
    etat["annonce"] = None
    etat["message"] = intro + f"Jour {etat['jour']} : débattez à voix haute, puis votez."
    _armer(etat, maintenant, alea)


def _elire(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    compte = [0] * n
    for k, cible in etat["votes"].items():
        if etat["vivants"][int(k)] and cible is not None and etat["vivants"][cible]:
            compte[cible] += 1
    vivants = _vivants(etat)
    haut = max(compte) if n else 0
    tete = [j for j in vivants if haut > 0 and compte[j] == haut] or vivants
    cap = tete[0] if len(tete) == 1 else alea.choice(tete)
    etat["capitaine"] = cap
    etat["election_faite"] = True
    ev.append(_tous({"type": "lg_capitaine", "capitaine": cap, "compte": compte, "election": True}))
    _ouvrir_debat(etat, ev, maintenant, alea, f"{etat['noms'][cap]} est élu Capitaine. ")


def _depouiller(etat, ev, maintenant, alea):
    n = etat["nb_joueurs"]
    compte = [0] * n
    blancs = 0
    votes = []
    for k, cible in sorted(etat["votes"].items(), key=lambda kv: int(kv[0])):
        j = int(k)
        if not etat["vivants"][j] or etat["idiot_revele"] == j:
            continue
        poids = 2 if etat["capitaine"] == j else 1
        votes.append({"joueur": j, "cible": cible, "poids": poids})
        if cible is None or not etat["vivants"][cible]:
            blancs += poids
        else:
            compte[cible] += poids
    corbeau = etat["corbeau_jour"]
    if corbeau is not None and etat["vivants"][corbeau]:
        compte[corbeau] += _VOIX_CORBEAU
    haut = max(compte) if n else 0
    tete = [j for j in range(n) if haut > 0 and compte[j] == haut]
    elimine = tete[0] if len(tete) == 1 and haut > blancs else None
    egalite = len(tete) > 1
    morts = []
    accusateurs = []
    idiot = None
    bouc = None
    if elimine is not None:
        if etat["roles"][elimine] == "idiot" and _effectif(etat, elimine) == "idiot" and etat["idiot_revele"] is None:
            # L Idiot est epargne, devoile, et ne vote plus.
            idiot = elimine
            etat["idiot_revele"] = elimine
            etat["gorgees"][elimine] += _G["elimine"]
        else:
            _mourir(etat, elimine, "elimine", morts)
        if not _est_loup(etat, elimine):
            for v in votes:
                if v["cible"] == elimine:
                    etat["gorgees"][v["joueur"]] += _G["accusateur_innocent"]
                    accusateurs.append({"joueur": v["joueur"], "boit": _G["accusateur_innocent"]})
    elif egalite and haut >= blancs:
        b = _role_vivant(etat, "bouc")
        if b is not None:
            bouc = b
            _mourir(etat, b, "bouc", morts)
    sans_vote = [j for j in _vivants(etat) + [m["joueur"] for m in morts]
                 if str(j) not in etat["votes"] and not etat["partis"][j] and etat["idiot_revele"] != j]
    etat["phase"] = "verdict"
    etat["suite"] = "nuit"
    etat["suivants"] = [False] * n
    etat["annonce"] = {
        "type": "verdict", "jour": etat["jour"], "compte": compte, "blancs": blancs, "votes": votes,
        "elimine": elimine, "egalite": egalite, "morts": morts, "accusateurs": accusateurs,
        "sans_vote": sans_vote, "corbeau": corbeau, "idiot": idiot, "bouc": bouc,
    }
    noms = etat["noms"]
    if idiot is not None:
        etat["message"] = f"Le village voulait éliminer {noms[idiot]}… c'est l'Idiot du Village ! Il est épargné mais ne votera plus."
    elif elimine is not None:
        etat["message"] = f"Le village élimine {noms[elimine]} : {LG_NOMS_ROLES[etat['roles'][elimine]]} !"
    elif bouc is not None:
        etat["message"] = f"Égalité : le Bouc Émissaire, {noms[bouc]}, est sacrifié."
    elif egalite:
        etat["message"] = "Égalité : le village n'arrive pas à trancher, personne n'est éliminé."
    else:
        etat["message"] = "Pas de majorité : personne n'est éliminé."
    etat["votes"] = {}
    ev.append(_tous({"type": "lg_verdict", "annonce": etat["annonce"]}))
    _armer(etat, maintenant, alea)


def _terminer(etat, ev, gagnant):
    n = etat["nb_joueurs"]
    if gagnant == "ange":
        gagnants = [j for j in range(n) if etat["roles"][j] == "ange"]
    elif gagnant == "personne":
        gagnants = []
    else:
        gagnants = [j for j in range(n) if _camp(etat, j) == gagnant]
    perdants = [j for j in range(n) if j not in gagnants]
    for j in perdants:
        etat["gorgees"][j] += _G["perdant"]
    haut = max([etat["suspects"][j] for j in range(n)] + [0])
    etat["final"] = {
        "vainqueur": gagnant,
        "gagnants": gagnants,
        "perdants": [{"joueur": j, "boit": _G["perdant"]} for j in perdants],
        "roles": list(etat["roles"]),
        "convertis": list(etat["convertis"]),
        "amoureux": etat["amoureux"],
        "flair": list(etat["suspects"]),
        "meilleur_flair": [j for j in range(n) if haut > 0 and etat["suspects"][j] == haut],
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    etat["tireur"] = None
    etat["annonce"] = None
    etat["message"] = {
        "village": "Le village est sauvé : plus aucun loup !",
        "loups": "Les loups ont dévoré le village !",
        "loup_blanc": "Le Loup Blanc reste seul : il gagne !",
        "amoureux": "Les amoureux restent seuls au monde : ils gagnent !",
        "ange": "L'Ange est remonté au ciel dès le premier tour : il gagne !",
        "personne": "Il ne reste personne…",
    }.get(gagnant, "Fin de la partie.")
    ev.append(_tous({"type": "lg_fin", "final": etat["final"]}))


# =============================================================
# ACTIONS
# =============================================================
def _pret(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "role" or etat["prets"][i]:
        return []
    if etat["roles"][i] == "chien_loup" and message.get("camp") in ("loups", "village"):
        etat["chien"][str(i)] = message["camp"]
        etat["convertis"][i] = message["camp"] == "loups"
    etat["prets"][i] = True
    if all(etat["prets"][j] for j in _presents_vivants(etat)):
        _nouvelle_nuit(etat, ev, maintenant, alea)
    return ev


def _meute_d_accord(etat):
    loups = [j for j in _loups_vivants(etat) if not etat["partis"][j]]
    choix = {etat["meute"].get(str(j)) for j in loups}
    return bool(loups) and len(choix) == 1 and None not in choix


def _accord_meute(etat):
    d_accord = _meute_d_accord(etat)
    for j in _loups_vivants(etat):
        etat["agi"][j] = d_accord


def _nuit(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "nuit":
        return _erreur(i, "Ce n'est pas la nuit.")
    if not etat["vivants"][i]:
        return _erreur(i, "Les fantômes ne jouent plus.")
    n = etat["nb_joueurs"]
    cible = _indice(message.get("cible"), n)
    if cible is None or not etat["vivants"][cible]:
        return _erreur(i, "Touche un joueur encore en vie.")
    genre = _type_nuit(etat, i)
    if genre == "meute":
        if _est_loup(etat, cible):
            return _erreur(i, "Un loup ne dévore pas un loup.")
        role = etat["roles"][i]
        if role == "loup_blanc" and "solo" in message:
            solo = message.get("solo")
            if solo is None:
                etat["solo"] = None
            else:
                solo = _indice(solo, n)
                if solo is None or not _solo_possible(etat) or solo == i or not etat["vivants"][solo] \
                        or not _est_loup(etat, solo):
                    return _erreur(i, "Le Loup Blanc ne dévore qu'un autre loup, une nuit sur deux.")
                etat["solo"] = solo
        if role == "infect" and etat["infection"] and "infecter" in message:
            etat["infecter"] = message.get("infecter") is True
        etat["meute"][str(i)] = cible
        # Toute la meute voit le choix en direct.
        for j in _loups_vivants(etat):
            ev.append(_prive(j, {"type": "lg_meute", "loup": i, "cible": cible}))
        _accord_meute(etat)
    else:
        if cible == i and genre not in ("protection", "cupidon"):
            return _erreur(i, "Touche un autre joueur.")
        if genre == "vision":
            if str(i) in etat["actions"]:
                return _erreur(i, "Une seule vision par nuit.")
            vision = {"nuit": etat["nuit"], "cible": cible, "role": etat["roles"][cible]}
            etat["visions"].append(vision)
            ev.append(_prive(i, {"type": "lg_vision", **vision}))
        elif genre == "renard":
            if str(i) in etat["actions"]:
                return _erreur(i, "Un seul flair par nuit.")
            groupe = [cible] + [v for v in _voisins(etat, cible) if v != cible]
            loup = any(_est_loup(etat, g) for g in groupe)
            flair = {"nuit": etat["nuit"], "cible": cible, "groupe": groupe, "loup": loup}
            etat["flairs"].append(flair)
            if not loup:
                etat["renard_actif"] = False
            ev.append(_prive(i, {"type": "lg_renard", **flair}))
        elif genre == "protection":
            if cible == etat["dernier_protege"]:
                return _erreur(i, "Pas deux nuits de suite le même joueur.")
            etat["protege"] = cible
        elif genre == "cupidon":
            autre = _indice(message.get("cible2"), n)
            if autre is None or autre == cible or not etat["vivants"][autre]:
                return _erreur(i, "Choisis deux amoureux différents.")
            etat["couple"] = sorted([cible, autre])
        elif genre == "modele":
            etat["modele_choix"] = cible
        etat["actions"][str(i)] = cible
        etat["agi"][i] = True
    if not _attendus(etat):
        _fin_nuit(etat, ev, maintenant, alea)
    return ev


def _potion(etat, ev, i, message, maintenant, alea):
    if etat["phase"] != "potion" or _role_vivant(etat, "herboriste") != i:
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
            return _erreur(i, "Ta potion de mort est déjà utilisée.")
    etat["potion"] = {"soin": soin, "poison": poison}
    if soin:
        etat["potions"]["soin"] = False
    if poison is not None:
        etat["potions"]["poison"] = False
    _aube(etat, ev, maintenant, alea)
    return ev


def _tir(etat, ev, i, message, maintenant, alea):
    if etat["phase"] not in ("tir", "succession") or etat["tireur"] != i:
        return _erreur(i, "Ce n'est pas à toi de choisir.")
    cible = _indice(message.get("cible"), etat["nb_joueurs"])
    if cible is None or not etat["vivants"][cible] or cible == i:
        return _erreur(i, "Choisis un joueur encore en vie.")
    if etat["phase"] == "tir":
        _resoudre_tir(etat, ev, cible, maintenant, alea)
    else:
        _resoudre_succession(etat, ev, cible, maintenant, alea)
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


def _resoudre_succession(etat, ev, cible, maintenant, alea):
    ancien = etat["tireur"]
    if cible is None:
        vivants = _vivants(etat)
        cible = alea.choice(vivants) if vivants else None
    etat["capitaine"] = cible
    annonce = dict(etat["annonce"] or {"type": etat["retour"], "morts": []})
    annonce["succession"] = {"ancien": ancien, "capitaine": cible}
    etat["annonce"] = annonce
    etat["phase"] = etat["retour"] or "aube"
    etat["tireur"] = None
    etat["suivants"] = [False] * etat["nb_joueurs"]
    if cible is not None:
        etat["message"] = f"{etat['noms'][cible]} devient Capitaine."
    ev.append(_tous({"type": "lg_capitaine", "capitaine": cible, "election": False}))
    _armer(etat, maintenant, alea)


def _voter(etat, ev, i, message, maintenant, alea):
    phase = etat["phase"]
    if phase not in ("debat", "election"):
        return _erreur(i, "Ce n'est pas l'heure du vote.")
    if not etat["vivants"][i]:
        return _erreur(i, "Les fantômes ne votent pas.")
    if phase == "debat" and etat["idiot_revele"] == i:
        return _erreur(i, "L'Idiot du Village ne vote plus.")
    brut = message.get("cible")
    if brut is None:
        cible = None
    else:
        cible = _indice(brut, etat["nb_joueurs"])
        if cible is None or not etat["vivants"][cible] or (cible == i and phase == "debat"):
            return _erreur(i, "Vote pour un joueur en vie (ou vote blanc).")
    etat["votes"][str(i)] = cible
    ev.append(_prive(i, {"type": "lg_vote", "index": i, "cible": cible}))
    if not _attendus(etat):
        if phase == "election":
            _elire(etat, ev, maintenant, alea)
        else:
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
def _votants(etat):
    return [j for j in _vivants(etat) if not (etat["phase"] == "debat" and etat["idiot_revele"] == j)]


def _concernes(etat):
    """Ceux dont on attend quelque chose dans la phase (presents ou non)."""
    phase = etat["phase"]
    if phase in ("role", "aube", "verdict"):
        return list(range(etat["nb_joueurs"]))
    if phase == "nuit":
        return _vivants(etat)
    if phase in ("debat", "election"):
        return _votants(etat)
    return []


def _attendus(etat):
    phase = etat["phase"]
    presents = [i for i in range(etat["nb_joueurs"]) if not etat["partis"][i]]
    if phase == "role":
        return [j for j in _presents_vivants(etat) if not etat["prets"][j]]
    if phase == "nuit":
        return [j for j in _presents_vivants(etat) if not etat["agi"][j]]
    if phase in ("debat", "election"):
        return [j for j in _votants(etat) if not etat["partis"][j] and str(j) not in etat["votes"]]
    if phase in ("aube", "verdict"):
        return [j for j in presents if not etat["suivants"][j]]
    if phase == "potion":
        h = _role_vivant(etat, "herboriste")
        if h is None or etat["partis"][h] or etat["potion"] is not None:
            return []
        return [h]
    if phase in ("tir", "succession"):
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
            # Sorciere morte, partie ou a sec : on attend quand meme un peu,
            # pour que la duree ne trahisse rien.
            etat["echeance"] = maintenant + alea.uniform(_D["potion_vide_min"], _D["potion_vide_max"])
        return
    cle = "tir" if phase == "succession" else phase
    delai = _D[cle] if _attendus(etat) else _D["absent"]
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
    elif phase == "succession":
        _resoudre_succession(etat, ev, None, maintenant, alea)
    elif phase == "election":
        _elire(etat, ev, maintenant, alea)
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
    if etat["phase"] == "nuit" and _est_loup(etat, index):
        # La meute restante peut etre d accord sans lui.
        _accord_meute(etat)
    if etat["phase"] not in ("fin", "potion") and etat["echeance"] is not None and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"], maintenant + _D["absent"])


def lg_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] in ("nuit", "debat", "election", "role") and index in _attendus(etat):
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
