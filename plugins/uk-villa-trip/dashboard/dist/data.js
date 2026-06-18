(function () {
  "use strict";

  const ns = window.UkVillaTrip = window.UkVillaTrip || {};

  ns.DATA = {
    places: {
      london: {
        name: "Londres",
        coords: [51.5074456, -0.1277653],
        note: "Arrivée, buffer jetlag, Tower/Thames, British Museum, soirée pub/marché.",
      },
      stonehenge: {
        name: "Excursion Stonehenge",
        coords: [51.1788293, -1.826183],
        note: "Must-have. À garder comme excursion depuis Londres via Salisbury pour éviter la friction d’hébergement.",
      },
      villa: {
        name: "Villa Park / Birmingham",
        coords: [52.5091264, -1.8850354],
        note: "Pèlerinage principal: Aston Villa à Villa Park.",
      },
      york: {
        name: "York",
        coords: [53.9656579, -1.0743052],
        note: "Ville médiévale, pause rail-friendly entre les Midlands et l’Écosse.",
      },
      edinburgh: {
        name: "Édimbourg",
        coords: [55.9533456, -3.1883749],
        note: "Base écossaise: ville + point de départ pour excursion.",
      },
      glencoe: {
        name: "Glencoe / excursion Highlands",
        coords: [56.6827647, -5.1014552],
        note: "Drame Highlands guidé sans voiture. Journée de 10–13h, snacks et météo existentielle inclus.",
      },
    },

    scenarios: {
      md2: {
        title: "Scénario A: Villa à domicile le 13/14 oct.",
        color: "#f5c15c",
        dashArray: null,
        route: ["london", "stonehenge", "london", "villa", "york", "edinburgh", "glencoe", "edinburgh"],
        summary: ["Londres", "Excursion Stonehenge", "Birmingham / Villa Park", "York optionnel", "Édimbourg", "Excursion Highlands", "Retour via EDI/MAN"],
        days: [
          ["Oct 8–11", "Londres + Stonehenge", "Buffer d’arrivée avec Tower/Thames, British Museum, soirée pub/marché et une excursion Stonehenge via Salisbury."],
          ["Oct 11–15", "Bloc Birmingham / Villa Park", "Positionnement pour un weekend PL si utile + LDC J2 le 13/14 oct. si Villa est à domicile. Villa Park est l’ancre."],
          ["Oct 15–16", "York optionnel", "Garder comme arrêt compact en train seulement si le calendrier laisse du slack; sinon direct Birmingham → Édimbourg."],
          ["Oct 16–21", "Édimbourg + Highlands", "Base à Édimbourg, un tour guidé Highlands/Glencoe, assez de slack pour que la pluie tue pas toute l’affaire."],
          ["Oct 21–22", "Buffer de sortie", "Vol depuis Édimbourg si le prix a de l’allure; sinon train vers Manchester/Londres selon le calcul des vols."],
        ],
      },
      md3: {
        title: "Scénario B: Villa à domicile le 20/21 oct.",
        color: "#58d68d",
        dashArray: "8 8",
        route: ["london", "stonehenge", "london", "edinburgh", "glencoe", "edinburgh", "villa"],
        summary: ["Londres", "Excursion Stonehenge", "Édimbourg", "Excursion Highlands", "Birmingham / Villa Park", "Retour via MAN/BHX/LHR"],
        days: [
          ["Oct 8–12", "Londres + Stonehenge", "Début smooth: Tower/Thames, British Museum, soirée pub/marché et Stonehenge en excursion sans voiture via Salisbury."],
          ["Oct 12–17", "Édimbourg + Highlands", "Monter au nord avant le climax Villa. Base à Édimbourg + tour guidé Highlands/Glencoe. York saute probablement ici."],
          ["Oct 17–22", "Bloc Birmingham / Villa Park", "Retour vers le sud pour le weekend PL et la LDC J3 le 20/21 oct. si Villa est à domicile. C’est la version “climax LDC”."],
          ["Oct 22", "Sortie", "Préférer un départ de Birmingham/Manchester si les prix se comportent. Londres seulement si c’est significativement moins cher."],
        ],
      },
    },

    decisions: [
      ["Échec à éviter #1", "rater Villa Park", true],
      ["Échec à éviter #1.1", "scam / pas de chambre / overpay de panique", true],
      ["Budget", "C$4k–5.5k sur place, hors vols + billets de match", false],
      ["Hôtels", "réservations remboursables tôt dans toutes les villes pivots", false],
      ["Réservation", "site direct de l’hôtel préféré; Booking.com/Expedia pour repérage/backup", false],
      ["Vols", "YQB si ça a de l’allure; YUL si ça sauve beaucoup d’argent/temps", false],
      ["Agence", "pas de full-agence; consultation ciblée seulement si elle améliore vols/support/flexibilité", false],
      ["Rythme", "sous-planifié, 1 ancre/jour", false],
      ["Stonehenge", "excursion depuis Londres; York devient optionnel si le calendrier est serré", false],
    ],

    postPremierLeaguePlan: [
      ["Dim. 21 juin", "Lire le calendrier PL de Villa", "Identifier les matchs à domicile autour des weekends 10/11, 17/18 et 24/25 octobre. Ne pas panic-buy les vols avant d’avoir vu la géométrie réelle.", true],
      ["Étape 1", "Choisir le squelette A/B", "Si Villa home tôt: Londres → Birmingham → Édimbourg. Si Villa home tard: Londres → Édimbourg → Birmingham. Garder York optionnel, pas sacré.", true],
      ["Étape 2", "Poser les filets réversibles", "Réserver des hôtels remboursables à Londres, Birmingham centre/New Street et Édimbourg/Waverley-New Town. Ça protège contre les hausses locales sans te piéger.", true],
      ["Étape 3", "Surveiller les vols 3–7 jours", "Comparer YQB et YUL vers Londres/Manchester/Birmingham/Édimbourg, idéalement open-jaw. Acheter seulement si le prix et les horaires sont bons.", false],
      ["Seuil rough", "Vols Canada → UK", "YUL sous ~C$900–1000: bon signal. YQB sous ~C$1100–1300 avec connexions raisonnables: acceptable. C$1400+: attendre sauf routing parfait.", false],
      ["Gate août", "Finaliser avec la LDC", "Le tirage/calendrier Champions League verrouille le vrai climax Villa Park. Avant ça, garder les hôtels flexibles et éviter les achats foot douteux.", true],
    ],

    agencyDecision: [
      ["Verdict", "Pas de voyage full-agence", "Le voyage est fixture-driven, flexible et centré Villa Park; une agence classique risque de lisser ça en forfait plus cher et moins agile.", true],
      ["Quand ça vaut la peine", "Consultation ciblée pour les vols", "Utile si elle améliore un routing YQB/YUL open-jaw, donne un vrai support en cas de pépin, ou bat le prix direct frais inclus.", false],
      ["À garder DIY", "Hôtels, trains UK, plan quotidien", "Réserve les hôtels remboursables toi-même; trains plus tard quand la route est fixée; plan volontairement sous-planifié.", false],
      ["Billets Villa", "Officiel ou autorisé seulement", "Priorité au club, membership/hospitality officielle ou revendeur autorisé vérifiable. Éviter les touts/scalpers: billet void = tragédie premium.", true],
      ["Questions agence", "Frais, modifs, support, open-jaw", "Demander les frais de service/modification/annulation, qui gère les problèmes, si l’open-jaw est pénalisé, et preuve officielle pour tout billet de match.", false],
      ["Protection Québec", "FICAV si agence enregistrée", "Un agent québécois enregistré peut ajouter la protection FICAV/OPC sur les services admissibles. Ce n’est pas magique, mais c’est une vraie couche de sécurité.", false],
    ],

    hotelZones: [
      ["Londres", "Priorité: bordure King’s Cross / St Pancras / Euston", "Meilleure utilité de route: Euston pour Birmingham, King’s Cross pour York/Édimbourg, Tube facile. Backup avec belle vibe: London Bridge/Borough. Option plus fancy/chère: Covent Garden."],
      ["Birmingham", "Priorité: centre-ville / New Street / Grand Central", "N’optimise pas pour dormir à côté de Villa Park. Dors central, transit/taxi vers le match. C’est le move anti-scam et anti-arrivée tardive.", true],
      ["York", "Optionnel: gare / Micklegate / dans les murs", "Garde seulement si le calendrier de Villa laisse du slack. Si inclus, priorise la marche facile vers la gare + vieux centre; évite le lodging cute-mais-loin qui ajoute de la friction taxi."],
      ["Édimbourg", "Priorité: Waverley / New Town / bordure Old Town", "Meilleur pour l’arrivée en train, le pickup de tour Highlands, et éviter de tirer ta valise en montée comme une sous-intrigue victorienne tragique. Grassmarket/Royal Mile si le prix se comporte."],
    ],

    cityAnchors: [
      ["Londres", "Tower/Thames + British Museum + soirée pub/marché", "Stonehenge est l’excursion incontournable via Salisbury; Londres reste simple, pas saturé en musées."],
      ["Birmingham", "Pèlerinage Villa + canaux/pub/reset", "Pas de tourisme par obligation. Birmingham existe pour protéger l’énergie de matchday.", true],
      ["York", "Arrêt compact optionnel en train", "Seulement si le calendrier de Villa laisse du slack; sinon on saute proprement."],
      ["Édimbourg", "Une ancre urbaine + une journée météo flexible", "Old Town/Royal Mile/extérieur du château ou Calton Hill/Arthur’s Seat selon la météo et les jambes."],
      ["Highlands", "Glencoe + Glenfinnan, longue journée cinématique", "À réserver comme tour guidé sans voiture depuis Édimbourg; garder une marge de récupération autour."],
    ],
  };
})();
