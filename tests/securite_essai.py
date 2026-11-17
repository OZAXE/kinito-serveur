"""
Essais de securite du serveur : ce qu un client malveillant ou deregle peut
tenter, et que le serveur doit refuser lui-meme (l interface ne protege rien).

Le vrai serveur tourne en local (uvicorn), des clients websockets l attaquent.

Usage : python tests/securite_essai.py
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import uvicorn
import websockets

import serveur

PORT = int(os.environ.get("SECU_PORT", "18767"))
URL = f"ws://127.0.0.1:{PORT}"
echecs = []


def verifier(condition, texte):
    print(("  ok  " if condition else "  ECHEC  ") + texte)
    if not condition:
        echecs.append(texte)


async def connecter(chemin, **options):
    # max_queue=None : les messages non lus ne bloquent pas la fermeture.
    options.setdefault("max_queue", None)
    return await websockets.connect(URL + chemin, ping_interval=None, **options)


def http(chemin, ip=None, entetes=None, avec_entetes=False):
    """GET sur le serveur ; rend (statut, JSON). ip : simule X-Forwarded-For."""
    entetes = dict(entetes or {})
    if ip:
        entetes["X-Forwarded-For"] = ip
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{chemin}", headers=entetes)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if avec_entetes:
                return r.status, json.loads(r.read()), r.headers
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


async def get(chemin, ip=None):
    return await asyncio.to_thread(http, chemin, ip)


async def code_fermeture(ws, delai=2.0):
    """Lit jusqu a la fermeture et rend son code (None si toujours ouverte)."""
    try:
        while True:
            await asyncio.wait_for(ws.recv(), delai)
    except websockets.ConnectionClosed as e:
        return e.rcvd.code if e.rcvd else None
    except asyncio.TimeoutError:
        return None


async def ping(ws, delai=1.0):
    """La connexion repond-elle encore ? (vide d abord les messages en attente)"""
    await ws.send('{"type":"ping"}')
    try:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), delai))
            if m.get("type") == "pong":
                return True
    except Exception:
        return False


async def essai_taille():
    print("Taille et forme des messages")
    code = serveur.generer_code()
    ws = await connecter(f"/ws/{code}/Alice")
    await ws.send("[" * 5000 + "]" * 5000)          # JSON trop imbrique : ignore
    await ws.send("pas du json")
    await ws.send("[1, 2]")
    verifier(await ping(ws), "JSON trop imbrique, invalide ou pas un objet : ignore, la connexion vit")
    await ws.send(json.dumps({"type": "x", "bourrage": "a" * (serveur.TAILLE_MAX_MESSAGE + 10)}))
    verifier(await code_fermeture(ws) == 1009, "message trop gros : connexion fermee (1009)")
    soir = await connecter(f"/ws-soiree/{code}/Alice")
    await soir.send("a" * (serveur.TAILLE_MAX_MESSAGE + 10))
    verifier(await code_fermeture(soir) == 1009, "  idem sur la connexion soiree")


async def essai_debit():
    print("Debit des messages")
    code = serveur.generer_code()
    ws = await connecter(f"/ws/{code}/Alice")
    await asyncio.sleep(0.2)
    for _ in range(serveur.RAFALE_MESSAGES + 30):
        await ws.send('{"type":"resync"}')
    await asyncio.sleep(1.5)
    verifier(await ping(ws), "rafale au-dela de la limite : messages ignores, la connexion vit")
    for _ in range(serveur.REFUS_AVANT_COUPURE + serveur.RAFALE_MESSAGES + 50):
        await ws.send('{"type":"resync"}')
    verifier(await code_fermeture(ws) == 1008, "inondation continue : connexion fermee (1008)")


async def essai_codes():
    print("Codes de salle et prenoms a l entree")
    for mauvais in ("abcd" * 50, "AB", "ABCD-1", "ABCD-10", "AB%20D", "ABC1"):
        refuse = False
        try:
            ws = await connecter(f"/ws/{mauvais}/Alice")
            await ws.close()
        except websockets.InvalidStatus:
            refuse = True
        verifier(refuse and not serveur.salons, f"code {mauvais[:12]!r} refuse, aucun salon cree")
    refuse = False
    try:
        await connecter("/ws-soiree/" + "x" * 300 + "/Alice")
    except websockets.InvalidStatus:
        refuse = True
    verifier(refuse, "connexion soiree a un code de travers : refusee")
    code = serveur.generer_code()
    ws = await connecter(f"/ws/{code.lower()}-3/Al%07i%0Ace")
    await asyncio.sleep(0.3)
    salon = serveur.salons.get(code + "-3")
    verifier(salon is not None and salon["joueurs"][0]["nom"] == "Ali ce",
             "code en minuscules ramene a la table en majuscules, prenom nettoye")
    await ws.close()


async def essai_places():
    print("Places a une table")
    code = serveur.generer_code()
    connexions = [await connecter(f"/ws/{code}/Joueur{i}") for i in range(serveur.PLACES_MAX)]
    await asyncio.sleep(0.3)
    de_trop = await connecter(f"/ws/{code}/Intrus")
    recu, ferme = [], None
    try:
        while True:
            recu.append(json.loads(await asyncio.wait_for(de_trop.recv(), 2)))
    except websockets.ConnectionClosed as e:
        ferme = e.rcvd.code if e.rcvd else None
    verifier(ferme == 4005 and recu and recu[-1]["type"] == "erreur",
             f"{serveur.PLACES_MAX + 1}e place refusee (message d erreur puis 4005)")
    verifier(len(serveur.salons[code]["joueurs"]) == serveur.PLACES_MAX, "  la table reste a la limite")
    revenant = await connecter(f"/ws/{code}/Joueur3")
    verifier(await ping(revenant), "  un joueur deja assis reprend toujours sa place")
    for ws in connexions + [revenant]:
        await ws.close()


async def refusee(chemin, ip):
    try:
        ws = await connecter(chemin, additional_headers={"X-Forwarded-For": ip})
    except websockets.InvalidStatus:
        return True, None
    return False, ws


async def essai_limites_ip():
    print("Limites par adresse IP")
    serveur.connexions_ip.limite, avant = 3, serveur.connexions_ip.limite
    try:
        code = (await get("/creer-salon"))[1]["code"]
        ouvertes = []
        for i in range(3):
            r, ws = await refusee(f"/ws/{code}/J{i}", "10.0.0.1")
            ouvertes.append(ws)
        r4, _ = await refusee(f"/ws-soiree/{code}/J9", "10.0.0.1")
        r5, autre = await refusee(f"/ws/{code}/Autre", "10.0.0.2")
        verifier(all(ouvertes) and r4 and not r5,
                 "connexions simultanees limitees par adresse (les autres adresses passent)")
        await ouvertes[0].close()
        await asyncio.sleep(0.3)
        r6, ws = await refusee(f"/ws/{code}/J0", "10.0.0.1")
        verifier(not r6, "  une place se libere a la fermeture d une connexion")
        for w in ouvertes[1:] + [ws, autre]:
            await w.close()
    finally:
        serveur.connexions_ip.limite = avant

    code = (await get("/creer-salon"))[1]["code"]
    statuts = []
    for i in range(serveur.RATES_PAR_IP):
        statuts.append((await get(f"/salon/QQ{chr(65 + i // 26)}{chr(65 + i % 26)}", "10.0.0.3"))[1]["existe"])
    bloque = (await get(f"/salon/{code}", "10.0.0.3"))[1]
    bloque_soiree = (await get(f"/soiree/{code}", "10.0.0.3"))[1]
    r_ws, _ = await refusee(f"/ws/{code}/Devin", "10.0.0.3")
    libre = (await get(f"/salon/{code}", "10.0.0.4"))[1]
    verifier(not any(statuts) and not bloque["existe"] and not bloque_soiree["existe"] and r_ws,
             f"{serveur.RATES_PAR_IP} codes inexistants essayes : l adresse ne voit plus aucune soiree")
    verifier(libre["existe"], "  une autre adresse voit toujours la soiree")

    codes = [await get("/creer-salon", "10.0.0.5") for _ in range(serveur.CREATIONS_PAR_IP + 1)]
    verifier(all(c[0] == 200 for c in codes[:-1]) and codes[-1][0] == 429,
             f"creation de soirees limitee a {serveur.CREATIONS_PAR_IP} par minute et par adresse")
    serveur.requetes_ip.limite, avant = 5, serveur.requetes_ip.limite
    try:
        rep = [(await get("/", "10.0.0.6"))[0] for _ in range(6)]
    finally:
        serveur.requetes_ip.limite = avant
    verifier(rep[:5] == [200] * 5 and rep[5] == 429, "requetes HTTP limitees par adresse (429)")


async def essai_origines():
    print("Origines autorisees")
    code = serveur.generer_code()
    for origine, attendu in (("https://site-malveillant.example", False), ("https://ozaxe.github.io", True),
                             ("http://localhost:8765", True), ("http://127.0.0.1:8000", True),
                             ("https://ozaxe.github.io.evil.example", False), ("http://localhost.evil.example", False)):
        try:
            ws = await connecter(f"/ws/{code}/Alice", origin=origine)
            await ws.close()
            accepte = True
        except websockets.InvalidStatus:
            accepte = False
        verifier(accepte == attendu, f"WebSocket depuis {origine} : {'acceptee' if attendu else 'refusee'}")
    _, _, h_bon = await asyncio.to_thread(http, "/", None, {"Origin": "https://ozaxe.github.io"}, True)
    _, _, h_mal = await asyncio.to_thread(http, "/", None, {"Origin": "https://site-malveillant.example"}, True)
    verifier(h_bon.get("access-control-allow-origin") == "https://ozaxe.github.io"
             and h_mal.get("access-control-allow-origin") is None,
             "CORS : seul le site est autorise a lire les reponses HTTP")


async def essai_nettoyage():
    print("Salons abandonnes et plafond global")
    abandonne = (await get("/creer-salon"))[1]["code"]
    accueil = (await get("/creer-salon"))[1]["code"]
    await get(f"/soiree/{accueil}?nom=Lea")             # quelqu un attend sur l accueil
    occupe = (await get("/creer-salon"))[1]["code"]
    ws = await connecter(f"/ws/{occupe}/Alice")
    await asyncio.sleep(0.3)
    serveur._verrous_soirees["ZZZZ"] = asyncio.Lock()   # soiree disparue
    plus_tard = serveur.time.time() + serveur.DELAI_SALON_ABANDONNE + 1
    for code in (abandonne, accueil, occupe):
        serveur.salons[code]["cree"] -= serveur.DELAI_SALON_ABANDONNE + 1
    serveur.nettoyer()
    verifier(abandonne not in serveur.salons, "salon cree mais jamais rejoint : libere par le nettoyage")
    verifier(accueil in serveur.salons and occupe in serveur.salons,
             "  garde si quelqu un est sur l accueil de la soiree ou assis a la table")
    verifier("ZZZZ" not in serveur._verrous_soirees, "  verrou d une soiree disparue oublie")
    serveur.nettoyer(plus_tard + 4 * serveur.PRESENCE_ACCUEIL)
    verifier(accueil not in serveur.salons and accueil not in serveur.presences_accueil,
             "  puis libere quand l accueil ne donne plus de nouvelles")

    serveur.SALONS_MAX, avant = len(serveur.salons), serveur.SALONS_MAX
    try:
        statut = (await get("/creer-salon"))[0]
        try:
            await connecter(f"/ws/{serveur.generer_code()}/Bob")
            nouveau_refuse = False
        except websockets.InvalidStatus:
            nouveau_refuse = True
        existant = await connecter(f"/ws/{occupe}/Bob")
        verifier(statut == 503 and nouveau_refuse and await ping(existant),
                 "plafond global de salons : plus de creation, les tables existantes continuent")
        await existant.close()
    finally:
        serveur.SALONS_MAX = avant
    await ws.close()


ESSAIS = [essai_taille, essai_debit, essai_codes, essai_places, essai_limites_ip, essai_origines,
          essai_nettoyage]


async def principal():
    config = uvicorn.Config(serveur.app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off")
    srv = uvicorn.Server(config)
    tache = asyncio.ensure_future(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    try:
        for essai in ESSAIS:
            serveur.salons.clear()
            serveur.presences_accueil.clear()
            await essai()
    finally:
        srv.should_exit = True
        await tache


if __name__ == "__main__":
    asyncio.run(principal())
    print("OK" if not echecs else f"ECHEC ({len(echecs)})")
    sys.exit(1 if echecs else 0)
