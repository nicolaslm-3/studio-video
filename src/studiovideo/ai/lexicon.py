"""Lexiques (français / anglais) utilisés par l'analyse locale des extraits.

Ce sont des indices, pas des certitudes : ils servent à classer des propositions
que l'utilisateur vérifie toujours.
"""

CATEGORIES = {
    "rire": "Rire",
    "polarisant": "Polarisant",
    "emotion": "Émotion",
    "politique": "Politique",
}

LEXICON: dict[str, list[str]] = {
    # ce qu'on dit quand on rit ou qu'on trouve ça drôle (et ce que la transcription note des rires)
    "rire": [
        "haha", "hahaha", "hahahaha", "hihi", "héhé", "mdr", "ptdr", "jpp", "lol", "xptdr", "rires", "rire", "rit",
        "(rires)", "[rires]", "*rires*", "c'est drôle", "trop drôle", "c'est marrant", "trop marrant", "marrant",
        "drôle", "mortel", "j'en peux plus", "j'en peux pu", "mort de rire", "je suis mort", "je suis morte",
        "on est morts", "je suis dead", "dead", "je pleure", "j'ai pleuré", "pété de rire", "explosé de rire",
        "je me suis pissé dessus", "c'est une blague", "je rigole", "on rigole", "tu rigoles", "ça me tue",
        "il est fou", "elle est folle", "t'es con", "t'es bête", "n'importe quoi", "c'est quoi ça",
        "oh non", "nan mais", "la honte", "lmao", "lmfao", "rofl", "laughing", "laughs", "(laughs)",
        "[laughter]", "laughter", "hilarious", "so funny", "that's funny", "i'm dying", "i'm crying", "i can't",
    ],
    # prises de position, opinions assumées, propos qui font réagir
    "polarisant": [
        "je pense que", "à mon avis", "pour moi", "selon moi", "personnellement", "perso", "honnêtement",
        "franchement", "sincèrement", "je vais être honnête", "je vais être cash", "je vais dire un truc",
        "je vais vous dire", "je le dis", "je l'assume", "j'assume", "je m'en fous", "je m'en fiche",
        "ça va pas plaire", "ça ne va pas plaire", "on va me tomber dessus", "je vais me faire", "désolé mais",
        "désolée mais", "personne n'ose", "personne ne le dit", "tout le monde se trompe", "vous vous trompez",
        "il faut arrêter", "arrêtez de", "arrêtons de", "c'est surcoté", "surcoté", "sous-coté", "c'est nul",
        "c'est de la merde", "c'est une arnaque", "le vrai problème", "le problème c'est", "la vérité c'est",
        "qu'on se le dise", "soyons clairs", "on ne peut plus rien dire", "c'est un scandale", "c'est honteux",
        "inadmissible", "lamentable", "ridicule", "débile", "stupide", "idiot", "idiots", "escroc", "escroquerie",
        "imposture", "mensonge", "hypocrite", "hypocrisie", "unpopular opinion", "hot take", "overrated",
        "underrated", "i think", "in my opinion", "honestly", "to be honest", "let's be real", "nobody wants to hear",
        "i don't care", "trash", "scam",
    ],
    "politique": [
        # institutions et élus
        "gouvernement", "président", "présidente", "présidentielle", "ministre", "première ministre",
        "premier ministre", "assemblée", "assemblée nationale", "sénat", "sénateur", "député", "députée", "maire",
        "élu", "élus", "conseil constitutionnel", "commission européenne", "parlement", "préfet", "élysée",
        "matignon", "république", "démocratie", "constitution", "49.3", "motion de censure", "dissolution",
        "référendum",
        # partis et courants
        "parti", "gauche", "droite", "extrême droite", "extrême gauche", "lfi", "insoumis", "rn",
        "rassemblement national", "renaissance", "macronie", "républicains", "socialiste", "socialistes", "ps",
        "écologistes", "reconquête", "nupes", "nouveau front populaire", "communiste", "macron",
        "le pen", "bardella", "mélenchon", "zemmour", "attal", "barnier", "bayrou", "retailleau", "darmanin",
        "trump", "biden", "harris", "poutine", "zelensky",
        # sujets
        "loi", "projet de loi", "réforme", "élection", "élections", "électeurs", "vote", "voter", "scrutin",
        "sondage", "impôt", "impôts", "taxe", "taxes", "budget", "dette", "déficit", "retraite", "retraites",
        "immigration", "migrants", "frontières", "sécurité", "insécurité", "police", "justice", "laïcité",
        "pouvoir d'achat", "inflation", "chômage", "smic", "salaires", "services publics", "hôpital", "école",
        "écologie", "climat", "nucléaire", "agriculteurs", "grève", "manifestation", "syndicats", "europe",
        "union européenne", "otan", "guerre", "ukraine", "russie", "israël", "gaza", "palestine", "état",
        "programme", "proposition", "government", "president", "minister", "election", "policy", "tax", "taxes",
        "law", "congress", "senate", "democrats", "republicans", "vote",
    ],
    # excitation, cris, hype, et mots forts
    "emotion": [
        "allez", "allez allez", "let's go", "lets go", "go go go", "oh mon dieu", "mon dieu", "oh la la", "oh là là",
        "c'est pas possible", "ce n'est pas possible", "pas possible", "incroyable", "énorme", "dingue", "de fou",
        "c'est fou", "il l'a fait", "elle l'a fait", "on l'a fait", "quel but", "magnifique", "monstrueux",
        "historique", "légendaire", "wow", "waouh", "putain", "oh putain", "non non non", "oui oui oui",
        "vas-y", "vas y", "regarde ça", "c'est chaud",
        "trop fort", "le boss", "omg", "oh my god", "insane", "clutch", "no way", "holy", "unbelievable",
        "goal", "come on", "let's gooo",
        # émotion forte
        "honte", "scandale", "peur", "colère", "furieux", "triste", "pleurer", "bouleversant", "choqué",
        "choquant", "heureux", "fier", "horrible", "terrible", "j'ai mal", "souvenir", "jamais oublié", "amazing",
        "shocking", "scared", "angry", "sad", "proud",
    ],
    # déclarations politiques (bonus du critère Politique)
    "politique_declaration": [
        "je propose", "nous proposons", "je m'engage", "nous allons", "il faut que", "il faudra",
        "nous devons", "le gouvernement doit", "l'état doit", "je demande", "j'appelle", "je dénonce",
        "je condamne", "notre programme", "mon programme", "si je suis élu", "si nous sommes élus", "nous ferons",
        "je ferai", "we will", "i will", "we must",
    ],
    # généralisations et absolus (bonus du critère Polarisant)
    "absolus": [
        "tous les", "toutes les", "tout le monde", "jamais", "toujours", "aucun", "aucune", "le pire",
        "la pire", "le meilleur", "la meilleure", "les gens", "les jeunes", "les vieux", "les femmes", "les hommes",
        "les riches", "les pauvres", "les français", "les politiques", "les médias", "les journalistes",
        "everyone", "nobody", "never", "always", "the worst", "the best",
    ],
}

# désaccord, contradiction (réactions qui suivent une prise de position)
DISAGREE = [
    "pas d'accord", "je ne suis pas d'accord", "c'est faux", "non mais", "attendez", "laissez-moi", "au contraire",
    "je conteste", "absolument pas", "vous mentez", "n'importe quoi", "laissez-moi finir", "c'est pas vrai",
    "ce n'est pas vrai", "je vous arrête", "t'es sérieux", "sérieusement", "disagree", "that's wrong", "not true",
    "nonsense", "you're lying",
]

RESERVATIONS = ["mais", "cependant", "toutefois", "néanmoins", "par contre", "sauf que", "en revanche", "quoique",
                "but", "however", "although", "except"]

CLAIM_MARKERS = ["selon", "étude", "études", "chiffres", "statistiques", "pour cent", "%", "milliards", "millions",
                 "prouvé", "démontré", "officiel", "according to", "study", "studies", "percent", "billion",
                 "million", "proven"]

FILLERS = {"euh", "bah", "ben", "bon", "alors", "et", "mais", "donc", "enfin", "voilà", "hein", "ouais", "oui",
           "non", "genre", "en", "fait", "so", "well", "and", "but", "like", "um", "uh"}

STOPWORDS = set("""
a à ai aie ait alors as au aucun aussi autre aux avec avoir avons bon c ça car ce cela celle celles celui ces cet
cette ceux chaque ci comme comment d dans de des du donc dont elle elles en encore est et étaient était été être eu
fait faire fais faut i il ils j je jusqu l la le les leur leurs lui m ma mais me même mes moi mon n ne ni non nos
notre nous on ont ou où par pas peu peut plus pour pourquoi quand que quel quelle quelles quels qui quoi s sa sans
se sera ses si son sont sous sur t ta te tes toi ton tous tout toute toutes tu un une va vais vos votre vous vu y
voilà bah ben euh hein bon ouais oui voila truc chose choses là ici c'est j'ai il y qu'il qu'on d'un d'une n'est
monsieur madame mesdames messieurs merci bienvenue bonjour bonsoir s'il plaît plait aujourd'hui alors enfin
vraiment beaucoup toujours jamais rien quelque quelques autres faut veux veut peux dire dit vais vont fait
avait avais était étais sais sait savoir crois pense pensez parce depuis après avant juste aussi trop très bien
genre gens fois peut-être cette celle celui ceux leur leurs comme alors donc ensuite déjà était
the a an and or of to in is it that this was for on with as are be at by from i you he she we they not have has
""".split())
