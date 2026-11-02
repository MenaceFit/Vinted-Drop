# Photos gratuites sur ton PC (0 € par image)

Le bot peut générer les photos sur ta propre carte graphique avec **ComfyUI** et le modèle
**Qwen-Image-Edit-2509** (licence Apache 2.0 : usage commercial autorisé). Chaque photo coûte 0 €,
hors électricité. Gemini et OpenAI ne servent plus qu'en secours (PC éteint, photo ratée), dans la
limite du budget du jour que tu fixes.

## Ce qu'il te faut

| | Minimum | Conseillé |
|---|---|---|
| Carte graphique NVIDIA | 12 Go de mémoire vidéo (RTX 3060 12 Go, 4070) | 16 à 24 Go (RTX 4080, 3090, 4090, 5090) |
| Mémoire vive (RAM) | 32 Go | 64 Go |
| Disque libre | 40 Go | 60 Go |

Pour savoir quelle carte tu as sous Windows : `Ctrl + Maj + Échap` → onglet **Performances** → **GPU**.
La ligne « Mémoire GPU dédiée » donne la mémoire vidéo.

Avec 8 Go, ça peut marcher avec une version réduite du modèle (GGUF), mais la qualité baisse nettement.

## 1. Installer ComfyUI

1. Télécharge **ComfyUI Desktop** sur https://www.comfy.org/download et installe-le (choisis « NVIDIA »).
2. Lance-le une première fois, puis ferme-le.

## 2. Télécharger les 4 fichiers du modèle

Mets chaque fichier dans le bon dossier de ComfyUI (dossier `models` de ton installation, ComfyUI
Desktop te l'indique dans Paramètres → « Model library ») :

| Fichier | Dossier | Lien |
|---|---|---|
| `qwen_image_edit_2509_fp8_e4m3fn.safetensors` (≈ 20 Go) | `models/diffusion_models` | [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/blob/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors) |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` (≈ 9 Go) | `models/text_encoders` | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors) |
| `qwen_image_vae.safetensors` | `models/vae` | [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors) |
| `Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors` | `models/loras` | [lightx2v/Qwen-Image-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Lightning/blob/main/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors) |

Le dernier fichier (« Lightning ») rend la génération environ 5 fois plus rapide (4 étapes au lieu de 20).

## 3. Brancher le bot

1. Relance ComfyUI. Il écoute sur `http://127.0.0.1:8188` (ComfyUI Desktop peut utiliser le port 8000 :
   regarde l'adresse affichée dans ses paramètres « Server »).
2. Dans le fichier `.env` du bot :
   ```
   COMFYUI_URL=http://127.0.0.1:8188
   ```
3. Relance le bot, puis dashboard → **Studio photo** → **Tester**. Tu dois voir le nom de ta carte graphique.
4. Dans le Studio, choisis le fournisseur **Auto** (ton PC d'abord) ou **Mon PC uniquement**.

## Le bot tourne sur un VPS ?

Le VPS doit pouvoir joindre ComfyUI sur ton PC. Deux solutions :

- **Le plus simple** : faire tourner le bot sur ton PC (`python -m vinted_drop.cli all`). Il fonctionne
  tant que le PC est allumé ; quand il est éteint, le bot repasse sur Gemini, dans la limite du budget.
- **Avec Tailscale** (gratuit) : installe Tailscale sur le PC et sur le VPS, puis
  `COMFYUI_URL=http://ADRESSE-TAILSCALE-DU-PC:8188`. Lance ComfyUI en écoutant sur le réseau
  (`--listen 0.0.0.0`). **N'ouvre jamais ComfyUI sur Internet** (pas de redirection de port sur ta box) :
  n'importe qui pourrait l'utiliser.

## À savoir

- **Vitesse** : 30 s à 2 min par photo selon la carte. La nuit, la préparation automatique fait
  15 produits, soit environ 45 photos.
- **Qualité** : un peu moins photoréaliste que Gemini. Le contrôle qualité note chaque photo : si elle
  est en dessous de 7/10, le bot la refait avec Gemini (payant, dans la limite du budget). Mets le
  budget à 0 € si tu veux du 100 % gratuit sans secours, mais tu auras alors plus de photos moyennes.
- **Contrôle qualité et fiches** : ils utilisent OpenAI ou Gemini en texte, pour un coût très inférieur
  à celui des images. Tu peux couper le contrôle qualité dans le Studio.
- **Ton propre workflow** : exporte-le depuis ComfyUI (Workflow → Export (API)), remplace les champs par
  les repères `__PROMPT__`, `__IMAGE1__`, `__IMAGE2__`, `__IMAGE3__`, `__SEED__`, `__WIDTH__`, `__HEIGHT__`
  et indique son chemin dans `COMFY_WORKFLOW`.
