# Analyse des niches Vinted — novembre 2026

Mise à jour le 2 novembre 2026 : **7 nouvelles niches** (voir juste dessous) et analyse du marché
Vinted en direct chaque nuit dès qu'une boutique est connectée.

## Nouvelles niches (moins de concurrence)

| Niche | Achat | Vente | Pourquoi |
|-------|-------|-------|----------|
| **Robes de fête fille** (Noël, velours, tulle) | 12–22 € | 25–38 € | Les parents cherchent une robe pour UN repas de Noël, dans une taille précise. Peu d'offre neuve en 4–12 ans, achat rapide sans négociation. **N°1 du classement en novembre.** Photos à plat uniquement, jamais d'enfant généré par IA. |
| **Pulls de Noël pour chien** | 5–10 € | 14–22 € | Achat coup de cœur, presque aucun vendeur en neuf, petit colis. Marge x2 à x3. |
| **Vestes simili cuir** (bomber, marron) | 20–40 € | 45–70 € | « Cuir total » est la 1re tendance automne-hiver 2026-27 selon Vogue. Gros panier. |
| **Cagoules tricot / balaclava** | 4–9 € | 14–20 € | Taille unique (aucun problème de taille), petit colis, se vend en lot. |
| **Vêtements de grossesse** | 15–25 € | 30–45 € | Personne ne veut payer cher un vêtement porté 6 mois : Vinted est le 1er réflexe. Toute l'année. |
| **Ensembles de sport sculptants** | 10–20 € | 25–38 € | Pic des bonnes résolutions en janvier : publier dès mi-décembre. |
| **Déguisements enfant** (carnaval) | 8–15 € | 18–28 € | Carnaval des écoles en février : publier dès mi-janvier. |

**Stratégie conseillée pour novembre-décembre (3 boutiques) :**
1. Une boutique **« Noël famille »** : pulls de Noël, pyjamas assortis, robes de fête fille, pulls pour chien.
   Elle mélange 4 niches à forte demande et couvre toute la famille, donc un acheteur prend souvent plusieurs articles.
2. Une boutique **gothique grande taille** : peu de concurrence toute l'année, clientes fidèles.
3. Une boutique **hiver tendance** : simili cuir, fausse fourrure, cagoules, old money homme.

Dès le 15 décembre, bascule une boutique vers le **sport** (janvier), puis vers les **déguisements** et la **Saint-Valentin** (février).

## Analyse en direct (automatique)

Chaque nuit à 4h, le bot cherche les mots-clés de chaque niche sur Vinted avec ta session et mesure :
le nombre d'annonces (concurrence), les favoris moyens par annonce (envie des acheteurs) et le prix
médian. Le score « opportunité » compte pour 30 % du classement final. Le résultat est sur le
dashboard (Pilote auto) et t'est envoyé sur Telegram.

---

Analyse initiale du 1er novembre 2026. Halloween vient de passer : l'argent de la saison va maintenant
sur **Noël, le réveillon, l'hiver et le ski**. Le dashboard (onglet *Niches*) et le bot (`/niches`)
recalculent le classement chaque mois et le corrigent avec tes vraies ventes.

## Comment lire le score

`score = (demande × 0,6 + (10 − concurrence) × 0,4) × saison`
- saison = 1 si on est dans les mois forts, 0,7 si ça commence le mois suivant, 0,3 sinon.
- Bonus jusqu'à +2 si la niche a déjà ≥ 10 articles publiés chez toi avec un bon taux de vente.

## Top niches pour novembre – décembre

| # | Niche | Achat | Vente Vinted | Pourquoi ça marche |
|---|-------|-------|--------------|--------------------|
| 1 | **Pulls de Noël** (moches / mignons) | 10–22 € | 25–38 € | Pic de fin novembre au 20 décembre (soirées pull moche, Secret Santa). Achat impulsif, peu de négociation. |
| 2 | **Gothique grande taille** | 15–30 € | 35–55 € | Très peu d'offre en 44–54, clientes fidèles qui reviennent sur la boutique. Marche toute l'année (c'est le compte « Mylis » de la vidéo). |
| 3 | **Vestes fausse fourrure** (mob wife) | 25–45 € | 50–75 € | Tendance forte de l'hiver, gros panier = gros bénéfice par vente. |
| 4 | **Homme old money** (pulls demi-zip, cols roulés) | 15–25 € | 30–45 € | Peu de boutiques homme en neuf sur Vinted, les hommes négocient moins. Idée cadeau. |
| 5 | **Robes de réveillon / paillettes** | 15–30 € | 35–55 € | Noël, Nouvel An, soirées d'entreprise : on veut du neuf pour un seul soir. |
| 6 | **Pyjamas de Noël assortis** | 8–18 € | 20–30 € | Le dropshipping a un vrai avantage : tu peux proposer toutes les tailles du même modèle. |
| 7 | **Ensembles de ski femme** | 35–60 € | 70–110 € | Gros panier, vacances de Noël et de février. |
| 8 | **Robes gothiques classiques** | 12–25 € | 30–45 € | Hors Halloween, vise « whimsigoth », « dark academia » et la Saint-Valentin version goth. |
| 9 | **Manteaux longs en laine** | 30–70 € | 60–110 € | Très recherché, mais concurrence de Zara/Mango d'occasion : mise sur des coloris tendance (chocolat, bordeaux, chevrons). |
| 10 | **Coquette / nœuds / velours** | 12–22 € | 25–40 € | Tendance Gen Z très active sur Vinted ; velours bordeaux pour les fêtes, rose pour février. |

À préparer : **Saint-Valentin** (dès mi-janvier), **médiéval / fée** (printemps-été : fêtes
médiévales, mariages à thème).

## Conseils par niche

- **Une boutique = une niche = un modèle.** Le modèle (la personne sur les photos) doit coller à la cliente :
  modèle ronde pour le grande taille, homme pour old money, etc.
- **Mots-clés** : ils sont dans `data/niches.json` et sont donnés à l'IA qui rédige les titres.
- **Prix** : sur Vinted le port et la protection acheteur sont payés par l'acheteur. Le bot affiche un prix
  ~15 % au-dessus du prix visé pour laisser de la place à la négociation et ne descend jamais sous le
  plancher (`/offre <id> <montant>`).
- **Commence petit** : 15–20 produits par niche, puis regarde le taux de vente dans le dashboard (onglet
  *Niches*). Coupe ce qui ne vend pas au bout de 10 jours, double ce qui vend.

## Produits gagnants (liens directs)

49 liens Shein / Temu, classés par niche, sont dans [`data/winning_products.json`](../data/winning_products.json).
Ils s'affichent dans l'onglet *Niches* du dashboard avec un bouton **Importer**, et dans Telegram avec
`/gagnants <niche>`.

Les prix indiqués viennent des pages au 1er novembre 2026. Les prix Shein/Temu bougent tous les jours
et certains articles passent en rupture : **vérifie le prix et le stock avant de publier**.

Quelques exemples :
- Pull de Noël color block (SHEIN LUNE), 13,29 € — https://fr.shein.com/Women%27s-Latest-Fashion-Comfortable-Christmas-Color-Block-Pullover-Sweater-p-291414644.html
- Pull demi-zip bleu marine homme old money, 16,41 € — https://fr.shein.com/Men-s-Navy-Blue-Half-Zip-Stand-Collar-Knit-Sweater-Old-Money-Style-Light-Business-Casual-Commute-Long-Sleeve-Polo-Shirt-Top-Autumn-Winter-p-572342533.html
- Manteau laine noir chevrons oversize, 29,24 € — https://fr.shein.com/Women%27s-Black-Herringbone-Patterned-Lined-Oversize-Wool-Coat-Overcoats-p-560562699.html
- Robe gothique dentelle grande taille (ROMWE) — https://fr.shein.com/ROMWE-Plus-Size-Gothic-Style-Asymmetrical-Hem-Lace-Flare-Sleeve-Dress-Gothic-Party-Evening-Gown-Lace-Splice-Tie-Front-Plus-Size-Autumn-Dress-Formal-Gown-Valentine-s-Day-p-166158428.html
- Robe paillettes rose poudré années 70 (Sweetra) — https://fr.shein.com/Sweetra-Women-70s-Dusty-Rose-Sequin-Dress-Shiny-Christmas-Party-New-Year-Outfit-Fall-Winter-Flare-Long-Sleeve-A-Line-Short-Dress-Suitable-For-Holiday-Travel-p-336068831.html
- Combinaison de ski femme (Temu, 4,8/5) — https://www.temu.com/fr/-combinaison-de-ski-pour-femme-ensemble-%C3%A0-imprim%C3%A9-tendance-veste-de-neige-avec-bretelles-r%C3%A9glables-et-pantalon-v%C3%AAtements-de--hiver-unisexes--g-601104025900506.html

## Ajouter tes propres produits gagnants

Ajoute une ligne dans `data/winning_products.json` :

```json
{"niche": "noel-pulls", "title": "Mon pull", "supplier": "Shein", "cost": 14.5, "url": "https://..."}
```
