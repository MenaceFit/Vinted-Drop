# Installation sur Windows

Temps : 15 minutes. Tout se fait dans PowerShell.

## 1. Installer Python

1. Télécharge Python 3.12 ou 3.13 sur https://www.python.org/downloads/windows/
2. À l'installation, **coche « Add python.exe to PATH »**.
3. Vérifie dans PowerShell :

```bash
python --version
```

## 2. Récupérer le projet

Télécharge le ZIP de la branche sur GitHub (bouton **Code → Download ZIP**) et décompresse-le, par exemple dans
`E:\Vinted-Drop`. Ouvre PowerShell dans ce dossier (Maj + clic droit → « Ouvrir PowerShell ici »).

## 3. Créer l'environnement et installer

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

Si PowerShell refuse d'exécuter le script d'activation :

```bash
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Puis :

```bash
pip install -r requirements.txt
```

## 4. Configurer `.env`

Copie `.env.example` en `.env` (dans le même dossier) et ouvre-le avec le Bloc-notes. Minimum :

| Variable | Où la trouver |
|---|---|
| `GEMINI_API_KEY` ou `OPENAI_API_KEY` | https://aistudio.google.com/apikey · https://platform.openai.com/api-keys |
| `COMFYUI_URL` (facultatif, photos gratuites) | voir [PHOTOS_GRATUITES.md](PHOTOS_GRATUITES.md) |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `TELEGRAM_ALLOWED_CHAT_IDS` | envoie `/start` au bot en privé : il t'affiche ton id |
| `TELEGRAM_CHANNEL_ID` (facultatif) | lien du canal `https://web.telegram.org/a/#-100…` (le bot doit être administrateur) |

Laisse `DASHBOARD_HOST=127.0.0.1` : le dashboard n'est alors accessible que depuis ton PC.

## 5. Lancer

```bash
python -m vinted_drop.cli all
```

Le navigateur s'ouvre sur **http://localhost:8000** (n'utilise jamais `http://0.0.0.0:8000`).
Vérifie la configuration à tout moment :

```bash
python -m vinted_drop.cli check
```

## 6. Premiers pas dans le dashboard

1. **Boutiques → Nouvelle boutique** (nom, niche).
2. **Connexion Vinted** : colle le cookie de session (la marche à suivre est sur la page), « Connecter et tester ».
3. Publie une annonce à la main sur Vinted, puis **« Apprendre d'une annonce »** avec son lien.
4. **Studio photo → Nouveau modèle** (la personne récurrente de la boutique).
5. **Pilote auto → Mode simulation : activé**, puis teste une préparation et une publication : rien n'est envoyé à Vinted.
6. Quand tout est bon, désactive la simulation.

## Lancer au démarrage de Windows (facultatif)

Crée un fichier `lancer.bat` dans le dossier du projet :

```bat
@echo off
cd /d %~dp0
call .venv\Scripts\activate
python -m vinted_drop.cli all
```

Puis `Win + R` → `shell:startup` → place un raccourci vers `lancer.bat`.

## Problèmes fréquents

| Message | Solution |
|---|---|
| `python` n'est pas reconnu | réinstalle Python en cochant « Add to PATH » |
| `Token Telegram refusé` | recopie le token de @BotFather (sans espace) |
| `Impossible d'écrire dans -100…` | ajoute le bot comme **administrateur** du canal |
| Le site ne s'affiche pas | ouvre http://localhost:8000, vérifie que la fenêtre PowerShell est toujours ouverte |
| `Impossible de déchiffrer le token Vinted` | `data/secret.key` a changé : recolle le cookie dans Connexion Vinted |
