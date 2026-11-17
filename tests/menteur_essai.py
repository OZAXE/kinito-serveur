"""
Essai du moteur de Menteur, hors ligne : des centaines de parties jouees au
hasard (actions valides et invalides, absents, delais depasses) dans les trois
modes, de 2 a 8 joueurs. On verifie qu aucune ne plante ni ne se fige, et
que les regles tiennent.
Usage : python tests/menteur_essai.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import menteur as M


def verifier(etat):
    n = etat["nb_joueurs"]
    vivants = [i for i in range(n) if etat["vivants"][i]]
    assert vivants, "il reste toujours au moins un joueur"
    for i in range(n):
        assert 0 <= etat["tirs"][i] <= M.MT_CHAMBRES
        if not etat["vivants"][i]:
            assert etat["mains"][i] == []
    if etat["phase"] == "fin":
        assert len(vivants) == 1 and etat["gagnant"] == vivants[0]
    else:
        assert len(vivants) >= 2
        assert etat["echeance"] is not None, "la table attend toujours quelque chose"
    if etat["phase"] == "jeu":
        c = etat["courant"]
        assert etat["vivants"][c]
        assert etat["mains"][c] or etat["pose"] is not None
    pub = M.mt_etat_public(etat, 0)
    texte = repr(pub)
    assert "balles" not in texte and "'mains'" not in texte


def regle_retournement(etat_avant, etat_apres):
    rev = etat_apres["revelation"]
    if rev["special"] is None:
        attendu = rev["poseur"] if rev["menteur"] else rev["appelant"]
        assert [t["joueur"] for t in rev["tirs"]] in ([attendu], [])
        menteur = any(c not in (rev["carte_table"], "J") for c in rev["cartes"])
        assert menteur == rev["menteur"]
    elif rev["special"] == "diable":
        assert "X" in rev["cartes"] and len(rev["cartes"]) == 1
        assert rev["poseur"] not in [t["joueur"] for t in rev["tirs"]]


def partie(mode, n, graine):
    random.seed(graine)
    t = 1000.0
    etat = M.mt_initialiser(["J%d" % k for k in range(n)], t, mode)
    total = sum(len(m) for m in etat["mains"])
    assert total == n * M.MT_MAIN[mode]
    if mode == "diable":
        assert sum(m.count("X") for m in etat["mains"]) <= 1
    for _ in range(5000):
        verifier(etat)
        if etat["phase"] == "fin":
            return etat
        r = random.random()
        if r < 0.05:
            i = random.randrange(n)
            if etat["partis"][i]:
                M.mt_joueur_revenu(etat, i, t)
            else:
                M.mt_joueur_parti(etat, i, t)
            continue
        if r < 0.12 or etat["phase"] == "revelation":
            t = etat["echeance"] + 0.1
            M.mt_expirer(etat, t)
            continue
        i = random.randrange(n)
        if etat["phase"] == "jeu" and random.random() < 0.8:
            i = etat["courant"]
        if etat["phase"] == "cible" and random.random() < 0.8:
            libres = [int(k) for k, v in etat["cibles"]["choix"].items() if v is None]
            i = random.choice(libres)
        main = etat["mains"][i]
        choix = random.random()
        if choix < 0.45 and main:
            k = random.randint(1, min(3, len(main)))
            msg = {"type": "mt_poser", "cartes": random.sample(range(len(main)), k)}
        elif choix < 0.75:
            msg = {"type": "mt_menteur"}
        elif choix < 0.9:
            msg = {"type": "mt_cible", "cible": random.randrange(-1, n + 1)}
        else:
            msg = random.choice([{"type": "mt_poser", "cartes": "x"}, {"type": "mt_poser", "cartes": [0, 0]},
                                 {"type": "mt_poser", "cartes": [9, 1, 2, 3]}, {"type": "inconnu"},
                                 {"type": "mt_cible", "cible": True}])
        version = etat["version"]
        avant = repr(etat)
        ev = M.mt_action(etat, i, msg, t)
        if etat["version"] == version:
            assert repr(etat) == avant, "un refus ne touche a rien"
        elif msg["type"] == "mt_menteur" and etat["revelation"]["special"] in (None, "diable"):
            regle_retournement(None, etat)
    raise AssertionError("partie figee")


def pluriel_des_cartes():
    """« As » est invariable : « 2 As. », pas « 2 Ass. » ; les autres prennent un s."""
    for carte, attendu in (("A", " pose 2 As."), ("R", " pose 2 Rois."), ("D", " pose 1 Dame.")):
        etat = M.mt_initialiser(["Lea", "Tom"], 1000.0, "classique")
        assert etat["phase"] == "jeu", etat["phase"]
        etat["carte_table"] = carte
        i = etat["courant"]
        etat["mains"][i] = ["R", "D", "A", "J"]
        nb = 2 if attendu.split()[1] == "2" else 1
        M.mt_action(etat, i, {"type": "mt_poser", "cartes": list(range(nb))}, 1000.0)
        assert etat["message"].endswith(attendu), etat["message"]


pluriel_des_cartes()

fins = {m: 0 for m in M.MT_MODES}
speciaux = {}
for graine in range(600):
    mode = M.MT_MODES[graine % 3]
    n = 2 + graine % 7
    e = partie(mode, n, graine)
    fins[mode] += 1
print("parties terminees :", fins)
print("OK")
