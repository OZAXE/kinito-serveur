"""
Essai du moteur de Pichenette FC, hors ligne : des centaines de parties
jouees au hasard (visees valides et absurdes, absents, chronos depasses), de
2 a 8 joueurs. On verifie qu aucune ne plante ni ne se fige, que les visees
et les formations ne fuient pas chez l adversaire, que la physique tient
(rien ne sort du terrain, rien ne se chevauche, meme resultat pour les memes
entrees) et que les regles tiennent (equipes, buts, gorgees, fin).
Usage : python tests/pichenette_essai.py [nombre de parties]
"""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pichenette as P

C = P.PF_CONFIG
LX, LY = C["terrain"]["largeur"] / 2, C["terrain"]["longueur"] / 2
G, PROF = C["terrain"]["but_largeur"] / 2, C["terrain"]["but_profondeur"]
RP, RB = C["palet"]["rayon"], C["balle"]["rayon"]


def dedans(x, y, r):
    """Centre dans le terrain ou une cage, et a distance d un rayon de chaque bande ou poteau."""
    m = 2e-3
    if not (abs(x) <= LX and abs(y) <= LY) and not (abs(x) <= G and abs(y) <= LY + PROF):
        return False
    for x1, y1, x2, y2, _ in P.SEGMENTS:
        sx, sy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * sx + (y - y1) * sy) / (sx * sx + sy * sy)))
        if math.hypot(x - x1 - sx * t, y - y1 - sy * t) < r - m:
            return False
    return True


def verifier_positions(palets, balle):
    objets = [(x, y, RP) for x, y in palets] + [(balle[0], balle[1], RB)]
    for x, y, r in objets:
        assert dedans(x, y, r), f"objet hors du terrain : {x, y}"
    for a in range(len(objets)):
        for b in range(a + 1, len(objets)):
            xa, ya, ra = objets[a]
            xb, yb, rb = objets[b]
            assert math.hypot(xa - xb, ya - yb) >= (ra + rb) * 0.97, f"chevauchement {a} {b}"


def verifier_formations():
    assert len(C["formations"]) == 6
    for f in C["formations"]:
        assert len(f["palets"]) == C["palets_par_equipe"], f["id"]
        for e in (0, 1):
            pts = P.positions_formation(f["id"], e)
            for x, y in pts:
                assert dedans(x, y, RP), f["id"]
                assert (y < 0) == (e == 0) and abs(y) >= RP + 0.05, "chacun dans sa moitie : " + f["id"]
                assert math.hypot(x, y) >= RP + RB + 0.02, "pas collee au bouchon : " + f["id"]
        verifier_positions(P.positions_formation(f["id"], 0) + P.positions_formation(f["id"], 1), [0, 0])


def verifier_physique():
    # Tir plein axe depuis le rond central : le bouchon file dans le but d en face.
    r = P.simuler([[0, -0.1]], [0, 0], [(0, 0.0, 1.0, 1.0)])
    assert r["but"] == 0 and r["dernier"] == 0, r
    # Contre son camp : une capsule du bas pousse le bouchon dans son propre but.
    r = P.simuler([[0, 0.1]], [0, 0], [(0, 0.0, -1.0, 0.7)])
    assert r["but"] == 1 and r["dernier"] == 0, r
    # Tir doux : rien ne bouge assez pour marquer, tout s arrete.
    r = P.simuler([[0.3, -0.6]], [0, 0.5], [(0, 0.0, 1.0, 0.2)])
    assert r["but"] is None and r["duree"] < 3
    # Sans tir, rien ne bouge.
    r = P.simuler(P.positions_formation("classique", 0), [0, 0], [])
    assert len(r["images"]) <= 2 and r["but"] is None
    # Le bouchon frappe la bande et revient (il ne la traverse pas).
    r = P.simuler([[0.3, 0]], [0.4, 0], [(0, 1.0, 0.0, 1.0)])
    verifier_positions(r["palets"], r["balle"])
    # Une capsule arretee dans sa propre cage ressort juste devant la ligne.
    pal = P.positions_formation("classique", 0) + P.positions_formation("classique", 1)
    pal[0] = [0.0, -0.84]
    r = P.simuler(pal, [0, 0], [])
    x, y = r["palets"][0]
    ly = C["terrain"]["longueur"] / 2
    assert -ly < y < -ly + 0.06 and abs(x) < 0.2, r["palets"][0]
    assert r["images"][-1][1] == round(y * 1000), "le replay ne montre pas la sortie de la cage"
    verifier_positions(r["palets"], r["balle"])
    # Memes entrees, meme resultat.
    rng = random.Random(5)
    pal = P.positions_formation("ailes", 0) + P.positions_formation("herse", 1)
    tirs = []
    for k in (1, 3, 6, 9):
        a = rng.uniform(0, 2 * math.pi)
        tirs.append((k, math.cos(a), math.sin(a), rng.random()))
    assert P.simuler(pal, [0, 0], tirs) == P.simuler(pal, [0, 0], tirs)


def verifier_public(etat):
    pub = P.pf_etat_public(etat, 0)
    texte = json.dumps(pub)
    assert "visees" not in pub and '"dx"' not in texte or pub["tir"] is not None, "les visees fuient"
    if etat["phase"] == "formation":
        assert pub["formations"] is None, "les formations fuient avant le coup d envoi"
    if etat["phase"] == "visee":
        assert pub["tir"] is None and '"dx"' not in texte, "visees publiques pendant la visee"
    for i in range(etat["nb_joueurs"]):
        vue = P.pf_vue(etat, i)
        e = etat["equipe_de"][i]
        if e is None:
            assert vue is None
            continue
        for j in vue["visees"]:
            assert etat["equipe_de"][int(j)] == e, "un joueur voit les visees adverses"
    assert all(g >= 0 for g in etat["gorgees"])
    verifier_positions(etat["palets"], etat["balle"])


def visee_au_hasard(etat, i, rng):
    e = etat["equipe_de"][i]
    mes = list(range(e * P.NB_PALETS, (e + 1) * P.NB_PALETS))
    rng.shuffle(mes)
    tirs = []
    for k in mes[:P.tirs_par_joueur(etat, i)]:
        if rng.random() < 0.6:
            # Vers le bouchon, comme un vrai joueur.
            px, py = etat["palets"][k]
            dx, dy = etat["balle"][0] - px, etat["balle"][1] - py
        else:
            a = rng.uniform(0, 2 * math.pi)
            dx, dy = math.cos(a), math.sin(a)
        tirs.append({"palet": k, "dx": dx, "dy": dy, "force": rng.uniform(0.3, 1.0)})
    return tirs


def partie(rng, n):
    noms = [f"J{k}" for k in range(n)]
    t = 1000.0
    etat = P.pf_initialiser(noms, t, buts=rng.choice([3, 4, 5, None, "x"]), rng=rng)
    # Equipes : tout le monde joue sauf le dernier arrive si impair.
    joueurs = sorted(etat["equipes"][0] + etat["equipes"][1])
    assert abs(len(etat["equipes"][0]) - len(etat["equipes"][1])) == 0
    if n % 2:
        assert etat["spectateurs"] == [n - 1] and joueurs == list(range(n - 1))
    else:
        assert etat["spectateurs"] == [] and joueurs == list(range(n))
    assert etat["buts_victoire"] in (3, 4, 5)
    score_avant = [0, 0]
    gorgees_avant = list(etat["gorgees"])
    tours = 0
    while etat["phase"] != "fin":
        tours += 1
        assert tours < 6000, "la partie ne finit pas"
        verifier_public(etat)
        score_avant, gorgees_avant = controler(etat, score_avant, gorgees_avant)
        if etat["phase"] == "fin":
            break
        ph = etat["phase"]
        # Departs et retours de temps en temps.
        if rng.random() < 0.03:
            j = rng.randrange(n)
            if etat["partis"][j]:
                P.pf_joueur_revenu(etat, j, t)
            else:
                P.pf_joueur_parti(etat, j, t)
        ph = etat["phase"]
        if ph == "formation":
            for i in P._presents(etat):
                if rng.random() < 0.15:
                    continue
                ids = list(P.FORMATIONS) + ["inconnue"]
                P.pf_action(etat, i, {"type": "pf_formation", "attaque": rng.choice(ids),
                                      "defense": rng.choice(ids), "valider": rng.random() < 0.7}, t)
                if etat["phase"] != "formation":
                    break
        elif ph == "visee":
            # Un spectateur ne peut pas tirer.
            for s in etat["spectateurs"]:
                assert P.pf_action(etat, s, {"type": "pf_viser", "tirs": [], "pret": True}, t) == []
            for i in P._joueurs_actifs(etat):
                if etat["phase"] != "visee":
                    break
                x = rng.random()
                if x < 0.05:
                    # Absurde : capsule adverse, trop de tirs, n importe quoi.
                    autre = (1 - etat["equipe_de"][i]) * P.NB_PALETS
                    for m in ({"type": "pf_viser", "tirs": [{"palet": autre, "dx": 1, "dy": 0, "force": 1}]},
                              {"type": "pf_viser", "tirs": "x"},
                              {"type": "pf_viser", "tirs": [{"palet": True, "dx": 1, "dy": 0, "force": 1}]},
                              {"type": "pf_viser", "tirs": [{"palet": 0, "dx": 0, "dy": 0, "force": 1}] * 3}):
                        v = etat["version"]
                        P.pf_action(etat, i, m, t)
                        assert etat["version"] == v, "une visee absurde change l etat"
                elif x < 0.15:
                    continue      # il dort : le chrono tirera pour lui
                else:
                    P.pf_action(etat, i, {"type": "pf_viser", "tirs": visee_au_hasard(etat, i, rng),
                                          "pret": rng.random() < 0.85}, t)
            # Une capsule deja prise par un coequipier est refusee.
            if etat["phase"] == "visee":
                for e in (0, 1):
                    membres = [j for j in etat["equipes"][e] if not etat["partis"][j]]
                    if len(membres) >= 2 and str(membres[0]) in etat["visees"]:
                        k = etat["visees"][str(membres[0])][0]["palet"]
                        v = etat["version"]
                        P.pf_action(etat, membres[1], {"type": "pf_viser", "tirs": [
                            {"palet": k, "dx": 0, "dy": 1, "force": 0.5}]}, t)
                        assert etat["version"] == v, "deux coequipiers sur la meme capsule"
        score_avant, gorgees_avant = controler(etat, score_avant, gorgees_avant)
        if etat["phase"] == "fin":
            break
        t = (etat["echeance"] or t) + 0.01
        P.pf_expirer(etat, t)
    verifier_fin(etat)
    return tours


def controler(etat, score_avant, gorgees_avant):
    """Un tir vient d etre joue : on le verifie une fois, avant tout depart ou retour."""
    tir = etat["tir"]
    if etat["phase"] in ("tir", "fin") and tir is not None and not tir.get("_vu"):
        verifier_tir(etat, tir, score_avant, gorgees_avant)
        return list(etat["score"]), list(etat["gorgees"])
    return score_avant, gorgees_avant


def verifier_tir(etat, tir, score_avant, gorgees_avant):
    pals = {t["palet"] for t in tir["tirs"]}
    assert len(pals) == len(tir["tirs"]), "une capsule tiree deux fois"
    for t in tir["tirs"]:
        assert etat["equipe_de"][t["joueur"]] == P._equipe_du_palet(t["palet"])
    par_joueur = {}
    for t in tir["tirs"]:
        par_joueur[t["joueur"]] = par_joueur.get(t["joueur"], 0) + 1
    for j, k in par_joueur.items():
        assert k <= P.tirs_par_joueur(etat, j)
    assert tir["images"] and all(len(im) == 2 * (2 * P.NB_PALETS + 1) for im in tir["images"])
    tir["_vu"] = True
    attendu = [0] * etat["nb_joueurs"]
    if tir["but"] is None:
        assert etat["score"] == score_avant
    else:
        m = tir["but"]
        assert etat["score"][m] == score_avant[m] + 1 and etat["score"][1 - m] == score_avant[1 - m]
        for j in etat["equipes"][1 - m]:
            if not etat["partis"][j]:
                attendu[j] += C["gorgees"]["but_encaisse"]
        if tir["contre_son_camp"] is not None and tir["contre_son_camp"] >= 0:
            assert etat["equipe_de"][tir["contre_son_camp"]] == 1 - m
            attendu[tir["contre_son_camp"]] += C["gorgees"]["contre_son_camp"]
        if etat["phase"] != "fin":
            # Replacement : attaque pour qui marque, defense pour l autre, bouchon au centre.
            f = etat["formations"]
            a = P.positions_formation(f[m]["attaque"], m)
            d = P.positions_formation(f[1 - m]["defense"], 1 - m)
            moi = etat["palets"][m * P.NB_PALETS:(m + 1) * P.NB_PALETS]
            lui = etat["palets"][(1 - m) * P.NB_PALETS:(2 - m) * P.NB_PALETS]
            assert moi == a and lui == d and etat["balle"] == [0.0, 0.0]
    if etat["phase"] == "fin":
        for j in etat["final"]["perdants"]:
            attendu[j] += C["gorgees"]["defaite"]
    assert [g - a for g, a in zip(etat["gorgees"], gorgees_avant)] == attendu, "gorgees du tir"


def verifier_fin(etat):
    f = etat["final"]
    assert f and etat["echeance"] is None
    g = f["gagnante"]
    assert etat["score"][g] == etat["buts_victoire"] and etat["score"][1 - g] < etat["buts_victoire"]
    assert f["gagnants"] == etat["equipes"][g] and f["perdants"] == etat["equipes"][1 - g]
    assert [d["joueur"] for d in f["derniers"]] == etat["equipes"][1 - g]
    for s in etat["spectateurs"]:
        assert etat["gorgees"][s] == 0, "un spectateur ne boit pas"


def main():
    parties = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    verifier_formations()
    verifier_physique()
    rng = random.Random(1234)
    total = 0
    for k in range(parties):
        n = rng.randint(P.PF_JOUEURS_MIN, P.PF_JOUEURS_MAX)
        total += partie(rng, n)
    print(f"OK : {parties} parties de Pichenette FC, {total} etapes, formations, physique, non-fuite, gorgees.")


if __name__ == "__main__":
    main()
