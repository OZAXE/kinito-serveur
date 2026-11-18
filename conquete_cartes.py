"""
Cartes de Royaumes (jeu de conquete, prefixe sw_).

Les cartes sont PREDEFINIES : ce script les fabrique une fois, avec une graine
fixe, et les ecrit dans conquete_cartes.json (le serveur ne lit que le JSON).
On ne le relance que pour redessiner une carte :

    python conquete_cartes.py            # reecrit conquete_cartes.json
    python conquete_cartes.py --verifier # controle sans rien ecrire

Une carte = une ile de regions entouree de mer, avec des lacs :
  - des germes « terre » tires dans une forme d ile irreguliere, relaches
    (Lloyd) pour des regions de taille voisine ; des germes « mer » autour ;
  - les cellules de Voronoi des germes donnent les regions ; quelques
    regions interieures deviennent des LACS (ni conquerables, ni voisines
    a travers l eau) ;
  - chaque frontiere est rendue sinueuse (deplacements recursifs du milieu,
    bornes pour ne jamais croiser une autre frontiere), le meme trace etant
    partage par les deux cotes : les regions s emboitent sans trou.
Le serveur n utilise que les voisinages et les drapeaux (terrain, marque,
bord) ; la page 3D dessine les polygones (regions et lacs).

Equilibrage (verifie a l ecriture) :
  - terres d un seul tenant, lacs compris ;
  - les 5 terrains presents, a 1 pres en nombre ;
  - deux voisines n ont pas le meme terrain quand c est possible ;
  - mines et sources magiques reparties (jamais voisines entre elles) ;
  - au moins la moitie des regions au bord de la mer (entree des peuples).
"""
import json
import math
import os
import random
import sys

TERRAINS = ["plaine", "foret", "colline", "marais", "montagne"]

# nom -> joueurs, regions conquerables (6 + 2 par joueur, haut de tranche),
# lacs, taille du cadre, graine
CARTES = {
    "petite":  {"joueurs": [2, 3], "regions": 12, "lacs": 1, "largeur": 8.5, "hauteur": 10.5, "graine": 3},
    "moyenne": {"joueurs": [4, 5], "regions": 16, "lacs": 1, "largeur": 9.5, "hauteur": 12.0, "graine": 8},
    "grande":  {"joueurs": [6, 8], "regions": 22, "lacs": 2, "largeur": 10.5, "hauteur": 13.5, "graine": 21},
}

EPS = 1e-7
ILE = 0.44          # demi-taille de l ile dans le cadre (le reste est de la mer)


# =============================================================
# GEOMETRIE
# =============================================================
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
    """Cellule de Voronoi du germe i, et pour chaque cote le germe voisin (None = cadre)."""
    poly = [(0.0, 0.0), (L, 0.0), (L, H), (0.0, H)]
    xi, yi = points[i]
    proches = sorted(range(len(points)), key=lambda j: (points[j][0] - xi) ** 2 + (points[j][1] - yi) ** 2)
    for j in proches[1:40]:
        xj, yj = points[j]
        a, b = xj - xi, yj - yi
        c = (xj * xj + yj * yj - xi * xi - yi * yi) / 2
        poly = _clip(poly, a, b, c)
    return poly


def _cote_de(points, i, p, q, L, H):
    """Le germe de l autre cote du segment p-q de la cellule i (None : bord du cadre)."""
    for (x0, y0, x1, y1) in ((0, 0, 0, H), (L, 0, L, H), (0, 0, L, 0), (0, H, L, H)):
        if (abs(p[0] - x0) < 1e-6 and abs(q[0] - x1) < 1e-6 and x0 == x1) or \
           (abs(p[1] - y0) < 1e-6 and abs(q[1] - y1) < 1e-6 and y0 == y1):
            return None
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    di = math.dist((mx, my), points[i])
    meilleur, ecart = None, 1e9
    for j, s in enumerate(points):
        if j == i:
            continue
        e = abs(math.dist((mx, my), s) - di)
        if e < ecart:
            meilleur, ecart = j, e
    return meilleur


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
    if abs(aire) < 1e-9:
        return poly[0], 0.0
    return (cx / (6 * aire), cy / (6 * aire)), abs(aire)


def _rayon_ile(rng):
    """Contour d ile : rayon relatif selon l angle (quelques harmoniques)."""
    harmoniques = [(k, rng.uniform(0.06, 0.15) / (1 + k * 0.3), rng.uniform(0, 2 * math.pi)) for k in (2, 3, 5, 7)]
    return lambda t: 1 + sum(a * math.sin(k * t + ph) for k, a, ph in harmoniques)


def _dans_ile(x, y, L, H, r):
    dx, dy = (x - L / 2) / (L * ILE), (y - H / 2) / (H * ILE)
    return math.hypot(dx, dy) <= r(math.atan2(dy, dx))


def _connexe(vois, noeuds):
    noeuds = set(noeuds)
    if not noeuds:
        return True
    depart = next(iter(noeuds))
    vus, pile = {depart}, [depart]
    while pile:
        for j in vois[pile.pop()]:
            if j in noeuds and j not in vus:
                vus.add(j)
                pile.append(j)
    return vus == noeuds


# =============================================================
# FRONTIERES SINUEUSES
# =============================================================
def _sinueux(a, b, s1, s2, rng, prof=0):
    """Points interieurs d une frontiere a->b, deplacee perpendiculairement.
    L amplitude est bornee par la distance aux deux germes (s1, s2) : le trace
    reste dans sa zone et ne croise jamais une autre frontiere."""
    longueur = math.dist(a, b)
    if longueur < 0.3 or prof > 4:
        return []
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    nx, ny = -(b[1] - a[1]) / longueur, (b[0] - a[0]) / longueur
    marge = min(math.dist((mx, my), s1), math.dist((mx, my), s2) if s2 else 9)
    amp = min(0.22 * longueur, 0.3 * marge)
    d = rng.uniform(-amp, amp)
    m = (mx + nx * d, my + ny * d)
    return _sinueux(a, m, s1, s2, rng, prof + 1) + [m] + _sinueux(m, b, s1, s2, rng, prof + 1)


def _lisser(pts, tours=2):
    """Chaikin sur une ligne ouverte : les extremites ne bougent pas (le trace reste raccorde)."""
    for _ in range(tours):
        if len(pts) < 3:
            return pts
        res = [pts[0]]
        for k in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[k], pts[k + 1]
            if k > 0:
                res.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            if k < len(pts) - 2:
                res.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        res.append(pts[-1])
        pts = res
    return pts


def _cle(p):
    return (round(p[0], 4), round(p[1], 4))


# =============================================================
# TERRAINS ET MARQUES
# =============================================================
def _terrains(vois, rng):
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


# =============================================================
# FABRICATION
# =============================================================
def fabriquer(nom, spec):
    rng = random.Random(spec["graine"])
    L, H = spec["largeur"], spec["hauteur"]
    n, nl = spec["regions"], spec["lacs"]
    for essai in range(300):
        r = _rayon_ile(rng)
        # Germes de mer : un anneau autour de l ile, plus serre que les terres
        mer = []
        for k in range(64):
            t = 2 * math.pi * k / 64
            for f in (1.17, 1.5):
                x = L / 2 + math.cos(t) * L * ILE * r(t) * f
                y = H / 2 + math.sin(t) * H * ILE * r(t) * f
                # toujours un filet de mer entre l ile et le cadre
                mer.append((min(max(x, 0.12), L - 0.12), min(max(y, 0.12), H - 0.12)))
        mer += [(0.01, 0.01), (L - 0.01, 0.01), (0.01, H - 0.01), (L - 0.01, H - 0.01)]
        terre = []
        while len(terre) < n + nl:
            x, y = rng.uniform(0, L), rng.uniform(0, H)
            if _dans_ile(x, y, L, H, lambda t: r(t) * 0.92):
                terre.append((x, y))
        for _ in range(8):
            points = terre + mer
            nouveaux = []
            for i in range(len(terre)):
                c, _a = _centroide(_cellule(points, i, L, H))
                if not _dans_ile(c[0], c[1], L, H, lambda t: r(t) * 0.95):
                    c = terre[i]
                nouveaux.append(c)
            terre = nouveaux
        points = terre + mer
        nt = len(terre)
        cellules = [_cellule(points, i, L, H) for i in range(nt)]
        # Voisinages (frontiere assez longue) et contact avec la mer
        vois = [set() for _ in range(nt)]
        touche_mer = [False] * nt
        for i in range(nt):
            poly = cellules[i]
            for k in range(len(poly)):
                p, q = poly[k], poly[(k + 1) % len(poly)]
                j = _cote_de(points, i, p, q, L, H)
                if j is None or j >= nt:
                    if math.dist(p, q) > 0.25:
                        touche_mer[i] = True
                elif math.dist(p, q) > 0.35:
                    vois[i].add(j)
        for i in range(nt):
            for j in list(vois[i]):
                vois[j].add(i)
        if any(math.dist(points[i], points[j]) < 0.9 for i in range(nt) for j in vois[i]):
            continue
        # Lacs : regions interieures bien entourees, jamais voisines entre elles
        interieures = [i for i in range(nt) if not touche_mer[i] and len(vois[i]) >= 4]
        rng.shuffle(interieures)
        lacs = []
        for i in interieures:
            if len(lacs) == nl:
                break
            if any(j in lacs for j in vois[i]):
                continue
            reste = [k for k in range(nt) if k not in lacs and k != i]
            if _connexe(vois, reste):
                lacs.append(i)
        if len(lacs) < nl:
            continue
        regions_idx = [i for i in range(nt) if i not in lacs]
        bords = [touche_mer[i] for i in regions_idx]
        if not (n / 2 <= sum(bords) < n):
            continue
        # Frontieres sinueuses, partagees entre les deux cotes
        traces = {}

        def trace(i, p, q):
            j = _cote_de(points, i, p, q, L, H)
            kp, kq = _cle(p), _cle(q)
            cle = (min(kp, kq), max(kp, kq))
            if cle not in traces:
                a, b = (p, q) if kp <= kq else (q, p)
                g = random.Random(hash(cle) & 0xFFFFFFFF ^ spec["graine"])
                s2 = points[j] if j is not None else None
                traces[cle] = _lisser([a] + _sinueux(a, b, points[i], s2, g) + [b])[1:-1]
            pts = traces[cle]
            return pts if kp <= kq else list(reversed(pts))

        def contour(i):
            poly = cellules[i]
            res = []
            for k in range(len(poly)):
                p, q = poly[k], poly[(k + 1) % len(poly)]
                res.append(p)
                res += trace(i, p, q)
            return res

        nouvel = {}
        ordre = sorted(regions_idx, key=lambda i: (round(points[i][1] / 2.2), points[i][0]))
        for k, i in enumerate(ordre):
            nouvel[i] = k
        vois_r = [sorted(nouvel[j] for j in vois[i] if j in nouvel) for i in ordre]
        terrains = _terrains([set(v) for v in vois_r], rng)
        marques = _marques([set(v) for v in vois_r], rng, max(2, n // 5), max(2, n // 6))
        cx, cy = L / 2, H / 2
        arr = lambda pts: [[round(x - cx, 2), round(y - cy, 2)] for x, y in pts]
        regions = []
        for k, i in enumerate(ordre):
            regions.append({
                "id": k,
                "terrain": terrains[k],
                "marque": marques[k],
                "bord": touche_mer[i],
                "voisins": vois_r[k],
                "centre": [round(points[i][0] - cx, 3), round(points[i][1] - cy, 3)],
                "poly": arr(contour(i)),
            })
        eaux = [{"type": "lac", "poly": arr(contour(i)), "centre": [round(points[i][0] - cx, 3), round(points[i][1] - cy, 3)]}
                for i in lacs]
        return {"nom": nom, "joueurs": spec["joueurs"], "largeur": L, "hauteur": H,
                "regions": regions, "eaux": eaux}
    raise RuntimeError(f"carte {nom} impossible a fabriquer")


def verifier(carte):
    """Leve AssertionError si la carte ne respecte pas les regles d equilibrage."""
    regions = carte["regions"]
    n = len(regions)
    vois = [set(r["voisins"]) for r in regions]
    assert all(r["id"] == k for k, r in enumerate(regions)), "ids"
    assert all(i in vois[j] for i in range(n) for j in vois[i]), "voisinage non symetrique"
    assert all(i not in vois[i] for i in range(n)), "region voisine d elle-meme"
    assert _connexe(vois, range(n)), "carte en plusieurs morceaux"
    compte = {t: sum(1 for r in regions if r["terrain"] == t) for t in TERRAINS}
    assert max(compte.values()) - min(compte.values()) <= 1, f"terrains desequilibres {compte}"
    bords = sum(1 for r in regions if r["bord"])
    assert n / 2 <= bords < n, f"{bords} regions de bord sur {n}"
    for r in regions:
        if r["marque"]:
            assert not any(regions[j]["marque"] for j in r["voisins"]), "marques voisines"
        assert len(r["poly"]) >= 6
    assert "eaux" in carte
    return True


def toutes():
    return {nom: fabriquer(nom, spec) for nom, spec in CARTES.items()}


if __name__ == "__main__":
    cartes = toutes()
    for c in cartes.values():
        verifier(c)
        n = len(c["regions"])
        print(f"{c['nom']:8} {n} regions, {sum(r['bord'] for r in c['regions'])} au bord, "
              f"{len(c['eaux'])} lac(s), {sum(len(r['voisins']) for r in c['regions']) / n:.1f} voisins en moyenne, "
              f"{sum(len(r['poly']) for r in c['regions'])} points")
    if "--verifier" not in sys.argv:
        chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conquete_cartes.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(cartes, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        print("ecrit", chemin, os.path.getsize(chemin) // 1024, "Ko")
