"""Messages d'erreur compréhensibles, avec une solution quand c'est possible."""
import requests

from .security import mask

HINTS = [
    ("database is locked", "La base était occupée : réessaie dans quelques secondes."),
    ("OPENAI_API_KEY manquant", "Ajoute ta clé OpenAI dans le fichier .env puis relance."),
    ("GEMINI_API_KEY manquant", "Ajoute ta clé Gemini dans le fichier .env puis relance."),
    ("budget du jour", "Augmente le budget dans Studio photo ou attends demain (ton PC reste gratuit)."),
    ("ComfyUI injoignable", "Lance ComfyUI sur ton PC, puis « Tester » dans Studio photo."),
    ("Token Vinted expiré", "Recolle le cookie de session dans Boutiques → Connexion Vinted."),
    ("403", "Vinted refuse la requête : ouvre vinted.fr dans ton navigateur, recolle le cookie. "
            "Si ça persiste, Vinted bloque l'accès depuis cet appareil."),
    ("429", "Vinted demande de ralentir : le bot réessaiera au prochain cycle."),
    ("modèle d'annonce", "Dashboard → Boutiques → « Apprendre d'une annonce »."),
    ("image fournisseur", "Envoie la capture du produit sur sa fiche ou par Telegram."),
]


def explain(e: Exception) -> str:
    msg = mask(str(e) or e.__class__.__name__)
    if isinstance(e, requests.Timeout):
        msg = "Le service n'a pas répondu à temps"
    elif isinstance(e, requests.ConnectionError):
        msg = "Connexion impossible (Internet ou service indisponible)"
    for needle, hint in HINTS:
        if needle.lower() in msg.lower() and hint not in msg:
            return f"{msg} → {hint}"
    return msg
