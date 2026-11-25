"""
Serveur d essai pour les tests navigateur (tests/navigateur.js).

Le vrai serveur, sur 127.0.0.1:8000 (l adresse que visent les pages servies en
local), avec un delai de silence raccourci et quelques routes reservees aux
tests : lire l etat d un salon, couper la connexion d un joueur.
Ne pas deployer : ce fichier n est utilise que pour les essais.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import uvicorn

import serveur

serveur.DELAI_SILENCE = float(os.environ.get("DELAI_SILENCE", "12"))
RESYNC = {"n": 0}
_instantane = serveur.envoyer_instantane


async def _instantane_compte(code_salon, joueur, complet=True, nouveau=False):
    if not complet:
        RESYNC["n"] += 1
    return await _instantane(code_salon, joueur, complet, nouveau)


serveur.envoyer_instantane = _instantane_compte

if os.environ.get("JOURNAL"):
    import time as _t
    _depart, _arrivee = serveur.depart, serveur.arrivee

    async def _depart_j(code, joueur, ws):
        if joueur.get("ws") is ws and not joueur.get("parti"):
            print(f"{_t.strftime('%X')} DEPART {code} {joueur['nom']} silence={_t.time() - joueur.get('vu', 0):.1f}s", flush=True)
        return await _depart(code, joueur, ws)

    async def _arrivee_j(code, salon, ws, nom):
        print(f"{_t.strftime('%X')} ARRIVEE {code} {nom} {dict(ws.query_params)}", flush=True)
        return await _arrivee(code, salon, ws, nom)

    serveur.depart, serveur.arrivee = _depart_j, _arrivee_j


@serveur.app.get("/test/jouer/{code}/{nom}")
async def jouer_comme_bot(code: str, nom: str):
    """Fait jouer UNE action au joueur nom avec le cerveau de bot du jeu (mode test)."""
    import bots
    salon = serveur.salons.get(code)
    if salon is None or salon.get("etat") is None or salon.get("jeu") not in bots.CERVEAUX:
        return {"joue": None}
    joueur = next((j for j in salon["joueurs"] if serveur.meme_prenom(j["nom"], nom)), None)
    if joueur is None:
        return {"joue": None}
    async with serveur.verrou(salon):
        action = bots.CERVEAUX[salon["jeu"]](salon["etat"], joueur["index"], {"vus": []})
        if action:
            action = {k: v for k, v in action.items() if not k.startswith("_")}
            await serveur.traiter_message(code, joueur, action)
    return {"joue": action and action.get("type")}


@serveur.app.get("/test/etat/{code}")
async def etat(code: str):
    salon = serveur.salons.get(code)
    if salon is None:
        return {"existe": False}
    e = salon.get("etat") or {}
    return {
        "existe": True,
        "jeu": salon.get("jeu"),
        "en_partie": salon.get("etat") is not None,
        "joueurs": [{"nom": j["nom"], "index": j["index"], "parti": bool(j.get("parti")),
                     "battement": bool(j.get("battement")),
                     "bot": bool(j.get("bot")), "actions": j.get("actions", 0)} for j in salon["joueurs"]],
        "phase": e.get("phase"),
        "resync": RESYNC["n"],
        # Undercover : roles et mots, pour verifier ce que montrent les pages.
        "uc": {"roles": e.get("roles"), "mots": e.get("mots"), "vivants": e.get("vivants")}
              if salon.get("jeu") == "undercover" else None,
        # Gribouillophone : les albums, pour verifier ce que les pages ont envoye.
        "td": {"albums": e.get("albums")} if salon.get("jeu") == "telephone" else None,
        # Le Juste Pinte : la question en cours et sa reponse, pour jouer « dans le mille ».
        "jp": {"manche": e.get("manche"), "nb_manches": e.get("nb_manches"), "points": e.get("points"),
               "gorgees": e.get("gorgees"), "reponse": (e.get("tirage") or [[None] * 6])[min(e.get("manche", 0), len(e.get("tirage") or [0]) - 1)][2]
               if e.get("tirage") else None}
              if salon.get("jeu") == "justepinte" else None,
        # Qui de nous ? : la manche, les votes deja recus, les points et gorgees.
        "qn": {"manche": e.get("manche"), "nb_manches": e.get("nb_manches"), "votes": e.get("votes"),
               "points": e.get("points"), "gorgees": e.get("gorgees")}
              if salon.get("jeu") == "quidenous" else None,
        # La Tournee : le tour, le plateau, les scores du mini-jeu en cours.
        "tr": {"tour": e.get("tour"), "nb_tours": e.get("nb_tours"), "positions": e.get("positions"),
               "pieces": e.get("pieces"), "capsules": e.get("capsules"), "gorgees": e.get("gorgees"),
               "scores": e.get("scores"), "minijeu": e.get("minijeu"), "des": e.get("des"),
               "plateau": e.get("plateau"), "choix": e.get("choix"), "phase": e.get("phase"),
               "objets": e.get("objets"), "boutiques": e.get("boutiques"), "capsule_case": e.get("capsule_case")}
              if salon.get("jeu") == "tournee" else None,
        # Loup-Garou : les roles et la nuit en cours, pour jouer chaque role depuis les pages.
        "lg": {"roles": e.get("roles"), "vivants": e.get("vivants"), "nuit": e.get("nuit"), "jour": e.get("jour"),
               "meute": e.get("meute"), "victime": e.get("victime"), "potions": e.get("potions"),
               "tireur": e.get("tireur"), "gorgees": e.get("gorgees"), "convertis": e.get("convertis"),
               "amoureux": e.get("amoureux"), "capitaine": e.get("capitaine")}
              if salon.get("jeu") == "loupgarou" else None,
        # Pichenette FC : equipes, score, positions et visees, pour jouer depuis les pages.
        "pf": {"equipes": e.get("equipes"), "spectateurs": e.get("spectateurs"), "score": e.get("score"),
               "palets": e.get("palets"), "balle": e.get("balle"), "visees": e.get("visees"),
               "prets": e.get("prets"), "tour": e.get("tour"), "gorgees": e.get("gorgees"),
               "formations": e.get("formations"), "buts_victoire": e.get("buts_victoire")}
              if salon.get("jeu") == "pichenette" else None,
    }


@serveur.app.get("/test/pf_preparer/{code}")
async def pf_preparer(code: str):
    """Pichenette FC : bouchon devant la cage des Brunes, une capsule des Blondes juste derriere,
    les autres capsules sur les cotes, pour marquer a coup sur depuis une page."""
    salon = serveur.salons.get(code)
    e = (salon or {}).get("etat")
    if not e or salon.get("jeu") != "pichenette" or e.get("phase") != "visee":
        return {"ok": False}
    async with serveur.verrou(salon):
        e["palets"] = ([[-0.4, -0.6 + 0.15 * k] for k in range(4)] + [[0.0, 0.42]]
                       + [[0.4, 0.6 - 0.15 * k] for k in range(5)])
        e["balle"] = [0.0, 0.6]
        e["version"] += 1
        await serveur.pf_publier(code, [])
    return {"ok": True, "palet": 5}


@serveur.app.get("/test/tr_preparer/{code}")
async def tr_preparer(code: str):
    """La Tournee : des objets dans les poches et le deuxieme joueur juste avant une boutique,
    pour essayer les objets et la boutique depuis les pages."""
    import tournee
    salon = serveur.salons.get(code)
    e = (salon or {}).get("etat")
    if not e or salon.get("jeu") != "tournee" or e.get("phase") != "des":
        return {"ok": False}
    cases = tournee.TR_PLATEAUX[e["plateau"]]["cases"]
    boutique = [c["id"] for c in cases if c["type"] == "boutique"][0]
    avant = [c["id"] for c in cases if c["suivants"] == [boutique]][0]
    n = e["nb_joueurs"]
    e["objets"] = [["double", "bouclier"]] + [[] for _ in range(n - 1)]
    if n > 2:
        e["objets"][2] = ["pickpocket"]
    e["pieces"] = [40] * n
    e["positions"][1] = avant
    import time as _t
    e["echeance"] = _t.time() + tournee._D["des"]   # le temps d essayer les objets depuis des pages lentes
    e["version"] += 1
    await serveur.tr_publier(code, [])
    return {"ok": True, "boutique": boutique}


@serveur.app.get("/test/couper/{code}/{nom}")
async def couper(code: str, nom: str):
    """Ferme la connexion d un joueur, comme une coupure reseau."""
    salon = serveur.salons.get(code)
    for j in (salon or {}).get("joueurs", []):
        if j["nom"] == nom and j.get("ws") is not None:
            await serveur.fermer(j["ws"], 1001)
            return {"coupe": True}
    return {"coupe": False}


@serveur.app.get("/test/lg_role/{code}/{index}/{role}")
async def lg_role(code: str, index: int, role: str):
    """Reserve aux tests : donne une autre carte a un joueur du Loup-Garou (pour voir chaque panneau)."""
    salon = serveur.salons.get(code)
    etat = (salon or {}).get("etat")
    if not etat or salon.get("jeu") != "loupgarou" or role not in __import__("loupgarou").LG_ROLES:
        return {"ok": False}
    async with serveur.verrou(salon):
        etat["roles"][index] = role
        etat["convertis"][index] = False
        etat["vampires"][index] = role == "vampire"
        if role == "infect":
            etat["infection"] = True
        if role == "renard":
            etat["renard_actif"] = True          # le vrai Renard a pu perdre son flair
        if etat["phase"] == "nuit":
            __import__("loupgarou")._accord_meute(etat)   # la meute a pu perdre ou gagner un membre
        await serveur.lg_publier(code, [])
    return {"ok": True}


# La Tournee : une boutique longue, pour que les pages lentes (trois navigateurs 3D) l affichent.
import tournee  # noqa: E402
tournee._D["boutique"] = float(os.environ.get("TR_BOUTIQUE", "60"))
tournee._D["des"] = float(os.environ.get("TR_DES", "60"))


# Pichenette FC : sur une machine d essai lente (4 scenes 3D sans carte graphique), le chrono
# de visee passerait avant que les pages aient fini de viser.
import pichenette  # noqa: E402
pichenette._D["visee"] = float(os.environ.get("PF_VISEE", "300"))
pichenette._D["formation"] = float(os.environ.get("PF_FORMATION", "300"))


if os.environ.get("LG_NUIT"):
    # Loup-Garou : une nuit longue, pour passer chaque panneau de role en revue.
    import loupgarou  # noqa: E402
    loupgarou._D["nuit"] = float(os.environ["LG_NUIT"])


# --- Enquete : crediter des PE sans jouer, et chrono d archive raccourci ---
import enquete  # noqa: E402

if os.environ.get("ENQ_RAPIDE"):
    for v in enquete.CONFIG["vitesses"].values():
        v["timer_consultation"] = 6
        v["delai_sabotage"] = 3
    enquete.CONFIG["sabotage"]["delai_annonce"] = [1, 2]
    enquete.CONFIG["sabotage"]["delai_publication"] = [1, 2]


@serveur.app.get("/test/pe/{code}/{nom}/{n}")
async def crediter(code: str, nom: str, n: int):
    soiree = serveur.soiree_de(code)
    couche = serveur.couches.get(soiree, {}).get("enq")
    if couche is None:
        return {"ok": False}
    async with serveur.verrou_soiree(soiree):
        ev = enquete.crediter_pe(couche.etat, nom, n, "menteur", "victoire")
        await couche._publier(serveur._contexte(soiree, couche), ev)
    return {"ok": True}


@serveur.app.get("/test/temoin/{code}")
async def temoin(code: str):
    """Reserve aux tests : declenche tout de suite un Temoin anonyme. Renvoie le joueur servi."""
    soiree = serveur.soiree_de(code)
    couche = serveur.couches.get(soiree, {}).get("enq")
    if couche is None:
        return {"nom": None}
    async with serveur.verrou_soiree(soiree):
        import time as _t
        ev = enquete.temoin_anonyme(couche.etat, _t.time())
        await couche._publier(serveur._contexte(soiree, couche), ev)
    return {"nom": ev[0]["a"] if ev else None}


@serveur.app.get("/test/evenement/{code}/{type_}")
async def evenement(code: str, type_: str):
    """Reserve aux tests : demarre tout de suite un evenement d ambiance."""
    soiree = serveur.soiree_de(code)
    couche = serveur.couches.get(soiree, {}).get("enq")
    if couche is None:
        return {"ok": False}
    async with serveur.verrou_soiree(soiree):
        import time as _t
        couche.etat["evenement"] = None
        ev = enquete.demarrer_evenement(couche.etat, type_, _t.time())
        await couche._publier(serveur._contexte(soiree, couche), ev)
    serveur._apres_couche(soiree, couche)
    return {"ok": True, "evenement": couche.etat["evenement"]}


@serveur.app.get("/test/bots/{code}/{tours}")
async def bots(code: str, tours: int):
    """Reserve aux tests : fait jouer les joueurs fictifs (avec des PE en poche) plusieurs tours."""
    soiree = serveur.soiree_de(code)
    couche = serveur.couches.get(soiree, {}).get("enq")
    if couche is None:
        return {"ok": False}
    import time as _t
    import random as _r
    async with serveur.verrou_soiree(soiree):
        ev = []
        for _ in range(tours):
            for j in couche.etat["joueurs"]:
                if j.get("bot"):
                    enquete._perso(couche.etat, j["nom"])["pe"] += 3
                    ev += enquete.jouer_bot(couche.etat, j["nom"], _t.time(), _r.Random())
        await couche._publier(serveur._contexte(soiree, couche), ev)
    return {"ok": True, "plis": sum(len(enquete._perso(couche.etat, j["nom"])["archive"]) for j in couche.etat["joueurs"] if j.get("bot"))}


@serveur.app.get("/test/solution/{code}")
async def solution(code: str):
    """Reserve aux tests : la solution secrete de l enquete."""
    couche = serveur.couches.get(serveur.soiree_de(code), {}).get("enq")
    return couche.etat["solution"] if couche else {}


if __name__ == "__main__":
    uvicorn.run(serveur.app, host="127.0.0.1", port=8000, log_level="warning")
