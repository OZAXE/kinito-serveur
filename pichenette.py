"""
Moteur de regles de Pichenette FC (foot de comptoir a pichenettes, prefixe pf_).

Meme principe que justepinte.py : moteur PUR, sans reseau ni horloge
implicite (le temps est passe en parametre). Chaque action prend l etat (un
dict serialisable en JSON) et renvoie la liste des messages a envoyer, au
format {"a": index ou None pour tous, "msg": {...}}. serveur.py diffuse
pf_etat apres chaque action acceptee, puis a chacun sa vue privee
(pf_vue : la formation et les visees de son equipe) et arme le minuteur sur
pf_prochaine_echeance. Chiffres : pichenette_config.json.

Regles :
  - Deux equipes, les Blondes (en bas, attaquent vers le haut) et les Brunes
    (en haut). 5 capsules (palets) par equipe et un bouchon (la balle).
    Equipes tirees au sort ; nombre impair : le dernier arrive a la table
    regarde la partie.
  - Phase « formation » : chaque equipe choisit, parmi 6 formations, une
    formation d attaque (apres avoir marque) et une de defense (apres avoir
    encaisse). Au premier coup d envoi, les deux equipes sont en defense.
  - Phase « visee » (chrono) : les deux equipes visent EN SECRET et EN MEME
    TEMPS. En 1 contre 1, chacun tire 2 capsules ; en equipes, chaque joueur
    en tire 1. Une capsule n est tiree qu une fois par tour. Chaque visee
    (capsule, direction, force) est gardee des qu elle est posee, meme sans
    valider ; la phase finit quand tous les presents ont valide, ou au chrono.
  - Phase « tir » : toutes les capsules visees partent ensemble. Le serveur
    calcule la physique (chocs entre disques, rebonds sur les bandes) et
    envoie la trajectoire a tous les telephones, qui la rejouent.
  - But : le bouchon passe entierement la ligne entre les poteaux. Chaque
    joueur de l equipe qui encaisse boit 1 gorgee ; si la derniere capsule a
    toucher le bouchon etait de cette equipe et avait ete tiree ce tour,
    son tireur boit 2 de plus (contre son camp). Les deux equipes se
    replacent (formation d attaque pour celle qui a marque, de defense pour
    l autre), bouchon au centre.
  - Premiere equipe a N buts (3, 4 ou 5) : chaque joueur de l equipe perdante
    boit 5 gorgees.
"""
import json
import math
import os
import random

_ICI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_ICI, "pichenette_config.json"), encoding="utf-8") as _f:
    PF_CONFIG = json.load(_f)

PF_JOUEURS_MIN = PF_CONFIG["joueurs"]["min"]
PF_JOUEURS_MAX = PF_CONFIG["joueurs"]["max"]
NB_PALETS = PF_CONFIG["palets_par_equipe"]
_D = PF_CONFIG["delais"]
_G = PF_CONFIG["gorgees"]
_T = PF_CONFIG["terrain"]
_P = PF_CONFIG["physique"]
FORMATIONS = {f["id"]: f for f in PF_CONFIG["formations"]}
NOMS_EQUIPES = ["Les Blondes", "Les Brunes"]


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


def _joueurs_actifs(etat):
    """Les joueurs presents qui jouent (pas les spectateurs)."""
    return [i for i in _presents(etat) if etat["equipe_de"][i] is not None]


def tirs_par_joueur(etat, i):
    e = etat["equipe_de"][i]
    if e is None:
        return 0
    return 2 if len(etat["equipes"][e]) == 1 else 1


def _equipe_du_palet(k):
    return k // NB_PALETS


def _nombre(v, bas, haut):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = float(v)
    if not math.isfinite(x):
        return None
    return min(haut, max(bas, x))


# =============================================================
# FORMATIONS
# =============================================================
def positions_formation(fid, equipe):
    """Positions des capsules d une equipe (l equipe 1 les recoit retournees)."""
    pts = FORMATIONS[fid]["palets"]
    if equipe == 0:
        return [[float(x), float(y)] for x, y in pts]
    return [[-float(x), -float(y)] for x, y in pts]


def _placer(etat, form0, form1):
    etat["palets"] = positions_formation(form0, 0) + positions_formation(form1, 1)
    etat["balle"] = [0.0, 0.0]


# =============================================================
# PHYSIQUE
# Disques sur un plan : frottement (deceleration constante + amorti),
# chocs entre disques (impulsion, coefficient de restitution), rebonds sur
# des segments (bandes, poteaux, filets). Pas fixe : le resultat ne depend
# que des entrees. Sortie : une image toutes les 1/images_par_s secondes,
# coordonnees en millimetres de terrain (entiers), pour que tous les
# telephones rejouent exactement la meme chose.
# =============================================================
def _segments():
    """(x1, y1, x2, y2, rebond) : bandes, puis cages (poteaux et filets)."""
    lx, ly = _T["largeur"] / 2, _T["longueur"] / 2
    g, p = _T["but_largeur"] / 2, _T["but_profondeur"]
    rb, rf = _P["rebond_bords"], _P["rebond_filet"]
    segs = [(-lx, -ly, -lx, ly, rb), (lx, -ly, lx, ly, rb)]
    for s in (-1, 1):
        y = s * ly
        segs += [(-lx, y, -g, y, rb), (g, y, lx, y, rb),
                 (-g, y, -g, y + s * p, rf), (g, y, g, y + s * p, rf),
                 (-g, y + s * p, g, y + s * p, rf)]
    return segs


SEGMENTS = _segments()


def _sortir_des_cages(pos, n, ray):
    """Replace devant la ligne de but les capsules dont le centre est dans une cage."""
    ly, g = _T["longueur"] / 2, _T["but_largeur"] / 2
    lx = _T["largeur"] / 2
    bouge = False
    for i in range(n):
        x, y = pos[i]
        if abs(y) <= ly:
            continue
        s = 1 if y > 0 else -1
        r = ray[i]
        ty = s * (ly - r - 0.004)
        place = None
        # On cherche une place libre : d abord pile devant, puis de part et
        # d autre, puis un rang plus haut sur le terrain.
        for rang in range(6):
            yy = ty - s * rang * 2.1 * r
            for k in range(12):
                dec = (k + 1) // 2 * 2.1 * r * (1 if k % 2 else -1)
                xx = max(-lx + r, min(lx - r, x + dec))
                if all((xx - pos[j][0]) ** 2 + (yy - pos[j][1]) ** 2 >= (r + ray[j] + 0.002) ** 2
                       for j in range(len(pos)) if j != i):
                    place = (xx, yy)
                    break
            if place:
                break
        pos[i] = list(place or (max(-g, min(g, x)), ty))
        bouge = True
    return bouge


def simuler(palets, balle, tirs):
    """
    palets : [[x, y]] (2 x NB_PALETS), balle : [x, y].
    tirs : [(palet, dx, dy, force)] avec (dx, dy) unitaire, force dans [0, 1].
    Renvoie {"images": [[x0, y0, x1, y1, ...]], "palets", "balle", "but": None|0|1
    (equipe qui MARQUE), "dernier": palet qui a touche la balle en dernier ou None,
    "duree": secondes}.
    """
    n = len(palets)
    pos = [list(map(float, p)) for p in palets] + [list(map(float, balle))]
    vit = [[0.0, 0.0] for _ in range(n + 1)]
    ray = [PF_CONFIG["palet"]["rayon"]] * n + [PF_CONFIG["balle"]["rayon"]]
    inv = [1.0 / PF_CONFIG["palet"]["masse"]] * n + [1.0 / PF_CONFIG["balle"]["masse"]]
    vmax = _P["vitesse_max"]
    for k, dx, dy, f in tirs:
        vit[k] = [dx * vmax * f, dy * vmax * f]
    dt = _P["pas"]
    frott, amorti = _P["frottement"] * dt, max(0.0, 1.0 - _P["amorti"] * dt)
    e_d = _P["rebond_disques"]
    arret = _P["arret"]
    tous = n + 1
    ly, g = _T["longueur"] / 2, _T["but_largeur"] / 2
    lx_libre, ly_libre = _T["largeur"] / 2 - 0.01, ly - 0.01
    pas_image = max(1, round(1.0 / (_P["images_par_s"] * dt)))
    pas_max = int(_P["duree_max"] / dt)

    def image():
        return [round(c * 1000) for p in pos for c in p]

    images = [image()]
    dernier = None
    but = None
    etape = 0
    while etape < pas_max:
        etape += 1
        # Deplacement et frottement.
        for i in range(tous):
            vx, vy = vit[i]
            if vx == 0.0 and vy == 0.0:
                continue
            pos[i][0] += vx * dt
            pos[i][1] += vy * dt
            v = math.hypot(vx, vy)
            nv = v * amorti - frott
            if nv <= 0:
                vit[i] = [0.0, 0.0]
            else:
                vit[i] = [vx * nv / v, vy * nv / v]
        # Chocs entre disques (deux disques arretes ne se cherchent pas).
        bouge = [vit[i][0] != 0.0 or vit[i][1] != 0.0 for i in range(tous)]
        for i in range(tous):
            for j in range(i + 1, tous):
                if not (bouge[i] or bouge[j]):
                    continue
                dx = pos[j][0] - pos[i][0]
                dy = pos[j][1] - pos[i][1]
                r = ray[i] + ray[j]
                d2 = dx * dx + dy * dy
                if d2 >= r * r:
                    continue
                d = math.sqrt(d2)
                if d < 1e-9:
                    nx, ny, d = 1.0, 0.0, 0.0
                else:
                    nx, ny = dx / d, dy / d
                # On les separe (au prorata des masses).
                rec = (r - d) / (inv[i] + inv[j])
                pos[i][0] -= nx * rec * inv[i]
                pos[i][1] -= ny * rec * inv[i]
                pos[j][0] += nx * rec * inv[j]
                pos[j][1] += ny * rec * inv[j]
                rv = (vit[j][0] - vit[i][0]) * nx + (vit[j][1] - vit[i][1]) * ny
                if rv < 0:
                    imp = -(1 + e_d) * rv / (inv[i] + inv[j])
                    vit[i][0] -= imp * inv[i] * nx
                    vit[i][1] -= imp * inv[i] * ny
                    vit[j][0] += imp * inv[j] * nx
                    vit[j][1] += imp * inv[j] * ny
                if j == n and i < n:
                    dernier = i
        # Bandes, poteaux et filets.
        for i in range(tous):
            r = ray[i]
            # Loin des bandes et des cages : rien a tester.
            if abs(pos[i][0]) < lx_libre - r and abs(pos[i][1]) < ly_libre - r:
                continue
            for x1, y1, x2, y2, reb in SEGMENTS:
                sx, sy = x2 - x1, y2 - y1
                l2 = sx * sx + sy * sy
                t = ((pos[i][0] - x1) * sx + (pos[i][1] - y1) * sy) / l2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                cx, cy = x1 + sx * t, y1 + sy * t
                dx, dy = pos[i][0] - cx, pos[i][1] - cy
                d2 = dx * dx + dy * dy
                if d2 >= r * r:
                    continue
                d = math.sqrt(d2)
                if d < 1e-9:
                    # Centre pile sur le segment : on pousse vers le terrain.
                    nx, ny = (-sy / math.sqrt(l2), sx / math.sqrt(l2))
                    if nx * -pos[i][0] + ny * -pos[i][1] < 0:
                        nx, ny = -nx, -ny
                else:
                    nx, ny = dx / d, dy / d
                pos[i][0] += nx * (r - d)
                pos[i][1] += ny * (r - d)
                vn = vit[i][0] * nx + vit[i][1] * ny
                if vn < 0:
                    vit[i][0] -= (1 + reb) * vn * nx
                    vit[i][1] -= (1 + reb) * vn * ny
        # But : le bouchon a passe la ligne en entier, entre les poteaux.
        bx, by = pos[n]
        if abs(bx) < g and abs(by) > ly + ray[n]:
            but = 0 if by > 0 else 1
        if etape % pas_image == 0 or but is not None:
            images.append(image())
        if but is not None:
            break
        # Tout s est arrete.
        if all(math.hypot(vx, vy) < arret for vx, vy in vit):
            if etape % pas_image != 0:
                images.append(image())
            break
    # Capsule arretee dans une cage : on la ressort juste devant la ligne,
    # pour qu elle ne bouche pas le but (image en plus pour le replay).
    if but is None and _sortir_des_cages(pos, n, ray):
        images.append(image())
    return {
        "images": images,
        "palets": [[round(p[0], 4), round(p[1], 4)] for p in pos[:n]],
        "balle": [round(pos[n][0], 4), round(pos[n][1], 4)],
        "but": but,
        "dernier": dernier,
        "duree": round(etape * dt, 3),
    }


# =============================================================
# CREATION
# =============================================================
def pf_initialiser(noms, maintenant, buts=None, rng=None):
    """noms : dans l ordre d arrivee a la table (le dernier regarde si impair)."""
    rng = rng or random.Random()
    n = len(noms)
    try:
        buts = int(buts)
    except (TypeError, ValueError, OverflowError):
        buts = None
    if buts not in PF_CONFIG["buts"]["choix"]:
        buts = PF_CONFIG["buts"]["defaut"]
    joueurs = list(range(n))
    spectateurs = []
    if n % 2 == 1:
        spectateurs = [joueurs.pop()]
    rng.shuffle(joueurs)
    moitie = len(joueurs) // 2
    equipes = [sorted(joueurs[:moitie]), sorted(joueurs[moitie:])]
    equipe_de = [None] * n
    for e, membres in enumerate(equipes):
        for i in membres:
            equipe_de[i] = e
    d = PF_CONFIG["formation_defaut"]
    etat = {
        "version": 0,
        "nb_joueurs": n,
        "noms": list(noms),
        "partis": [False] * n,
        "equipes": equipes,
        "equipe_de": equipe_de,
        "spectateurs": spectateurs,
        "buts_victoire": buts,
        "score": [0, 0],
        "gorgees": [0] * n,
        "formations": [{"attaque": d["attaque"], "defense": d["defense"], "valide": False} for _ in range(2)],
        "phase": "formation",
        "tour": 0,
        "palets": [],
        "balle": [0.0, 0.0],
        # SECRET (visible de la seule equipe) : str(joueur) -> [{palet, dx, dy, force}]
        "visees": {},
        "prets": [False] * n,
        "tir": None,              # dernier tir joue (public) : images, tirs, but, gorgees
        "buts": [],               # [{tour, equipe, contre_son_camp, tireur}]
        "final": None,
        "echeance": None,
        "message": "Choisissez vos formations !",
    }
    _placer(etat, d["defense"], d["defense"])
    _armer(etat, maintenant)
    return etat


# =============================================================
# ETAT PUBLIC ET VUES PRIVEES
# =============================================================
def _visees_equipe(etat, e):
    """Les visees des joueurs de l equipe e : {joueur: [...]}."""
    return {int(j): v for j, v in etat["visees"].items() if etat["equipe_de"][int(j)] == e}


def pf_etat_public(etat, maintenant):
    delai = None
    if etat["echeance"] is not None:
        delai = max(0, round(etat["echeance"] - maintenant))
    phase = etat["phase"]
    return {
        "type": "pf_etat",
        "version": etat["version"],
        "joueurs": etat["noms"],
        "partis": etat["partis"],
        "equipes": etat["equipes"],
        "equipe_de": etat["equipe_de"],
        "noms_equipes": NOMS_EQUIPES,
        "spectateurs": etat["spectateurs"],
        "buts_victoire": etat["buts_victoire"],
        "score": etat["score"],
        "gorgees": etat["gorgees"],
        "phase": phase,
        "tour": etat["tour"],
        "palets": etat["palets"],
        "balle": etat["balle"],
        # Les formations restent secretes jusqu au coup d envoi ; on sait
        # seulement qui a valide.
        "formations_validees": [f["valide"] for f in etat["formations"]],
        "formations": ([{"attaque": f["attaque"], "defense": f["defense"]} for f in etat["formations"]]
                       if phase != "formation" else None),
        # Qui a vise / valide : jamais OU il vise.
        "a_vise": [len(etat["visees"].get(str(i), [])) for i in range(etat["nb_joueurs"])],
        "prets": etat["prets"],
        # Le dernier tir, sans sa trajectoire (envoyee une fois, par pf_tir).
        "tir": ({k: v for k, v in etat["tir"].items() if k != "images"}
                if phase in ("tir", "fin") and etat["tir"] else None),
        "buts": etat["buts"],
        "final": etat["final"],
        "delai": delai,
        "message": etat["message"],
    }


def pf_vue(etat, i):
    """Ce que le joueur i voit en plus : les choix secrets de son equipe."""
    if not 0 <= i < etat["nb_joueurs"]:
        return None
    e = etat["equipe_de"][i]
    if e is None:
        return None
    f = etat["formations"][e]
    return {"type": "pf_vue", "index": i, "equipe": e, "tour": etat["tour"],
            "formation": {"attaque": f["attaque"], "defense": f["defense"], "valide": f["valide"]},
            "visees": {str(j): v for j, v in _visees_equipe(etat, e).items()}}


def pf_catalogue():
    """Les formations et le terrain (pour la page)."""
    return {"type": "pf_catalogue", "formations": PF_CONFIG["formations"], "terrain": _T,
            "palet": PF_CONFIG["palet"]["rayon"], "balle": PF_CONFIG["balle"]["rayon"],
            "palets_par_equipe": NB_PALETS, "images_par_s": _P["images_par_s"],
            "force_max": _P["vitesse_max"], "gorgees": _G}


# =============================================================
# DEROULEMENT
# =============================================================
def _coup_envoi(etat, ev, maintenant):
    etat["phase"] = "visee"
    etat["tour"] += 1
    etat["visees"] = {}
    etat["prets"] = [False] * etat["nb_joueurs"]
    etat["tir"] = None
    if etat["tour"] == 1:
        etat["message"] = "Coup d'envoi ! Visez en secret."
    _armer(etat, maintenant)


def _formation(etat, ev, i, message, maintenant):
    if etat["phase"] != "formation":
        return []
    e = etat["equipe_de"][i]
    if e is None:
        return []
    f = etat["formations"][e]
    att, dfn = message.get("attaque"), message.get("defense")
    if att is not None:
        if att not in FORMATIONS:
            return _erreur(i, "Formation inconnue.")
        f["attaque"] = att
    if dfn is not None:
        if dfn not in FORMATIONS:
            return _erreur(i, "Formation inconnue.")
        f["defense"] = dfn
    if message.get("valider") is True:
        f["valide"] = True
    elif att is not None or dfn is not None:
        f["valide"] = False
    if all(x["valide"] for x in etat["formations"]):
        _placer(etat, etat["formations"][0]["defense"], etat["formations"][1]["defense"])
        _coup_envoi(etat, ev, maintenant)
    return ev


def _viser(etat, ev, i, message, maintenant):
    """{"tirs": [{"palet", "dx", "dy", "force"}], "pret": bool} : remplace les visees de i."""
    if etat["phase"] != "visee":
        return []
    e = etat["equipe_de"][i]
    if e is None:
        return []
    tirs = message.get("tirs")
    if not isinstance(tirs, list):
        return _erreur(i, "Visée illisible.")
    maxi = tirs_par_joueur(etat, i)
    if len(tirs) > maxi:
        return _erreur(i, f"Tu tires {maxi} capsule{'s' if maxi > 1 else ''} par tour.")
    pris = {v["palet"] for j, vs in _visees_equipe(etat, e).items() if j != i for v in vs}
    propres = []
    for t in tirs:
        if not isinstance(t, dict):
            return _erreur(i, "Visée illisible.")
        k = t.get("palet")
        if isinstance(k, bool) or not isinstance(k, int) or _equipe_du_palet(k) != e or not 0 <= k < 2 * NB_PALETS:
            return _erreur(i, "Ce n'est pas une de tes capsules.")
        if k in pris:
            return _erreur(i, "Un coéquipier tire déjà cette capsule.")
        if any(p["palet"] == k for p in propres):
            return _erreur(i, "Une capsule ne se tire qu'une fois par tour.")
        dx, dy = _nombre(t.get("dx"), -1e6, 1e6), _nombre(t.get("dy"), -1e6, 1e6)
        force = _nombre(t.get("force"), 0.0, 1.0)
        if dx is None or dy is None or force is None:
            return _erreur(i, "Visée illisible.")
        lg = math.hypot(dx, dy)
        if lg < 1e-6:
            return _erreur(i, "Donne une direction à ton tir.")
        propres.append({"palet": k, "dx": round(dx / lg, 5), "dy": round(dy / lg, 5), "force": round(force, 4)})
    if propres:
        etat["visees"][str(i)] = propres
    else:
        etat["visees"].pop(str(i), None)
    etat["prets"][i] = message.get("pret") is True
    if all(etat["prets"][j] for j in _joueurs_actifs(etat)):
        _tirer(etat, ev, maintenant)
    return ev


def _tirer(etat, ev, maintenant):
    """Toutes les visees partent ensemble."""
    tirs, tireurs = [], {}
    for j, vs in etat["visees"].items():
        for v in vs:
            tirs.append((v["palet"], v["dx"], v["dy"], v["force"]))
            tireurs[v["palet"]] = int(j)
    tirs.sort()
    res = simuler(etat["palets"], etat["balle"], tirs)
    etat["palets"], etat["balle"] = res["palets"], res["balle"]
    noms = etat["noms"]
    tir = {
        "tour": etat["tour"],
        "depart": None,
        "tirs": [{"palet": k, "dx": dx, "dy": dy, "force": f, "joueur": tireurs[k]} for k, dx, dy, f in tirs],
        "images": res["images"],
        "duree": res["duree"],
        "but": res["but"],
        "contre_son_camp": None,
        "boit": [],
    }
    pause = _D["apres_tir"]
    if res["but"] is not None:
        marque = res["but"]
        encaisse = 1 - marque
        etat["score"][marque] += 1
        boit = {}
        for j in etat["equipes"][encaisse]:
            if not etat["partis"][j]:
                boit[j] = boit.get(j, 0) + _G["but_encaisse"]
        csc = None
        k = res["dernier"]
        if k is not None and _equipe_du_palet(k) == encaisse and k in tireurs:
            csc = tireurs[k]
            boit[csc] = boit.get(csc, 0) + _G["contre_son_camp"]
        elif k is not None and _equipe_du_palet(k) == encaisse:
            csc = -1           # contre son camp, mais d une capsule poussee : personne en plus
        for j, g in boit.items():
            etat["gorgees"][j] += g
        tir["contre_son_camp"] = csc
        tir["boit"] = [{"joueur": j, "gorgees": g} for j, g in sorted(boit.items())]
        buteur = tireurs.get(k) if k is not None and _equipe_du_palet(k) == marque else None
        tir["buteur"] = buteur
        etat["buts"].append({"tour": etat["tour"], "equipe": marque,
                             "contre_son_camp": csc is not None, "buteur": buteur})
        if csc is not None and csc >= 0:
            etat["message"] = f"Contre son camp de {noms[csc]} ! But pour {NOMS_EQUIPES[marque]}."
        elif csc is not None:
            etat["message"] = f"Contre son camp ! But pour {NOMS_EQUIPES[marque]}."
        elif buteur is not None:
            etat["message"] = f"BUUUT de {noms[buteur]} ! {etat['score'][0]} - {etat['score'][1]}"
        else:
            etat["message"] = f"BUUUT pour {NOMS_EQUIPES[marque]} ! {etat['score'][0]} - {etat['score'][1]}"
        pause = _D["apres_but"]
        if etat["score"][marque] >= etat["buts_victoire"]:
            _terminer(etat, marque)
        else:
            f = etat["formations"]
            if marque == 0:
                _placer(etat, f[0]["attaque"], f[1]["defense"])
            else:
                _placer(etat, f[0]["defense"], f[1]["attaque"])
    else:
        etat["message"] = "Pas de but. On revise !" if tirs else "Personne n'a tiré !"
    etat["tir"] = tir
    if etat["phase"] != "fin":
        etat["phase"] = "tir"
        etat["echeance"] = maintenant + res["duree"] + pause
    ev.append(_tous({"type": "pf_tir", "tir": tir, "score": etat["score"]}))


def _terminer(etat, gagnante):
    perdante = 1 - gagnante
    derniers = []
    for j in etat["equipes"][perdante]:
        etat["gorgees"][j] += _G["defaite"]
        derniers.append({"joueur": j, "boit": _G["defaite"]})
    etat["final"] = {
        "gagnante": gagnante,
        "gagnants": list(etat["equipes"][gagnante]),
        "perdants": list(etat["equipes"][perdante]),
        "derniers": derniers,
        "score": list(etat["score"]),
    }
    etat["phase"] = "fin"
    etat["echeance"] = None
    noms = etat["noms"]
    etat["message"] = (f"Victoire des {NOMS_EQUIPES[gagnante].split(' ', 1)[1]} "
                       f"({', '.join(noms[j] for j in etat['equipes'][gagnante])}) !")


PF_ACTIONS = {
    "pf_formation": _formation,
    "pf_viser": _viser,
}


def pf_action(etat, i, message, maintenant):
    fn = PF_ACTIONS.get(message.get("type"))
    if fn is None or not 0 <= i < etat["nb_joueurs"]:
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
    if etat["phase"] == "formation":
        return [j for j in _joueurs_actifs(etat) if not etat["formations"][etat["equipe_de"][j]]["valide"]]
    if etat["phase"] == "visee":
        return [j for j in _joueurs_actifs(etat) if not etat["prets"][j]]
    return []


def _armer(etat, maintenant):
    phase = etat["phase"]
    if phase in ("formation", "visee"):
        etat["echeance"] = maintenant + (_D[phase] if _attendus(etat) else _D["absent"])
    elif phase != "tir":
        etat["echeance"] = None


def pf_prochaine_echeance(etat):
    return etat["echeance"]


def pf_expirer(etat, maintenant):
    """L echeance est passee : on avance sans ceux qu on attend."""
    if etat["echeance"] is None or maintenant < etat["echeance"]:
        return []
    ev = []
    avant = etat["version"]
    if etat["phase"] == "formation":
        for f in etat["formations"]:
            f["valide"] = True
        _placer(etat, etat["formations"][0]["defense"], etat["formations"][1]["defense"])
        _coup_envoi(etat, ev, maintenant)
    elif etat["phase"] == "visee":
        _tirer(etat, ev, maintenant)
    elif etat["phase"] == "tir":
        _coup_envoi(etat, ev, maintenant)
    else:
        etat["echeance"] = None
        return ev
    etat["version"] = avant + 1
    return ev


# =============================================================
# DEPARTS ET RETOURS
# =============================================================
def pf_joueur_parti(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = True
    etat["version"] += 1
    if etat["phase"] in ("formation", "visee") and not _attendus(etat):
        etat["echeance"] = min(etat["echeance"] or maintenant, maintenant + _D["absent"])


def pf_joueur_revenu(etat, index, maintenant):
    if not 0 <= index < etat["nb_joueurs"]:
        return
    etat["partis"][index] = False
    etat["version"] += 1
    if etat["phase"] == "visee" and etat["equipe_de"][index] is not None and not etat["prets"][index]:
        etat["echeance"] = max(etat["echeance"] or 0, maintenant + _D["retour"])
