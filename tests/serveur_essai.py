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
                     "battement": bool(j.get("battement"))} for j in salon["joueurs"]],
        "phase": e.get("phase"),
        "resync": RESYNC["n"],
        # Undercover : roles et mots, pour verifier ce que montrent les pages.
        "uc": {"roles": e.get("roles"), "mots": e.get("mots"), "vivants": e.get("vivants")}
              if salon.get("jeu") == "undercover" else None,
    }


@serveur.app.get("/test/couper/{code}/{nom}")
async def couper(code: str, nom: str):
    """Ferme la connexion d un joueur, comme une coupure reseau."""
    salon = serveur.salons.get(code)
    for j in (salon or {}).get("joueurs", []):
        if j["nom"] == nom and j.get("ws") is not None:
            await serveur.fermer(j["ws"], 1001)
            return {"coupe": True}
    return {"coupe": False}


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
