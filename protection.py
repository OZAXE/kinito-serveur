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


# Adresses locales : tests et serveur lance sur son propre poste. Sur Render,
# toutes les requetes passent par le proxy, qui ajoute X-Forwarded-For.
LOCALES = {"127.0.0.1", "::1", "localhost"}


def ip_client(entetes, client):
    """
    Adresse du client. Derriere le proxy de Render, la vraie adresse est la
    premiere de X-Forwarded-For (l adresse vue directement est celle du proxy,
    la meme pour tout le monde). None pour une connexion locale sans proxy :
    elle n est pas limitee.
    """
    xff = (entetes.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    hote = getattr(client, "host", None) if client is not None else None
    if hote is None or hote in LOCALES:
        return None
    return hote


class Fenetre:
    """Au plus `limite` evenements par `duree` secondes, pour chaque cle (adresse)."""

    CLES_MAX = 20000

    def __init__(self, limite, duree=60.0, horloge=time.monotonic):
        self.limite = limite
        self.duree = duree
        self.horloge = horloge
        self.evenements = {}        # cle -> liste des instants, du plus ancien au plus recent

    def _recents(self, cle, maintenant):
        liste = self.evenements.get(cle)
        if not liste:
            return []
        seuil = maintenant - self.duree
        i = 0
        while i < len(liste) and liste[i] <= seuil:
            i += 1
        if i:
            del liste[:i]
        return liste

    def plein(self, cle):
        """La limite est-elle atteinte (sans compter un evenement) ?"""
        if cle is None:
            return False
        return len(self._recents(cle, self.horloge())) >= self.limite

    def autoriser(self, cle):
        """Compte un evenement s il reste de la place ; False si la limite est atteinte."""
        if cle is None:
            return True
        maintenant = self.horloge()
        liste = self._recents(cle, maintenant)
        if len(liste) >= self.limite:
            return False
        if not liste:
            if len(self.evenements) >= self.CLES_MAX:
                self.purger()
            liste = self.evenements.setdefault(cle, [])
        liste.append(maintenant)
        return True

    def purger(self):
        """Oublie les cles sans evenement recent (et tout, s il y en a encore trop)."""
        maintenant = self.horloge()
        for cle in list(self.evenements):
            if not self._recents(cle, maintenant):
                del self.evenements[cle]
        if len(self.evenements) >= self.CLES_MAX:
            self.evenements.clear()


class Connexions:
    """Nombre de connexions ouvertes en meme temps, par cle (adresse)."""

    def __init__(self, limite):
        self.limite = limite
        self.ouvertes = {}

    def entrer(self, cle):
        if cle is None:
            return True
        n = self.ouvertes.get(cle, 0)
        if n >= self.limite:
            return False
        self.ouvertes[cle] = n + 1
        return True

    def sortir(self, cle):
        if cle is None:
            return
        n = self.ouvertes.get(cle, 0) - 1
        if n > 0:
            self.ouvertes[cle] = n
        else:
            self.ouvertes.pop(cle, None)
