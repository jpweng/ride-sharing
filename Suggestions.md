Suggestions, par ordre de priorité
Rendre la démo vivante. Charger des trajets de démo et les régénérer chaque nuit avec des dates futures. Sans ça, les trajets passent et la démo se vide d'elle-même. Purger au passage les données réelles saisies par les visiteurs.
Afficher clairement que c'est une démo. Un bandeau « Démo – ne saisissez pas de données réelles », une courte mention de confidentialité (la nouvelle loi suisse sur la protection des données, nLPD, s'applique dès qu'on collecte des noms), et éventuellement noindex pour que les moteurs de recherche ne référencent pas le site.
Aligner l'identité sur « Ride and Rail ». Le nom, le titre, et une page d'accueil qui explique le concept en 30 secondes : la voiture jusqu'à une gare, puis le train. C'est ce qui distingue le projet de BlaBlaCar, et aujourd'hui l'interface ne le met pas en avant.
Traduire en français et en allemand, l'anglais en option. Le frontend est petit (environ 730 lignes de JavaScript), donc c'est faisable sans framework.
Surveiller la disponibilité à faible coût. Une alerte CloudWatch par e-mail si /api/health ne répond plus, pour environ 1 $/mois. Aujourd'hui, une panne passerait inaperçue.
Questions
Public : à qui sert la démo ? Investisseurs ou partenaires (CFF, cantons), testeurs réels, portfolio, projet d'études ? Ça change le niveau de finition et l'urgence de la confidentialité.
Utilisation réelle : veux-tu que de vraies personnes publient des trajets, ou seulement qu'on puisse la montrer ? Dans le premier cas, il faudra une vraie connexion (un lien envoyé par e-mail, par exemple) avant tout le reste.
Langues : français seulement, français et allemand, ou les trois langues nationales plus l'anglais ?
Nom : « Ride and Rail » est-il le nom définitif ? As-tu un logo ou des couleurs, ou on garde le style inspiré des CFF ?
Échéance : y a-t-il une date de présentation ? Ça m'aiderait à choisir entre peaufiner l'existant et ajouter des fonctionnalités.
Si tu veux un point de départ rapide sans attendre tes réponses, je ferais les points 1 et 2. Ils sont petits, et ils rendent le site présentable et sûr à partager dès aujourd'hui.



