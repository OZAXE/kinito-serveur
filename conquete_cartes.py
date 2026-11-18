"""
Cartes de Royaumes (jeu de conquete, prefixe sw_).

Les cartes sont PREDEFINIES : ce script les fabrique une fois, avec une graine
fixe, et les ecrit dans conquete_cartes.json (le serveur ne lit que le JSON).
On ne le relance que pour redessiner une carte :

    python conquete_cartes.py            # reecrit conquete_cartes.json
    python conquete_cartes.py --verifier # controle sans rien ecrire

Une carte = un graphe de regions. Chaque region est une cellule de Voronoi
(points relaches par Lloyd pour des formes regulieres) dans un rectangle en
portrait : la page 3D l extrude telle quelle, et le serveur n utilise que
les voisinages. Une region de bord touche le bord du rectangle.

Equilibrage (verifie a l ecriture) :
  - carte d un seul tenant ;
  - les 5 terrains presents, a 1 pres en nombre ;
  - deux voisines n ont pas le meme terrain quand c est possible ;
  - mines et sources magiques reparties (jamais voisines entre elles) ;
  - au moins la moitie des regions au bord (entree des nouveaux peuples).
"""
import json
import math
import os
import random
import sys

TERRAINS = ["plaine", "foret", "colline", "marais", "montagne"]

# nom -> (joueurs, nombre de regions, largeur, hauteur, graine)
# Nombre de regions : 6 + 2 par joueur, pris en haut de chaque tranche.
CARTES = {
    "petite":  {"joueurs": [2, 3], "regions": 12, "largeur": 8.0, "hauteur": 10.0, "graine": 7},
    "moyenne": {"joueurs": [4, 5], "regions": 16, "largeur": 9.0, "hauteur": 11.5, "graine": 11},
    "grande":  {"joueurs": [6, 8], "regions": 22, "largeur": 10.0, "hauteur": 13.0, "graine": 5},
}

EPS = 1e-7


def _clip(poly, a, b, c):
    """Garde la partie de poly ou a*x + b*y <= c (Sutherland-Hodgman)."""
    sortie = []
    n = len(poly)
    for k in range(n):
        p, q = poly[k], poly[(k + 1) % n]
        dp = a * p[0] + b * p[1] - c
        dq = a * q[0] + b * q[1] - c
        if dp <= EPS:
            sortie.append(p)
        if (dp < -EPS and dq > EPS) or (dp > EPS and dq < -EPS):
            t = dp / (dp - dq)
            sortie.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return sortie


def _cellule(points, i, L, H):
    poly = [(0.0, 0.0), (L, 0.0), (L, H), (0.0, H)]
    xi, yi = points[i]
    for j, (xj, yj) in enumerate(points):
        if j == i:
            continue
        a, b = xj - xi, yj - yi
        c = (xj * xj + yj * yj - xi * xi - yi * yi) / 2
        poly = _clip(poly, a, b, c)
    return poly


def _centroide(poly):
    aire = cx = cy = 0.0
    for k in range(len(poly)):
        x0, y0 = poly[k]
        x1, y1 = poly[(k + 1) % len(poly)]
        f = x0 * y1 - x1 * y0
        aire += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    aire /= 2
    return (cx / (6 * aire), cy / (6 * aire)), abs(aire)


def _sur_ligne(p, a, b, c):
    return abs(a * p[0] + b * p[1] - c) < 1e-5 * max(1.0, math.hypot(a, b))


def _voisins(points, cellules):
    n = len(points)
    vois = [set() for _ in range(n)]
    for i in range(n):
        xi, yi = points[i]
        for j in range(n):
            if j == i:
                continue
            xj, yj = points[j]
            a, b = xj - xi, yj - yi
            c = (xj * xj + yj * yj - xi * xi - yi * yi) / 2
            sur = [p for p in cellules[i] if _sur_ligne(p, a, b, c)]
            # une frontiere commune assez longue pour qu on la voie
            if len(sur) >= 2 and math.dist(sur[0], sur[-1]) > 0.35:
                vois[i].add(j)
    # symetrique
    for i in range(n):
        for j in list(vois[i]):
            vois[j].add(i)
    return vois


def _au_bord(poly, L, H):
    for cote in (lambda p: abs(p[0]) < 1e-6, lambda p: abs(p[0] - L) < 1e-6,
                 lambda p: abs(p[1]) < 1e-6, lambda p: abs(p[1] - H) < 1e-6):
        sur = [p for p in poly if cote(p)]
        if len(sur) >= 2 and math.dist(sur[0], sur[-1]) > 0.3:
            return True
    return False


def _connexe(vois):
    vus, pile = {0}, [0]
    while pile:
        for j in vois[pile.pop()]:
            if j not in vus:
                vus.add(j)
                pile.append(j)
    return len(vus) == len(vois)


def _terrains(vois, rng):
    """Coloriage glouton equilibre : chaque terrain a 1 pres, voisines differentes si possible."""
    n = len(vois)
    cible = {t: n // 5 + (1 if k < n % 5 else 0) for k, t in enumerate(rng.sample(TERRAINS, 5))}
    ordre = sorted(range(n), key=lambda i: -len(vois[i]))
    res = [None] * n
    for i in ordre:
        pris = {res[j] for j in vois[i] if res[j]}
        dispo = [t for t in TERRAINS if cible[t] > 0]
        bons = [t for t in dispo if t not in pris] or dispo
        t = max(bons, key=lambda t: (cible[t], rng.random()))
        res[i] = t
        cible[t] -= 1
    return res


def _marques(vois, rng, n_mines, n_sources):
    n = len(vois)
    marques = [None] * n
    for nom, k in (("mine", n_mines), ("source", n_sources)):
        candidats = list(range(n))
        rng.shuffle(candidats)
        poses = 0
        for i in candidats:
            if poses == k:
                break
            if marques[i] or any(marques[j] for j in vois[i]):
                continue
            marques[i] = nom
            poses += 1
    return marques


def fabriquer(nom, spec):
    rng = random.Random(spec["graine"])
    L, H, n = spec["largeur"], spec["hauteur"], spec["regions"]
    for essai in range(200):
        points = [(rng.uniform(0.6, L - 0.6), rng.uniform(0.6, H - 0.6)) for _ in range(n)]
        for _ in range(6):   # relaxation de Lloyd : des cellules de taille voisine
            cellules = [_cellule(points, i, L, H) for i in range(n)]
            points = [_centroide(c)[0] for c in cellules]
        cellules = [_cellule(points, i, L, H) for i in range(n)]
        vois = _voisins(points, cellules)
        bords = [_au_bord(c, L, H) for c in cellules]
        if not _connexe(vois) or sum(bords) < n / 2 or sum(bords) == n:
            continue
        terrains = _terrains(vois, rng)
        marques = _marques(vois, rng, max(2, n // 5), max(2, n // 6))
        regions = []
        # Ordre de lecture : de haut en bas puis de gauche a droite
        ordre = sorted(range(n), key=lambda i: (round(points[i][1] / 2.2), points[i][0]))
        nouvel = {anc: k for k, anc in enumerate(ordre)}
        for k, i in enumerate(ordre):
            regions.append({
                "id": k,
                "terrain": terrains[i],
                "marque": marques[i],
                "bord": bords[i],
                "voisins": sorted(nouvel[j] for j in vois[i]),
                "centre": [round(points[i][0] - L / 2, 3), round(points[i][1] - H / 2, 3)],
                "poly": [[round(x - L / 2, 3), round(y - H / 2, 3)] for x, y in cellules[i]],
            })
        return {"nom": nom, "joueurs": spec["joueurs"], "largeur": L, "hauteur": H, "regions": regions}
    raise RuntimeError(f"carte {nom} impossible a fabriquer")


def verifier(carte):
    """Leve AssertionError si la carte ne respecte pas les regles d equilibrage."""
    regions = carte["regions"]
    n = len(regions)
    vois = [set(r["voisins"]) for r in regions]
    assert all(r["id"] == k for k, r in enumerate(regions)), "ids"
    assert all(i in vois[j] for i in range(n) for j in vois[i]), "voisinage non symetrique"
    assert all(i not in vois[i] for i in range(n)), "region voisine d elle-meme"
    assert _connexe(vois), "carte en plusieurs morceaux"
    compte = {t: sum(1 for r in regions if r["terrain"] == t) for t in TERRAINS}
    assert max(compte.values()) - min(compte.values()) <= 1, f"terrains desequilibres {compte}"
    bords = sum(1 for r in regions if r["bord"])
    assert n / 2 <= bords < n, f"{bords} regions de bord sur {n}"
    for r in regions:
        if r["marque"]:
            assert not any(regions[j]["marque"] for j in r["voisins"]), "marques voisines"
    return True


def toutes():
    return {nom: fabriquer(nom, spec) for nom, spec in CARTES.items()}


if __name__ == "__main__":
    cartes = toutes()
    for c in cartes.values():
        verifier(c)
        n = len(c["regions"])
        print(f"{c['nom']:8} {n} regions, {sum(r['bord'] for r in c['regions'])} au bord, "
              f"{sum(len(r['voisins']) for r in c['regions']) / n:.1f} voisins en moyenne")
    if "--verifier" not in sys.argv:
        chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conquete_cartes.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(cartes, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        print("ecrit", chemin)
