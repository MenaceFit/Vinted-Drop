# Audit technique — Vinted Drop 1.x → 2.0

Audit réalisé le 3 novembre 2026 sur la branche `claude/vigilant-goodall-xdxrej` (7 commits, ~5 900 lignes,
25 modules Python, 12 templates, 37 tests).

## Architecture existante

```
vinted_drop/
  config.py           variables d'environnement
  db.py               SQLite (personas, shops, products, sales, settings, events, templates,
                      conversations, ai_costs) + statistiques
  pricing.py          prix affiché / prix plancher
  listing.py          fiche Vinted (titre, description) via IA
  photos.py           studio photo (prises de vue, contrôle qualité, retouches)
  image_providers.py  ComfyUI local, Gemini, OpenAI + budget
  comfyui.py          workflow Qwen-Image-Edit-2509
  ai.py               IA texte (OpenAI puis Gemini)
  vinted_client.py    connexion au compte Vinted par cookie de session
  sync.py             synchro annonces + détection des ventes
  publisher.py        publication + modèles d'annonce
  responder.py        réponses acheteurs + offres
  market.py           scan du marché Vinted
  niches.py           classement des niches + produits gagnants
  autopilot.py        tâches planifiées
  assistant.py        assistant Telegram (function calling)
  telegram_bot.py     bot Telegram
  notify.py           notifications Telegram
  dashboard/          FastAPI + Jinja + CSS/JS maison
```

Flux : le **bot Telegram** (process principal, `cli all`) porte le planning (`job_queue`) et partage la boucle
asyncio avec le **dashboard** (uvicorn). Les deux appellent les mêmes modules métier, qui écrivent dans SQLite.
Les appels lourds (IA, Vinted) passent par des threads (`asyncio.to_thread`, `BackgroundTasks`,
`ThreadPoolExecutor` dans le studio).

## Points forts

- Modules métier séparés de l'interface, réutilisés par le bot, le dashboard et la CLI.
- Aucune dépendance lourde (pas d'ORM, pas de framework front), démarrage rapide.
- Fournisseurs IA interchangeables avec secours automatique et budget.
- Garde-fous déjà présents : prix plancher jamais franchi, validation humaine par défaut, pas de
  contournement anti-bot, aucune photo de personne générée pour les niches enfants.
- Tests sans réseau (faux Vinted, faux fournisseurs d'images).

## Problèmes prioritaires (corrigés en 2.0)

| # | Gravité | Problème | Conséquence |
|---|---|---|---|
| 1 | Haute | SQLite sans WAL ni `busy_timeout`, une connexion par appel, écritures concurrentes depuis plusieurs threads | erreurs « database is locked » pendant les générations photo en parallèle |
| 2 | Haute | Horodatage `CURRENT_TIMESTAMP` (UTC) comparé à `datetime.now()` (heure locale) | statistiques « aujourd'hui » et budget du jour décalés de 1 à 2 h |
| 3 | Haute | `record_sale` accepte de vendre deux fois le même produit | chiffre d'affaires et bénéfice gonflés |
| 4 | Haute | Bénéfice = prix − achat − emballage : frais fournisseur, port et autres frais ignorés | marge surestimée |
| 5 | Haute | Bot Telegram ouvert à tout le monde si aucune discussion n'est configurée | n'importe qui peut piloter les boutiques |
| 6 | Haute | L'assistant Telegram publie ou enregistre une vente sans confirmation | action irréversible déclenchée par une phrase mal comprise |
| 7 | Haute | Cookies Vinted stockés en clair dans la base | vol de session si la base est copiée |
| 8 | Haute | Formulaires du dashboard sans protection CSRF | une page web malveillante peut déclencher une action sur `localhost` |
| 9 | Moyenne | Le contrôle qualité garde la meilleure photo même si elle est sous le seuil | photo infidèle publiée |
| 10 | Moyenne | Tâches de fond en mémoire (`JOBS_RUNNING`) : perdues au redémarrage, doublons possibles (double clic) | générations en double, aucune trace des erreurs |
| 11 | Moyenne | Aucun index SQL (`sales.sold_at`, `products.status`, `products.shop_id`…) | dashboard plus lent quand la base grossit |
| 12 | Moyenne | Les annonces importées depuis Vinted ont un coût d'achat de 0 € | bénéfice faux tant que le coût n'est pas saisi |
| 13 | Moyenne | Pas de sauvegarde de la base | perte de données en cas de corruption |
| 14 | Basse | `MIN_PROFIT` = 8 € par défaut | trop bas pour l'objectif de 30 € de bénéfice par vente |
| 15 | Basse | Statistiques limitées (pas de capital investi, dépenses fournisseurs, période personnalisée, comparaison) | pilotage financier impossible |

## Fonctionnalités annoncées mais non vérifiables

| Fonction | État réel |
|---|---|
| Connexion Vinted, synchro, ventes, publication, réponses, offres, baisse des prix | Code complet et testé contre un **faux Vinted**. Vinted n'a pas d'API publique : les adresses utilisées (`vinted_client.ENDPOINTS`) n'ont jamais pu être testées sur un vrai compte. |
| Génération photo ComfyUI | Testée avec un faux serveur ComfyUI ; le workflow réel dépend des fichiers installés. |
| Gemini / OpenAI | Format des requêtes vérifié dans la documentation officielle ; pas d'appel réel effectué. |
| Analyse du marché | Fonctionne uniquement avec une session Vinted connectée ; sinon le score des niches repose sur des estimations manuelles. |

## Fonctionnalités manquantes (ajoutées en 2.0)

Gestion financière et export, commandes fournisseurs, comparaison des boutiques, paramètres par boutique,
historique des générations photo, comparaison avant/après, analyse du vêtement, Opportunités avec favoris,
historique du marché, journal d'activité filtrable, tâches de fond persistantes, mode simulation, arrêt
d'urgence, sauvegardes, recherche globale, pagination et tri.

## Risques de sécurité

- Secrets dans `.env` (non versionné, `.gitignore` OK).
- Token du bot affiché dans les logs `httpx` → corrigé en 1.x, conservé.
- Dashboard sans mot de passe par défaut → acceptable seulement sur `127.0.0.1` ; la 2.0 refuse de démarrer
  sur `0.0.0.0` sans `DASHBOARD_PASSWORD`.
- Cookies Vinted → chiffrés en 2.0 (Fernet, clé dans `data/secret.key` ou `SECRET_KEY`).
- Requêtes SQL : toutes paramétrées ; seuls les noms de colonnes sont interpolés dans `_update` → la 2.0
  vérifie les noms de colonnes contre le schéma.

## Plan de développement (réalisé)

1. Fiabilité de la base : WAL, timeouts, index, horodatage local, migrations non destructives + sauvegarde avant migration.
2. Sécurité : chiffrement des cookies, CSRF (vérification d'origine), mot de passe obligatoire hors local, Telegram fermé par défaut, confirmation des actions sensibles.
3. Tâches de fond persistantes (identifiant, statut, début, fin, résultat, erreur, anti-doublon).
4. Finances et commandes fournisseurs + exports CSV/Excel.
5. Moteur de prix (frais, comparables, demande, historique, confiance) avec bénéfice minimum 30 €.
6. Studio photo 3.0 (analyse du vêtement, décors, poses, refus automatique, historique, avant/après).
7. Market Intelligence (historique, score explicable /100, opportunités, favoris) + niches à forte marge.
8. Dashboard 2.0 (thème noir/anthracite/blanc, centre de contrôle, filtres de période, comparaison, recherche globale, tri, pagination, actions groupées).
9. Boutiques (paramètres individuels, fiche boutique, comparaison).
10. Telegram 2.0 (menu, notifications dédoublonnées, assistant avec confirmation).
11. Pilote automatique (réglages par tâche, journaux, réessais, arrêt d'urgence, simulation).
12. Tests, documentation, guides Windows / VPS / migration, changelog.
