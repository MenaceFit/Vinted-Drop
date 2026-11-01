# Vinted Drop

Système pour gérer des boutiques Vinted en dropshipping (Shein, Temu, AliExpress) :

- **📸 Photos IA** : un modèle récurrent par boutique, et pour chaque produit une photo portée
  (selfie miroir style amateur), une photo à plat et un gros plan matière.
- **📝 Fiches** : titre optimisé pour la recherche Vinted, description complète, prix calculé
  automatiquement (marge, prix plancher pour la négociation).
- **📊 Dashboard web** : CA, bénéfice, marge, ventes par boutique, ventes à commander, niches.
- **🤖 Bot Telegram** : ajoute des produits, génère photos et fiches, enregistre les ventes, t'envoie le
  lien fournisseur à commander, fait la compta et t'envoie un récap chaque soir.
- **🔎 Niches** : classement des niches du moment + 29 liens directs vers des produits gagnants
  ([docs/NICHES.md](docs/NICHES.md)).

La publication automatique sur Vinted, les réponses aux clients et le planning ne sont pas encore inclus.

## Installation

```bash
git clone <ce dépôt> && cd Vinted-Drop
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # puis remplis les clés
python -m vinted_drop.cli init
```

Clés nécessaires dans `.env` :
- `OPENAI_API_KEY` : sur https://platform.openai.com/api-keys (c'est la « clé API ChatGPT » de la vidéo).
  Compte vérifié requis pour le modèle d'image `gpt-image-1`. Coût indicatif : ~0,05 € par photo en
  qualité `medium`, donc ~0,15 € par produit.
- `TELEGRAM_BOT_TOKEN` : parle à [@BotFather](https://t.me/BotFather) → `/newbot`.
- `TELEGRAM_ALLOWED_CHAT_IDS` : lance le bot, envoie-lui `/start`, il t'affiche ton chat id. Colle-le
  ici puis relance le bot (sinon n'importe qui pourrait utiliser ton bot).
- `DASHBOARD_PASSWORD` : obligatoire si le dashboard tourne sur un VPS.

## Lancer

```bash
python -m vinted_drop.cli dashboard   # http://localhost:8000
python -m vinted_drop.cli bot         # bot Telegram
```

## Utilisation en 5 étapes

1. **Crée un modèle** (dashboard → Boutiques, ou CLI) :
   ```bash
   python -m vinted_drop.cli persona "Sophie" "a 25 year old woman, pale skin, long straight black hair with bangs, slim, gothic style, silver rings"
   ```
2. **Crée la boutique** et associe-lui une niche et le modèle :
   ```bash
   python -m vinted_drop.cli shop "Sophie" --niche goth-classique --persona 1
   ```
3. **Ajoute des produits** : onglet *Niches* → *Importer*, ou dans Telegram
   `/ajouter <lien> <prix_achat> Sophie M`, ou envoie une **capture du produit** au bot avec la légende
   `14.90 <lien> Sophie`. (Shein bloque souvent la récupération automatique de l'image : la capture
   d'écran marche toujours.)
4. **Prépare** : `/preparer <id>` → le bot génère les 3 photos + titre + description + prix et te les
   envoie. Tu publies sur Vinted, puis `/publie <id>`.
5. **Vente** : `/vendu <id> <prix>` → bénéfice calculé + lien pour commander chez le fournisseur.
   Suis la commande avec `/commande`, `/recu`, `/envoye`. `/acommander` liste ce qui reste à commander.

Toutes les commandes : `/aide` dans Telegram.

## Sur un VPS (24h/24)

```bash
sudo tee /etc/systemd/system/vinted-bot.service >/dev/null <<UNIT
[Unit]
After=network.target
[Service]
WorkingDirectory=$PWD
ExecStart=$PWD/.venv/bin/python -m vinted_drop.cli bot
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo sed 's/cli bot/cli dashboard/' /etc/systemd/system/vinted-bot.service | sudo tee /etc/systemd/system/vinted-dashboard.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now vinted-bot vinted-dashboard
```

## Réglages des prix (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `TARGET_MULTIPLIER` | 2.5 | Prix visé = achat × 2,5 |
| `MIN_PROFIT` | 8 | Bénéfice minimum par article (€) |
| `NEGOTIATION_BUFFER` | 0.15 | Prix affiché 15 % au-dessus du prix plancher |
| `PACKAGING_COST` | 0.5 | Emballage par colis (€) |

Exemple : article acheté 14,90 € → affiché 43,90 €, offre acceptée jusqu'à 37,90 € (22,50 € de bénéfice minimum).

## À savoir avant de vendre

- **Statut pro** : Vinted interdit la revente professionnelle depuis un compte particulier. Acheter pour
  revendre avec bénéfice, c'est une activité commerciale : il faut un statut (micro-entreprise) et un
  compte **Vinted Pro**, et déclarer ce que tu gagnes. Vinted transmet chaque année aux impôts les ventes
  des comptes au-delà de 30 ventes ou 2 000 €.
- **Photos fidèles** : les prompts demandent à l'IA de reproduire le vêtement à l'identique. Vérifie
  chaque photo avant de publier : un article qui ne ressemble pas aux photos, c'est un litige et un
  remboursement.
- **État** : mets « Neuf avec étiquette » ou « Neuf sans étiquette », et des délais d'envoi qui
  tiennent compte de la livraison du fournisseur.

## Tests

```bash
python -m pytest -q
```

## Structure

```
vinted_drop/
  photos.py        modèles + photos portées / à plat / détail (OpenAI gpt-image-1)
  listing.py       titre, description, prix
  pricing.py       calcul des prix et des offres
  supplier.py      lecture des pages Shein/Temu (titre, image)
  niches.py        classement des niches
  db.py            base SQLite (data/vinted_drop.db)
  telegram_bot.py  bot Telegram
  notify.py        envoi de notifications Telegram
  dashboard/       dashboard web (FastAPI)
  cli.py           ligne de commande
data/niches.json, data/winning_products.json
docs/NICHES.md     analyse des niches
```
