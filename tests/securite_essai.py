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
    return await websockets.connect(URL + chemin, ping_interval=None, **options)


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


ESSAIS = [essai_taille, essai_debit, essai_codes]


async def principal():
    config = uvicorn.Config(serveur.app, host="127.0.0.1", port=PORT, log_level="error", lifespan="off")
    srv = uvicorn.Server(config)
    tache = asyncio.ensure_future(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    try:
        for essai in ESSAIS:
            serveur.salons.clear()
            await essai()
    finally:
        srv.should_exit = True
        await tache


if __name__ == "__main__":
    asyncio.run(principal())
    print("OK" if not echecs else f"ECHEC ({len(echecs)})")
    sys.exit(1 if echecs else 0)
