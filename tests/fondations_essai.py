"""
Essais des fondations du serveur (sans reseau, sans navigateur) :
- regle unique de lancement et de changement de jeu, dans un meme salon ;
- retour en salle d attente ;
- couches paralleles : creation, routage des messages, signaux des jeux,
  minuteur, survie a la liberation du salon ;
- sauvegarde et rechargement apres redemarrage (memoire, et Postgres si
  TEST_DATABASE_URL est defini).

Usage : python tests/fondations_essai.py
        TEST_DATABASE_URL=postgresql://... python tests/fondations_essai.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import serveur
from couches import Couche, enregistrer
from stockage import StockageMemoire, StockagePostgres, Sauvegarde

serveur.DELAI_SALON_VIDE = 0.2
Sauvegarde.INTERVALLE = 0.05


class FauxWS:
    def __init__(self, jeton=None):
        self.query_params = {"jeton": jeton} if jeton else {}
        self.recus = []

    async def send_text(self, t):
        import json
        self.recus.append(json.loads(t))

    async def close(self, code=1000):
        pass

    def types(self):
        return [m.get("type") for m in self.recus]


@enregistrer
class CoucheEssai(Couche):
    prefixe = "tst"

    def __init__(self, code):
        super().__init__(code)
        self.evenements = []
        self.actions = []
        self.reveils = 0
        self.echeance_a = None
        self.fini = False

    @classmethod
    async def creer(cls, code, nom, message):
        return cls(code) if message["type"] == "tst_lancer" else None

    def terminee(self):
        return self.fini

    def exporter(self):
        return {"evenements": self.evenements, "actions": self.actions}

    @classmethod
    def importer(cls, code, d):
        c = cls(code)
        c.evenements, c.actions = d["evenements"], d["actions"]
        return c

    def prochaine_echeance(self):
        return self.echeance_a

    async def echeance(self, ctx):
        self.reveils += 1
        self.echeance_a = None
        await ctx.diffuser({"type": "tst_reveil"})

    async def action(self, ctx, nom, message):
        self.actions.append([nom, message["type"]])
        if message["type"] == "tst_minuteur":
            self.echeance_a = ctx.maintenant() + 0.1
        if message["type"] == "tst_finir":
            self.fini = True
        await ctx.envoyer_nom(nom, {"type": "tst_ok"})

    async def arrivee(self, ctx, nom):
        await ctx.envoyer_nom(nom, {"type": "tst_etat", "n": len(self.actions)})

    async def evenement_jeu(self, ctx, ev):
        self.evenements.append([ev["jeu"], ev["type"]])


ECHECS = []


def verifier(cond, quoi):
    print(("  ok  " if cond else "  ECHEC ") + quoi)
    if not cond:
        ECHECS.append(quoi)


async def entrer(code, nom):
    if code not in serveur.salons:
        serveur.salons[code] = serveur.nouveau_salon()
    salon = serveur.salons[code]
    ws = FauxWS()
    async with serveur.verrou(salon):
        j = await serveur.arrivee(code, salon, ws, nom)
    return j, ws


async def envoyer(code, joueur, message):
    salon = serveur.salons[code]
    async with serveur.verrou(salon):
        await serveur.traiter_message(code, joueur, message)


async def partir(code, joueur):
    salon = serveur.salons[code]
    async with serveur.verrou(salon):
        await serveur.depart(code, joueur, joueur["ws"])


async def essai_changement_de_jeu():
    print("Changement de jeu dans un meme salon")
    await serveur.demarrage(StockageMemoire())
    code = "ABCD"
    joueurs = [await entrer(code, n) for n in ("Lea", "Max", "Zoe")]
    a, wa = joueurs[0]
    salon = serveur.salons[code]

    await envoyer(code, a, {"type": "demarrer"})
    verifier(salon["jeu"] == "kinito", "Kinito demarre depuis la salle d attente")
    etat = salon["etat"]
    await envoyer(code, a, {"type": "demarrer"})
    verifier(salon["etat"] is etat, "un second « demarrer » ne remet pas Kinito a zero")

    # Sans confirmation (« remplacer »), une partie en cours n est pas remplacee :
    # seul le demandeur recoit le refus, et la partie continue.
    _, wb = joueurs[1]
    wa.recus.clear()
    wb.recus.clear()
    await envoyer(code, a, {"type": "pp_demarrer"})
    verifier(salon["jeu"] == "kinito" and salon["etat"] is etat, "Picopoly sans confirmation ne remplace pas Kinito")
    refus = [m for m in wa.recus if m.get("type") == "erreur"]
    verifier(refus and refus[0].get("partie_en_cours") == "kinito" and "Kinito" in refus[0]["message"],
             "  le demandeur est prevenu qu une partie est en cours")
    verifier("erreur" not in wb.types(), "  les autres ne sont pas deranges")
    await envoyer(code, a, {"type": "pp_demarrer", "remplacer": "oui"})
    verifier(salon["jeu"] == "kinito", "  « remplacer » doit valoir true, pas n importe quoi")
    info = await serveur.info_salon(code)
    verifier(info["jeu"] == "kinito" and isinstance(info["depuis"], int) and info["fini"] is False,
             "/salon donne depuis quand le jeu tourne")

    ordre = ["casino", "kinito", "buckshot", "menteur", "undercover", "telephone", "justepinte", "quidenous", "tournee", "loupgarou", "pichenette", "beerbattle", "picolopoly", "casino"]
    for nom in ordre:
        if nom == "loupgarou":
            ugo = await entrer(code, "Ugo")      # le Loup-Garou se joue a 4 au moins
        wa.recus.clear()
        await envoyer(code, a, {"type": serveur.JEUX[nom].demarrage, "remplacer": True})
        verifier(salon["jeu"] == nom and salon["etat"] is not None, f"on passe a {nom}")
        verifier({"type": "salon_jeu", "jeu": nom, "titre": serveur.JEUX[nom].titre,
                  "page": serveur.JEUX[nom].page} in wa.recus, f"  annonce salon_jeu pour {nom}")
        verifier(not [k for j in serveur.JEUX.values() for k in j.minuteurs
                      if k != f"{serveur.JEUX[nom].minuteurs[0] if serveur.JEUX[nom].minuteurs else ''}"
                      and salon.get(k) is not None and not salon[k].done()],
                 "  aucun minuteur de l ancien jeu ne tourne encore")

    await partir(code, ugo[0])             # Ugo repart : la suite compte sans lui

    # Une action d un autre jeu est ignoree.
    etat = salon["etat"]
    await envoyer(code, a, {"type": "lancer"})
    verifier(salon["etat"] is etat and salon["jeu"] == "casino", "une action Kinito pendant le Casino est ignoree")

    # Trop de monde pour BeerBattle (8 au plus) : la partie en cours n est pas detruite.
    for n in ("Ana", "Bob", "Cyd", "Dan", "Eve", "Fox"):
        await entrer(code, n)
    wa.recus.clear()
    await envoyer(code, a, {"type": "bb_demarrer", "remplacer": True})
    verifier(salon["jeu"] == "casino" and salon["etat"] is etat, "BeerBattle a 9 refuse sans casser le Casino")
    verifier("erreur" in wa.types(), "  avec un message d erreur")

    # Un absent ne recoit pas de place dans le jeu suivant.
    _, wz = joueurs[2]
    await partir(code, joueurs[2][0])
    await envoyer(code, a, {"type": "mt_demarrer", "remplacer": True})
    verifier(salon["jeu"] == "menteur" and salon["etat"]["nb_joueurs"] == 8,
             "Menteur repart des 8 presents (l absente est retiree)")
    verifier([j["index"] for j in salon["joueurs"]] == list(range(8)), "  index renumerotes")

    wa.recus.clear()
    await envoyer(code, a, {"type": "retour_salon"})
    verifier(salon["etat"] is None and salon["jeu"] is None, "retour en salle d attente")
    verifier("salon_retour" in wa.types(), "  annonce salon_retour")
    serveur.salons.clear()


async def essai_couches():
    print("Couches paralleles")
    memoire = StockageMemoire()
    await serveur.demarrage(memoire)
    code = "EFGH"
    (a, _), (b, _), (c, _) = [await entrer(code, n) for n in ("Lea", "Max", "Zoe")]
    # Les couches parlent par la connexion « soiree » de chacun.
    wa, wb, wc = FauxWS(), FauxWS(), FauxWS()
    for nom, w in (("Lea", wa), ("Max", wb), ("Zoe", wc)):
        await serveur.abonner(code, nom, w)

    await envoyer(code, a, {"type": "tst_action"})
    verifier(code not in serveur.couches, "un message ordinaire ne cree pas la couche")
    await envoyer(code, a, {"type": "tst_lancer"})
    couche = serveur.couches[code]["tst"]
    verifier(couche.actions == [["Lea", "tst_lancer"]], "le message de lancement cree la couche")
    verifier("tst_ok" in wa.types() and "tst_ok" not in wb.types(), "  la couche repond au seul joueur vise")

    await envoyer(code, a, {"type": "demarrer"})
    await envoyer(code, b, {"type": "tst_action"})
    verifier(couche.actions[-1] == ["Max", "tst_action"], "la couche recoit ses messages pendant un jeu")
    await envoyer(code, a, {"type": "cs_demarrer", "remplacer": True})
    verifier(["kinito", "fin_jeu"] in couche.evenements, "le remplacement du jeu est signale (fin_jeu)")
    await serveur.signaler(code, "casino", "victoire", gagnants=["Max"])
    verifier(["casino", "victoire"] in couche.evenements, "signaler relaie les victoires")

    await envoyer(code, a, {"type": "tst_minuteur"})
    await asyncio.sleep(0.3)
    verifier(couche.reveils == 1 and "tst_reveil" in wc.types(), "le minuteur de la couche reveille la couche")

    # Tout le monde part : le salon est libere, la couche reste.
    for j in (a, b, c):
        await partir(code, j)
    await asyncio.sleep(0.4)
    verifier(code not in serveur.salons and code in serveur.couches, "salon libere, couche conservee")
    verifier(serveur.generer_code() != code, "le code n est pas redonne")
    info = await serveur.info_salon(code)
    verifier(info["existe"] and info["couches"] == ["tst"], "/salon/<code> le donne toujours comme existant")

    (a2, _) = await entrer(code, "Lea")
    wa2 = FauxWS()
    await serveur.abonner(code, "Lea", wa2)
    verifier({"type": "tst_etat", "n": len(couche.actions)} in wa2.recus, "au retour, la couche renvoie son etat")
    verifier({"type": "remplace"} in wa.recus, "  l ancienne connexion soiree est prevenue")

    # Sauvegarde puis redemarrage du serveur.
    await serveur.sauvegarde.vider()
    sauvee = await memoire.lire(f"couche:{code}:tst")
    verifier(sauvee and sauvee["donnees"]["actions"] == couche.actions, "la couche est sauvegardee")
    serveur.couches.clear()
    serveur.salons.clear()
    await serveur.demarrage(memoire)
    rechargee = serveur.couches.get(code, {}).get("tst")
    verifier(rechargee is not None and rechargee.actions == couche.actions, "rechargee apres redemarrage")

    (a3, _) = await entrer(code, "Lea")
    await envoyer(code, a3, {"type": "tst_finir"})
    await serveur.sauvegarde.vider()
    verifier(code not in serveur.couches, "une couche terminee est oubliee")
    verifier(await memoire.lire(f"couche:{code}:tst") is None, "  et effacee de la base")
    serveur.salons.clear()


async def essai_tables():
    print("Plusieurs tables dans une soiree")
    await serveur.demarrage(StockageMemoire())
    code = (await serveur.creer_salon())["code"]
    (lea, wl), (maxi, wm) = [await entrer(code, n) for n in ("Lea", "Max")]
    t2 = (await serveur.nouvelle_table(code))["code"]
    verifier(t2 == code + "-2", "nouvelle table : " + str(t2))
    (zoe, wz) = await entrer(t2, "Zoe")
    info = await serveur.info_soiree(t2)
    verifier([t["code"] for t in info["tables"]] == [code, t2], "la soiree liste ses deux tables")
    verifier(info["tables"][0]["joueurs"] == ["Lea", "Max"] and info["tables"][1]["joueurs"] == ["Zoe"],
             "  avec leurs joueurs")

    # Chaque table a son propre jeu.
    await envoyer(code, lea, {"type": "demarrer"})
    await envoyer(t2, zoe, {"type": "cs_demarrer"})
    verifier(serveur.salons[code]["jeu"] == "kinito" and serveur.salons[t2]["jeu"] == "casino",
             "Kinito a la table 1, Casino a la table 2")
    verifier(not any(m.get("type") == "salon_jeu" and m["jeu"] == "casino" for m in wl.recus),
             "  le changement de la table 2 n est pas annonce a la table 1")

    # Un prenom n est qu a une table : Max passe a la table 2.
    wm.recus.clear()
    (max2, _) = await entrer(t2, "Max")
    await asyncio.sleep(0.1)
    j1 = [j for j in serveur.salons[code]["joueurs"] if j["nom"] == "Max"]
    verifier(all(j.get("parti") for j in j1), "Max qui s assoit a la table 2 quitte la table 1")
    verifier({"type": "remplace"} in wm.recus, "  son ancienne connexion est prevenue")
    info = await serveur.info_soiree(code)
    verifier(info["tables"][0]["joueurs"] == ["Lea"] and sorted(info["tables"][1]["joueurs"]) == ["Max", "Zoe"],
             "  la soiree le voit a la table 2")

    # Annonce des defaites a la table (animation sur les telephones)
    wz.recus.clear(); wl.recus.clear()
    await serveur.signaler(t2, "beerbattle", "victoire", gagnants=["Zoe"])
    r = [m for m in wz.recus if m.get("type") == "table_resultat"]
    verifier(r and r[-1]["perdants"] == ["Max"] and r[-1]["gagnants"] == ["Zoe"],
             "fin de partie : la table apprend que Max a perdu (Zoe gagne)")
    verifier(not any(m.get("type") == "table_resultat" for m in wl.recus), "  l autre table n en sait rien")
    await serveur.signaler(code, "kinito", "manche", perdants=["Lea"], details={"gorgees": 3})
    r = [m for m in wl.recus if m.get("type") == "table_resultat"]
    verifier(r and r[-1]["perdants"] == ["Lea"] and r[-1]["gorgees"] == {"Lea": 3}, "manche de Kinito perdue : Lea, 3 gorgees")

    # Les couches couvrent toute la soiree.
    await envoyer(t2, zoe, {"type": "tst_lancer"})
    couche = serveur.couches.get(code, {}).get("tst")
    verifier(couche is not None and t2 not in serveur.couches, "une couche lancee depuis la table 2 appartient a la soiree")
    await envoyer(code, lea, {"type": "tst_action"})
    verifier(couche.actions[-1] == ["Lea", "tst_action"], "  elle recoit les messages de la table 1")
    await serveur.signaler(t2, "casino", "victoire", gagnants=["Zoe"])
    verifier(["casino", "victoire"] in couche.evenements, "  et les victoires de la table 2")
    ctx = serveur._contexte(code, couche)
    verifier(sorted(ctx.noms_presents()) == ["Lea", "Max", "Zoe"], "  elle voit les presents de toutes les tables")
    sz = FauxWS()
    await serveur.abonner(code, "Zoe", sz)
    await ctx.envoyer_nom("Zoe", {"type": "tst_prive"})
    verifier({"type": "tst_prive"} in sz.recus, "  et joint un joueur par sa connexion soiree")

    # Qui est dans la soiree : tables, Enquete (connexion soiree) et accueil
    info = await serveur.info_soiree(code, nom="Ana")
    p = {x["nom"]: x["ou"] for x in info["presents"]}
    verifier(p.get("Ana") == "Accueil" and p.get("Lea", "").startswith("Table 1") and p.get("Zoe", "").startswith("Table 2"),
             "presents : Ana sur l accueil, Lea a la table 1, Zoe a la table 2 (" + str(p) + ")")
    verifier("Kinito" in p.get("Lea", ""), "  avec le jeu de la table")
    tab = {x["nom"]: x["table"] for x in (await serveur.info_soiree(code, nom="Ana", table=t2))["presents"]}
    verifier(tab.get("Lea") == code and tab.get("Zoe") == t2 and tab.get("Ana") == t2,
             "  et la table de chacun, pour la rejoindre d un toucher (Ana, sur l accueil, est a la table 2)")
    noe = FauxWS()
    await serveur.abonner(code, "Noe", noe)
    verifier({x["nom"]: x["ou"] for x in (await serveur.info_soiree(code))["presents"]}.get("Noe") == "Enquête",
             "  Noe, qui n a que l Enquete ouverte, compte aussi")
    serveur.presences_accueil[code]["ana"]["vu"] -= serveur.PRESENCE_ACCUEIL + 1
    verifier("Ana" not in [x["nom"] for x in (await serveur.info_soiree(code))["presents"]],
             "  Ana, sans nouvelles depuis 30 s, n est plus comptee")

    # La table 1 liberee, le code reste pris tant que la table 2 vit.
    del serveur.salons[code]
    verifier(serveur.soiree_existe(code) and (await serveur.info_salon(code))["existe"],
             "table 1 liberee : la soiree existe toujours")
    serveur.salons.clear()
    serveur.couches.clear()
    # Redemarrage du serveur : la soiree inconnue est rouverte sous le meme code.
    verifier(not (await serveur.info_soiree(code))["existe"], "apres redemarrage, la soiree est inconnue")
    r = await serveur.rouvrir_soiree(code)
    verifier(r["rouverte"] and (await serveur.info_soiree(code))["existe"], "  /rouvrir la rouvre sous le meme code")
    verifier(not (await serveur.rouvrir_soiree("ab1"))["existe"], "  un code invalide n est pas ouvert")
    serveur.salons.clear()


async def essai_postgres(url):
    print("Stockage Postgres")
    s = StockagePostgres(url)
    await s.ouvrir()
    await s.supprimer("essai:a")
    await s.supprimer("essai:b_x")
    await s.ecrire("essai:a", {"n": 1, "liste": [1, 2], "texte": "é"})
    verifier(await s.lire("essai:a") == {"n": 1, "liste": [1, 2], "texte": "é"}, "ecriture et relecture JSON")
    await s.ecrire("essai:a", {"n": 2})
    verifier(await s.lire("essai:a") == {"n": 2}, "mise a jour d une cle")
    await s.ecrire("essai:b_x", {"n": 3})
    verifier([c for c, _ in await s.lister("essai:")] == ["essai:a", "essai:b_x"], "liste par prefixe")
    verifier([c for c, _ in await s.lister("essai:b_")] == ["essai:b_x"], "  le _ du prefixe n est pas un joker")
    await s._cnx.close()        # Neon coupe les connexions inactives
    verifier(await s.lire("essai:a") == {"n": 2}, "reconnexion automatique apres coupure")
    await s.supprimer("essai:a")
    await s.supprimer("essai:b_x")
    verifier(await s.lire("essai:a") is None, "suppression")
    await s.fermer()


async def essai_changer_de_jeu_apres_abandon():
    print("Changer de jeu quand tout le monde a quitte la partie")
    await serveur.demarrage(StockageMemoire())
    code = (await serveur.creer_salon())["code"]
    (lea, wl), (maxi, wm) = [await entrer(code, n) for n in ("Lea", "Max")]
    await envoyer(code, lea, {"type": "demarrer"})
    salon = serveur.salons[code]
    verifier(salon["jeu"] == "kinito", "Lea et Max jouent a Kinito")
    # Max reste, Lea ouvre le Menteur : la partie de Max n est pas cassee
    ws = FauxWS(); ws.query_params = {"page": "menteur"}
    async with serveur.verrou(salon):
        await serveur.depart(code, lea, wl)
        await serveur.arrivee(code, salon, ws, "Lea")
    verifier(salon["jeu"] == "kinito", "  Max joue encore : la partie de Kinito continue")
    # Tout le monde quitte, puis Lea ouvre le Menteur
    lea2 = next(j for j in salon["joueurs"] if j["nom"] == "Lea")
    async with serveur.verrou(salon):
        await serveur.depart(code, lea2, ws)
        await serveur.depart(code, maxi, wm)
    verifier(all(j.get("parti") for j in salon["joueurs"]), "tous deux quittent la partie")
    ws2 = FauxWS(); ws2.query_params = {"page": "menteur"}
    async with serveur.verrou(salon):
        await serveur.arrivee(code, salon, ws2, "Lea")
    verifier(salon["jeu"] is None and salon["etat"] is None, "  Lea ouvre le Menteur : la partie abandonnee est close, salle d attente")
    verifier([j["nom"] for j in salon["joueurs"]] == ["Lea"], "  elle y est seule, sans fantome")
    info = await serveur.info_soiree(code)
    verifier(info["tables"][0]["attente"] == "menteur", "  l accueil sait que la table attend au Menteur")
    serveur.salons.clear()


async def principal():
    await essai_changement_de_jeu()
    await essai_couches()
    await essai_tables()
    await essai_changer_de_jeu_apres_abandon()
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        await essai_postgres(url)
    else:
        print("(Postgres non teste : TEST_DATABASE_URL absent)")
    await serveur.arret()


asyncio.run(principal())
print("OK" if not ECHECS else f"{len(ECHECS)} ECHEC(S)")
sys.exit(1 if ECHECS else 0)
