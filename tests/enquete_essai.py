"""
Essais de l'Enquete, tranche 1 (sans reseau) :
- salle d attente : portraits, reglages de l hote, lancement ;
- tirage : solution coherente, tueur parmi les joueurs, de 4 a 12 joueurs ;
- temps : ouverture puis enquete, victoire du tueur a l heure butoir ;
- NON-FUITE : etat public sans solution ni tueur avant la fin, vue privee de
  meme forme pour tous ;
- couche branchee sur le serveur (connexions soiree), sauvegarde.

Usage : python tests/enquete_essai.py
"""
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import enquete as m
import serveur
from stockage import StockageMemoire

ECHECS = []


def verifier(cond, quoi):
    print(("  ok  " if cond else "  ECHEC ") + quoi)
    if not cond:
        ECHECS.append(quoi)


NOMS = ["Lea", "Max", "Zoe", "Tom", "Ana", "Bob", "Cyd", "Dan", "Eve", "Fox", "Gus", "Ida", "Jo"]


def partie(n, graine=1, vitesse=None):
    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    if vitesse:
        m.action(e, "Lea", {"type": "enq_config", "vitesse": vitesse}, 1000.0)
    portraits = [p["id"] for p in m.PACKS[e["univers"]]["portraits"]]
    for i in range(n):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": portraits[i]}, 1000.0)
    return e


def essai_salle_attente():
    print("Salle d'attente")
    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    ev = m.action(e, "Lea", {"type": "enq_portrait", "portrait": "comtesse"}, 1000.0)
    verifier(e["joueurs"] == [{"nom": "Lea", "portrait": "comtesse"}] and not ev, "Léa prend la comtesse")
    ev = m.action(e, "Max", {"type": "enq_portrait", "portrait": "comtesse"}, 1000.0)
    verifier(len(e["joueurs"]) == 1 and ev and ev[0]["msg"]["type"] == "enq_erreur", "  portrait déjà pris : refusé")
    m.action(e, "Max", {"type": "enq_portrait", "portrait": "docteur"}, 1000.0)
    m.action(e, "Max", {"type": "enq_portrait", "portrait": "capitaine"}, 1000.0)
    verifier([j["portrait"] for j in e["joueurs"]] == ["comtesse", "capitaine"], "  on peut changer de portrait")
    v = e["version"]
    ev = m.action(e, "Max", {"type": "enq_config", "vitesse": "flash"}, 1000.0)
    verifier(e["version"] == v and ev[0]["msg"]["type"] == "enq_erreur", "seul l'hôte règle l'enquête")
    m.action(e, "Lea", {"type": "enq_config", "vitesse": "flash"}, 1000.0)
    verifier(e["vitesse"] == "flash" and e["jeux"] == ["kinito", "menteur", "buckshot"], "Flash : jeux rapides seulement")
    m.action(e, "Lea", {"type": "enq_config", "jeux": ["kinito", "casino"]}, 1000.0)
    verifier(e["jeux"] == ["kinito"], "  un jeu non autorisé par la vitesse est écarté")
    ev = m.action(e, "Lea", {"type": "enq_config", "duree": 60}, 1000.0)
    verifier(ev and ev[0]["msg"]["type"] == "enq_erreur", "durée trop courte refusée")
    m.action(e, "Lea", {"type": "enq_config", "unite": "pompe"}, 1000.0)
    verifier(e["unite"] == "pompe", "unité de pénalité réglable (mode sans alcool)")
    ev = m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0)
    verifier(e["phase"] == "lobby" and ev[0]["msg"]["type"] == "enq_erreur", "lancement refusé à 2 joueurs")

    e = m.nouvelle_partie("ABCD", "Lea", 1000.0)
    for i in range(12):
        m.action(e, NOMS[i], {"type": "enq_portrait", "portrait": m.PACKS["manoir"]["portraits"][i]["id"]}, 1000.0)
    ev = m.action(e, NOMS[12], {"type": "enq_portrait", "portrait": "voyante"}, 1000.0)
    verifier(len(e["joueurs"]) == 12, "12 joueurs au plus")


def essai_tirage():
    print("Tirage")
    bons = True
    tueurs = set()
    for n in range(4, 13):
        for vitesse in ("flash", "express", "soiree", "marathon"):
            for g in range(30):
                e = partie(n, vitesse=vitesse)
                m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(g))
                k = m.CONFIG["vitesses"][vitesse]["lieux_armes"]
                s = e["solution"]
                bons &= (e["phase"] == "ouverture" and len(e["lieux"]) == k and len(e["armes"]) == k
                         and s["lieu"] in e["lieux"] and s["arme"] in e["armes"]
                         and s["tueur"] in [j["nom"] for j in e["joueurs"]] and s["tueur"] == e["tueur"])
                tueurs.add(s["tueur"])
    verifier(bons, "de 4 à 12 joueurs, à chaque vitesse : solution cohérente et lieux/armes retenus selon la vitesse")
    verifier(len(tueurs) == 12, "  chacun peut être tiré comme tueur")


def essai_temps():
    print("Temps")
    e = partie(5)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(3))
    verifier(m.prochaine_echeance(e) == 1000.0 + m.CONFIG["duree_ouverture"], "fin de l'ouverture programmée")
    m.expirer(e, 1000.0 + m.CONFIG["duree_ouverture"])
    verifier(e["phase"] == "enquete" and m.prochaine_echeance(e) <= e["fin_a"], "l'enquête commence après l'ouverture")
    m.expirer(e, e["fin_a"])
    verifier(e["phase"] == "fin" and e["vainqueur"] == "tueur", "heure butoir : le tueur gagne")
    v = m.vue_publique(e, e["fin_a"])
    verifier(v["fin"]["solution"] == e["solution"] and e["tueur"] in v["fin"]["recit"], "la fin révèle la solution et le récit")


def essai_non_fuite():
    print("Non-fuite")
    e = partie(8)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(7))
    for phase_t in (1000.0, 1000.0 + m.CONFIG["duree_ouverture"]):
        m.expirer(e, phase_t)
        pub = m.vue_publique(e, phase_t)
        texte = json.dumps(pub)
        verifier(pub["fin"] is None and "tueur" not in pub and '"solution"' not in texte.replace('"fin": null', ''),
                 f"phase {e['phase']} : ni solution ni tueur dans l'état public")
        verifier(e["solution"]["arme"] in pub["armes"], "  (les armes retenues, elles, sont publiques)")
    vues = [m.vue_privee(e, j["nom"]) for j in e["joueurs"]]
    verifier(len({tuple(sorted(v)) for v in vues}) == 1, "vue privée : mêmes clés pour les 8 joueurs")
    verifier(sum(v["role"] == "tueur" for v in vues) == 1 and all(v["solution"] is None for v in vues if v["role"] != "tueur"),
             "  un seul tueur, et lui seul connaît la solution")
    spect = m.vue_privee(e, "Inconnu")
    verifier(sorted(spect) == sorted(vues[0]) and spect["role"] is None, "  même forme pour un spectateur")


def en_cours(n, graine=1, vitesse=None):
    e = partie(n, vitesse=vitesse)
    m.action(e, "Lea", {"type": "enq_lancer"}, 1000.0, rng=random.Random(graine))
    m.expirer(e, 1000.0 + m.CONFIG["duree_ouverture"])
    return e, 1000.0 + m.CONFIG["duree_ouverture"]


def essai_stock_indices():
    print("Stock d'indices")
    bons = True
    for n in range(4, 13):
        for vitesse in ("flash", "soiree"):
            for g in range(20):
                e, t = en_cours(n, g, vitesse)
                s = e["solution"]
                pool = e["pool"]
                cats = {c: [x["element"] for x in pool if x["categorie"] == c] for c in ("suspect", "lieu", "arme")}
                k = len(e["lieux"])
                bons &= (len(cats["suspect"]) == max(0, n - 3) and len(cats["lieu"]) == k - 2 and len(cats["arme"]) == k - 2
                         and s["tueur"] not in cats["suspect"] and s["lieu"] not in cats["lieu"] and s["arme"] not in cats["arme"]
                         and len({(x["categorie"], x["element"]) for x in pool}) == len(pool))
    verifier(bons, "plafonds K-3 (suspects) et K-2 (lieux, armes), jamais la solution, aucun doublon")
    # Même en mettant tout en commun, il reste 2 candidats par catégorie pour chacun.
    e, t = en_cours(4, 5)
    verifier(len([x for x in e["pool"] if x["categorie"] == "suspect"]) == 1,
             "  à 4 joueurs : une seule élimination de suspect (chacun s'élimine déjà lui-même)")
    pub = json.dumps(m.vue_publique(e, t))
    verifier('"pool"' not in pub and "element" not in pub, "  le stock reste secret")


def essai_points_et_indices():
    print("Points d'enquête, indices, archive")
    e, t = en_cours(5, 2)
    noms = [j["nom"] for j in e["joueurs"]]
    p = lambda n: e["perso"][m.cle_nom(n)]
    m.evenement_jeu(e, {"jeu": "menteur", "type": "victoire", "joueurs": noms, "gagnants": ["Max"], "perdants": [], "details": {}}, t)
    m.evenement_jeu(e, {"jeu": "beerbattle", "type": "victoire", "joueurs": noms, "gagnants": ["Max"], "perdants": [], "details": {}}, t)
    verifier(p("Max")["pe"] == 3, "Menteur +1, BeerBattle +2")
    for _ in range(3):
        m.evenement_jeu(e, {"jeu": "kinito", "type": "manche", "joueurs": noms, "gagnants": [], "perdants": ["Zoe"], "details": {}}, t)
    verifier(p("Lea")["pe"] == 1 and p("Zoe")["pe"] == 0, "Kinito : 1 PE toutes les 3 manches non perdues")
    m.evenement_jeu(e, {"jeu": "buckshot", "type": "manche", "joueurs": noms, "gagnants": [], "perdants": [],
                        "details": {"gorgees": {"Lea": 3, "Tom": 0, "Ana": 0, "Inconnu": 0}}}, t)
    # (Tom et Ana avaient déjà 1 PE de la série Kinito.)
    verifier(p("Tom")["pe"] == 2 and p("Ana")["pe"] == 2 and p("Lea")["pe"] == 1, "Buckshot : +1 aux moins pénalisés (ex aequo compris)")
    tables = [{"code": "ABCD", "jeu": "casino", "joueurs": ["Zoe", "Tom"]}]
    m.tic_paliers(e, tables, t)
    for i in range(1, 16):
        m.tic_paliers(e, tables, t + 60 * i)
    verifier(p("Zoe")["pe"] == 1, "Casino : 1 PE par quart d'heure passé à la table")
    m.evenement_jeu(e, {"jeu": "picolopoly", "type": "victoire", "joueurs": noms, "gagnants": ["Zoe"], "perdants": [], "details": {}}, t)
    verifier(p("Zoe")["pe"] == 3, "Picopoly : bonus de victoire finale")
    e2, t2 = en_cours(5, 2, "flash")
    m.evenement_jeu(e2, {"jeu": "picolopoly", "type": "victoire", "joueurs": noms, "gagnants": ["Zoe"], "perdants": [], "details": {}}, t2)
    verifier(e2["perso"][m.cle_nom("Zoe")]["pe"] == 0, "un jeu non compté par l'hôte ne rapporte rien")

    ev = m.action(e, "Lea", {"type": "enq_indice"}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and p("Lea")["archive"] == [], "pas assez de PE : refusé")
    ev = m.action(e, "Max", {"type": "enq_indice"}, t)
    a = p("Max")["archive"]
    # Max avait 4 PE (Menteur 1, BeerBattle 2, Kinito 1) ; son dernier gain vient de Kinito.
    verifier(len(a) == 1 and p("Max")["pe"] == 1 and a[0]["source"] == "Victoire (Kinito)", "3 PE : un indice, source « Victoire (Kinito) »")
    verifier(not (a[0]["categorie"] == "suspect" and a[0]["element"] == "Max"), "  jamais sa propre innocence")
    vp = m.vue_privee(e, "Max", t)
    verifier(vp["archive"][0]["lu"] is False and "texte" not in json.dumps(vp["archive"]), "  non lu, le texte n'est pas encore envoyé")
    verifier(any("pli" in x["texte"] for x in e["journal"]), "  le narrateur annonce un pli, sans dire à qui")
    ev = m.action(e, "Max", {"type": "enq_lire", "id": a[0]["id"]}, t)
    verifier(ev[0]["msg"]["type"] == "enq_texte" and ev[0]["msg"]["gratuit"] and len(ev) == 1, "première lecture : gratuite")
    ev = m.action(e, "Max", {"type": "enq_lire", "id": a[0]["id"]}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "  une seule fois")
    ev = m.action(e, "Max", {"type": "enq_consulter", "id": a[0]["id"]}, t + 1)
    types = [x["msg"]["type"] for x in ev]
    verifier(types == ["enq_texte", "enq_gorgee"] and ev[1]["msg"]["nombre"] == 1, "consulter l'archive : le texte et 1 gorgée")
    ev = m.action(e, "Max", {"type": "enq_consulter", "id": a[0]["id"]}, t + 30)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and "verrouillée" in ev[0]["msg"]["message"], "  puis chrono avant la suivante")
    ev = m.action(e, "Max", {"type": "enq_consulter", "id": a[0]["id"]}, t + 1 + 180)
    verifier(ev[0]["msg"]["type"] == "enq_texte", "  chrono écoulé : consultation possible")
    ev = m.action(e, "Lea", {"type": "enq_acheter"}, t)
    g = [x["msg"] for x in ev if x["msg"]["type"] == "enq_gorgee"]
    verifier(p("Lea")["archive"][-1]["source"] == "Achat" and g and g[0]["nombre"] == 2, "achat : un indice « Achat » contre 2 gorgées")
    m.action(e, "Lea", {"type": "enq_acheter"}, t + 10)
    ev = m.action(e, "Lea", {"type": "enq_acheter"}, t + 20)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and len(p("Lea")["archive"]) == 2, "2 indices par heure au plus")
    ev = m.action(e, "Lea", {"type": "enq_acheter"}, t + 3601)
    verifier(len(p("Lea")["archive"]) == 3, "  une heure plus tard, c'est reparti")

    vues = [m.vue_privee(e, n, t) for n in noms] + [m.vue_privee(e, "Inconnu", t)]
    verifier(len({tuple(sorted(v)) for v in vues}) == 1, "vue privée : toujours les mêmes clés pour tous")

    # Épuisement du stock
    e, t = en_cours(4, 9)
    taille = len(e["pool"])
    distribues = 0
    for i in range(60):
        nom = NOMS[i % 4]
        e["perso"][m.cle_nom(nom)]["obtenus"] = []
        e["perso"][m.cle_nom(nom)]["pe"] = 99
        ev = m.action(e, nom, {"type": "enq_indice"}, t)
        distribues += any(x["msg"]["type"] == "enq_nouvel_indice" for x in ev)
    tous = [(x["categorie"], x["element"]) for j in e["perso"].values() for x in j["archive"]]
    verifier(distribues == taille and len(set(tous)) == len(tous), "tout le stock est distribué, sans doublon entre joueurs")
    verifier(e["dossier_epuise"] or not e["pool"], "  puis « dossier épuisé »")


def essai_accusations():
    print("Accusations et fin")
    e, t = en_cours(5, 4)
    sol = e["solution"]
    p = lambda n: e["perso"][m.cle_nom(n)]
    enq = [j["nom"] for j in e["joueurs"] if j["nom"] != sol["tueur"]]
    a, b = enq[0], enq[1]
    autre_arme = [x for x in e["armes"] if x != sol["arme"]][0]
    autre_lieu = [x for x in e["lieux"] if x != sol["lieu"]][0]
    ev = m.action(e, a, {"type": "enq_accuser", "suspect": sol["tueur"], "arme": autre_arme, "lieu": autre_lieu}, t)
    types = [x["msg"]["type"] for x in ev]
    verdict = ev[0]["msg"]
    verifier(types == ["enq_verdict", "enq_gorgee"] and verdict["justes"] == 1 and ev[1]["msg"]["nombre"] == 2,
             "1 élément juste : on l'apprend sans savoir lequel, et 2 gorgées")
    verifier("suspect" in verdict and "bon" not in json.dumps(verdict), "  le verdict ne dit pas lequel est juste")
    verifier(e["echecs"] == 1 and "a échoué" in e["journal"][-1]["texte"], "  annonce publique, sans auteur ni contenu")
    pub = json.dumps(m.vue_publique(e, t))
    verifier(autre_arme not in pub.split('"armes"')[0] and '"accusations"' not in pub, "  le contenu de l'accusation n'est pas public")
    ev = m.action(e, a, {"type": "enq_accuser", "suspect": sol["tueur"], "arme": sol["arme"], "lieu": sol["lieu"]}, t + 60)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "délai avant la prochaine accusation")
    attente = p(a)["prochaine_accusation_a"] - t
    verifier(attente == 600 * 2, "  doublé après un échec (20 min en Soirée)")
    ev = m.action(e, a, {"type": "enq_accuser", "suspect": "Personne", "arme": sol["arme"], "lieu": sol["lieu"]}, t + 5000)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and e["echecs"] == 1, "accusation incomplète ou invalide : refusée, ne compte pas")
    m.action(e, a, {"type": "enq_accuser", "suspect": b, "arme": autre_arme, "lieu": autre_lieu}, t + 5000)
    verifier(p(a)["prochaine_accusation_a"] - (t + 5000) == 600 * 4 and p(a)["gorgees"][-1][1] == 4,
             "deuxième échec : délai x4, 4 gorgées")
    # Le tueur bluffe : même en accusant juste, c'est un échec pour tous.
    ev = m.action(e, sol["tueur"], {"type": "enq_accuser", "suspect": sol["tueur"], "arme": sol["arme"], "lieu": sol["lieu"]}, t)
    verifier(e["phase"] == "enquete" and ev[0]["msg"]["type"] == "enq_verdict" and ev[0]["msg"]["justes"] == 2,
             "le tueur peut accuser pour bluffer : jamais une victoire, échec pour tous")
    ev = m.action(e, b, {"type": "enq_accuser", "suspect": sol["tueur"], "arme": sol["arme"], "lieu": sol["lieu"]}, t)
    verifier(e["phase"] == "fin" and e["vainqueur"] == "enqueteurs" and e["detective"] == b, "3 sur 3 : victoire, " + b + " devient le Détective")
    v = m.vue_publique(e, t)
    r = v["fin"]["recap"]
    verifier(len(r["accusations"]) == 4 and any(x["par_le_tueur"] for x in r["accusations"]), "récapitulatif : toutes les accusations, bluff du tueur compris")
    verifier(r["gorgees"][0] == {"qui": b, "action": "distribue", "nombre": 6} and r["gorgees"][1]["qui"] == sol["tueur"],
             "  le Détective distribue 6, le tueur boit 6")
    verifier(b in v["fin"]["recit"] and sol["tueur"] in v["fin"]["recit"], "  récit de fin avec la solution")

    # Seuil de patience
    e, t = en_cours(4, 8)
    seuil = m.seuil_patience(e)
    sol = e["solution"]
    enq = [j["nom"] for j in e["joueurs"] if j["nom"] != sol["tueur"]]
    autre = [x for x in e["armes"] if x != sol["arme"]][0]
    n = 0
    while e["phase"] == "enquete" and n < 20:
        nom = enq[n % len(enq)]
        e["perso"][m.cle_nom(nom)]["prochaine_accusation_a"] = None
        m.action(e, nom, {"type": "enq_accuser", "suspect": sol["tueur"], "arme": autre, "lieu": sol["lieu"]}, t)
        n += 1
    verifier(e["phase"] == "fin" and e["vainqueur"] == "tueur" and e["raison_fin"] == "patience" and n == seuil,
             f"seuil de patience ({seuil} échecs à 4 en Soirée) : le tueur gagne")
    verifier(m.vue_publique(e, t)["fin"]["recap"]["gorgees"][0]["action"].startswith("distribue"), "  et distribue ses gorgées")


class FauxWS:
    def __init__(self):
        self.recus = []

    async def send_text(self, t):
        self.recus.append(json.loads(t))

    async def close(self, code=1000):
        pass

    def de_type(self, t):
        return [x for x in self.recus if x.get("type") == t]


async def essai_couche():
    print("Couche branchée sur le serveur")
    memoire = StockageMemoire()
    await serveur.demarrage(memoire)
    code = (await serveur.creer_salon())["code"]
    ws = {}
    for nom in NOMS[:5]:
        ws[nom] = FauxWS()
        await serveur.abonner(code, nom, ws[nom])
    await serveur.couche_action(code, "Lea", {"type": "enq_creer"})
    verifier("enq" in serveur.couches.get(code, {}), "Léa crée l'enquête de la soirée")
    verifier(all(w.de_type("enq_etat") for w in ws.values()), "  tous les abonnés reçoivent l'état")
    portraits = [p["id"] for p in m.PACKS["manoir"]["portraits"]]
    for i, nom in enumerate(NOMS[:5]):
        await serveur.couche_action(code, nom, {"type": "enq_portrait", "portrait": portraits[i]})
    for w in ws.values():
        w.recus.clear()
    await serveur.couche_action(code, "Lea", {"type": "enq_lancer"})
    moi = {n: w.de_type("enq_moi")[-1] for n, w in ws.items()}
    verifier(sum(x["role"] == "tueur" for x in moi.values()) == 1, "lancée : un seul tueur parmi les 5")
    verifier(len({tuple(sorted(x)) for x in moi.values()}) == 1, "  messages privés de même forme pour tous")
    pub = [w.de_type("enq_etat")[-1] for w in ws.values()]
    verifier(all(p == pub[0] for p in pub) and pub[0]["fin"] is None, "  état public identique pour tous, sans solution")
    tueur = [n for n, x in moi.items() if x["role"] == "tueur"][0]
    verifier(all(tueur not in json.dumps(w.de_type("enq_moi")[-1]) for n, w in ws.items() if n != tueur),
             "  aucun message privé ne nomme le tueur à un autre joueur")
    await serveur.sauvegarde.vider()
    sauvee = await memoire.lire(f"couche:{code}:enq")
    verifier(sauvee and sauvee["donnees"]["solution"]["tueur"] == tueur, "l'enquête est sauvegardée")
    serveur.couches.clear()
    await serveur.demarrage(memoire)
    rechargee = serveur.couches.get(code, {}).get("enq")
    verifier(rechargee is not None and rechargee.etat["tueur"] == tueur, "  et rechargée après redémarrage")
    w = FauxWS()
    await serveur.abonner(code, "Max", w)
    verifier(w.de_type("enq_pack") and w.de_type("enq_etat") and w.de_type("enq_moi"),
             "un joueur qui se reconnecte reçoit le pack, l'état et sa vue")
    await serveur.arret()


def essai_temoin_anonyme():
    print("Témoin anonyme")
    e, t = en_cours(6, 3)
    v = m.CONFIG["vitesses"][e["vitesse"]]["bonus_toutes"]
    alea = m.CONFIG["bonus_alea"]
    b = e["prochain_bonus_a"]
    verifier(t + v * (1 - alea) - 1e-6 <= b <= t + v * (1 + alea) + 1e-6, "premier bonus programmé après l'ouverture, à ± l'aléa près")
    verifier(m.prochaine_echeance(e) <= b, "  le serveur se réveille à temps pour le bonus")
    verifier("prochain_bonus_a" not in json.dumps(m.vue_publique(e, t)) and str(b) not in json.dumps(m.vue_publique(e, t)),
             "  l'heure exacte du bonus reste secrète")
    rng = random.Random(7)
    servis = []
    avant = sum(len(p["archive"]) for p in e["perso"].values())
    ev = m.expirer(e, b, rng=rng)
    servis.append(ev[0]["a"] if ev else None)
    nom = servis[0]
    p = m._perso(e, nom)
    verifier(len(ev) == 1 and ev[0]["msg"]["source"] == "Témoin anonyme"
             and sum(len(x["archive"]) for x in e["perso"].values()) == avant + 1,
             "à l'heure dite, un seul joueur reçoit un pli « Témoin anonyme »")
    verifier(p["obtenus"] == [], "  hors plafond horaire (ce n'est pas une demande)")
    verifier(e["journal"][-1]["texte"] == m.PACKS[e["univers"]]["textes"]["annonces"]["indice"],
             "  annonce publique identique à celle d'un indice gagné")
    # Sur beaucoup de tirages : jamais deux fois de suite le même, tueur compris.
    tirages = []
    for g in range(60):
        e, t = en_cours(6, g)
        r = random.Random(g)
        noms = []
        for _ in range(3):
            ev = m.expirer(e, e["prochain_bonus_a"], rng=r)
            if ev:
                noms.append(m.cle_nom(ev[0]["a"]))
        tirages.append((noms, m.cle_nom(e["tueur"])))
    verifier(all(a != b for noms, _ in tirages for a, b in zip(noms, noms[1:])), "  jamais deux fois de suite le même joueur")
    verifier(any(k in noms for noms, k in tirages), "  le tueur peut être tiré (indice de couverture)")
    # Stock vidé : les bonus s'arrêtent, dossier épuisé annoncé.
    e, t = en_cours(4, 2)
    r = random.Random(1)
    for _ in range(40):
        if e["prochain_bonus_a"] is None:
            break
        m.expirer(e, e["prochain_bonus_a"], rng=r)
    verifier(e["prochain_bonus_a"] is None and e["dossier_epuise"] and not e["pool"],
             "stock vide : plus de bonus, « dossier épuisé » annoncé")
    verifier(all(x["vrai"] for p in e["perso"].values() for x in p["archive"]), "  tous les indices du témoin sont vrais")


def essai_contre_expertise():
    print("Contre-expertise")
    e, t = en_cours(5, 4)
    nom = "Max"
    p = m._perso(e, nom)
    p["pe"] = 10
    m.action(e, nom, {"type": "enq_indice"}, t)
    i = p["archive"][0]["id"]
    ev = m.action(e, nom, {"type": "enq_expertise", "id": i, "paiement": "pe"}, t)
    verifier(ev and ev[0]["msg"]["type"] == "enq_erreur", "pas d'expertise sur un pli pas encore ouvert")
    m.action(e, nom, {"type": "enq_lire", "id": i}, t)
    pe = p["pe"]
    ev = m.action(e, nom, {"type": "enq_expertise", "id": i, "paiement": "pe"}, t + 1)
    msg = ev[0]["msg"]
    verifier(msg["type"] == "enq_texte" and msg["tampon"] == "authentique" and msg["expertise"] and len(ev) == 1,
             "payée en PE : tampon « authentique » renvoyé avec le texte, sans gorgée")
    verifier(p["pe"] == pe - m.CONFIG["vitesses"][e["vitesse"]]["pe_par_indice"], "  coûte le prix d'un indice en PE")
    verifier(m.vue_privee(e, nom, t + 1)["archive"][0]["tampon"] == "authentique", "  tampon permanent dans l'archive")
    verifier(p["derniere_consultation"] == t + 1, "  compte comme une consultation (relance le chrono)")
    ev = m.action(e, nom, {"type": "enq_expertise", "id": i, "paiement": "gorgee"}, t + 10000)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "  pas deux tampons sur le même indice")
    m.action(e, nom, {"type": "enq_acheter"}, t + 2)
    j = p["archive"][1]["id"]
    m.action(e, nom, {"type": "enq_lire", "id": j}, t + 2)
    ev = m.action(e, nom, {"type": "enq_expertise", "id": j, "paiement": "gorgee"}, t + 3)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and "verrouill" in ev[0]["msg"]["message"], "  même chrono que la consultation")
    timer = m.CONFIG["vitesses"][e["vitesse"]]["timer_consultation"]
    ev = m.action(e, nom, {"type": "enq_expertise", "id": j, "paiement": "gorgee"}, t + 1 + timer)
    verifier(len(ev) == 2 and ev[1]["msg"]["type"] == "enq_gorgee" and ev[1]["msg"]["raison"] == "Contre-expertise",
             "achetée : une gorgée")
    # Un faux (prépare la tranche 5) sort « falsifié ».
    p["archive"][1]["vrai"] = False
    p["archive"][1]["tampon"] = None
    ev = m.action(e, nom, {"type": "enq_expertise", "id": j, "paiement": "gorgee"}, t + 1 + 3 * timer)
    verifier(ev[0]["msg"]["tampon"] == "falsifie", "un faux indice sort « falsifié »")
    verifier(not any("expertis" in x["texte"].lower() for x in e["journal"]), "  aucune annonce publique")
    q = m._perso(e, "Zoe")
    q["pe"] = 0
    m.action(e, "Zoe", {"type": "enq_acheter"}, t)
    m.action(e, "Zoe", {"type": "enq_lire", "id": q["archive"][0]["id"]}, t)
    ev = m.action(e, "Zoe", {"type": "enq_expertise", "id": q["archive"][0]["id"], "paiement": "pe"}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and q["archive"][0]["tampon"] is None, "sans assez de PE : refusée")
    ev = m.action(e, "Zoe", {"type": "enq_expertise", "id": p["archive"][0]["id"], "paiement": "gorgee"}, t + 99999)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "impossible d'expertiser l'archive d'un autre")
    formes = {tuple(sorted(m.vue_privee(e, n, t)["archive"][0].keys())) for n in ("Max", "Zoe") }
    verifier(len(formes) == 1, "  archives de même forme pour tous")


def _tueur_et_autres(e):
    k = e["tueur"]
    return k, [j["nom"] for j in e["joueurs"] if j["nom"] != k]


def essai_sabotages():
    print("Sabotages")
    e, t = en_cours(6, 11)
    k, autres = _tueur_et_autres(e)
    pk = m._perso(e, k)
    c = m.CONFIG["vitesses"][e["vitesse"]]["pe_par_indice"]
    ev = m.action(e, autres[0], {"type": "enq_charge"}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and ev[0]["msg"]["message"] == "Action impossible.",
             "un enquêteur ne peut ni acheter de charge…")
    ev = m.action(e, autres[0], {"type": "enq_sabotage", "sabotage": "interception", "cible": k}, t)
    verifier(ev[0]["msg"]["message"] == "Action impossible.", "  …ni saboter (même réponse neutre)")
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "interception", "cible": autres[0]}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "sans charge : pas de sabotage")
    pk["pe"] = 5 * c
    for _ in range(4):
        m.action(e, k, {"type": "enq_charge"}, t)
    verifier(pk["charges"] == 3 and pk["pe"] == 2 * c, "le tueur achète des charges avec ses PE, 3 au plus")
    vue = m.vue_privee(e, k, t)["console"]
    verifier(vue and vue["charges"] == 3 and set(vue["cibles"]) == set(autres) and abs(vue["detection"] - 0.5) < 1e-6,
             "  sa console : charges, cibles, détection de départ (50 %)")
    verifier(all(m.vue_privee(e, n, t)["console"] is None for n in autres), "  la console n'existe que pour lui (None pour les autres)")
    pub = json.dumps(m.vue_publique(e, t), ensure_ascii=False)
    verifier('"charges"' not in pub and "console" not in pub, "  rien dans l'état public")

    # Écoute
    cible = autres[0]
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "ecoute", "cible": cible}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and pk["charges"] == 3, "écoute d'une archive vide : refusée sans perdre de charge")
    q = m._perso(e, cible)
    q["pe"] = 10
    m.action(e, cible, {"type": "enq_indice"}, t)
    version = e["version"]
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "ecoute", "cible": cible}, t + 1, rng=random.Random(1))
    verifier(len(ev) == 1 and ev[0]["a"] == k and ev[0]["msg"]["texte"] == q["archive"][-1]["texte"],
             "écoute : le tueur lit le dernier indice de la cible")
    verifier(not any(x["a"] == cible for x in ev), "  sans aucune trace pour la cible")
    verifier(pk["charges"] == 2 and e["version"] > version, "  une charge dépensée")
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "interception", "cible": cible}, t + 2)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and "s" in ev[0]["msg"]["message"], "délai minimum entre deux sabotages")

    # Interception
    t2 = t + m.CONFIG["vitesses"][e["vitesse"]]["delai_sabotage"] + 1
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "interception", "cible": cible}, t2, rng=random.Random(2))
    n = len(m.vue_privee(e, cible, t2)["archive"])
    ev = m.action(e, cible, {"type": "enq_indice"}, t2 + 5)
    verifier(not any(x["msg"]["type"] == "enq_nouvel_indice" for x in ev) and len(m.vue_privee(e, cible, t2 + 5)["archive"]) == n,
             "interception : le prochain pli de la cible n'arrive pas…")
    retenu = q["archive"][-1]
    verifier(m.prochaine_echeance(e) <= retenu["visible_a"], "  le serveur se réveillera pour le libérer")
    ev = m.expirer(e, retenu["visible_a"])
    verifier(any(x["a"] == cible and x["msg"]["type"] == "enq_nouvel_indice" for x in ev)
             and retenu["id"] in [x["id"] for x in m.vue_privee(e, cible, retenu["visible_a"])["archive"]],
             "  …puis arrive " + str(m.CONFIG["sabotage"]["interception"] // 60) + " min plus tard")
    verifier(retenu["recu_a"] == retenu["visible_a"], "  avec l'heure de son arrivée (rien ne trahit le retard)")

    # Fausse piste en remplacement
    t3 = t2 + m.CONFIG["vitesses"][e["vitesse"]]["delai_sabotage"] + 1
    c2 = autres[1]
    q2 = m._perso(e, c2)
    risque = m.vue_privee(e, k, t3)["console"]["risques"][c2]["arme"]
    verifier(risque in ("faible", "moyen", "eleve"), "fausse piste : indicateur de risque affiché (" + str(risque) + ")")
    taille_pool = len(e["pool"])
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "fausse_piste", "cible": c2, "categorie": "arme", "mode": "remplacement"}, t3, rng=random.Random(3))
    q2["pe"] = 10
    m.action(e, c2, {"type": "enq_indice"}, t3 + 1)
    faux = q2["archive"][-1]
    verifier(not faux["vrai"] and faux["element"] == e["solution"]["arme"] and faux["categorie"] == "arme",
             "  le prochain indice de la cible est un faux : il élimine l'arme du crime")
    verifier(len(e["pool"]) == taille_pool, "  l'indice vrai qu'il remplace reste dans le stock")
    formes = {tuple(sorted(m._texte(x, True).keys())) for x in q2["archive"] + q["archive"]}
    verifier(len(formes) == 1 and set(faux.keys()) - {"vrai"} == set(q["archive"][0].keys()) - {"vrai", "retenu", "visible_a"},
             "  même format qu'un vrai (même message, mêmes champs)")
    m.action(e, c2, {"type": "enq_lire", "id": faux["id"]}, t3 + 2)
    ev = m.action(e, c2, {"type": "enq_expertise", "id": faux["id"], "paiement": "gorgee"}, t3 + 3)
    verifier(ev[0]["msg"]["tampon"] == "falsifie", "  la contre-expertise le démasque : « falsifié »")
    t4 = t3 + m.CONFIG["vitesses"][e["vitesse"]]["delai_sabotage"] + 1
    pk["charges"] = 3
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "fausse_piste", "cible": autres[2], "categorie": "arme", "mode": "remplacement"}, t4)
    verifier(ev[0]["msg"]["type"] == "enq_erreur" and m.vue_privee(e, k, t4)["console"]["risques"][autres[2]]["arme"] is None,
             "  deux faux identiques : interdit")

    # Fausse piste en détournement du prochain témoin
    c3 = autres[2]
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "fausse_piste", "cible": c3, "categorie": "lieu", "mode": "detournement"}, t4, rng=random.Random(4))
    total = sum(len(x["archive"]) for x in e["perso"].values())
    ev = m.expirer(e, max(t4, e["prochain_bonus_a"]))
    q3 = m._perso(e, c3)
    verifier(ev and ev[0]["a"] == c3 and ev[0]["msg"]["source"] == "Témoin anonyme" and not q3["archive"][-1]["vrai"]
             and q3["archive"][-1]["element"] == e["solution"]["lieu"],
             "détournement : le prochain témoin va à la cible, et il est faux")
    verifier(sum(len(x["archive"]) for x in e["perso"].values()) == total + 1, "  aucun indice de plus n'est créé")

    # Contradiction directe
    e2, t5 = en_cours(4, 12)
    k2, a2 = _tueur_et_autres(e2)
    qa = m._perso(e2, a2[0])
    for x in e2["lieux"]:
        if x != e2["solution"]["lieu"]:
            qa["archive"].append({"id": 900, "categorie": "lieu", "element": x, "vrai": True, "source": "Victoire",
                                  "recu_a": t5, "lu": True, "tampon": None, "texte": "-"})
    el, raison = m._fausse_piste(e2, a2[0], "lieu", m.CONFIG)
    verifier(el is None and "contredirait" in raison, "refus d'un faux qui contredirait l'archive de la cible")


def essai_detection_et_rumeurs():
    print("Détection et rumeurs")
    e, t = en_cours(6, 13)
    k, autres = _tueur_et_autres(e)
    d0, d1 = m.CONFIG["vitesses"][e["vitesse"]]["detection"]
    verifier(abs(m.detection_actuelle(e, t) - d0) < 1e-9 and abs(m.detection_actuelle(e, e["fin_a"]) - d1) < 1e-9
             and m.detection_actuelle(e, (t + e["fin_a"]) / 2) < d0, "la détection baisse avec le temps (de 50 % à 15 %)")
    # Un sabotage détecté : annonce différée, neutre.
    pk = m._perso(e, k)
    pk["charges"] = 1
    class Toujours:
        def random(self): return 0.0
        def uniform(self, a, b): return a
        def choice(self, x): return x[0]
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "interception", "cible": autres[0]}, t, rng=Toujours())
    texte = m.PACKS[e["univers"]]["textes"]["annonces"]["interference"]
    verifier(not any(x["texte"] == texte for x in e["journal"]), "détecté : rien d'annoncé sur le moment…")
    a = m.CONFIG["sabotage"]["delai_annonce"][0]
    verifier(m.prochaine_echeance(e) <= t + a, "  le serveur se réveille pour l'annonce")
    m.expirer(e, t + a)
    j = [x["texte"] for x in e["journal"]]
    verifier(texte in j and autres[0] not in j[-1] and k not in j[-1], "  …puis « interférence » annoncée, sans cible ni auteur ni type")
    # Non détecté : aucune annonce.
    e, t = en_cours(6, 14)
    k, autres = _tueur_et_autres(e)
    m._perso(e, k)["charges"] = 1
    class Jamais(Toujours):
        def random(self): return 0.99
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "interception", "cible": autres[0]}, t, rng=Jamais())
    m.expirer(e, t + 200)
    verifier(texte not in [x["texte"] for x in e["journal"]] and e["sabotages"][-1]["detecte"] is False, "non détecté : silence total")

    # Rumeurs ambiantes et fausse rumeur
    e, t = en_cours(6, 15)
    k, autres = _tueur_et_autres(e)
    r = random.Random(5)
    for _ in range(6):
        m.expirer(e, e["prochaine_rumeur_a"], rng=r)
    pub = m.vue_publique(e, e["prochaine_rumeur_a"])
    verifier(len(pub["rumeurs"]) == 6, "des rumeurs ambiantes circulent régulièrement (canal public)")
    pk = m._perso(e, k)
    pk["charges"] = 2
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "rumeur", "categorie": "lieu", "element": e["solution"]["lieu"]}, t)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "fausse rumeur : jamais sur la solution (elle vise un innocent)")
    innocent = next(x for x in e["lieux"] if x != e["solution"]["lieu"])
    m.action(e, k, {"type": "enq_sabotage", "sabotage": "rumeur", "categorie": "lieu", "element": innocent}, t, rng=random.Random(6))
    n = len(e["rumeurs"])
    b = m.CONFIG["sabotage"]["delai_publication"][1]
    ev = m.expirer(e, t + b)
    rum = [x for x in ev if x["msg"]["type"] == "enq_rumeur"]
    verifier(len(e["rumeurs"]) >= n + 1 and rum and rum[0]["a"] is None and rum[-1]["msg"]["lieu"] == innocent,
             "  publiée à tous après un délai, avec un repère sur le lieu")
    verifier(set(rum[-1]["msg"].keys()) == {"type", "texte", "lieu"}, "  même format qu'une rumeur ambiante")
    pk["prochain_sabotage_a"] = None
    ev = m.action(e, k, {"type": "enq_sabotage", "sabotage": "rumeur", "categorie": "suspect", "element": autres[0]}, t + 1)
    verifier(ev[0]["msg"]["type"] == "enq_erreur", "  une seule fausse rumeur par partie")
    # Récap de fin
    m._terminer(e, "tueur", "heure", None)
    rec = m.recapitulatif(e)
    verifier(rec["sabotages"] and rec["sabotages"][0]["type"] == "rumeur", "le récapitulatif de fin révèle les sabotages")


essai_salle_attente()
essai_tirage()
essai_temps()
essai_non_fuite()
essai_stock_indices()
essai_points_et_indices()
essai_accusations()
essai_temoin_anonyme()
essai_contre_expertise()
essai_sabotages()
essai_detection_et_rumeurs()
asyncio.run(essai_couche())
print("OK" if not ECHECS else f"{len(ECHECS)} ECHEC(S)")
sys.exit(1 if ECHECS else 0)
