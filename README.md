# Vinted Drop 2.3

Plateforme de gestion pour boutiques Vinted en dropshipping : boutiques, produits, photos IA, annonces,
ventes, commandes fournisseurs, finances, analyse du marché et pilote automatique, depuis un dashboard web
et un bot Telegram.

> Nouveau en 2.3 (photos gratuites gérées depuis le dashboard, nouvelles niches) : voir [CHANGELOG.md](CHANGELOG.md). Tu viens de la 1.x ? Suis [docs/MIGRATION.md](docs/MIGRATION.md).

## Ce que fait l'outil

| Domaine | Fonctions |
|---|---|
| **Centre de contrôle** | CA, bénéfice net, ventes, annonces actives, brouillons, capital engagé, marge, panier moyen, dépenses fournisseurs, coûts IA. Filtres aujourd'hui / 7 j / 30 j / 90 j / année / période personnalisée, comparaison avec la période précédente. Chiffres en direct. |
| **Boutiques** | Environnement par boutique : compte Vinted, niche, modèle photo, style d'annonce, horaires de publication, quota, objectif mensuel, bénéfice minimum. Fiche boutique et page de comparaison. |
| **Connexion Vinted** | Page dédiée pour coller le token (cookie de session) de chaque compte, test de connexion, apprentissage d'un modèle d'annonce. Token **chiffré** en base. |
| **Photos gratuites** | Page dédiée pour ComfyUI sur ton PC : détection, carte graphique, choix du modèle (Qwen-Image-Edit-2511 qualité ou léger 8 Go), téléchargement des modèles en un clic avec reprise, démarrage de ComfyUI, image test, file d'attente, mode 100 % gratuit. |
| **Studio photo IA** | Ton PC (ComfyUI, gratuit) → Gemini → OpenAI. Analyse du vêtement, nettoyage de l'image fournisseur, 5 prises de vue, décors et poses, modèle récurrent par boutique, **contrôle qualité** contre l'image d'origine (photo refusée si infidèle), historique des générations, avant / après, budget journalier avec alerte. |
| **Annonces** | Titre, description, caractéristiques, mots-clés, catégorie, marque. 4 styles rédactionnels, anti-répétition. Prix conseillé (coût complet, marché, demande, historique, bénéfice minimum) avec niveau de confiance. |
| **Depop** | Prix Depop conseillé (même bénéfice qu'au prix Vinted, frais 10 % + PayPal compris), annonce Depop prête à copier, ventes Depop enregistrées avec leurs frais, finances par plateforme. |
| **Ventes et commandes** | Ventes détectées sur Vinted, commande fournisseur créée à chaque vente (à acheter → commandé → expédié → livré, problème, annulé), référence, suivi, notes, historique. **Aucun achat automatique.** |
| **Finances** | Fiche financière par produit, journal des transactions, dépenses, bénéfice net (après coûts IA et dépenses), capital engagé, invendus, export CSV et Excel. |
| **Market Intelligence** | Score de potentiel /100 expliqué (demande, concurrence, marge, saison, tendance, tes résultats) avec niveau de confiance, relevés Vinted historisés, marques, 48 niches (dont mode alternative, bottes cavalières et western…), 138 produits fournisseurs avec lien direct, prix Depop relevés, preuves de vente fournisseur dans le score. Page **Opportunités** avec favoris et suivi. |
| **Telegram** | Menu en 10 sections, notifications utiles sans doublon (ventes, offres, messages, publications, token expiré, budget, opportunités…), assistant en langage naturel qui demande confirmation avant toute action importante. |
| **Pilote automatique** | Chaque tâche réglable (activée, horaire, réessais, alerte), journaux d'exécution, historique des erreurs, pause globale, **arrêt d'urgence**, **mode simulation**. Validation humaine par défaut pour les opérations sensibles. |
| **Fiabilité** | Base SQLite en WAL, tâches de fond traçables sans doublon, sauvegarde automatique chaque nuit et avant migration, restauration testée, page Activité filtrable. |

## Installation

- **Windows** : [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)
- **VPS Linux (24h/24)** : [docs/VPS.md](docs/VPS.md)
- **Photos gratuites sur ton PC** : [docs/PHOTOS_GRATUITES.md](docs/PHOTOS_GRATUITES.md)

En bref :

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

(sur Linux / macOS : `source .venv/bin/activate`)

```bash
pip install -r requirements.txt
```

Copie `.env.example` en `.env`, remplis au moins une clé IA et le bot Telegram, puis :

```bash
python -m vinted_drop.cli all
```

Le dashboard s'ouvre sur http://localhost:8000. La liste « Mise en route » de l'accueil indique ce qu'il reste à configurer.

## Commandes utiles

| Commande | Rôle |
|---|---|
| `python -m vinted_drop.cli all` | dashboard + bot Telegram + pilote automatique |
| `python -m vinted_drop.cli dashboard` | dashboard seul |
| `python -m vinted_drop.cli autopilot` | pilote automatique seul, sans Telegram |
| `python -m vinted_drop.cli check` | vérifie la configuration |
| `python -m vinted_drop.cli backup` | sauvegarde la base (`--restore fichier.db` pour restaurer) |
| `python -m pytest -q` | lance les 95 tests (aucun appel réseau) |

Raccourcis du dashboard : `Ctrl K` ou `/` recherche globale, `g` puis `a` accueil, `p` produits, `s` studio,
`v` ventes, `c` commandes, `f` finances, `b` boutiques, `m` marché, `o` opportunités, `t` activité, `r` pilote.

## Telegram

`/menu` ouvre le menu : Dashboard, Finances, Boutiques, Produits, Studio photo, Niches, Produits gagnants,
Pilote automatique, Statistiques, Paramètres. Tu peux aussi écrire normalement :
« combien ai-je gagné cette semaine ? », « quelle est ma boutique la plus rentable ? »,
« montre mes 10 produits les plus performants », « combien j'ai dépensé en photos ce mois-ci ? »,
« prépare les annonces en attente ». Les actions importantes affichent ✅ Confirmer / ❌ Annuler.

Autres commandes : `/finances`, `/commandes`, `/studio`, `/couts`, `/niches`, `/gagnants`, `/auto`, `/parametres`,
`/vendu <id> <prix> depop` (vente Depop), `/stop` (arrêt d'urgence), `/reprendre`, `/aide`.

## Limites à connaître

- **Vinted n'a pas d'API officielle.** La connexion utilise les adresses du site quand tu es connecté
  (`vinted_drop/vinted_client.py`, `ENDPOINTS`). Elles ont été testées contre un **faux Vinted**, jamais sur un
  vrai compte : vérifie d'abord avec le **mode simulation**, puis une publication manuelle. Aucun contournement
  de CAPTCHA ou d'anti-robot n'est utilisé ; si Vinted refuse l'accès depuis un serveur, lance le bot depuis ton PC.
- **Conditions d'utilisation** : l'automatisation peut être contraire aux conditions de Vinted, qui peut
  restreindre un compte. La revente avec bénéfice est une activité commerciale : micro-entreprise et compte
  Vinted Pro nécessaires. Vinted transmet aux impôts les comptes au-delà de 30 ventes ou 2 000 € par an.
- **Photos IA** : le contrôle qualité réduit les erreurs mais ne remplace pas ta vérification. La mention
  « Photos de présentation réalisées à partir des photos du produit » est ajoutée par défaut aux descriptions.
- **Market Intelligence** : sans compte Vinted connecté, les scores reposent sur des estimations (confiance « faible »).
- **Gemini / OpenAI / ComfyUI** : intégrations conformes à la documentation officielle de novembre 2026, testées
  avec des simulations ; le premier essai réel se fait avec tes clés.

## Structure

```
vinted_drop/
  config.py         variables d'environnement
  db.py             base SQLite, migrations, statistiques
  backup.py         sauvegardes / restauration
  security.py       chiffrement des tokens, masquage des secrets, CSRF
  tasks.py          tâches de fond persistantes
  errors.py         messages d'erreur compréhensibles
  pricing.py        moteur de prix
  finance.py        finances, exports CSV / Excel
  listing.py        rédaction des annonces
  photos.py         studio photo (analyse, prises de vue, contrôle qualité, historique)
  image_providers.py  ComfyUI / Gemini / OpenAI, budget
  comfyui.py        génération locale gratuite
  ai.py             IA texte (OpenAI puis Gemini)
  market.py         Market Intelligence, opportunités
  niches.py         niches et produits gagnants
  vinted_client.py  connexion Vinted (token)
  sync.py, publisher.py, responder.py   synchro, publication, messages
  autopilot.py      planning configurable
  assistant.py      assistant Telegram
  telegram_bot.py   bot Telegram
  notify.py         notifications
  dashboard/        app.py, common.py, routes_*.py, templates/, static/
data/niches.json, data/winning_products.json, data/comfyui/
docs/  AUDIT.md, INSTALL_WINDOWS.md, VPS.md, MIGRATION.md, PHOTOS_GRATUITES.md, NICHES.md
```
