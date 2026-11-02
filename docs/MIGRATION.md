# Migration de la version 1.x vers 2.0

La migration est automatique et **non destructive** : aucune table ni colonne n'est supprimée.

## Ce qui se passe au premier lancement de la 2.0

1. **Sauvegarde automatique** de ta base dans `data/backups/vinted_drop-…-avant-migration.db`.
2. Ajout des nouvelles tables (tâches, commandes fournisseurs, dépenses, historique photo, relevés du marché,
   opportunités) et des nouvelles colonnes (frais par produit, réglages par boutique…).
3. Activation du mode WAL et création des index.
4. **Reprise des ventes** : chaque vente existante reçoit sa commande fournisseur
   (« À commander » → À acheter, « Commandé » → Commandé, « Reçu / Envoyé / Terminé » → Livré).
5. Les **tokens Vinted** en clair restent lisibles et sont **chiffrés** au premier renouvellement ou à la
   prochaine connexion. Une clé est créée dans `data/secret.key` : **sauvegarde-la avec la base**.

## Étapes

1. Arrête l'ancienne version (Ctrl + C dans PowerShell, ou `sudo systemctl stop vinted-drop`).
2. Copie le dossier `data/` de l'ancienne version dans la nouvelle (base, médias, photos).
3. Installe les nouvelles dépendances :

```bash
pip install -r requirements.txt
```

4. Compare ton `.env` avec le nouveau `.env.example` (voir les changements ci-dessous).
5. Lance :

```bash
python -m vinted_drop.cli all
```

6. Vérifie : `python -m vinted_drop.cli check`, puis l'accueil du dashboard et la page Commandes.

## Changements dans `.env`

| Variable | Changement |
|---|---|
| `MIN_PROFIT` | défaut **30 €** (au lieu de 8). Modifiable ensuite dans Paramètres. |
| `PLATFORM_FEE_PCT` | **nouveau** : frais vendeur en % (0 pour un compte particulier). |
| `SECRET_KEY` | **nouveau** : clé de chiffrement des tokens (vide = fichier `data/secret.key`). |
| `BACKUP_KEEP` | **nouveau** : nombre de sauvegardes gardées (14). |
| `TASK_WORKERS` | **nouveau** : tâches de fond simultanées (3). |
| `OPENAI_TEXT_FALLBACKS` | **nouveau** : modèles texte de secours. |
| `DAILY_REPORT_TIME` | n'est plus utilisé : l'heure du rapport se règle dans Pilote auto. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | désormais **obligatoire** : sans lui, le bot refuse tout (il affiche seulement ton id). |

## Changements de comportement

- **Offres des acheteurs** : passent en validation manuelle par défaut (Pilote auto pour remettre l'automatique).
- **Photos infidèles** : sous la note minimale après les essais, la photo est **refusée** (avant : gardée).
- **Bénéfice** : compte maintenant frais fournisseur, port et autres frais ; les bénéfices passés déjà
  enregistrés ne sont pas recalculés.
- **Heures** : les nouvelles dates sont en heure locale. Les dates des ventes 1.x étaient en heure universelle
  (décalage de 1 à 2 h sur les statistiques « aujourd'hui » de ces anciennes ventes uniquement).
- **Annonces importées depuis Vinted** sans prix d'achat : signalées « prix d'achat à renseigner ».
- **Assistant Telegram** : demande confirmation avant publier, vendre, préparer ou changer un réglage.

## Revenir en arrière

1. Arrête la 2.0.
2. Remets en place la sauvegarde `…-avant-migration.db` sous le nom `data/vinted_drop.db`.
3. Relance l'ancienne version. (La 1.x ignore les tables ajoutées ; les tokens déjà chiffrés devront être recollés.)
