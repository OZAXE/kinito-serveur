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


if __name__ == "__main__":
    uvicorn.run(serveur.app, host="127.0.0.1", port=8000, log_level="warning")
