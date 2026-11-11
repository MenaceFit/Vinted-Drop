# Photos gratuites sur ton PC (0 € par image)

Le bot génère les photos sur ta propre carte graphique avec **ComfyUI** et **Qwen-Image-Edit-2511**
(licence Apache 2.0 : usage commercial autorisé). Chaque photo coûte 0 €, hors électricité.
**Tout se gère depuis le dashboard, page « Photos gratuites »** : branchement, choix du modèle,
téléchargement des fichiers, démarrage de ComfyUI, test, file d'attente et mode 100 % gratuit.

## Ce qu'il te faut

| Profil | Carte NVIDIA | Mémoire vive | Disque |
|---|---|---|---|
| **Qualité** (2511 fp8 + Lightning) | 12 Go ou plus (RTX 3060 12 Go, 4070, 4080, 3090, 4090, 5090) | 32 Go | 31 Go |
| **Léger** (2511 GGUF Q4 + Lightning) | 8 à 11 Go (RTX 3060 8 Go, 3070, 4060) | 32 Go | 23 Go |

Pour connaître ta carte sous Windows : `Ctrl + Maj + Échap` → **Performances** → **GPU** (« Mémoire GPU
dédiée »). Le dashboard l'affiche aussi dès que ComfyUI est branché, et te conseille le bon profil.

## En 5 étapes

1. **Installer ComfyUI** (une seule fois) : télécharge **ComfyUI Desktop** sur https://www.comfy.org/download
   (choisis « NVIDIA »), ou la version portable. Lance-le.
   Avec ComfyUI Desktop, mets son port sur **8188** (Paramètres → Server) : le dashboard utilise déjà le 8000.
2. **Dashboard → Photos gratuites → Détecter** : l'adresse de ComfyUI est trouvée et enregistrée.
   Indique aussi le **dossier de ComfyUI** sur ton PC (ex. `C:\Users\toi\Documents\ComfyUI` pour Desktop,
   `D:\ComfyUI_windows_portable` pour la version portable).
3. **Choisir le modèle** : « Qualité » ou « Léger », selon le conseil affiché pour ta carte.
4. **Télécharger les fichiers manquants** : un clic. Le dashboard télécharge les 4 fichiers depuis
   Hugging Face (Comfy-Org, lightx2v, unsloth) directement dans le bon dossier, avec la progression, la
   vitesse et le temps restant. Une coupure n'est pas grave : relance, ça reprend où ça s'est arrêté.
   Profil « Léger » : clique aussi **Installer l'extension ComfyUI-GGUF**, puis redémarre ComfyUI.
5. **Générer une image test** : tu vois le résultat et le temps de génération. C'est prêt.

Option : **Activer le mode 100 % gratuit** (photos uniquement sur ton PC, budget payant à 0 €, et si tu
veux, contrôle qualité coupé). Le Studio photo et la préparation de nuit passent alors par ton PC.

## Ce que fait la page

- **Démarrer ComfyUI** : possible avec la version portable ou une installation manuelle (dossier contenant
  `main.py`). Avec ComfyUI Desktop, ouvre simplement l'application. Journal : `data/comfyui.log`.
- **Contrôles** : carte graphique, mémoire vidéo libre, mémoire vive, version de ComfyUI, fichiers vus
  par ComfyUI, nœuds nécessaires (« ComfyUI trop ancien » s'il faut le mettre à jour), espace disque.
- **File d'attente** : interrompre la génération en cours, vider la file, libérer la mémoire vidéo.
- **Historique** : nombre d'images, durée moyenne et coût par fournisseur sur 30 jours.

## Le bot tourne sur un VPS ?

Le téléchargement et le démarrage se font sur le PC qui a ComfyUI. Deux solutions :

- **Le plus simple** : faire tourner le bot sur ton PC (`python -m vinted_drop.cli all`).
- **Avec Tailscale** (gratuit) : installe Tailscale sur le PC et sur le VPS, lance ComfyUI avec
  `--listen 0.0.0.0`, puis indique `http://ADRESSE-TAILSCALE-DU-PC:8188` dans la page. Télécharge les
  modèles depuis ComfyUI Desktop (Model library) ou depuis un dashboard lancé sur le PC.
  **N'ouvre jamais ComfyUI sur Internet** (pas de redirection de port sur ta box).

## À savoir

- **Vitesse** : 20 s à 2 min par photo selon la carte (4 étapes grâce au LoRA Lightning). Le premier essai
  est plus long : ComfyUI charge le modèle en mémoire.
- **Qualité** : un peu en dessous de Gemini sur certains tissus. Le contrôle qualité note chaque photo ;
  en dessous de 7/10 elle est refaite (avec Gemini si ton budget le permet, jamais en mode 100 % gratuit).
- **Contrôle qualité et fiches** : ils utilisent OpenAI ou Gemini en texte (quelques millièmes d'euro par
  photo). Coupe le contrôle qualité si tu veux 0 € absolu.
- **Ancienne installation 2509** : elle continue de marcher (profil « Ancien »). Passe au profil « Qualité »
  quand tu veux, le dashboard télécharge seulement ce qui manque (le text encoder et le VAE sont communs).
- **Ton propre workflow** : exporte-le depuis ComfyUI (Workflow → Export (API)), remplace les champs par les
  repères `__PROMPT__`, `__IMAGE1__`, `__IMAGE2__`, `__IMAGE3__`, `__SEED__`, `__WIDTH__`, `__HEIGHT__`
  et indique son chemin dans `COMFY_WORKFLOW`.
