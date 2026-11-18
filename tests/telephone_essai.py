"""
Essai du moteur du Gribouillophone, hors ligne : des parties jouees au
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
    out, vus = [], set()
    for a, album in enumerate(etat["albums"]):
        for k, c in enumerate(album):
            s = json.dumps(c["contenu"], ensure_ascii=False)
            if k >= montre_par_album.get(a, 0):
                out.append(s)
            else:
                vus.add(s)
    # Knock-Off : la meme phrase ouvre tous les albums, deja devoilee une fois.
    return [s for s in out if s not in vus]


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
        a = T.album_de(etat, i)
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
                assert T.album_de(etat, c["auteur"], k) == a, "mauvaise rotation"
                if etat["mode"] != "speedrun":
                    assert (c["auteur"] + k) % n == a, "mauvaise rotation"
            assert c["type"] == ("dessin" if T.nature_etape(k, etat["mode"]) == "dessin" else "texte")
            if c["type"] == "texte":
                assert isinstance(c["contenu"], str) and 1 <= len(c["contenu"]) <= TD_CONFIG["texte_max"]
            else:
                assert T.valider_dessin(c["contenu"]) is not None
    # Chaque joueur sur un album different a chaque etape.
    for k, aff in enumerate(etat["affectations"]):
        if aff is not None:
            assert sorted(aff) == list(range(n)), "deux joueurs sur le meme album"
    if etat["mode"] == "knockoff":
        assert len({json.dumps(al[0]) for al in etat["albums"]}) == 1, "Knock-Off : une seule phrase de depart"
    assert 0 <= etat["hote"] < n


def partie(n, graine):
    rng = random.Random(graine)
    t = 1000.0
    noms = [f"J{i}" for i in range(n)]
    source = rng.choice(["libre", "banque", "mixte", "nimporte"])
    mode = rng.choice(["normal", "knockoff", "speedrun", "nimporte"])
    vitesse = rng.choice(["flash", "normal", "long", None])
    etapes = rng.choice([None, 1, 2, n - 1, n + 3, "x"])
    etat = T.td_initialiser(noms, t, hote=rng.randrange(n), vitesse=vitesse, source=source,
                            etapes=etapes, mode=mode, rng=rng)
    maxi = n if mode == "knockoff" else n - 1
    defaut = min(maxi, TD_CONFIG["speedrun"]["etapes_defaut"]) if mode == "speedrun" else maxi
    attendu = defaut if not isinstance(etapes, int) or not 1 <= etapes <= maxi else etapes
    assert etat["nb_etapes"] == attendu
    assert etat["etape"] == (1 if source == "banque" or mode == "knockoff" else 0)
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
            nature = T.nature_etape(etat["etape"], etat["mode"])
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


def scenario_knockoff():
    """Knock-Off : meme phrase partout, copies de memoire, de moins en moins de temps."""
    t = 0.0
    e = T.td_initialiser(["A", "B", "C", "D"], t, mode="knockoff", vitesse="normal", source="libre")
    assert e["source"] == "banque" and e["etape"] == 1 and e["nb_etapes"] == 4
    phrase = e["albums"][0][0]["contenu"]
    assert all(al[0]["contenu"] == phrase for al in e["albums"])
    assert T.td_tache(e, 2, t)["modele"] == phrase and not T.td_tache(e, 2, t)["copie"]
    v = TD_CONFIG["vitesses"]["normal"]
    assert e["fin_etape"] - t == v["dessin"]
    durees = []
    for k in range(1, 5):
        debut = t
        d = [{"c": k, "e": 0, "p": enc(k) + enc(k) + enc(50 + k) + enc(60)}]
        for i in range(4):
            T.td_action(e, i, {"type": "td_contribuer", "contenu": d}, t)
        if k < 4:
            assert e["etape"] == k + 1
            durees.append(e["fin_etape"] - t - v["visionnage"])
            tache = T.td_tache(e, 0, t + 1)
            assert tache["copie"] and tache["nature"] == "dessin" and tache["modele"] == d
            assert 0 < tache["vision"] <= v["visionnage"]
            # Le temps de visionnage passe : le modele n est plus envoye.
            tard = T.td_tache(e, 0, e["fin_vision"] + 0.1)
            assert tard["modele"] is None and tard["vision"] == 0
            assert "phrase" not in json.dumps(tard)
    assert durees == sorted(durees, reverse=True) and min(durees) >= TD_CONFIG["knockoff"]["minimum"]
    assert e["phase"] == "pret"
    assert all(al[k]["type"] == "dessin" for al in e["albums"] for k in range(1, 5))
    print("  Knock-Off : ok", [round(x) for x in durees])


def scenario_speedrun():
    """Speedrun : minuteurs courts, peu d etapes, repartition au hasard sans repasser."""
    for n in range(3, 9):
        for graine in range(30):
            rng = random.Random(graine)
            e = T.td_initialiser([f"J{i}" for i in range(n)], 0.0, mode="speedrun", vitesse="flash",
                                 etapes=n - 1, rng=rng)
            assert e["fin_etape"] == TD_CONFIG["vitesses"]["flash"]["speedrun_phrase"]
            for k in range(n):
                for i in range(n):
                    T.td_action(e, i, {"type": "td_contribuer", "contenu": "x%d %d" % (k, i)
                                       if T.nature_etape(e["etape"], "speedrun") != "dessin" else []}, 0.0)
            assert e["phase"] == "pret"
            for album in e["albums"]:
                assert sorted(c["auteur"] for c in album) == list(range(n)), "chacun une fois par album"
    e = T.td_initialiser(["A", "B", "C", "D", "E", "F", "G", "H"], 0.0, mode="speedrun")
    assert e["nb_etapes"] == TD_CONFIG["speedrun"]["etapes_defaut"]
    print("  Speedrun : ok")


def main():
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    scenario_simple()
    scenario_knockoff()
    scenario_speedrun()
    for g in range(nb):
        partie(3 + g % 6, g)
    print(f"  {nb} parties au hasard (3 a 8 joueurs) : ok")
    print("OK")


if __name__ == "__main__":
    main()
