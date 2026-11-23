"""
Fabrique tournee_plateaux.json : les trois plateaux de La Tournee.

Usage : python tournee_plateaux.py   (reecrit tournee_plateaux.json)
        python tournee_plateaux.py svg (dessine aussi tournee_plateaux_<id>.svg pour verifier)

Construits comme les cartes de Mario Party : une grande boucle decoupee en
quartiers (zone), des embranchements ou le joueur choisit sa route, un ou deux
raccourcis (dont un peage), un gadget propre a la carte (bateau, tram,
geysers, trappes), deux boutiques, des cases objet et quelques emplacements
possibles pour la capsule d or. Le relief est donne par la hauteur y de
chaque case : le site modele les collines, escaliers et ponts autour.

Les coordonnees (x, z) vont d environ -2.5 a 2.5 (le depart en bas, z > 0),
y de 0 (bord de l eau) a environ 1 (sommet). Une case tous les ~0,24.

Chaque case : {id, type, x, z, y, suivants, zone, vers?, moyen?, nom?, stock?}
  - type : depart, pieces, piege, evenement, tournee, chance, duel, trappe,
    objet, boutique, peage, transport
  - suivants : les cases ou l on peut aller ensuite (2 = embranchement)
  - zone : le quartier (affiche au choix du chemin)
  - vers : trappe (aller-retour) ou transport (aller simple) : ou l on ressort
  - moyen : pour un transport, bateau, tram ou geyser
  - nom, stock : pour une boutique, son nom et ses objets
"""
import json
import math
import os
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
ECART = 0.27   # distance visee entre deux cases


# =============================================================
# GEOMETRIE : courbes de Catmull-Rom echantillonnees a pas regulier
# =============================================================
def _cr(p0, p1, p2, p3, t):
    return tuple(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t ** 3)
                 for a, b, c, d in zip(p0, p1, p2, p3))


def _dense(points, ferme):
    """Points serres le long de la courbe, avec la longueur cumulee et l indice du point de controle."""
    n = len(points)
    segs = n if ferme else n - 1
    out = []
    for i in range(segs):
        p0 = points[(i - 1) % n] if ferme or i > 0 else points[0]
        p1, p2 = points[i], points[(i + 1) % n]
        p3 = points[(i + 2) % n] if ferme or i + 2 < n else points[-1]
        for k in range(40):
            out.append((_cr(p0, p1, p2, p3, k / 40), i + k / 40))
    if not ferme:
        out.append((points[-1], n - 1))
    longs = [0.0]
    for a, b in zip(out, out[1:]):
        longs.append(longs[-1] + math.dist((a[0][0], a[0][1]), (b[0][0], b[0][1])))
    if ferme:
        longs.append(longs[-1] + math.dist(out[-1][0][:2], out[0][0][:2]))
    return out, longs


def _a_longueur(dense, longs, l):
    for k in range(1, len(dense)):
        if longs[k] >= l:
            a, b = dense[k - 1][0], dense[k][0]
            u = (l - longs[k - 1]) / max(1e-9, longs[k] - longs[k - 1])
            return tuple(x + (y - x) * u for x, y in zip(a, b))
    return dense[-1][0]


def _longueur_au_point(dense, longs, indice):
    for k, (_, t) in enumerate(dense):
        if t >= indice - 1e-9:
            return longs[k]
    return longs[-1]


def _ajuster(types, voulu):
    """Le nombre de cases suit la longueur du trajet : on ajoute ou retire des cases pieces,
    reparties regulierement, sans toucher aux cases speciales."""
    types = list(types)
    n = max(2, round(voulu))
    while len(types) < n:
        # On glisse une case pieces au milieu du plus long passage sans case pieces.
        k = max(range(1, len(types) + 1), key=lambda i: _sans_pieces(types, i))
        types.insert(k, "pieces")
    while len(types) > n:
        pieces = [i for i, t in enumerate(types) if t == "pieces"]
        if not pieces:
            break
        k = max(pieces, key=lambda i: sum(1 for j in pieces if abs(j - i) <= 2))
        del types[k]
    return types


def _sans_pieces(types, i):
    """Longueur du passage sans case pieces autour de la position i."""
    g = i - 1
    while g >= 0 and types[g] != "pieces":
        g -= 1
    d = i
    while d < len(types) and types[d] != "pieces":
        d += 1
    return d - g


def _case(id_, type_, p, zone):
    return {"id": id_, "type": type_, "x": round(p[0], 3), "z": round(p[1], 3), "y": round(p[2], 3),
            "suivants": [], "zone": zone}


class Carte:
    def __init__(self):
        self.cases = []

    def boucle(self, quartiers):
        """quartiers : [(zone, [points (x, z, y)], [types])]. La boucle passe par tous les
        points ; chaque quartier va de son premier point au premier du suivant."""
        points, debuts = [], []
        for zone, pts, types in quartiers:
            debuts.append(len(points))
            points.extend(pts)
        dense, longs = _dense(points, True)
        total = longs[-1]
        bornes = [_longueur_au_point(dense, longs, d) for d in debuts] + [total]
        premier = len(self.cases)
        for q, (zone, pts, types) in enumerate(quartiers):
            l0, l1 = bornes[q], bornes[q + 1]
            types = _ajuster(types, (l1 - l0) / ECART)
            for i, t in enumerate(types):
                p = _a_longueur(dense + [dense[0]], longs, l0 + (l1 - l0) * i / len(types))
                self.cases.append(_case(len(self.cases), t, p, zone))
        ids = list(range(premier, len(self.cases)))
        for a, b in zip(ids, ids[1:] + ids[:1]):
            self.cases[a]["suivants"].append(b)
        return ids

    def pres(self, x, z, parmi=None):
        """La case la plus proche du point (x, z)."""
        liste = parmi if parmi is not None else range(len(self.cases))
        return min(liste, key=lambda c: (self.cases[c]["x"] - x) ** 2 + (self.cases[c]["z"] - z) ** 2)

    def branche(self, depuis, vers, points, types, zone):
        """Une route de la case depuis a la case vers, par les points donnes."""
        a, b = self.cases[depuis], self.cases[vers]
        tous = [(a["x"], a["z"], a["y"])] + list(points) + [(b["x"], b["z"], b["y"])]
        dense, longs = _dense(tous, False)
        total = longs[-1]
        premier = len(self.cases)
        types = _ajuster(types, total / ECART - 1)
        n = len(types)
        for i, t in enumerate(types):
            p = _a_longueur(dense, longs, total * (i + 1) / (n + 1))
            self.cases.append(_case(len(self.cases), t, p, zone))
        for i in range(n):
            self.cases[premier + i]["suivants"].append(premier + i + 1 if i < n - 1 else vers)
        a["suivants"].append(premier)
        return list(range(premier, premier + n))

    def poser(self, c, type_, **champs):
        self.cases[c]["type"] = type_
        self.cases[c].update(champs)


# =============================================================
# LE PORT (La Terrasse, facile)
# =============================================================
def terrasse():
    k = Carte()
    boucle = k.boucle([
        ("les quais", [(0.25, 1.72, 0.05), (-0.35, 1.78, 0.05)],
         ["depart", "pieces", "objet"]),
        ("la plage", [(-0.95, 1.58, 0.03), (-1.45, 1.8, 0.03), (-2.05, 1.58, 0.03)],
         ["pieces", "evenement", "pieces", "transport", "pieces", "chance"]),
        ("les dunes", [(-2.38, 1.05, 0.08), (-1.85, 0.6, 0.16), (-2.3, 0.08, 0.24)],
         ["pieces", "boutique", "pieces", "piege", "pieces", "evenement"]),
        ("la colline du phare", [(-2.2, -0.55, 0.4), (-1.68, -0.86, 0.58), (-1.4, -1.45, 0.82), (-0.75, -1.78, 0.8)],
         ["pieces", "chance", "pieces", "objet", "pieces", "pieces", "evenement", "pieces", "tournee", "pieces"]),
        ("les jardins", [(-0.15, -1.45, 0.62), (0.45, -1.82, 0.5), (1.15, -1.62, 0.44)],
         ["pieces", "piege", "pieces", "chance", "pieces"]),
        ("le belvédère", [(1.75, -1.88, 0.52), (2.38, -1.4, 0.44), (2.1, -0.78, 0.32)],
         ["evenement", "pieces", "piege", "pieces", "objet", "pieces"]),
        ("le marché", [(2.45, -0.2, 0.2), (2.08, 0.38, 0.12), (2.4, 0.95, 0.08)],
         ["pieces", "boutique", "pieces", "evenement", "pieces", "chance"]),
        ("le port de pêche", [(1.88, 1.42, 0.05), (1.22, 1.25, 0.05), (0.78, 1.72, 0.05)],
         ["pieces", "tournee", "pieces", "piege", "pieces", "chance", "pieces"]),
    ])
    # La rue du village : des dunes, on coupe par le village au lieu de grimper au phare.
    debut = k.pres(-1.85, 0.6, boucle)
    fin = k.pres(-0.15, -1.45, boucle)
    k.branche(debut, fin, [(-1.15, 0.52, 0.2), (-0.55, 0.1, 0.28), (-0.3, -0.62, 0.42)],
              ["pieces", "objet", "pieces", "evenement", "pieces", "chance", "pieces", "piege"],
              "la rue du village")
    # La jetée : du belvédère, on redescend par la jetée vers le port de pêche.
    debut = k.pres(2.1, -0.78, boucle)
    fin = k.pres(1.22, 1.25, boucle)
    k.branche(debut, fin, [(1.4, -0.62, 0.22), (0.8, -0.12, 0.14), (0.55, 0.55, 0.08), (0.95, 0.95, 0.06)],
              ["pieces", "chance", "pieces", "objet", "pieces", "evenement", "pieces", "tournee", "pieces"],
              "la jetée")
    # Le bateau : de la plage, on file par la mer jusqu au marché.
    bateau = [c for c in boucle if k.cases[c]["type"] == "transport"][0]
    k.poser(bateau, "transport", moyen="bateau", vers=k.pres(2.08, 0.38, boucle))
    _boutiques(k, [("L'épicerie des dunes", ["cafe", "double", "pipe", "bouclier"]),
                   ("La halle du marché", ["cafe", "double", "triple", "chaises", "taxi"])])
    return {
        "id": "terrasse", "nom": "La Terrasse", "difficulte": "facile", "icone": "🌞",
        "description": "Un port au soleil : la plage, les dunes, la colline du phare, le marché et la jetée. Un bateau pour filer par la mer, peu de pièges, on boit peu.",
        "cases": k.cases,
        "capsule_possibles": _emplacements(k, [(-1.4, -1.45), (2.38, -1.4), (-1.45, 1.8), (-0.55, 0.1), (0.8, -0.12), (1.88, 1.42), (0.45, -1.82), (2.45, -0.2)]),
        "videur": None,
        "prix_capsule": 20,
        "peage": 0,
        "pieces": {"pieces": 3, "piege": -2, "depart": 5},
        "boire": {"piege": 1, "tournee": 1, "duel": 1, "minijeu_dernier": 1, "fin_dernier": 2, "videur": 0},
        # Facile : pas de vol, pas d echange, peu de retournements.
        "evenements": ["pourboire", "raccourci", "addition", "chanson"],
        "chance": ["jackpot", "poche_trouee", "elan"],
        "objets_case": ["cafe", "double", "pipe", "bouclier"],
        "duel_capsule": False,
        "evenements_plateau": {"tous_les": 4, "liste": ["soleil", "apero", "promo_capsule", "soldes"]},
    }


# =============================================================
# LA VILLE (Le Pub, moyen)
# =============================================================
def pub():
    k = Carte()
    boucle = k.boucle([
        ("la grand-place", [(0.0, 1.76, 0.05), (-0.6, 1.76, 0.05)],
         ["depart", "pieces", "evenement"]),
        ("les ruelles", [(-1.08, 1.38, 0.05), (-1.6, 1.78, 0.05), (-2.25, 1.55, 0.06)],
         ["pieces", "piege", "transport", "pieces", "chance", "pieces", "objet"]),
        ("les escaliers", [(-2.4, 0.92, 0.14), (-1.92, 0.58, 0.28), (-2.32, 0.08, 0.42), (-2.22, -0.5, 0.58)],
         ["pieces", "evenement", "pieces", "piege", "pieces", "duel", "pieces"]),
        ("la butte", [(-1.72, -0.95, 0.76), (-1.18, -1.15, 0.9), (-0.78, -1.72, 0.92)],
         ["pieces", "boutique", "pieces", "chance", "pieces", "tournee", "pieces"]),
        ("le pont des amoureux", [(-0.15, -1.88, 0.66), (0.45, -1.6, 0.42)],
         ["pieces", "evenement", "pieces", "objet"]),
        ("le quartier des néons", [(1.02, -1.9, 0.24), (1.6, -1.55, 0.16), (2.3, -1.75, 0.15), (2.45, -1.08, 0.15)],
         ["pieces", "piege", "pieces", "duel", "pieces", "chance", "pieces"]),
        ("le pont-levis", [(1.98, -0.62, 0.18), (2.42, -0.05, 0.22), (2.2, 0.55, 0.14)],
         ["evenement", "pieces", "tournee", "pieces", "piege"]),
        ("les docks", [(2.45, 1.12, 0.05), (1.85, 1.55, 0.05), (1.2, 1.38, 0.05), (0.6, 1.78, 0.05)],
         ["pieces", "chance", "pieces", "evenement", "pieces", "objet", "pieces", "piege"]),
    ])
    # Le pont à péage : coupe la ville par le milieu, en évitant la butte.
    debut = k.pres(-2.32, 0.08, boucle)
    fin = k.pres(1.98, -0.62, boucle)
    k.branche(debut, fin, [(-1.55, -0.22, 0.42), (-0.82, -0.42, 0.46), (-0.05, -0.55, 0.42), (0.75, -0.8, 0.3), (1.38, -0.66, 0.22)],
              ["peage", "pieces", "chance", "pieces", "evenement", "pieces", "objet", "pieces", "duel", "pieces",
               "pieces", "evenement", "pieces", "pieces", "chance", "pieces"],
              "le pont à péage")
    # Le marché de nuit : de la sortie du pont-levis, une autre route vers la grand-place.
    debut = k.pres(2.2, 0.55, boucle)
    k.branche(debut, 0, [(1.5, 0.58, 0.08), (0.88, 0.22, 0.08), (0.25, 0.5, 0.06), (-0.25, 0.95, 0.05), (-0.15, 1.35, 0.05)],
              ["pieces", "boutique", "pieces", "chance", "pieces", "evenement", "pieces", "objet", "pieces", "piege"],
              "le marché de nuit")
    # Le tram : des ruelles, il monte d un coup au quartier des néons.
    tram = [c for c in boucle if k.cases[c]["type"] == "transport"][0]
    k.poser(tram, "transport", moyen="tram", vers=k.pres(1.6, -1.55, boucle))
    _boutiques(k, [("Le bazar de la butte", ["cafe", "double", "pipe", "pickpocket", "bouclier", "tournee"]),
                   ("L'épicerie de nuit", ["cafe", "double", "triple", "chaises", "piege", "vide_poche", "taxi"])])
    return {
        "id": "pub", "nom": "Le Pub", "difficulte": "moyen", "icone": "🍺",
        "description": "La ville de nuit : la butte et ses escaliers, le quartier des néons, un canal et ses ponts. Un tram, un pont à péage, des duels et des vols.",
        "cases": k.cases,
        "capsule_possibles": _emplacements(k, [(-1.18, -1.15), (0.45, -1.6), (2.42, -0.05), (-0.05, -0.55), (-1.6, 1.78), (1.85, 1.55), (0.25, 0.5), (2.3, -1.75)]),
        "videur": None,
        "prix_capsule": 20,
        "peage": 8,
        "pieces": {"pieces": 3, "piege": -3, "depart": 5},
        "boire": {"piege": 1, "tournee": 2, "duel": 2, "minijeu_dernier": 2, "fin_dernier": 3, "videur": 0},
        # Moyen : vols de pieces, duels, echanges de place, coup de pouce au dernier.
        "evenements": ["pourboire", "tournee_generale", "pickpocket", "raccourci", "addition", "courage", "chanson"],
        "chance": ["jackpot", "poche_trouee", "echange", "elan"],
        "objets_case": ["cafe", "double", "pipe", "chaises", "pickpocket", "bouclier", "tournee", "piege"],
        "duel_capsule": False,
        "evenements_plateau": {"tous_les": 3, "liste": ["happy_hour", "tournee_patron", "capsule_bouge", "addition_salee", "coup_de_pouce", "soldes"]},
    }


# =============================================================
# LE VOLCAN (La Cave, difficile)
# =============================================================
def cave():
    k = Carte()
    boucle = k.boucle([
        ("l'entrée", [(0.0, 1.76, 0.1), (-0.6, 1.7, 0.1)],
         ["depart", "pieces", "piege"]),
        ("la galerie des fûts", [(-1.1, 1.35, 0.08), (-1.7, 1.62, 0.06), (-2.3, 1.22, 0.08)],
         ["pieces", "evenement", "trappe", "pieces", "objet", "pieces", "duel"]),
        ("la grotte", [(-2.1, 0.65, 0.12), (-2.45, 0.15, 0.18), (-2.2, -0.4, 0.28), (-2.42, -0.95, 0.4)],
         ["pieces", "transport", "pieces", "piege", "pieces", "chance"]),
        ("la montée du cratère", [(-1.88, -1.3, 0.6), (-1.25, -1.1, 0.76), (-0.92, -1.65, 0.9)],
         ["pieces", "evenement", "pieces", "boutique", "pieces"]),
        ("le cratère", [(-0.3, -1.9, 1.02), (0.35, -1.58, 1.08), (0.92, -1.9, 1.0)],
         ["piege", "pieces", "tournee", "pieces", "chance", "pieces"]),
        ("la crête", [(1.52, -1.75, 0.82), (1.98, -1.3, 0.62), (2.45, -1.02, 0.46)],
         ["pieces", "trappe", "pieces", "duel", "pieces"]),
        ("le pont de lave", [(2.2, -0.42, 0.32), (2.45, 0.2, 0.26), (2.1, 0.75, 0.2)],
         ["evenement", "pieces", "piege", "pieces", "objet", "pieces"]),
        ("les forges", [(2.38, 1.3, 0.14), (1.78, 1.6, 0.12), (1.15, 1.3, 0.1), (0.6, 1.72, 0.1)],
         ["pieces", "chance", "pieces", "tournee", "pieces", "evenement", "pieces", "piege"]),
    ])
    # Le pont suspendu (péage) : au-dessus du lac de lave, de la grotte aux forges.
    debut = k.pres(-2.45, 0.15, boucle)
    fin = k.pres(2.1, 0.75, boucle)
    k.branche(debut, fin, [(-1.62, 0.05, 0.3), (-0.88, 0.32, 0.38), (-0.12, 0.2, 0.42), (0.62, 0.48, 0.4), (1.38, 0.38, 0.3)],
              ["peage", "pieces", "piege", "pieces", "evenement", "pieces", "objet", "pieces", "duel", "pieces",
               "chance", "pieces", "piege", "pieces", "pieces", "evenement", "pieces"],
              "le pont suspendu")
    # La cheminee : du cratere, on redescend par l interieur du volcan.
    debut = k.pres(0.35, -1.58, boucle)
    fin = k.pres(2.2, -0.42, boucle)
    k.branche(debut, fin, [(0.5, -1.0, 0.84), (1.0, -0.62, 0.62), (1.6, -0.55, 0.42)],
              ["pieces", "boutique", "pieces", "chance", "piege", "pieces", "objet"],
              "la cheminée")
    # Le geyser : de la grotte, il te projette au bord du cratere.
    geyser = [c for c in boucle if k.cases[c]["type"] == "transport"][0]
    k.poser(geyser, "transport", moyen="geyser", vers=k.pres(-0.3, -1.9, boucle))
    # Les trappes : de la galerie a la crete, et retour.
    a, b = [c for c in boucle if k.cases[c]["type"] == "trappe"]
    k.poser(a, "trappe", vers=b)
    k.poser(b, "trappe", vers=a)
    _boutiques(k, [("Le marchand du cratère", ["cafe", "double", "triple", "pipe", "bouclier", "taxi"]),
                   ("La forge de la cheminée", ["cafe", "pipe", "chaises", "pickpocket", "piege", "vide_poche", "tournee"])])
    videur = k.pres(0.92, -1.9, boucle)
    return {
        "id": "cave", "nom": "La Cave", "difficulte": "difficile", "icone": "💀",
        "description": "Des caves volcaniques : la galerie des fûts, le cratère, un pont suspendu au-dessus de la lave. Geyser, trappes, un videur qui rôde, des capsules qui se volent : tout peut basculer.",
        "cases": k.cases,
        "capsule_possibles": _emplacements(k, [(0.35, -1.58), (-1.7, 1.62), (2.45, 0.2), (-0.12, 0.2), (1.0, -0.62), (1.78, 1.6), (-2.2, -0.4), (-1.25, -1.1)]),
        "videur": {"depart": videur, "pas_min": 2, "pas_max": 5, "fouille": -2},
        "prix_capsule": 20,
        "peage": 10,
        "pieces": {"pieces": 3, "piege": -4, "depart": 5},
        "boire": {"piege": 2, "tournee": 3, "duel": 3, "minijeu_dernier": 3, "fin_dernier": 5, "videur": 2},
        # Difficile : on peut voler une capsule, les duels se jouent pour une capsule,
        # et le patron peut tout redistribuer. Rien n est joue avant le dernier tour.
        "evenements": ["pourboire", "tournee_generale", "pickpocket", "raccourci", "addition", "courage", "double_peine", "vol_capsule"],
        "chance": ["jackpot", "poche_trouee", "echange", "elan", "vol_capsule"],
        "objets_case": ["cafe", "double", "pipe", "chaises", "pickpocket", "vide_poche", "bouclier", "tournee", "piege"],
        "duel_capsule": True,
        "evenements_plateau": {"tous_les": 2, "liste": ["tournee_patron", "capsule_bouge", "videur_enerve", "melange", "grosse_tournee", "partage", "coup_de_pouce"]},
    }


def _boutiques(k, definitions):
    """Nomme les boutiques de la carte, dans l ordre des cases."""
    boutiques = [c for c in k.cases if c["type"] == "boutique"]
    assert len(boutiques) == len(definitions)
    for c, (nom, stock) in zip(boutiques, definitions):
        c["nom"] = nom
        c["stock"] = stock


def _emplacements(k, points):
    """Les emplacements possibles de la capsule (comme les etoiles de Mario Party)."""
    bonnes = [c["id"] for c in k.cases if c["type"] in ("pieces", "evenement", "chance")]
    vus = []
    for x, z in points:
        c = k.pres(x, z, [b for b in bonnes if b not in vus])
        vus.append(c)
    return vus


# =============================================================
# VERIFICATIONS
# =============================================================
def verifier(p):
    cases = p["cases"]
    ids = {c["id"] for c in cases}
    assert [c["id"] for c in cases] == list(range(len(cases))), p["id"]
    assert cases[0]["type"] == "depart"
    for c in cases:
        assert c["suivants"] and all(s in ids for s in c["suivants"]), (p["id"], c)
        assert len(c["suivants"]) <= 2, (p["id"], c)
        if c["type"] == "trappe":
            assert cases[c["vers"]]["type"] == "trappe"
        if c["type"] == "transport":
            assert c["moyen"] in ("bateau", "tram", "geyser") and cases[c["vers"]]["type"] not in ("transport", "trappe")
        if c["type"] == "boutique":
            assert c["nom"] and c["stock"]
    # Toute case est atteignable depuis le depart, et le depart depuis toute case.
    vus, pile = set(), [0]
    while pile:
        c = pile.pop()
        if c not in vus:
            vus.add(c)
            pile.extend(cases[c]["suivants"])
    assert vus == ids, p["id"]
    for c in p["capsule_possibles"]:
        assert cases[c]["type"] in ("pieces", "evenement", "chance")
    # Des cases ni trop serrees ni trop ecartees.
    for c in cases:
        for s in c["suivants"]:
            d = math.dist((c["x"], c["z"]), (cases[s]["x"], cases[s]["z"]))
            assert 0.17 < d < 0.38, (p["id"], c["id"], s, round(d, 3))
            assert abs(c["y"] - cases[s]["y"]) < 0.16, (p["id"], c["id"], s)
    voisins = {(c["id"], s) for c in cases for s in c["suivants"]}
    for a in cases:
        for b in cases:
            if a["id"] < b["id"] and (a["id"], b["id"]) not in voisins and (b["id"], a["id"]) not in voisins:
                d = math.dist((a["x"], a["z"]), (b["x"], b["z"]))
                assert d > 0.17, (p["id"], "cases trop proches", a["id"], b["id"], round(d, 3))


def svg(p):
    """Un dessin de la carte (vue de dessus, hauteur en couleur) pour verifier a l oeil."""
    couleurs = {"depart": "#fff", "pieces": "#3a6ad4", "piege": "#c43a3a", "tournee": "#2a9a5a", "evenement": "#a85ad4",
                "chance": "#7ac43a", "duel": "#e07a1a", "trappe": "#333", "objet": "#f0c020", "boutique": "#ff6ab0",
                "peage": "#888", "transport": "#20d0e0"}
    e = 100
    lignes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-280 -230 560 460" width="1120" height="920" style="background:#123">']
    for c in p["cases"]:
        for s in c["suivants"]:
            d = p["cases"][s]
            lignes.append(f'<line x1="{c["x"]*e}" y1="{c["z"]*e}" x2="{d["x"]*e}" y2="{d["z"]*e}" stroke="#ccc" stroke-width="2"/>')
        if "vers" in c:
            d = p["cases"][c["vers"]]
            lignes.append(f'<line x1="{c["x"]*e}" y1="{c["z"]*e}" x2="{d["x"]*e}" y2="{d["z"]*e}" stroke="#2ee" stroke-dasharray="4 4"/>')
    for c in p["cases"]:
        r = 7 + c["y"] * 6
        bord = "#ffd700" if c["id"] in p["capsule_possibles"] else "#000"
        lignes.append(f'<circle cx="{c["x"]*e}" cy="{c["z"]*e}" r="{r}" fill="{couleurs[c["type"]]}" stroke="{bord}" stroke-width="2"/>')
        lignes.append(f'<text x="{c["x"]*e}" y="{c["z"]*e+3}" font-size="7" text-anchor="middle" fill="#000">{c["id"]}</text>')
    lignes.append("</svg>")
    with open(os.path.join(_ICI, f"tournee_plateaux_{p['id']}.svg"), "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))


def fabriquer(dessins=False):
    plateaux = [terrasse(), pub(), cave()]
    for p in plateaux:
        if dessins:
            svg(p)
        verifier(p)
    donnees = {
        "_note": "Plateaux de La Tournée, fabriqués par tournee_plateaux.py (ne pas modifier à la main).",
        "defaut": "pub",
        "plateaux": plateaux,
    }
    with open(os.path.join(_ICI, "tournee_plateaux.json"), "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return donnees


if __name__ == "__main__":
    d = fabriquer("svg" in sys.argv[1:])
    for p in d["plateaux"]:
        print(p["id"], len(p["cases"]), "cases,", sum(len(c["suivants"]) > 1 for c in p["cases"]), "embranchements")
