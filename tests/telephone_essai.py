"""
Essai du moteur du Telephone Dessine, hors ligne : des parties jouees au
hasard (contributions valides et invalides, retards, absents, revenants,
votes de travers), de 3 a 8 joueurs, dans toutes les sources et vitesses.
On verifie qu aucune ne plante ni ne se fige, que la rotation est juste,
qu aucun contenu ne fuit avant son heure (etat public et taches), et que
les votes et les gorgees suivent les regles.
Usage : python tests/telephone_essai.py [nb_parties]
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import telephone as T
from telephone import TD_CONFIG

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def enc(v):
    return B64[v // 64] + B64[v % 64]


def dessin_au_hasard(rng, marque):
    traits = []
    for _ in range(rng.randint(0, 6)):
        pts = "".join(enc(rng.randint(0, 400)) + enc(rng.randint(0, 300)) for _ in range(rng.randint(1, 30)))
        traits.append({"c": rng.randint(0, 8), "e": rng.randint(0, 2), "p": pts})
    # Un trait reconnaissable : sa signature ne doit jamais fuir avant l heure.
    traits.append({"c": 0, "e": 0, "p": marque})
    return traits


def contenus_secrets(etat, montre_par_album):
    """Ce qui n a pas encore ete devoile, sous une forme cherchable dans du JSON."""
    out = []
    for a, album in enumerate(etat["albums"]):
        for k, c in enumerate(album):
            if k >= montre_par_album.get(a, 0):
                out.append(json.dumps(c["contenu"], ensure_ascii=False))
    return out


def verifier_secret(etat, maintenant):
    pub = json.dumps(T.td_etat_public(etat, maintenant), ensure_ascii=False)
    montre = {}
    phase = etat["phase"]
    if phase in ("revelation", "vote_album", "resultat_album"):
        r = etat["revelation"]
        for a in range(r["album"]):
            montre[a] = len(etat["albums"][a])
        montre[r["album"]] = r["montre"]
    elif phase in ("vote_final", "fin"):
        montre = {a: len(al) for a, al in enumerate(etat["albums"])}
    for secret in contenus_secrets(etat, montre):
        if len(secret) > 8:              # "..." ou "[]" se retrouvent partout
            assert secret not in pub, "un contenu fuit dans td_etat : " + secret[:60]
    # Le detail des albums deja passes n est pas renvoye non plus (seul l album courant).
    if phase in ("revelation", "vote_album", "resultat_album"):
        r = etat["revelation"]
        for a in range(r["album"]):
            for c in etat["albums"][a]:
                s = json.dumps(c["contenu"], ensure_ascii=False)
                if len(s) > 8 and all(s != json.dumps(x["contenu"], ensure_ascii=False)
                                      for x in etat["albums"][r["album"]]):
                    assert s not in pub
    assert ("td_galerie" in json.dumps(T.td_galerie(etat))) == (phase in ("vote_final", "fin"))
    # Taches : seulement la contribution precedente de SON album.
    for i in range(etat["nb_joueurs"]):
        t = T.td_tache(etat, i)
        if phase != "etape":
            assert t is None
            continue
        k = etat["etape"]
        a = (i + k) % etat["nb_joueurs"]
        if k == 0:
            assert t["modele"] is None
        else:
            assert t["modele"] == etat["albums"][a][k - 1]["contenu"]
        texte = json.dumps(t, ensure_ascii=False)
        for b, album in enumerate(etat["albums"]):
            for kk, c in enumerate(album):
                if (b, kk) == (a, k - 1) or c["contenu"] == t["modele"]:
                    continue
                s = json.dumps(c["contenu"], ensure_ascii=False)
                if len(s) > 8:
                    assert s not in texte, "une tache montre plus que la contribution precedente"


def verifier(etat):
    n = etat["nb_joueurs"]
    phase = etat["phase"]
    if phase != "fin" and phase != "pret":
        if phase in ("revelation", "resultat_album") and not etat["revelation"]["auto"]:
            assert etat["echeance"] is None
        else:
            assert etat["echeance"] is not None, "la table attend toujours quelque chose (" + phase + ")"
    if phase == "etape":
        k = etat["etape"]
        for a, album in enumerate(etat["albums"]):
            assert len(album) in (k, k + 1)
    # Rotation : chacun au plus une fois par album, jamais deux contributions a la meme etape.
    for a, album in enumerate(etat["albums"]):
        auteurs = [c["auteur"] for c in album if c["auteur"] is not None]
        assert len(auteurs) == len(set(auteurs)), "un joueur revient sur le meme album"
        for k, c in enumerate(album):
            if c["auteur"] is not None:
                assert (c["auteur"] + k) % n == a, "mauvaise rotation"
            assert c["type"] == ("dessin" if T.nature_etape(k) == "dessin" else "texte")
            if c["type"] == "texte":
                assert isinstance(c["contenu"], str) and 1 <= len(c["contenu"]) <= TD_CONFIG["texte_max"]
            else:
                assert T.valider_dessin(c["contenu"]) is not None
    assert 0 <= etat["hote"] < n


def partie(n, graine):
    rng = random.Random(graine)
    t = 1000.0
    noms = [f"J{i}" for i in range(n)]
    source = rng.choice(["libre", "banque", "mixte", "nimporte"])
    vitesse = rng.choice(["flash", "normal", "long", None])
    etapes = rng.choice([None, 1, 2, n - 1, n + 3, "x"])
    etat = T.td_initialiser(noms, t, hote=rng.randrange(n), vitesse=vitesse, source=source,
                            etapes=etapes, rng=rng)
    attendu = n - 1 if not isinstance(etapes, int) or not 1 <= etapes <= n - 1 else etapes
    assert etat["nb_etapes"] == attendu
    assert etat["etape"] == (1 if source == "banque" else 0)
    compteur = [0]

    def marque():
        compteur[0] += 1
        return enc(compteur[0] % 400) + enc(299) + enc(compteur[0] // 400) + enc(1) + enc(77) + enc(77)

    coups = 0
    while etat["phase"] != "fin":
        coups += 1
        assert coups < 20000, "partie figee"
        verifier(etat)
        verifier_secret(etat, t)
        t += rng.choice([0.1, 0.5, 2, 5])
        i = rng.randrange(n)
        r = rng.random()
        if r < 0.04:
            T.td_joueur_parti(etat, i, t) if not etat["partis"][i] else T.td_joueur_revenu(etat, i, t)
            continue
        if r < 0.12 and etat["echeance"] is not None:
            T.td_expirer(etat, etat["echeance"] + 0.01)
            t = max(t, (etat["echeance"] or t))
            continue
        if etat["partis"][i]:
            continue
        phase = etat["phase"]
        if phase == "etape":
            nature = T.nature_etape(etat["etape"])
            if nature == "dessin":
                contenu = dessin_au_hasard(rng, marque())
                if rng.random() < 0.05:
                    contenu = [{"c": 99, "e": 0, "p": "AAAA"}]
            else:
                compteur[0] += 1
                contenu = rng.choice(["", "   ", "n%d " % compteur[0] + "x" * 200,
                                      "phrase numéro %d bien longue" % compteur[0], 42])
            typ = "td_brouillon" if rng.random() < 0.3 else "td_contribuer"
            avant = list(etat["envoyes"])
            T.td_action(etat, i, {"type": typ, "contenu": contenu, "auto": rng.random() < 0.1}, t)
            if typ == "td_contribuer" and avant[i] and etat["phase"] == "etape":
                assert etat["envoyes"][i]
        elif phase == "pret":
            j = etat["hote"] if rng.random() < 0.7 else i
            T.td_action(etat, j, {"type": "td_reveler", "auto": rng.random() < 0.3}, t)
        elif phase in ("revelation", "resultat_album"):
            j = etat["hote"] if rng.random() < 0.8 else i
            typ = rng.choice(["td_suivant", "td_suivant", "td_auto", "td_reaction"])
            ev = T.td_action(etat, j, {"type": typ, "actif": rng.random() < 0.5, "emoji": "😂"}, t)
            if typ == "td_reaction":
                assert all(e["msg"]["type"] == "td_reaction" for e in ev)
        elif phase == "vote_album":
            a = etat["revelation"]["album"]
            k = rng.randrange(-1, len(etat["albums"][a]) + 1)
            avant = dict(etat["votes"])
            T.td_action(etat, i, {"type": "td_voter", "cible": k}, t)
            if 0 <= k < len(etat["albums"][a]) and etat["albums"][a][k]["auteur"] == i:
                assert etat["votes"].get(str(i)) == avant.get(str(i)), "vote pour soi accepte"
        elif phase == "vote_final":
            cands = T._candidats_final(etat)
            a, k = rng.choice(cands) if rng.random() < 0.9 else (0, 99)
            avant = dict(etat["votes_final"])
            T.td_action(etat, i, {"type": "td_voter", "album": a, "cible": k}, t)
            if etat["phase"] == "vote_final" and 0 <= k < len(etat["albums"][a]) \
                    and etat["albums"][a][k]["auteur"] == i:
                assert etat["votes_final"].get(str(i)) == avant.get(str(i)), "vote pour son propre dessin accepte"
    verifier(etat)
    verifier_secret(etat, t)
    # Fin : un resultat par album, et des gorgees coherentes.
    assert len(etat["resultats"]) == n
    mult = TD_CONFIG["vitesses"][etat["vitesse"]]["multiplicateur"]
    for g in etat["gorgees"]:
        base = TD_CONFIG["gorgees"][g["raison"] if g["raison"] != "retard" else "retard"]
        assert g["n"] == max(1, int(base * mult + 0.5))
        assert (g["sens"] == "boit") == (g["raison"] in ("retard", "mal_compris"))
    for r in etat["resultats"]:
        album = etat["albums"][r["album"]]
        for k in r["gagnants"]:
            assert album[k]["auteur"] is not None
    pub = T.td_etat_public(etat, t)
    assert pub["final"] is not None and pub["resultats"] is not None
    return etat


def scenario_simple():
    """3 joueurs, tout le monde joue le jeu : rotation exacte et secret."""
    t = 0.0
    e = T.td_initialiser(["A", "B", "C"], t, hote=1, source="libre")
    assert e["nb_etapes"] == 2
    for i in range(3):
        assert T.td_tache(e, i)["nature"] == "phrase"
        T.td_action(e, i, {"type": "td_contribuer", "contenu": f"phrase de {i}"}, t)
    assert e["etape"] == 1
    # A l etape 1, le joueur 0 dessine la phrase de l album 1 (celle de B).
    assert T.td_tache(e, 0) ["modele"] == "phrase de 1"
    assert "phrase de" not in json.dumps(T.td_etat_public(e, t))
    d = [{"c": 1, "e": 1, "p": enc(10) + enc(10) + enc(20) + enc(20)}]
    for i in range(3):
        T.td_action(e, i, {"type": "td_contribuer", "contenu": d}, t)
    assert e["etape"] == 2 and T.td_tache(e, 0)["modele"] == d
    T.td_action(e, 0, {"type": "td_contribuer", "contenu": "un truc"}, t)
    ev = T.td_action(e, 0, {"type": "td_contribuer", "contenu": "encore"}, t)
    assert ev and ev[0]["msg"]["type"] == "erreur", "une seule contribution par etape"
    T.td_action(e, 1, {"type": "td_brouillon", "contenu": "brouillon de B"}, t)
    ev = T.td_expirer(e, e["echeance"] + 0.1)
    assert e["phase"] == "pret"
    assert e["albums"][(1 + 2) % 3][2]["contenu"] == "brouillon de B"
    assert e["albums"][(2 + 2) % 3][2]["contenu"] == "..."
    assert [g["joueur"] for g in e["gorgees"]] == [1, 2], "les retardataires boivent"
    # Seul l hote revele.
    assert T.td_action(e, 0, {"type": "td_reveler"}, t)[0]["msg"]["type"] == "erreur"
    T.td_action(e, 1, {"type": "td_reveler"}, t)
    pub = T.td_etat_public(e, t)
    assert len(pub["revelation"]["contributions"]) == 1
    assert "un truc" not in json.dumps(pub)
    T.td_action(e, 1, {"type": "td_suivant"}, t)
    T.td_action(e, 1, {"type": "td_suivant"}, t)
    T.td_action(e, 1, {"type": "td_suivant"}, t)
    assert e["phase"] == "vote_album"
    # Album 0 : phrase de A (0), dessin de C (2), texte de B (1).
    assert T.td_action(e, 0, {"type": "td_voter", "cible": 0}, t)[0]["msg"]["type"] == "erreur"
    T.td_action(e, 0, {"type": "td_voter", "cible": 1}, t)
    T.td_action(e, 1, {"type": "td_voter", "cible": 1}, t)
    T.td_action(e, 2, {"type": "td_voter", "cible": 0}, t)
    assert e["phase"] == "resultat_album" and e["resultats"][0]["gagnants"] == [1]
    assert e["gorgees"][-1] == {"joueur": 2, "n": 2, "sens": "distribue", "raison": "meilleur_album"}
    print("  scenario a 3 joueurs : ok")


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    scenario_simple()
    for g in range(nb):
        partie(3 + g % 6, g)
    print(f"  {nb} parties au hasard (3 a 8 joueurs) : ok")
    print("OK")


if __name__ == "__main__":
    main()
