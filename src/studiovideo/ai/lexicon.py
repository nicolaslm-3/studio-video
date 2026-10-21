"""Lexiques (français / anglais) utilisés par l'analyse locale des moments forts.

Ce sont des indices, pas des certitudes : ils servent à classer des propositions
que l'utilisateur vérifie toujours.
"""

CATEGORIES = {
    "humour": "Humour",
    "debat": "Débat et désaccord",
    "politique": "Politique",
    "emotion": "Émotion",
    "pedagogie": "Pédagogie",
    "potentiel": "Potentiel d'intérêt",
}

LEXICON: dict[str, list[str]] = {
    "humour": [
        "haha", "hahaha", "mdr", "ptdr", "lol", "rires", "rire", "drôle", "drole", "marrant", "blague", "délire",
        "absurde", "n'importe quoi", "c'est une blague", "je rigole", "on rigole", "trop fort", "génial",
        "funny", "joke", "laugh", "laughing", "hilarious", "kidding", "crazy",
    ],
    "debat": [
        "pas d'accord", "je ne suis pas d'accord", "c'est faux", "faux", "non mais", "attendez", "laissez-moi",
        "vous dites", "tu dis", "au contraire", "contrairement", "je conteste", "absolument pas", "mensonge",
        "vous mentez", "n'importe quoi", "pardon mais", "excusez-moi", "laissez-moi finir", "c'est pas vrai",
        "ce n'est pas vrai", "permettez", "je vous arrête", "disagree", "that's wrong", "not true", "wrong",
        "let me finish", "hold on", "nonsense", "you're lying",
    ],
    "politique": [
        "gouvernement", "président", "ministre", "assemblée", "sénat", "loi", "réforme", "élection", "électeurs",
        "vote", "parti", "gauche", "droite", "impôt", "impôts", "taxe", "budget", "dette", "retraite", "retraites",
        "immigration", "sécurité", "pouvoir d'achat", "chômage", "smic", "salaire", "europe", "état", "propose",
        "proposition", "programme", "député", "maire", "république", "démocratie", "government", "president",
        "minister", "election", "policy", "tax", "taxes", "law", "congress", "vote",
    ],
    "emotion": [
        "incroyable", "honte", "scandale", "scandaleux", "peur", "colère", "furieux", "triste", "pleurer",
        "bouleversant", "choqué", "choquant", "heureux", "fier", "magnifique", "horrible", "terrible", "j'ai mal",
        "mon père", "ma mère", "mon fils", "ma fille", "souvenir", "jamais oublié", "amazing", "shocking",
        "ashamed", "scared", "angry", "sad", "proud", "unbelievable",
    ],
    "pedagogie": [
        "c'est-à-dire", "par exemple", "en fait", "la raison", "parce que", "c'est pour ça", "définition",
        "il faut", "il ne faut pas", "conseil", "astuce", "comment", "pourquoi", "expliquer", "explique",
        "première chose", "deuxième", "ensuite", "enfin", "en résumé", "retenir", "le secret", "méthode",
        "for example", "because", "the reason", "here's how", "tip", "first", "second", "in short",
    ],
    "potentiel": [
        "personne ne sait", "personne ne vous dit", "le secret", "la vérité", "vous ne croirez pas", "jamais",
        "première fois", "record", "révélation", "révèle", "exclusif", "attention", "imaginez", "savez-vous",
        "saviez-vous", "le problème", "le pire", "le meilleur", "incroyable", "nobody knows", "the truth",
        "secret", "never", "first time", "imagine", "did you know", "the problem",
    ],
}

RESERVATIONS = ["mais", "cependant", "toutefois", "néanmoins", "par contre", "sauf que", "en revanche", "quoique",
                "but", "however", "although", "except"]

CLAIM_MARKERS = ["selon", "étude", "études", "chiffres", "statistiques", "pour cent", "%", "milliards", "millions",
                 "prouvé", "démontré", "officiel", "according to", "study", "studies", "percent", "billion",
                 "million", "proven"]

STOPWORDS = set("""
a à ai aie ait alors as au aucun aussi autre aux avec avoir avons bon c ça car ce cela celle celles celui ces cet
cette ceux chaque ci comme comment d dans de des du donc dont elle elles en encore est et étaient était été être eu
fait faire fais faut i il ils j je jusqu l la le les leur leurs lui m ma mais me même mes moi mon n ne ni non nos
notre nous on ont ou où par pas peu peut plus pour pourquoi quand que quel quelle quelles quels qui quoi s sa sans
se sera ses si son sont sous sur t ta te tes toi ton tous tout toute toutes tu un une va vais vos votre vous vu y
voilà bah ben euh hein bon ouais oui voila truc chose choses là ici c'est j'ai il y qu'il qu'on d'un d'une n'est
monsieur madame mesdames messieurs merci bienvenue bonjour bonsoir s'il plaît plait aujourd'hui alors enfin
vraiment beaucoup toujours jamais rien quelque quelques autres faut veux veut peux dire dit vais vont fait
the a an and or of to in is it that this was for on with as are be at by from i you he she we they not have has
""".split())
