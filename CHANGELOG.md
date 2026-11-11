# Changelog

## 2.3.0 — Photos gratuites pilotées depuis le dashboard — 11 novembre 2026

### Photos gratuites (ComfyUI sur ton PC)
- Nouvelle page **Photos gratuites** : détection automatique de ComfyUI, carte graphique (VRAM totale et
  libre), mémoire vive, version, nœuds nécessaires, espace disque, file d'attente.
- Passage à **Qwen-Image-Edit-2511** (workflow officiel : Edit Model Reference Method, CFGNorm, shift 3,1,
  LoRA Lightning 4 étapes). Trois profils : Qualité (fp8, 12 Go+), Léger (GGUF Q4, 8-11 Go) et Ancien
  (2509, pour garder une installation existante, reconnue automatiquement).
- **Téléchargement des modèles en un clic** dans le dossier de ComfyUI, avec progression, vitesse, temps
  restant, pause et reprise après coupure ; contrôle de l'espace disque avant de commencer.
- Installation de l'extension ComfyUI-GGUF (profil Léger), démarrage de ComfyUI (portable ou installation
  manuelle), interrompre / vider la file / libérer la mémoire vidéo.
- **Image test** (photo fournisseur du dernier produit, ou vêtement dessiné) avec la durée de génération.
- **Mode 100 % gratuit** en un clic : photos uniquement sur le PC, budget payant à 0 €, contrôle qualité
  coupable.
- Ton PC passe toujours en premier dès que ComfyUI est branché (même s'il est branché depuis le dashboard).

### Niches
- 4 nouvelles niches avec prix Depop relevés : bottes cavalières (médiane 75 €), bottes western (58 €),
  manteaux léopard (60 €), vestes à franges (70 €, à tester). 48 niches, 138 produits.
- Écartées après relevé : robes « bandage », vestes varsity, tops en cotte de mailles.

### Changements dans `.env`
- Nouveaux : `COMFY_DIR`, `COMFY_PRESET` (facultatifs, réglables dans le dashboard).
- `COMFY_UNET`, `COMFY_CLIP`, `COMFY_VAE`, `COMFY_LORA` : vides par défaut (fichiers du profil). Une ancienne
  configuration 2509 continue de fonctionner sans rien changer.
- `PHOTO_PROVIDER` : vide par défaut (ton PC d'abord).

## 2.2.0 — Depop et mode alternative — 11 novembre 2026

### Depop
- Frais Depop pour un vendeur en France (10 % + PayPal ≈ 2,9 % + 0,35 €), réglables avec `DEPOP_FEE_PCT`
  et `DEPOP_FIXED_FEE`.
- Fiche produit : prix, prix mini, frais et bénéfice sur Vinted et Depop, et annonce Depop prête à copier
  (Depop n'a pas d'API ouverte aux vendeurs en France).
- Une vente peut être enregistrée sur Depop (dashboard, ou `/vendu <id> <prix> depop` sur Telegram) : les frais
  sont déduits du bénéfice. Page Finances : tableau par plateforme.

### Bénéfice
- Nouveau réglage **coût fixe par vente** (3 € par défaut, `COST_PER_SALE`), qui remplace l'emballage à 0,50 €.
- Bénéfice minimum par défaut : **20 €**. Si ta base avait encore l'ancien défaut (30 €), il passe à 20 € une
  seule fois ; une autre valeur que tu as choisie n'est pas modifiée.
- Score des niches et des produits : bénéfice calculé sur la meilleure plateforme, frais compris.

### Niches et produits
- 8 niches de mode alternative avec les prix Depop relevés le 11/11/2026 (44 niches au total) : vestes
  « Napoléon », bottes plateforme gothiques, tenues whimsigoth en lot, et 5 niches testées puis écartées
  (corsets seuls, jeans baggy, lolita, Mary Jane, sweats à strass), visibles avec le filtre « toutes ».
- Médiéval mis à jour avec les données Depop.
- 31 produits AliExpress et Shein (129 au total), avec nombre exact de commandes et prix hors soldes.
- Détails et sources : [docs/NICHES.md](docs/NICHES.md).

### Changements dans `.env`
- `COST_PER_SALE` (nouveau, 3), `DEPOP_FEE_PCT` (nouveau, 12.9), `DEPOP_FIXED_FEE` (nouveau, 0.35).
- `MIN_PROFIT` : défaut 20. `PACKAGING_COST` n'est plus utilisé.

## 2.1.0 — version finale — 3 novembre 2026

### Niches et produits gagnants
- 5 nouvelles niches hors Noël : costumes homme mariage, robes de soirée grande taille, vestes moto homme,
  robes bohème été, vestes à poils. 36 niches au total, dont 19 à 30 € de bénéfice estimé ou plus.
- 98 produits fournisseurs (+20 depuis la 2.0) avec lien direct ; plus aucune niche sans produit.
- Score de potentiel : sans compte Vinted connecté, la demande est complétée par les preuves de vente
  du fournisseur (avis, note, ventes). Le total « vendus » de la boutique Temu a un poids limité.

### Performances
- Toutes les pages du dashboard sous 80 ms avec 2 000 produits, 800 ventes et 5 000 événements
  (avant : jusqu'à 170 ms).
- Relevés du marché lus en une requête et mis en cache (rechargés après chaque relevé, ou sous 5 s s'ils
  viennent d'un autre processus).
- Fichiers de niches et de produits lus une seule fois tant qu'ils ne changent pas.
- Page Commandes : historiques chargés en une requête au lieu d'une par commande.
- Pages compressées (gzip) ; CSS et JavaScript gardés en cache par le navigateur.

Aucun changement de base ni de `.env` : mise à jour directe depuis la 2.0.

## 2.0.0 — 3 novembre 2026

Refonte complète : audit ([docs/AUDIT.md](docs/AUDIT.md)), fiabilité, sécurité, finances, studio photo 3.0,
Market Intelligence, dashboard, Telegram et pilote automatique.

### Bugs corrigés
- Base SQLite qui pouvait se bloquer (« database is locked ») pendant les générations photo en parallèle : WAL + délai d'attente.
- Statistiques « aujourd'hui » et budget du jour décalés de 1 à 2 h (heure universelle comparée à l'heure locale).
- Un même produit pouvait être vendu deux fois (chiffre d'affaires gonflé).
- Bénéfice surestimé : frais fournisseur, port et autres frais n'étaient pas déduits.
- Le contrôle qualité gardait une photo jugée infidèle : elle est maintenant refusée.
- Une photo sans métadonnées faisait planter la fiche produit.
- Les réglages de notifications n'étaient pas enregistrés.
- Le budget IA pouvait bloquer une génération sans envoyer d'alerte.
- Annonces importées depuis Vinted avec un coût de 0 € : signalées « prix d'achat à renseigner ».

### Sécurité
- Tokens Vinted chiffrés en base (Fernet), jamais affichés dans les logs ni les exports.
- Masquage automatique des secrets dans tous les logs (tokens Telegram, clés API, cookies).
- Protection CSRF (vérification de l'origine) et en-têtes de sécurité sur le dashboard.
- Dashboard refusé sur le réseau (`0.0.0.0`) sans mot de passe.
- Bot Telegram fermé par défaut ; boutons d'un canal réservés à ses administrateurs.
- Assistant Telegram : confirmation obligatoire avant publier, vendre, préparer ou changer un réglage.
- Noms de colonnes SQL vérifiés contre le schéma ; restauration de sauvegarde limitée au dossier des sauvegardes.

### Nouveautés
- **Centre de contrôle** : 10 indicateurs réels, filtres de période (dont personnalisée), comparaison avec la
  période précédente, graphiques (bénéfice, CA, ventes par jour, boutiques, niches), produits les plus rentables,
  liste de mise en route.
- **Finances** : fiche financière par produit, journal des transactions, dépenses, bénéfice net, capital engagé,
  stock en transit, invendus, export CSV (compatible Excel) et Excel.
- **Commandes fournisseurs** : créées à chaque vente, 8 statuts, référence, prix payé, suivi, transporteur,
  notes, historique. Jamais de commande payante automatique.
- **Boutiques** : réglages propres à chaque boutique (horaires, quota, objectif, bénéfice minimum, style,
  fournisseur photo…), fiche boutique, comparaison avec la meilleure boutique par indicateur.
- **Connexion Vinted** : page dédiée pour coller et tester le token de chaque compte.
- **Moteur de prix** : coût de revient complet, prix du marché, demande, historique, bénéfice minimum 30 € par
  défaut, prix minimum rentable, niveau de confiance et explications.
- **Annonces** : 4 styles rédactionnels, anti-répétition, marque, mots-clés, caractéristiques, mention honnête des
  photos de présentation.
- **Studio photo 3.0** : analyse du vêtement (forme, couleur, fermetures, logos) utilisée dans les prompts et le
  contrôle qualité, 6 décors, 4 poses, refus automatique, historique des générations (modèle, durée, coût,
  essais, résultat), avant / après, statistiques par fournisseur, alerte de budget.
- **Market Intelligence** : score de potentiel /100 détaillé et sourcé, niveau de confiance, relevés Vinted
  historisés (prix, quartiles, favoris, marques), tendance, filtres rentables / saison.
- **Opportunités** : niches émergentes, produits sous-évalués, marques en progression, saisons à venir, forte
  demande et peu de concurrence, forte marge ; favoris et suivi de l'évolution.
- **12 nouvelles niches à forte marge** (≥ 30 € estimés par vente) au-delà de Noël : tailleurs, trench-coats,
  robes de soirée longues, abayas, bottes hautes, doudounes longues, blousons aviateur, vestes chauffantes,
  cosplay, robes de mariée civile, ski adulte, vestes simili cuir longues ; 15 nouveaux liens fournisseurs.
- **Telegram 2.0** : menu en 10 sections, tableau de bord, finances par période, paramètres (simulation, arrêt
  d'urgence), commandes à passer avec suivi, notifications par catégorie sans doublon, nouvelles questions
  pour l'assistant (boutique la plus rentable, invendus, coûts IA du mois…).
- **Pilote automatique** : réglages par tâche (activation, horaire, réessais, alerte) modifiables sans
  redémarrer, journal d'exécution, historique des erreurs, pause globale, arrêt d'urgence, mode simulation,
  rapport financier et sauvegarde quotidiens.
- **Tâches de fond** persistantes (identifiant, statut, début, fin, résultat, erreur), anti-doublon, reprise
  propre après redémarrage.
- **Activité** : journal complet filtrable (catégorie, boutique, statut, date, texte) avec pagination.
- **Dashboard** : nouveau design noir / anthracite / blanc, thème clair, recherche globale (Ctrl+K), raccourcis
  clavier, tableaux triables, pagination, actions groupées, états vides, notifications visuelles.
- **CLI** : `autopilot`, `backup`, `check`.
- **Documentation** : guides Windows, VPS, migration ; audit technique.

### Compatibilité
- Migration automatique et non destructive, sauvegarde avant migration (voir [docs/MIGRATION.md](docs/MIGRATION.md)).
- Commandes Telegram et routes du dashboard de la 1.x conservées.
- Nouvelles dépendances : `cryptography`, `openpyxl`.

### Tests
69 tests automatisés (aucun appel réseau) : base, concurrence, migration, finances, exports, commandes, tâches,
sauvegardes, planning, prix, marché, opportunités, studio photo, fournisseurs d'images, ComfyUI, Vinted simulé,
Telegram, assistant, toutes les pages et formulaires du dashboard, CSRF, mot de passe.

## 1.x — 1er au 2 novembre 2026
Première version : photos IA, fiches, dashboard, bot Telegram, connexion Vinted, pilote automatique, studio
photo Gemini / OpenAI / ComfyUI.
