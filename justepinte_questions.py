"""
Questions du Juste Pinte : une reponse chiffree chacune.

Ton : la fete, l alcool, les soirees, et un peu de culture de comptoir.
Pas de question sur une ville en particulier (choix d Enzo).

Chaque question : (categorie, texte, reponse, unite, tolerance, info)
  - unite : affichee apres le nombre ("%", "ans", "cl"...). "année" = une
    date : la tolerance « dans le mille » est alors d un an (config).
  - tolerance : None = la regle de la config (5 % de la reponse) ; sinon
    l ecart absolu maximal pour etre « dans le mille ».
  - info : une phrase affichee a la revelation (facultative).
Les chiffres sont des valeurs connues et stables ; quand une valeur varie
selon les sources, la tolerance est elargie.
"""

JP_CATEGORIES = {
    "alcool": "Alcool et cocktails",
    "fete": "Fête et soirées",
    "culture": "Ciné et musique",
    "bouffe": "Bouffe",
    "jeux": "Jeux de bar et sport",
    "insolite": "Insolite",
}

QUESTIONS = [
    # --- Alcool et cocktails ---------------------------------------------
    ("alcool", "Combien de bulles environ dans une coupe de champagne ?", 1000000, "bulles", 300000,
     "Environ un million, selon les chercheurs de l'université de Reims."),
    ("alcool", "Quelle est la pression dans une bouteille de champagne fermée ?", 6, "bars", 1,
     "Environ 6 bars, trois fois un pneu de voiture."),
    ("alcool", "À quelle vitesse sort un bouchon de champagne ?", 40, "km/h", 10, None),
    ("alcool", "Combien de demi-tours faut-il pour défaire le muselet d'une bouteille de champagne ?", 6, "demi-tours", 0,
     "Toujours 6 demi-tours, c'est la norme."),
    ("alcool", "Combien de cépages sont autorisés pour faire du champagne ?", 7, "cépages", 0,
     "7, même si le pinot noir, le meunier et le chardonnay font presque tout."),
    ("alcool", "Combien de kilos de raisin faut-il pour une bouteille de champagne ?", 1.2, "kg", 0.2, None),
    ("alcool", "Combien de litres contient un Jéroboam de champagne ?", 3, "litres", 0, None),
    ("alcool", "Combien de litres contient un Mathusalem ?", 6, "litres", 0, None),
    ("alcool", "Combien de litres contient un Balthazar ?", 12, "litres", 0, None),
    ("alcool", "Combien de bouteilles de 75 cl tient un Nabuchodonosor ?", 20, "bouteilles", 0,
     "15 litres, soit 20 bouteilles."),
    ("alcool", "Combien de verres de 12,5 cl sert-on avec un magnum ?", 12, "verres", 0, None),
    ("alcool", "Combien de bouteilles de champagne ont été vendues dans le monde en 2023 ?", 299, "millions", 15, None),
    ("alcool", "Quel est le degré d'alcool d'une Leffe Blonde ?", 6.6, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Heineken ?", 5, "%", 0.2, None),
    ("alcool", "Quel est le degré d'alcool d'une Guinness à la pression ?", 4.2, "%", 0.2, None),
    ("alcool", "Quel est le degré d'alcool d'une Corona ?", 4.5, "%", 0.2, None),
    ("alcool", "Quel est le degré d'alcool d'une Desperados ?", 5.9, "%", 0.2, None),
    ("alcool", "Quel est le degré d'alcool d'une Duvel ?", 8.5, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Kwak ?", 8.4, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Chimay Bleue ?", 9, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Delirium Tremens ?", 8.5, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Chouffe ?", 8, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une Grimbergen Blonde ?", 6.7, "%", 0.3, None),
    ("alcool", "Quel est le degré d'alcool d'une 1664 blonde ?", 5.5, "%", 0.2, None),
    ("alcool", "Quel est le degré de la bière la plus forte du monde, la Snake Venom ?", 67.5, "%", 3,
     "67,5 %, plus fort que la plupart des whiskys."),
    ("alcool", "Quel est le degré d'alcool du Ricard ?", 45, "%", 0, None),
    ("alcool", "Quel est le degré d'alcool du Jägermeister ?", 35, "%", 0, None),
    ("alcool", "Quel est le degré d'alcool de la Chartreuse verte ?", 55, "%", 1, None),
    ("alcool", "Quel est le degré d'alcool du Malibu ?", 21, "%", 1, None),
    ("alcool", "Quel est le degré d'alcool du Baileys ?", 17, "%", 1, None),
    ("alcool", "Quel est le degré d'alcool de l'Aperol ?", 11, "%", 0.5, None),
    ("alcool", "Quel est le degré d'alcool du Campari ?", 25, "%", 1, None),
    ("alcool", "Quel est le degré d'alcool du Cointreau ?", 40, "%", 0, None),
    ("alcool", "Quel est le degré moyen d'un vin rouge ?", 13, "%", 1, None),
    ("alcool", "Quel est le degré d'un cidre brut ?", 5, "%", 0.7, None),
    ("alcool", "Quel est le degré d'un saké ?", 15, "%", 2, None),
    ("alcool", "Combien de centilitres dans une pinte anglaise ?", 56.8, "cl", 0.5, None),
    ("alcool", "Combien de centilitres dans un demi en France ?", 25, "cl", 0, None),
    ("alcool", "Combien de litres contient une chope (Maß) de l'Oktoberfest ?", 1, "litre", 0, None),
    ("alcool", "Combien de grammes d'alcool pur dans un verre standard en France ?", 10, "g", 0, None),
    ("alcool", "Quelle est la limite d'alcool au volant en France ?", 0.5, "g/L de sang", 0,
     "0,5 g/L, et 0,2 g/L pour les jeunes conducteurs. Tu rentres comment ce soir ?"),
    ("alcool", "Quelle est la limite d'alcool au volant pour un jeune conducteur en France ?", 0.2, "g/L de sang", 0, None),
    ("alcool", "Quel est l'âge légal pour boire de l'alcool aux États-Unis ?", 21, "ans", 0, None),
    ("alcool", "Quel est l'âge minimum pour boire une bière en Allemagne ?", 16, "ans", 0, None),
    ("alcool", "Combien d'années minimum doit vieillir un whisky écossais ?", 3, "ans", 0, None),
    ("alcool", "Combien d'années minimum de vieillissement pour un cognac VSOP ?", 4, "ans", 0, None),
    ("alcool", "Quel pourcentage minimum d'agave doit contenir une tequila ?", 51, "%", 0,
     "51 % ; les « 100 % agave » sont les meilleures."),
    ("alcool", "À quelle température gèle une vodka à 40 % ?", -27, "°C", 3,
     "Vers -27 °C : ton congélateur (-18 °C) ne la fera jamais geler."),
    ("alcool", "Combien de calories dans une pinte (50 cl) de blonde à 5 % ?", 215, "kcal", 35, None),
    ("alcool", "Combien de calories dans un verre de vin rouge de 12,5 cl ?", 90, "kcal", 15, None),
    ("alcool", "Combien de centilitres de rhum dans un mojito officiel ?", 4.5, "cl", 0.5, None),
    ("alcool", "Combien de centilitres de tequila dans une margarita officielle ?", 5, "cl", 0.5, None),
    ("alcool", "Combien d'alcools forts différents dans un Long Island Iced Tea ?", 5, "alcools", 0,
     "Vodka, gin, rhum, tequila et triple sec. Rien que ça."),
    ("alcool", "En quelle année a été créée la brasserie Guinness ?", 1759, "année", None, None),
    ("alcool", "En quelle année a été fondée la brasserie Heineken ?", 1864, "année", None, None),
    ("alcool", "En quelle année la Prohibition a-t-elle commencé aux États-Unis ?", 1920, "année", None, None),
    ("alcool", "En quelle année la Prohibition a-t-elle pris fin aux États-Unis ?", 1933, "année", None, None),
    ("alcool", "En quelle année l'absinthe a-t-elle été interdite en France ?", 1915, "année", None, None),
    ("alcool", "Combien de litres d'alcool pur un adulte français boit-il en moyenne par an ?", 10.5, "litres", 1.5, None),
    ("alcool", "Combien de litres de vin un Français boit-il en moyenne par an ?", 40, "litres", 6, None),
    ("alcool", "Combien de verres de 25 cl peut-on tirer d'un fût de 30 litres ?", 120, "verres", 0,
     "Sans compter la mousse ratée du début."),

    # --- Fête et soirées ---------------------------------------------------
    ("fete", "En quelle année a eu lieu le premier Oktoberfest ?", 1810, "année", None,
     "Pour le mariage du prince héritier de Bavière."),
    ("fete", "Combien de jours dure environ l'Oktoberfest ?", 17, "jours", 2, None),
    ("fete", "Combien de jours durent les Fêtes de Bayonne ?", 5, "jours", 0, None),
    ("fete", "Combien de personnes environ viennent aux Fêtes de Bayonne chaque année ?", 1000000, "personnes", 300000, None),
    ("fete", "Quel jour de mars fête-t-on la Saint-Patrick ?", 17, "mars", 0, None),
    ("fete", "Le Beaujolais nouveau arrive le combientième jeudi de novembre ?", 3, "e jeudi", 0, None),
    ("fete", "En quelle année a eu lieu la première Fête de la musique ?", 1982, "année", None, None),
    ("fete", "En quelle année a eu lieu le festival de Woodstock ?", 1969, "année", None, None),
    ("fete", "Combien de personnes environ sont venues à Woodstock en 1969 ?", 400000, "personnes", 80000, None),
    ("fete", "En quelle année a ouvert le Studio 54 à New York ?", 1977, "année", None, None),
    ("fete", "En quelle année Gangnam Style est-il devenu la première vidéo à 1 milliard de vues ?", 2012, "année", None, None),
    ("fete", "En quelle année est sorti « Dancing Queen » d'ABBA ?", 1976, "année", None, None),
    ("fete", "En quelle année est sorti « I Will Survive » de Gloria Gaynor ?", 1978, "année", None, None),
    ("fete", "En quelle année est sorti « Y.M.C.A. » des Village People ?", 1978, "année", None, None),
    ("fete", "En quelle année est sorti « Freed from Desire » de Gala ?", 1996, "année", None, None),
    ("fete", "En quelle année est sorti « Daddy Cool » de Boney M. ?", 1976, "année", None, None),
    ("fete", "En quelle année est sorti « Mambo No. 5 » de Lou Bega ?", 1999, "année", None, None),
    ("fete", "En quelle année est sorti « Livin' la Vida Loca » de Ricky Martin ?", 1999, "année", None, None),
    ("fete", "En quelle année est sorti « Mr. Saxobeat » d'Alexandra Stan ?", 2010, "année", None, None),
    ("fete", "En quelle année est sorti « Get Lucky » de Daft Punk ?", 2013, "année", None, None),
    ("fete", "En quelle année est sorti « Despacito » ?", 2017, "année", None, None),
    ("fete", "En quelle année est sorti « Last Christmas » de Wham! ?", 1984, "année", None, None),
    ("fete", "En quelle année est sorti « Africa » de Toto ?", 1982, "année", None, None),
    ("fete", "En quelle année est sorti « Bohemian Rhapsody » de Queen ?", 1975, "année", None, None),
    ("fete", "Combien de secondes dure « Bohemian Rhapsody » ?", 355, "secondes", 15,
     "5 minutes 55 : de quoi finir ton verre."),
    ("fete", "En quelle année est sorti l'album « Thriller » de Michael Jackson ?", 1982, "année", None, None),

    # --- Ciné et musique ---------------------------------------------------
    ("culture", "En quelle année est sorti le film « Very Bad Trip » ?", 2009, "année", None, None),
    ("culture", "En quelle année est sorti le film « Projet X » ?", 2012, "année", None, None),
    ("culture", "En quelle année est sorti le film « American Pie » ?", 1999, "année", None, None),
    ("culture", "En quelle année est sorti « Les Bronzés font du ski » ?", 1979, "année", None, None),
    ("culture", "En quelle année est sorti « Dirty Dancing » ?", 1987, "année", None, None),
    ("culture", "En quelle année est sorti « Grease » ?", 1978, "année", None, None),
    ("culture", "En quelle année est sorti « La Fièvre du samedi soir » ?", 1977, "année", None, None),
    ("culture", "En quelle année est sorti « Cocktail » avec Tom Cruise barman ?", 1988, "année", None, None),
    ("culture", "En quelle année est sorti « Coyote Girls » ?", 2000, "année", None, None),
    ("culture", "En quelle année est sorti « Mamma Mia! » au cinéma ?", 2008, "année", None, None),
    ("culture", "En quelle année est sorti « Le Loup de Wall Street » ?", 2013, "année", None, None),
    ("culture", "En quelle année est sorti « Shrek » ?", 2001, "année", None, None),
    ("culture", "En quelle année est sorti « Titanic » ?", 1997, "année", None, None),
    ("culture", "Combien de minutes dure « Titanic » ?", 194, "minutes", 10, None),
    ("culture", "En quelle année est sorti « Les Visiteurs » ?", 1993, "année", None, None),
    ("culture", "Combien d'entrées en France pour « Bienvenue chez les Ch'tis » ?", 20.4, "millions", 1.5, None),
    ("culture", "Combien d'entrées en France pour « Intouchables » ?", 19.4, "millions", 1.5, None),
    ("culture", "Combien de saisons compte la série « Friends » ?", 10, "saisons", 0, None),
    ("culture", "Combien d'épisodes compte la série « Friends » ?", 236, "épisodes", 15, None),
    ("culture", "Combien de fois Ross se marie-t-il dans « Friends » ?", 3, "fois", 0,
     "Carol, Emily et Rachel (à Las Vegas, évidemment)."),
    ("culture", "Combien de saisons compte « How I Met Your Mother » ?", 9, "saisons", 0, None),

    # --- Bouffe ------------------------------------------------------------
    ("bouffe", "En quelle année a ouvert le premier McDonald's en France ?", 1972, "année", None, None),
    ("bouffe", "En quelle année a été créé le Nutella ?", 1964, "année", None, None),
    ("bouffe", "En quelle année a été inventé le Coca-Cola ?", 1886, "année", None,
     "Par un pharmacien d'Atlanta, vendu au départ comme un remède."),
    ("bouffe", "Combien de calories dans un Big Mac ?", 520, "kcal", 60, None),
    ("bouffe", "Combien de calories dans 100 g de chips ?", 540, "kcal", 40, None),
    ("bouffe", "À quelle température cuit une vraie pizza napolitaine au feu de bois ?", 450, "°C", 40, None),
    ("bouffe", "Combien d'unités Scoville en moyenne pour le piment Carolina Reaper ?", 1.6, "millions", 0.3, None),
    ("bouffe", "Quel pourcentage d'eau contient un concombre ?", 96, "%", 2, None),

    # --- Jeux de bar et sport ---------------------------------------------
    ("jeux", "Quel est le meilleur score possible avec 3 fléchettes ?", 180, "points", 0, "« One hundred and eightyyyy ! »"),
    ("jeux", "À quelle hauteur se trouve le centre d'une cible de fléchettes ?", 173, "cm", 3, None),
    ("jeux", "À quelle distance de la cible se tient-on aux fléchettes ?", 237, "cm", 5, None),
    ("jeux", "Combien de boules au billard américain, blanche comprise ?", 16, "boules", 0, None),
    ("jeux", "Quel est le break maximum au snooker ?", 147, "points", 0, None),
    ("jeux", "Combien de gobelets par équipe au beer pong ?", 10, "gobelets", 0, None),
    ("jeux", "Combien de joueurs (figurines) par équipe sur un baby-foot ?", 11, "joueurs", 0, None),
    ("jeux", "Quel est le score parfait au bowling ?", 300, "points", 0, None),
    ("jeux", "Combien de cartes dans un jeu de tarot ?", 78, "cartes", 0, None),
    ("jeux", "Combien de cartes dans un jeu de Uno classique ?", 108, "cartes", 4, None),
    ("jeux", "Combien de cases sur un plateau de Monopoly ?", 40, "cases", 0, None),
    ("jeux", "Avec combien d'euros commence-t-on au Monopoly ?", 1500, "€", 0, None),
    ("jeux", "Quelle est la somme de toutes les faces d'un dé à 6 faces ?", 21, "points", 0, None),
    ("jeux", "Combien de pions au total au jeu de dames international ?", 40, "pions", 0, None),
    ("jeux", "Quel est le record du monde du 100 mètres ?", 9.58, "secondes", 0.1, "Usain Bolt, 2009."),
    ("jeux", "Combien de kilomètres fait un marathon ?", 42.195, "km", 0.5, None),
    ("jeux", "À quelle hauteur se trouve un panier de basket ?", 305, "cm", 5, None),
    ("jeux", "Combien de kilomètres fait environ le Tour de France ?", 3400, "km", 250, None),

    # --- Insolite ----------------------------------------------------------
    ("insolite", "Combien de fois par jour pète-t-on en moyenne ?", 15, "fois", 7, "Entre 10 et 20 fois. Toi aussi."),
    ("insolite", "Combien d'os compte le corps d'un adulte ?", 206, "os", 5, None),
    ("insolite", "Combien de cheveux environ sur une tête ?", 120000, "cheveux", 30000, None),
    ("insolite", "Combien de fois cligne-t-on des yeux par minute en moyenne ?", 17, "fois", 6, None),
    ("insolite", "Quel pourcentage du corps d'un adulte est fait d'eau ?", 60, "%", 6, None),
    ("insolite", "Combien de litres de salive produit-on par jour ?", 1, "litre", 0.5, None),
    ("insolite", "Combien de jours peut-on survivre sans boire ?", 3, "jours", 1, "Donc bois de l'eau entre deux pintes."),
    ("insolite", "Combien de kilomètres de vaisseaux sanguins dans le corps humain ?", 100000, "km", 20000,
     "De quoi faire deux fois et demie le tour de la Terre."),
    ("insolite", "Quelle est la hauteur de la tour Eiffel ?", 330, "mètres", 15, None),
    ("insolite", "Combien de marches jusqu'au sommet de la tour Eiffel ?", 1665, "marches", 100, None),
    ("insolite", "Combien de pays sont membres de l'ONU ?", 193, "pays", 0, None),
    ("insolite", "Quel est le record de vitesse du TGV ?", 574.8, "km/h", 15, "En 2007."),
    ("insolite", "Combien d'habitants compte la France ?", 68, "millions", 3, None),
]


def questions(categories=None):
    """Les questions des categories choisies (toutes si rien n est choisi)."""
    choix = [c for c in (categories or []) if c in JP_CATEGORIES]
    if not choix:
        choix = list(JP_CATEGORIES)
    return [q for q in QUESTIONS if q[0] in choix]


def cle(q):
    """Identifiant stable d une question (pour ne pas la reposer ce soir)."""
    return q[1]
