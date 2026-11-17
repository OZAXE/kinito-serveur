"""
Garde-fous contre les clients abusifs (inondation de messages, de connexions,
de requetes). Pur : ni reseau ni asyncio, testable hors ligne.
"""
import time


class Debit:
    """
    Seau a jetons : `par_seconde` messages en regime continu, `rafale` d un
    coup. Un client normal (une action de temps en temps, un battement toutes
    les 5 s) n en approche jamais.
    """

    def __init__(self, par_seconde, rafale, horloge=time.monotonic):
        self.par_seconde = par_seconde
        self.rafale = rafale
        self.horloge = horloge
        self.jetons = float(rafale)
        self.dernier = horloge()
        self.refus_de_suite = 0

    def accepter(self):
        maintenant = self.horloge()
        self.jetons = min(self.rafale, self.jetons + (maintenant - self.dernier) * self.par_seconde)
        self.dernier = maintenant
        if self.jetons >= 1:
            self.jetons -= 1
            self.refus_de_suite = 0
            return True
        self.refus_de_suite += 1
        return False
