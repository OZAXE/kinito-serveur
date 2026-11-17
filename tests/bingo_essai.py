"""
Essais du Bingo de soiree (sans reseau, puis couche branchee sur le serveur) :
- tirage : taille, sans doublon, categories, part de rares, cases perso,
  cases conditionnelles ;
- cochage : annulation sous 10 s, case definitive ensuite, delai entre deux ;
- BINGO : jury (tous jusqu a 4, sinon 3 tires, pas deux fois de suite),
  majorite « Pas vu », defaut « Vu », file d attente, grille pleine ;
- points, gorgees, plafond commun avec l Enquete, classement et fin ;
- NON-FUITE : jamais la grille d un autre, vues privees de meme forme ;
- couche sur le serveur : sauvegarde, reconnexion, Enquete et Bingo ouverts
  en meme temps par le meme prenom.

Usage : python tests/bingo_essai.py
"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bingo as b
import serveur
from stockage import StockageMemoire

ECHECS = []


def verifier(cond, quoi):
    print(("  ok  " if cond else "  ECHEC ") + quoi)
    if not cond:
        ECHECS.append(quoi)


CONDITIONNELLES = {c["id"] for c in b.BANQUE["cases"] if c.get("condition")}
NOMS = ["Lea", "Max", "Zoe", "Tom", "Ana", "Bob", "Cyd"]


def partie(n, vitesse="soiree", graine=1, commun=None, perso=None):
    rng = random.Random(graine)
    e = b.nouvelle_partie("ABCD", "Lea", 1000.0)
    b.synchroniser(e, commun or {})
    for nom in NOMS[:n]:
        b.action(e, nom, {"type": "bng_rejoindre"}, 1000.0, rng)
    cfg = {"type": "bng_config", "vitesse": vitesse}
    if perso:
        cfg["perso"] = perso
    b.action(e, "Lea", cfg, 1000.0, rng)
    b.action(e, "Lea", {"type": "bng_lancer"}, 1000.0, rng)
    return e, rng


def cocher_tout(e, nom, indices, t, rng):
    """Coche des cases en respectant le delai ; renvoie l heure finale."""
    d = b.CONFIG["vitesses"][e["vitesse"]]["delai_cochage"]
    for i in indices:
        t += d
        r = b.cocher(e, nom, i, t)
        assert not r, r
    return t + 11


def essai_tirage():
    print("Tirage des grilles")
    for vitesse, n in [("flash", 3), ("express", 4), ("soiree", 4), ("marathon", 5)]:
        for graine in range(30):
            e, _ = partie(4, vitesse, graine)
            for p in e["j"].values():
                g = p["grille"]
                ok = len(g) == n * n and len({c["id"] for c in g}) == len(g)
                cats = {c: sum(x["categorie"] == c for x in g) for c in b.CATEGORIES}
                mini = min(2, (n * n) // 5)
                rares = sum(x["rarete"] == "rare" for x in g)
                if not ok or min(cats.values()) < mini or rares > int(b.CONFIG["vitesses"][vitesse]["part_rares"] * n * n):
                    verifier(False, f"grille {vitesse} graine {graine} : {cats}, {rares} rares")
                    return
                if any(c["id"] in CONDITIONNELLES for c in g):
                    verifier(False, "case conditionnelle tirée sans son jeu")
                    return
    verifier(True, "taille, sans doublon, catégories et rares respectés (4 vitesses x 30 tirages)")
    e, _ = partie(3, perso=["Paul parle de son chat", "Quelqu'un dit 'wesh'", "A", "B", "C"])
    verifier(all(sum(c["categorie"] == "perso" for c in p["grille"]) == 3 for p in e["j"].values()),
             "3 cases perso par grille au plus, tirées en priorité")
    trouve = False
    for graine in range(40):
        e, _ = partie(3, "marathon", graine, commun={"enquete": True, "jeux": ["menteur"]})
        trouve |= any(c["id"] in CONDITIONNELLES for p in e["j"].values() for c in p["grille"])
    verifier(trouve, "cases conditionnelles tirées quand l'Enquête ou Menteur tourne")
    e, rng = partie(3)
    b.action(e, "Lea", {"type": "bng_config", "perso": ["x" * 80] * 3 + [f"c{i}" for i in range(20)]}, 1000, rng)
    verifier(e["phase"] == "jeu", "réglages refusés une fois lancé")
    e = b.nouvelle_partie("ABCD", "Lea", 0)
    b.action(e, "Lea", {"type": "bng_config", "perso": ["x" * 80, "x" * 80] + [f"c{i}" for i in range(20)]}, 0)
    verifier(len(e["perso"]) == 10 and len(e["perso"][0]) == 40, "10 cases perso de 40 caractères au plus, sans doublon")
    r = b.action(e, "Max", {"type": "bng_config", "vitesse": "flash"}, 0)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "seul l'hôte règle la partie")
    r = b.action(e, "Lea", {"type": "bng_lancer"}, 0)
    verifier(r and "au moins" in r[0]["msg"]["message"], "pas de lancement à un seul joueur")


def essai_cochage():
    print("Cochage")
    e, rng = partie(3)
    verifier(not b.cocher(e, "Max", 0, 2000), "cocher une case")
    verifier(b.vue_privee(e, "Max", 2005)["coches"][0]["statut"] == "provisoire", "  provisoire pendant 10 s")
    verifier(not b.cocher(e, "Max", 0, 2005) and not e["j"]["max"]["coches"], "  deuxième appui sous 10 s : annulée")
    r = b.cocher(e, "Max", 1, 2006)
    verifier(r and "Doucement" in r[0]["msg"]["message"], "délai minimum entre deux cochages")
    b.cocher(e, "Max", 1, 2020)
    r = b.cocher(e, "Max", 1, 2031)
    verifier(r and "ne se décoche plus" in r[0]["msg"]["message"], "après 10 s la case est définitive")
    verifier(b.vue_privee(e, "Max", 2031)["coches"][0]["statut"] == "definitive", "  statut « definitive »")
    r = b.cocher(e, "Max", 99, 2100)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "case inconnue refusée")
    r = b.cocher(e, "Max", True, 2100)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "  index non entier refusé")


def essai_bingo_audit():
    print("BINGO et audit")
    e, rng = partie(3)          # jury : les 2 autres
    t = cocher_tout(e, "Lea", [0, 1, 2], 2000, rng)
    verifier(b.vue_privee(e, "Lea", t)["annonce"] is None, "pas de bouton BINGO sans ligne complète")
    r = b.annoncer(e, "Lea", t, rng)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "  et le serveur refuse l'annonce")
    t = cocher_tout(e, "Lea", [3], t, rng)
    verifier(b.vue_privee(e, "Lea", t)["annonce"] == "lignes", "ligne complète : bouton BINGO")
    r = b.annoncer(e, "Lea", t, rng)
    a = e["audit"]
    verifier(a and sorted(a["jures"]) == ["max", "zoe"], "jury : tous les autres à 3 joueurs")
    verifier({x["a"] for x in r if x["msg"]["type"] == "bng_jury"} == {"Max", "Zoe"}, "  les jurés sont prévenus")
    vj = b.vue_privee(e, "Max", t)
    verifier(vj["jury"] and [c["index"] for c in vj["jury"]["cases"]] == [0, 1, 2, 3], "  le juré voit les cases de la ligne")
    verifier(b.vue_privee(e, "Lea", t)["jury"] is None and b.vue_privee(e, "Lea", t)["mon_audit"]["etat"] == "en_cours",
             "  le joueur sait que son BINGO est vérifié")
    r = b.cocher(e, "Lea", 0, t + 20)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "  les cases annoncées sont figées")
    # Un seul "pas vu" sur deux : pas de majorite, validee
    b.voter(e, "Max", {"id": a["id"], "votes": {"0": "pas_vu", "1": "vu", "2": "vu", "3": "vu"}}, t + 1, rng)
    r = b.voter(e, "Zoe", {"id": a["id"], "votes": {str(i): "vu" for i in range(4)}}, t + 2, rng)
    res = [x for x in r if x["msg"]["type"] == "bng_resultat"]
    verifier(res and res[0]["msg"]["ok"] and e["j"]["lea"]["points"] == 1, "1 « Pas vu » sur 2 : ligne validée, 1 point")
    verifier(res[0]["msg"]["distribue"] == 2, "  la joueuse distribue 2 gorgées (vitesse Soirée)")
    verifier(all(e["j"]["lea"]["coches"][str(i)]["valide"] for i in range(4)), "  cases validées")
    verifier(b.vue_privee(e, "Lea", t + 3)["annonce"] is None, "  la même ligne ne rapporte qu'une fois")
    # Colonne 0 : la case 0 deja validee n est pas reauditee
    t = cocher_tout(e, "Lea", [4, 8, 12], t + 3, rng)
    b.annoncer(e, "Lea", t, rng)
    verifier(sorted(e["audit"]["cases"]) == [4, 8, 12], "une case déjà validée n'est jamais réauditée")
    # Refus : majorite de « Pas vu » sur la case 8
    a = e["audit"]
    b.voter(e, "Max", {"id": a["id"], "votes": {"8": "pas_vu"}}, t + 1, rng)
    verifier(e["audit"] is not None, "  l'audit attend que tous les jurés aient tout voté")
    b.voter(e, "Max", {"id": a["id"], "votes": {"4": "vu", "12": "vu"}}, t + 1, rng)
    r = b.voter(e, "Zoe", {"id": a["id"], "votes": {"4": "vu", "8": "pas_vu", "12": "vu"}}, t + 2, rng)
    verifier(e["audit"] is None, "  puis il se clôt sans attendre l'échéance")
    verifier("8" not in e["j"]["lea"]["coches"], "majorité « Pas vu » : case décochée")
    res = [x for x in r if x["msg"]["type"] == "bng_resultat"][0]["msg"]
    verifier(not res["ok"] and res["boit"] == 2 and e["j"]["lea"]["points"] == 1, "  BINGO refusé : elle boit 2, pas de point")
    verifier("4" in e["j"]["lea"]["coches"] and not e["j"]["lea"]["coches"]["4"]["fige"], "  les autres cases restent cochées")
    t2 = cocher_tout(e, "Lea", [8], t + 2, rng)
    r = b.annoncer(e, "Lea", t2, rng)
    verifier(r and "Pas de BINGO" in r[0]["msg"]["message"], "  pas de nouveau BINGO pendant 5 min")
    r = b.annoncer(e, "Lea", t + 2 + 301, rng)
    verifier(e["audit"] is not None, "  possible après le délai")
    # Sans vote : « Vu » par defaut a l echeance
    r = b.expirer(e, e["audit"]["echeance"], rng)
    verifier(any(x["msg"].get("ok") for x in r) and e["j"]["lea"]["points"] == 2, "sans vote à l'échéance : « Vu », validé")
    r = b.voter(e, "Max", {"id": 999, "votes": {}}, t + 400, rng)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "vote sur un audit clos refusé")


def essai_jury_file():
    print("Jury et file d'attente")
    e, rng = partie(7)
    t = 2000
    for nom in ("Lea", "Max"):
        p = e["j"][b.cle_nom(nom)]
        for i in range(4):
            p["coches"][str(i)] = {"t": t, "fige": False, "valide": False}
    b.annoncer(e, "Lea", t, rng, connectes=NOMS)
    a1 = list(e["audit"]["jures"])
    verifier(len(a1) == 3 and "lea" not in a1, "plus de 4 autres joueurs : jury de 3 tirés au hasard")
    r = b.annoncer(e, "Max", t, rng, connectes=NOMS)
    verifier(len(e["file"]) == 1 and e["audit"]["joueur"] == "lea", "un seul audit à la fois, le suivant attend")
    verifier(b.vue_privee(e, "Max", t)["mon_audit"] == {"etat": "file", "echeance": None, "position": 1}, "  Max voit sa place dans la file")
    autres = [n for n in NOMS if b.cle_nom(n) not in a1]
    verifier(not any(b.vue_privee(e, n, t)["jury"] for n in autres), "les non-jurés ne sont pas dérangés")
    b.expirer(e, e["audit"]["echeance"], rng, connectes=NOMS)
    a2 = e["audit"]
    verifier(a2 and a2["joueur"] == "max", "audit clos : le suivant démarre")
    verifier(not (set(a1) - {"max"}) & set(a2["jures"]) or len(set(a2["jures"]) & set(a1)) < 3,
             "  on évite les mêmes jurés deux audits de suite")
    # Jures absents : on prefere les presents
    e, rng = partie(7, graine=3)
    p = e["j"]["lea"]
    for i in range(4):
        p["coches"][str(i)] = {"t": 0, "fige": False, "valide": False}
    b.annoncer(e, "Lea", 2000, rng, connectes=["Lea", "Ana", "Bob", "Cyd"])
    verifier(sorted(e["audit"]["jures"]) == ["ana", "bob", "cyd"], "jury pris parmi les joueurs connectés")
    b.expirer(e, e["audit"]["echeance"], rng)
    verifier(e["j"]["lea"]["points"] == 1, "jurés absents : « Vu » par défaut, pas de blocage")


def essai_grille_pleine():
    print("Grille pleine")
    e, rng = partie(2, "flash")
    p = e["j"]["max"]
    for i in range(9):
        p["coches"][str(i)] = {"t": 0, "fige": False, "valide": i < 3}
    p["lignes"] = [0]
    p["points"] = 1
    verifier(b.vue_privee(e, "Max", 2000)["annonce"] == "grille", "toutes cochées : annonce de grille pleine")
    b.annoncer(e, "Max", 2000, rng)
    verifier(sorted(e["audit"]["cases"]) == list(range(3, 9)), "  l'audit ne porte que sur les cases pas encore validées")
    r = b.expirer(e, e["audit"]["echeance"], rng)
    res = [x for x in r if x["msg"]["type"] == "bng_resultat"][0]["msg"]
    verifier(res["grille"] and p["grille_validee"] and p["points"] == 1 + 7 + 5, "  les 7 lignes restantes + 5 points de bonus")
    verifier(res["distribue"] == 8, "  distribue 5 x 1,5 = 8 en Flash (arrondi)")
    verifier(len(p["lignes"]) == 8, "  tous les alignements comptent comme faits")
    verifier(b.vue_privee(e, "Max", 2100)["annonce"] is None, "  plus rien à annoncer")


def essai_plafond_commun():
    print("Plafond de gorgées commun")
    e, rng = partie(3, commun={"enquete": True, "plafond": 6, "unite": "shot"})
    verifier(b.vue_publique(e, 2000)["unite"] == "shot" and b.plafond_effectif(e) == 6, "unité et plafond repris de l'Enquête")
    p = e["j"]["lea"]
    for i in range(4):
        p["coches"][str(i)] = {"t": 0, "fige": False, "valide": False}
    b.annoncer(e, "Lea", 2000, rng)
    a = e["audit"]
    for j in a["jures"]:
        b.voter(e, e["j"][j]["nom"], {"id": a["id"], "votes": {str(i): "pas_vu" for i in a["cases"]}}, 2001, rng,
                ailleurs=lambda nom, t: 5)
    verifier(p["gorgees"][-1][1] == 1, "5 gorgées déjà bues à l'Enquête : elle n'en boit qu'1 au bingo")
    verifier(p["refus_jusqua"] == 2001 + 300 + 60, "  la gorgée au-delà du plafond devient 1 min de délai en plus")


def essai_fin_et_non_fuite():
    print("Fin, classement et non-fuite")
    e, rng = partie(3)
    for nom, t in (("Max", 3000), ("Zoe", 2500)):
        p = e["j"][b.cle_nom(nom)]
        p["points"], p["score_a"] = 2, t
    cl = b.classement(e)
    verifier([x["nom"] for x in cl[:2]] == ["Zoe", "Max"], "égalité : le premier arrivé au score l'emporte")
    vues = {n: b.vue_privee(e, n, 2000) for n in NOMS[:3]}
    verifier(len({tuple(sorted(v)) for v in vues.values()}) == 1, "vues privées de même forme pour tous")
    pub = json.dumps(b.vue_publique(e, 2000), ensure_ascii=False)
    fuite = any(c["texte"] in pub for p in e["j"].values() for c in p["grille"])
    verifier(not fuite, "l'état public ne contient aucune case d'aucune grille")
    for n in NOMS[:3]:
        moi = json.dumps(vues[n], ensure_ascii=False)
        autres = [c["texte"] for k, p in e["j"].items() if k != b.cle_nom(n) for c in p["grille"]]
        mes = {c["texte"] for c in e["j"][b.cle_nom(n)]["grille"]}
        if any(x not in mes and json.dumps(x, ensure_ascii=False)[1:-1] in moi for x in autres):
            verifier(False, f"la vue de {n} contient une case d'un autre")
            break
    else:
        verifier(True, "aucune vue privée ne contient la grille d'un autre (hors jury)")
    verifier(vues["Lea"]["perso"] == [] or vues["Lea"]["hote"], "cases perso visibles par l'hôte seulement")
    r = b.expirer(e, e["fin_a"], rng)
    verifier(e["phase"] == "fin" and any(x["msg"]["type"] == "bng_fin" for x in r), "fin à l'heure prévue")
    res = e["resultat"]
    verifier(res["vainqueur"] == "Zoe" and res["grille_vainqueur"] and res["distribue"] == 5,
             "  vainqueur, sa grille et 5 gorgées à distribuer")
    r = b.cocher(e, "Max", 5, e["fin_a"] + 1)
    verifier(r and r[0]["msg"]["type"] == "bng_erreur", "  plus de cochage après la fin")
    b.action(e, "Lea", {"type": "bng_fermer"}, e["fin_a"] + 2)
    verifier(e["phase"] == "fermee", "l'hôte ferme la partie")
    e, rng = partie(3, commun={"enquete": True, "fin_a": 1000 + 7200})
    verifier(e["fin_a"] == 8200 and e["fin_calee"], "heure de fin reprise de l'Enquête")
    b.action(e, "Ana", {"type": "bng_rejoindre"}, 1500, rng)
    verifier(len(e["j"]["ana"]["grille"]) == 16, "un joueur qui arrive en cours reçoit sa grille")


class FauxWS:
    def __init__(self):
        self.recus = []
        self.ferme = None

    async def send_text(self, texte):
        self.recus.append(json.loads(texte))

    async def close(self, code=1000):
        self.ferme = code

    def de_type(self, t):
        return [x for x in self.recus if x.get("type") == t]


async def essai_couche():
    print("Couche branchée sur le serveur")
    memoire = StockageMemoire()
    await serveur.demarrage(memoire)
    code = (await serveur.creer_salon())["code"]
    ws = {}
    for nom in NOMS[:3]:
        ws[nom] = FauxWS()
        await serveur.abonner(code, nom, ws[nom], "bingo")
    enq = FauxWS()
    await serveur.abonner(code, "Lea", enq, "")         # l Enquete ouverte par la meme personne
    await serveur.couche_action(code, "Lea", {"type": "bng_creer"})
    verifier("bng" in serveur.couches.get(code, {}), "Léa crée le bingo de la soirée")
    verifier(ws["Lea"].ferme is None and enq.ferme is None, "Enquête et Bingo ouverts en même temps : personne n'est éjecté")
    verifier(ws["Max"].de_type("soiree_couches")[-1]["couches"] == ["bng"], "  tous apprennent que le bingo existe")
    for nom in NOMS[1:3]:
        await serveur.couche_action(code, nom, {"type": "bng_rejoindre"})
    await serveur.couche_action(code, "Lea", {"type": "bng_lancer"})
    moi = {n: w.de_type("bng_moi")[-1] for n, w in ws.items()}
    verifier(all(len(x["grille"]) == 16 for x in moi.values()), "lancé : chacun reçoit sa grille 4x4")
    verifier(len({json.dumps(x["grille"]) for x in moi.values()}) == 3, "  grilles différentes")
    verifier(enq.de_type("bng_moi")[-1]["grille"] == moi["Lea"]["grille"], "  sa vue va à toutes ses pages, pas à celles des autres")
    await serveur.couche_action(code, "Max", {"type": "bng_cocher", "index": 2})
    verifier(ws["Max"].de_type("bng_moi")[-1]["coches"][0]["index"] == 2, "cocher passe par le serveur")
    await serveur.sauvegarde.vider()
    sauvee = await memoire.lire(f"couche:{code}:bng")
    verifier(sauvee and "2" in sauvee["donnees"]["j"]["max"]["coches"], "la partie est sauvegardée")
    serveur.couches.clear()
    await serveur.demarrage(memoire)
    w = FauxWS()
    await serveur.abonner(code, "Max", w, "bingo")
    verifier(ws["Max"].ferme == 4002, "même page ouverte deux fois : l'ancienne est prévenue")
    verifier(w.de_type("bng_moi") and w.de_type("bng_moi")[-1]["grille"] == moi["Max"]["grille"],
             "après redémarrage et reconnexion, Max retrouve sa grille")
    # Plafond partage : les gorgees du bingo comptent pour l Enquete
    couche = serveur.couches[code]["bng"]
    couche.etat["j"]["max"]["gorgees"].append([__import__("time").time(), 4])
    total = serveur._gorgees_ailleurs(code, object(), "Max", __import__("time").time())
    verifier(total == 4, "l'Enquête voit les gorgées bues au bingo (compteur commun)")
    await serveur.arret()


essai_tirage()
essai_cochage()
essai_bingo_audit()
essai_jury_file()
essai_grille_pleine()
essai_plafond_commun()
essai_fin_et_non_fuite()
asyncio.run(essai_couche())

print()
if ECHECS:
    print(f"{len(ECHECS)} ÉCHEC(S)")
    sys.exit(1)
print("Tous les essais du bingo passent.")
