"""
Paires de mots d Undercover.

Une paire = deux mots PROCHES : assez pour que l Undercover ne se sache pas
different des Civils, assez eloignes pour qu un indice bien choisi le trahisse.
Le moteur tire une paire, puis tire a pile ou face lequel des deux mots
revient aux Civils.

Deux familles :
  classique  tout public (bouffe, objets, lieux, ciné, sport, soirée...)
  trash      humour noir, sexe, glauque, gênant : pour les soirées qui
             s assument. Jamais d insulte ni de cible facile.
"""

MOTS = {
    "classique": {
        "Bouffe": [
            ("Pizza", "Quiche"), ("Croissant", "Pain au chocolat"), ("Burger", "Kebab"),
            ("Sushi", "Maki"), ("Crêpe", "Gaufre"), ("Frites", "Chips"), ("Raclette", "Fondue"),
            ("Tacos", "Burrito"), ("Ketchup", "Mayonnaise"), ("Nutella", "Confiture"),
            ("Pâtes", "Riz"), ("Glace", "Sorbet"), ("Beurre", "Margarine"), ("Miel", "Sirop d'érable"),
            ("Camembert", "Brie"), ("Saucisson", "Jambon"), ("Moutarde", "Wasabi"),
            ("Citron", "Pamplemousse"), ("Fraise", "Framboise"), ("Banane", "Plantain"),
            ("Pop-corn", "Cacahuètes"), ("Cookie", "Brownie"), ("Macaron", "Meringue"),
            ("Omelette", "Œuf au plat"), ("Soupe", "Velouté"), ("Lasagnes", "Hachis parmentier"),
            ("Couscous", "Paella"), ("Tiramisu", "Panna cotta"), ("Avocat", "Guacamole"),
            ("Baguette", "Pain de mie"),
        ],
        "Boissons et soirée": [
            ("Bière", "Cidre"), ("Vin rouge", "Vin rosé"), ("Champagne", "Prosecco"),
            ("Whisky", "Rhum"), ("Vodka", "Gin"), ("Mojito", "Caïpirinha"), ("Café", "Thé"),
            ("Coca", "Pepsi"), ("Pastis", "Ricard"), ("Tequila", "Mezcal"), ("Spritz", "Sangria"),
            ("Jägermeister", "Get 27"), ("Limonade", "Diabolo"), ("Shot", "Cocktail"),
            ("Boîte de nuit", "Bar"), ("Karaoké", "Blind test"), ("Apéro", "Brunch"),
            ("Beer pong", "Flip cup"), ("DJ", "Barman"), ("After", "Before"),
        ],
        "Objets": [
            ("Fourchette", "Cuillère"), ("Stylo", "Crayon"), ("Parapluie", "Parasol"),
            ("Oreiller", "Coussin"), ("Lunettes", "Lentilles"), ("Montre", "Bracelet"),
            ("Brosse à dents", "Fil dentaire"), ("Bougie", "Lampe torche"), ("Valise", "Sac à dos"),
            ("Ciseaux", "Cutter"), ("Clé", "Cadenas"), ("Miroir", "Vitre"), ("Chaise", "Tabouret"),
            ("Canapé", "Fauteuil"), ("Couette", "Plaid"), ("Briquet", "Allumettes"),
            ("Télécommande", "Manette"), ("Casque", "Écouteurs"), ("Tasse", "Verre"),
            ("Agrafeuse", "Trombone"), ("Balai", "Aspirateur"), ("Savon", "Gel douche"),
        ],
        "Lieux": [
            ("Plage", "Piscine"), ("Montagne", "Colline"), ("Hôpital", "Clinique"),
            ("Bibliothèque", "Librairie"), ("Cinéma", "Théâtre"), ("Zoo", "Aquarium"),
            ("Aéroport", "Gare"), ("Supermarché", "Marché"), ("École", "Collège"),
            ("Église", "Cathédrale"), ("Prison", "Commissariat"), ("Camping", "Hôtel"),
            ("Musée", "Galerie d'art"), ("Stade", "Gymnase"), ("Forêt", "Jungle"),
            ("Paris", "Londres"), ("Italie", "Espagne"), ("Désert", "Banquise"),
            ("Boulangerie", "Pâtisserie"), ("Mairie", "Préfecture"),
        ],
        "Animaux": [
            ("Chat", "Tigre"), ("Chien", "Loup"), ("Lion", "Guépard"), ("Dauphin", "Requin"),
            ("Abeille", "Guêpe"), ("Crocodile", "Alligator"), ("Lapin", "Lièvre"),
            ("Corbeau", "Pie"), ("Grenouille", "Crapaud"), ("Cheval", "Âne"),
            ("Papillon", "Libellule"), ("Pingouin", "Manchot"), ("Souris", "Rat"),
            ("Escargot", "Limace"), ("Phoque", "Otarie"), ("Hibou", "Chouette"),
            ("Vache", "Chèvre"), ("Moustique", "Mouche"),
        ],
        "Sport et loisirs": [
            ("Football", "Rugby"), ("Tennis", "Badminton"), ("Ski", "Snowboard"),
            ("Basket", "Handball"), ("Natation", "Plongée"), ("Vélo", "Trottinette"),
            ("Boxe", "Judo"), ("Golf", "Mini-golf"), ("Échecs", "Dames"),
            ("Yoga", "Pilates"), ("Surf", "Skate"), ("Bowling", "Pétanque"),
            ("Poker", "Blackjack"), ("Monopoly", "Uno"), ("Course à pied", "Marathon"),
            ("Formule 1", "Rallye"), ("Escalade", "Randonnée"), ("Fléchettes", "Billard"),
        ],
        "Ciné, séries, musique": [
            ("Harry Potter", "Le Seigneur des anneaux"), ("Star Wars", "Star Trek"),
            ("Batman", "Superman"), ("Netflix", "YouTube"), ("Titanic", "Avatar"),
            ("Disney", "Pixar"), ("Shrek", "Le Roi lion"), ("Rap", "Rock"),
            ("Guitare", "Violon"), ("Piano", "Batterie"), ("Concert", "Festival"),
            ("Mario", "Sonic"), ("Fortnite", "Minecraft"), ("Spotify", "Deezer"),
            ("Instagram", "TikTok"), ("Game of Thrones", "Vikings"), ("Zombie", "Vampire"),
            ("Sorcière", "Fée"), ("Pirate", "Viking"), ("Astérix", "Tintin"),
        ],
        "Métiers et gens": [
            ("Médecin", "Infirmier"), ("Pompier", "Policier"), ("Boulanger", "Pâtissier"),
            ("Prof", "Directeur"), ("Avocat", "Juge"), ("Pilote", "Hôtesse de l'air"),
            ("Chirurgien", "Dentiste"), ("Plombier", "Électricien"), ("Cuisinier", "Serveur"),
            ("Youtubeur", "Influenceur"), ("Astronaute", "Pilote de chasse"),
            ("Magicien", "Clown"), ("Facteur", "Livreur"), ("Ingénieur", "Technicien"),
            ("Grand-mère", "Belle-mère"), ("Voisin", "Colocataire"),
        ],
        "Quotidien": [
            ("Douche", "Bain"), ("Réveil", "Alarme"), ("Lundi", "Dimanche"),
            ("Été", "Printemps"), ("Neige", "Grêle"), ("Soleil", "Lune"),
            ("Anniversaire", "Mariage"), ("Noël", "Nouvel an"), ("Halloween", "Carnaval"),
            ("Vacances", "Week-end"), ("Sieste", "Grasse matinée"), ("Selfie", "Photo de groupe"),
            ("SMS", "Mail"), ("Uber", "Taxi"), ("Métro", "Tramway"), ("Avion", "Hélicoptère"),
            ("Bateau", "Sous-marin"), ("Voiture", "Moto"), ("Ordinateur", "Tablette"),
            ("Wi-Fi", "Bluetooth"), ("Examen", "Partiel"), ("Stage", "Alternance"),
        ],
    },
    "trash": {
        "Lendemains difficiles": [
            ("Gueule de bois", "Coma éthylique"), ("Vomi", "Diarrhée"), ("Pet", "Rot"),
            ("Black-out", "Trou de mémoire"), ("Constipation", "Gastro"), ("Hémorroïdes", "Mycose"),
            ("Morning after", "Walk of shame"), ("Doliprane", "Smecta"), ("Urgences", "SOS Médecins"),
            ("Cuvette des toilettes", "Bidet"), ("Haleine de chacal", "Odeur de pieds"),
        ],
        "Sous la couette": [
            ("Préservatif", "Pilule du lendemain"), ("Tinder", "Grindr"), ("Plan cul", "Ex"),
            ("Strip-tease", "Lap dance"), ("Menottes", "Fouet"), ("Viagra", "Lubrifiant"),
            ("Sextoy", "Poupée gonflable"), ("Plan à trois", "Échangisme"), ("OnlyFans", "Pornhub"),
            ("Gynécologue", "Proctologue"), ("Suçon", "Morsure"), ("Cocu", "Amant"),
            ("Nudiste", "Exhibitionniste"), ("Dick pic", "Nude"), ("Capote percée", "Test de grossesse"),
            ("Sex shop", "Love hôtel"), ("Préliminaires", "Câlin"), ("Fantasme", "Rêve érotique"),
        ],
        "Humour noir": [
            ("Enterrement", "Mariage"), ("Cercueil", "Urne funéraire"), ("Corbillard", "Ambulance"),
            ("Testament", "Héritage"), ("Croque-mort", "Médecin légiste"), ("Autopsie", "Embaumement"),
            ("Cimetière", "Crématorium"), ("Fantôme", "Zombie"), ("Tueur en série", "Tueur à gages"),
            ("Chaise électrique", "Guillotine"), ("Pierre tombale", "Couronne de fleurs"),
            ("Veuve", "Divorcée"), ("Maison de retraite", "Hôpital psychiatrique"),
            ("Exorcisme", "Séance de spiritisme"), ("Momie", "Squelette"),
        ],
        "Border": [
            ("Garde à vue", "Prison"), ("Joint", "Chicha"), ("Cocaïne", "Ecstasy"),
            ("Dealer", "Proxénète"), ("Braquage", "Cambriolage"), ("Arnaque", "Pyramide de Ponzi"),
            ("Pot-de-vin", "Fraude fiscale"), ("Crise de la quarantaine", "Divorce"),
            ("Belle-mère", "Ex de son mec"), ("Radar", "Alcootest"), ("Gilet jaune", "Black bloc"),
            ("Secte", "Gourou"), ("Paparazzi", "Stalker"), ("Mafia", "Cartel"),
            ("Faux diplôme", "Plagiat"), ("Doigt d'honneur", "Bras d'honneur"),
            ("Ghosting", "Friendzone"), ("Toxic", "Red flag"), ("Sugar daddy", "Cougar"),
            ("Bodycount", "Liste d'ex"),
        ],
    },
}

UC_FAMILLES = ("classique", "trash", "melange")


def paires(famille):
    """Toutes les paires [(categorie, mot_a, mot_b)] d une famille de mots."""
    if famille == "melange":
        noms = ("classique", "trash")
    elif famille in MOTS:
        noms = (famille,)
    else:
        noms = ("classique",)
    out = []
    for nom in noms:
        for categorie, liste in MOTS[nom].items():
            for a, b in liste:
                out.append((categorie, a, b))
    return out
