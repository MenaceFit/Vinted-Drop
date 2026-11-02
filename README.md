# Vinted Drop

Système complet pour faire tourner des boutiques Vinted en dropshipping (Shein, Temu, AliExpress),
**en pilote automatique** :

- **🔑 Connexion Vinted par token** : colle le cookie de ta session dans le dashboard, c'est tout.
- **💸 Ventes détectées toutes seules** : toutes les 15 min, le bot voit les nouvelles ventes, fait la compta
  et t'envoie sur Telegram le bouton « Commander » qui mène au produit chez le fournisseur.
- **📤 Publication automatique** : les produits prêts sont publiés de 10h à 15h, étalés, avec un quota par boutique.
- **💬 Réponses aux acheteurs + négociation** : l'IA rédige la réponse. Les offres sont acceptées si elles
  atteignent le prix mini ; sinon le bot fait une contre-offre.
- **📉 Baisse des prix** des articles qui ne bougent pas (jamais sous le prix mini).
- **🆓 Photos gratuites sur ton PC** : ComfyUI + Qwen-Image-Edit-2509 sur ta carte graphique NVIDIA, 0 € par
  image ; Gemini / OpenAI seulement en secours avec un **budget max par jour**. Guide : [docs/PHOTOS_GRATUITES.md](docs/PHOTOS_GRATUITES.md).
- **📸 Studio photo IA (PC, Gemini, OpenAI)** : un modèle récurrent par boutique (2 photos de référence +
  un décor fixe), 5 prises de vue (portée miroir, portée pose, à plat, cintre, détail), nettoyage de
  l'image fournisseur, **contrôle qualité IA** de chaque photo (fidélité notée sur 10, nouvel essai avec
  l'autre IA si la photo n'est pas fidèle), recadrage 3:4 en JPEG, suivi des coûts. Retouches photo par
  photo : ⭐ couverture, 🔄 refaire, 🗑 supprimer, depuis le dashboard ou Telegram.
- **🌙 Préparation de nuit** : les brouillons reçoivent leurs photos et leur fiche pendant la nuit.
- **🔎 Analyse des niches** : 19 niches classées par saison et par données Vinted en direct, et 49 liens
  fournisseurs ([docs/NICHES.md](docs/NICHES.md)).
- **🤖 Bot Telegram** : boutons partout, validation en 1 clic, et assistant à qui tu écris normalement
  (« combien j'ai gagné cette semaine ? », « passe la publication en automatique »).
- **📊 Dashboard** : site web complet, thème sombre / clair, animations, graphique interactif,
  chiffres en direct (mis à jour toutes les 30 s), objectif du jour, version mobile avec barre d'onglets.
- **🏆 Produits gagnants** : 64 produits avec un score /100 basé sur les preuves de vente (avis, notes,
  nombre de ventes), la saison et la marge, importables en un clic.

## Installation (sur ton PC ou ton VPS)

Python 3.12 ou plus récent.

```bash
git clone <ce dépôt> && cd Vinted-Drop
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # puis remplis les clés
python -m vinted_drop.cli init
```

Dans `.env` :
- `COMFYUI_URL` (facultatif, gratuit) : adresse de ComfyUI sur ton PC, voir [docs/PHOTOS_GRATUITES.md](docs/PHOTOS_GRATUITES.md).
- `GEMINI_API_KEY` : sur https://aistudio.google.com/apikey (photos avec Nano Banana, `gemini-nano-banana-2.1`).
- `OPENAI_API_KEY` : sur https://platform.openai.com/api-keys (photos avec `gpt-image-2.5-sunburst`, fiches,
  réponses, assistant). Mets les deux clés : si l'une échoue ou fait une photo pas fidèle, l'autre prend le relais.
  Compte environ 0,15 à 0,40 € par produit selon le nombre de photos et de nouveaux essais (le studio affiche le coût réel estimé).
- ⚠️ Si ton `.env` contient encore `OPENAI_IMAGE_MODEL=gpt-image-1`, remplace-le : ce modèle a été arrêté
  par OpenAI le 23 octobre 2026.
- `TELEGRAM_BOT_TOKEN` : avec [@BotFather](https://t.me/BotFather) → `/newbot`.
- `TELEGRAM_ALLOWED_CHAT_IDS` : envoie `/start` au bot, il t'affiche ton chat id, colle-le ici.
- `DASHBOARD_PASSWORD` : **obligatoire sur un VPS** (le dashboard contient tes tokens Vinted).

## Lancer

```bash
python -m vinted_drop.cli all     # dashboard + bot + pilote automatique
```

Le navigateur s'ouvre tout seul sur http://localhost:8000. Sur un VPS, mets `DASHBOARD_HOST=0.0.0.0`
et un `DASHBOARD_PASSWORD`, puis ouvre `http://IP-DU-VPS:8000`. N'utilise jamais `http://0.0.0.0:8000`
dans le navigateur : ce n'est pas une adresse qu'on peut ouvrir.

## Mise en route (10 minutes)

1. **Dashboard → Boutiques** : crée la boutique (nom, niche) et génère son modèle (la personne sur les photos).
2. **Connecte Vinted** : sur la fiche de la boutique, colle le cookie (la marche à suivre est affichée).
   Le bot vérifie le compte et affiche `@ton_pseudo` en vert.
3. **Apprends un modèle d'annonce** : publie UNE annonce à la main dans la bonne catégorie, puis colle
   son lien dans « Apprendre d'une annonce ». Le bot recopie la catégorie, la marque, la taille et le
   format de colis pour toutes ses publications.
4. **Ajoute des produits** : onglet *Niches* → *Importer*, ou envoie une capture au bot Telegram avec la
   légende `14.90 <lien> Sophie M`.
5. C'est tout : la nuit le bot prépare, le jour il publie, il répond, il détecte les ventes.

**Conseil** : laisse la publication et les réponses en mode **« je valide sur Telegram »** la
première semaine (c'est le réglage par défaut). Tu touches ✅ sur ton téléphone. Quand tu es en
confiance, passe en **automatique** avec `/auto` ou dans *Pilote auto*.

## Planning du pilote automatique

| Quand | Quoi |
|---|---|
| toutes les 15 min | synchro annonces (vues, favoris) + détection des ventes |
| toutes les 10 min | messages acheteurs et offres |
| 10h → 15h | publication : 1 article par boutique toutes les 15 min, jusqu'au quota (20 par défaut) |
| 2h | préparation des brouillons (photos + fiches), 15 par nuit |
| 3h | baisse de 10 % des articles sans favoris depuis 7 jours |
| 4h | analyse du marché Vinted → classement des niches |
| 21h | récap compta |

Tout se règle dans le dashboard (*Pilote auto*) ou dans Telegram (`/auto`).

## Commandes Telegram principales

`/menu` (boutons) · `/studio` · `/photos <id> [gemini|openai]` · `/couts` · `/stats` · `/acommander` · `/messages` · `/auto` · `/sync` · `/vinted` ·
`/niches` · `/gagnants` · `/preparer <id>` · `/publier <id>` · `/aide` pour tout le reste.
Ou écris simplement ta question.

## Sur un VPS (24h/24)

```bash
sudo tee /etc/systemd/system/vinted-drop.service >/dev/null <<UNIT
[Unit]
After=network.target
[Service]
WorkingDirectory=$PWD
ExecStart=$PWD/.venv/bin/python -m vinted_drop.cli all
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now vinted-drop
```

## Limites à connaître

- **Vinted n'a pas d'API officielle.** La connexion utilise les mêmes adresses que le site quand tu es
  connecté. Elles n'ont pas pu être testées sur un vrai compte pendant le développement et Vinted peut les
  changer à tout moment. Si une action échoue, le bot te prévient sur Telegram avec l'erreur. Toutes les
  adresses sont dans `vinted_drop/vinted_client.py` (`ENDPOINTS`) pour être corrigées facilement.
- **Token** : il expire. Le bot le renouvelle avec le `refresh_token_web`. Si ça ne marche plus,
  Telegram affiche « 🔑 token expiré » : recolle le cookie.
- **Risque de blocage** : l'automatisation est contraire aux conditions d'utilisation de Vinted, qui
  peut restreindre ou fermer un compte. Le bot reste raisonnable (une requête toutes les 2,5 s au
  maximum, publications étalées, quota par jour), mais le risque existe. Si Vinted bloque les requêtes
  depuis ton VPS (erreur 403), lance le bot depuis ton PC.
- **Statut pro** : la revente avec bénéfice est une activité commerciale. Il faut une micro-entreprise
  et un compte **Vinted Pro**, sinon le compte peut être fermé. Vinted transmet aux impôts les comptes
  au-delà de 30 ventes ou 2 000 € par an.
- **Photos fidèles** : vérifie les photos (mode validation). Un article différent des photos =
  litige et remboursement. Pour les niches enfants et animaux, le bot ne fait que des photos à plat.
- **Délais** : le délai d'envoi annoncé aux acheteurs se règle dans *Pilote auto* : mets un délai réel.

## Tests

```bash
python -m pytest -q
```

Les tests simulent Vinted (aucun appel réseau).

## Structure

```
vinted_drop/
  vinted_client.py connexion à ton compte Vinted (token)
  sync.py          synchro annonces + détection des ventes
  publisher.py     publication + modèles d'annonce
  responder.py     réponses aux acheteurs + offres
  market.py        analyse du marché Vinted
  autopilot.py     planning automatique
  assistant.py     assistant en langage naturel (Telegram)
  telegram_bot.py  bot Telegram
  photos.py        studio photo : modèles, prises de vue, contrôle qualité, retouches
  image_providers.py  PC (ComfyUI), Gemini (Nano Banana) et OpenAI (GPT Image), secours + budget du jour
  comfyui.py       génération gratuite sur ton PC (workflow data/comfyui/qwen_image_edit_2509.json)
  listing.py       titres, descriptions, prix
  pricing.py       calcul des prix et des offres
  niches.py        classement des niches
  db.py            base SQLite
  dashboard/       dashboard web
data/niches.json, data/winning_products.json, docs/NICHES.md
```
