# Lancement sur un VPS Linux (24h/24)

Testé pour Ubuntu 22.04 / 24.04 et Debian 12. Un VPS à 2 Go de RAM suffit (la génération photo se fait chez
Gemini / OpenAI, ou sur ton PC via ComfyUI).

> Vinted peut refuser les requêtes venant d'adresses de serveurs. Le bot ne contourne aucune protection :
> si les connexions Vinted échouent avec une erreur 403 depuis le VPS, fais tourner le bot sur ton PC.

## 1. Installation

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
```

```bash
git clone -b claude/vigilant-goodall-xdxrej https://github.com/MenaceFit/Vinted-Drop.git && cd Vinted-Drop
```

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```

```bash
cp .env.example .env && nano .env
```

## 2. Sécurité obligatoire

Dans `.env` :

```
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PASSWORD=un-mot-de-passe-long-et-unique
DASHBOARD_URL=https://ton-domaine.fr
SECRET_KEY=
```

- Le programme **refuse de démarrer** sur `0.0.0.0` sans `DASHBOARD_PASSWORD`.
- Génère une `SECRET_KEY` (chiffrement des tokens Vinted) et garde-la précieusement :

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Mets le dashboard derrière HTTPS (Caddy, simple) :

```bash
sudo apt install -y caddy
```

```bash
echo 'ton-domaine.fr { reverse_proxy 127.0.0.1:8000 }' | sudo tee /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

  Dans ce cas, garde `DASHBOARD_HOST=127.0.0.1` : seul Caddy, sur la même machine, parle au dashboard.

- Pare-feu : n'ouvre que SSH et HTTPS.

```bash
sudo ufw allow OpenSSH && sudo ufw allow 443 && sudo ufw enable
```

## 3. Service systemd

```bash
sudo tee /etc/systemd/system/vinted-drop.service >/dev/null <<UNIT
[Unit]
Description=Vinted Drop
After=network-online.target
[Service]
WorkingDirectory=$PWD
ExecStart=$PWD/.venv/bin/python -m vinted_drop.cli all
Environment=DASHBOARD_OPEN_BROWSER=0
Restart=always
RestartSec=5
User=$USER
[Install]
WantedBy=multi-user.target
UNIT
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vinted-drop
```

Journal en direct (les secrets y sont masqués) :

```bash
journalctl -u vinted-drop -f
```

## 4. Sauvegardes

- Automatiques chaque nuit à 5 h (Pilote auto → Sauvegarde de la base), et avant chaque migration.
- Copie régulièrement hors du serveur `data/backups/` **et** `data/secret.key` (ou garde ta `SECRET_KEY`) :

```bash
rsync -av utilisateur@vps:~/Vinted-Drop/data/backups/ ./sauvegardes-vinted/
```

- Restaurer :

```bash
.venv/bin/python -m vinted_drop.cli backup --restore vinted_drop-AAAAMMJJ-HHMMSS-auto.db
```

## 5. Mise à jour

```bash
cd ~/Vinted-Drop && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart vinted-drop
```

La base est migrée automatiquement au démarrage, après une sauvegarde.

## 6. Photos gratuites avec ComfyUI sur ton PC

Le VPS doit joindre ComfyUI sur ton PC : utilise Tailscale (voir [PHOTOS_GRATUITES.md](PHOTOS_GRATUITES.md)).
N'ouvre jamais ComfyUI sur Internet.
