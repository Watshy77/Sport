# Allure : coach course et vélo synchronisé avec Garmin

Appli web pour iPhone (à installer sur l'écran d'accueil), hébergée sur GitHub Pages.
Toutes les 3 h, une GitHub Action récupère tes activités et tes données santé sur Garmin Connect
avec la bibliothèque de [garmin_mcp](https://github.com/Taxuspt/garmin_mcp) (python-garminconnect).
Elle les **chiffre** avec ta phrase secrète, puis publie le site. L'appli déchiffre tout sur ton téléphone.

> Avec un compte GitHub gratuit, le dépôt et le site Pages sont **publics**. Seuls des fichiers chiffrés
> (AES-256-GCM, clé dérivée de ta phrase par PBKDF2) y sont stockés. Choisis une phrase longue.

## Installation (environ 15 min, une seule fois)

### 1. Créer le dépôt
1. Sur github.com : **New repository**, nom `allure`, visibilité **Public**, sans README.
2. **Add file → Upload files**, puis glisse tout le contenu de ce dossier.
   Sur Mac, le dossier caché `.github` peut ne pas partir. Dans ce cas, fais **Add file → Create new file**,
   tape `.github/workflows/sync.yml` comme nom et colle le contenu du fichier.

### 2. Créer le jeton Garmin (sur un ordinateur, avec garmin_mcp)
Installe [uv](https://docs.astral.sh/uv/), puis :
```bash
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
cat ~/.garminconnect/garmin_tokens.json
```
La commande demande ton e-mail, ton mot de passe et le code de double authentification si tu en as un.
Copie tout le texte affiché par `cat`. Ton mot de passe n'est jamais stocké sur GitHub.

### 3. Ajouter les secrets
Dépôt → **Settings → Secrets and variables → Actions → New repository secret** :

| Nom | Valeur |
|---|---|
| `GARMIN_TOKENS` | le texte copié à l'étape 2 |
| `SYNC_PASSPHRASE` | une phrase secrète longue (au moins 8 caractères, idéalement 4-5 mots) |
| `CALENDAR_URL` | *(facultatif)* le lien `webcal://…` de ton calendrier de travail iCloud |

### 4. Activer GitHub Pages
Dépôt → **Settings → Pages → Source : GitHub Actions**.

### 5. Première synchro
Onglet **Actions → Synchro Garmin et publication → Run workflow**.
Le premier passage récupère 1 an d'activités et 6 semaines de santé. Ça prend quelques minutes.
Les fichiers détaillés arrivent par paquets de 120 au fil des passages suivants.

### 6. Sur l'iPhone
1. Ouvre `https://TON-PSEUDO.github.io/allure/` dans **Safari**.
2. **Profil → Synchronisation Garmin** : saisis ta phrase secrète, puis **Synchroniser maintenant**.
3. **Partager → Sur l'écran d'accueil**. L'appli se synchronise ensuite toute seule à chaque ouverture.

## Entretien
- **Le jeton Garmin dure environ 6 mois.** Quand l'Action échoue avec « Aucun jeton valide »,
  relance `garmin-mcp-auth --force-reauth` puis mets à jour le secret `GARMIN_TOKENS`.
- Si Garmin bloque les serveurs de GitHub (erreurs 403 ou 429 répétées dans l'Action), lance la synchro depuis ton ordinateur :
  ```bash
  pip install -r sync/requirements.txt
  SYNC_PASSPHRASE='ta phrase' python sync/garmin_sync.py   # utilise ~/.garminconnect
  git add data && git commit -m synchro && git push
  ```
- Ta phrase secrète ne se récupère pas. Si tu la changes, supprime le dossier `data/` du dépôt avant de relancer.
- garmin_mcp reste utilisable en parallèle dans Claude Desktop pour discuter de tes données.

## Ce qui est synchronisé
- **Activités** : résumé de toutes tes activités, fichier original (.FIT) pour la course et le vélo,
  qui donne les zones, les records et la puissance normalisée.
- **Santé (par jour)** : préparation à l'entraînement, VFC nocturne, FC au repos, score et durée de sommeil,
  Body Battery, stress, pas, VO2max course et vélo.
  Le coach allège la séance du jour si ta préparation est basse ou si ta VFC est en dessous de ta normale.
  Ta FC au repos est mise à jour automatiquement.

- **Calendrier de travail** (si `CALENDAR_URL` est renseigné) : tes créneaux occupés sur les 7 derniers jours
  et les 4 semaines à venir. Le coach choisit tes jours d'entraînement parmi les plus libres et met les sorties longues
  et l'intensité les jours sans travail. Il limite la durée de chaque séance au créneau libre et t'indique l'heure conseillée.
  Réglages dans Profil → Calendrier de travail : séances par semaine, horaires possibles, marge trajet,
  traitement des événements « journée entière ». Après une nuit de travail, aucune séance n'est proposée dans les 6 h.
  Le lien reste dans un secret GitHub et n'apparaît jamais dans le dépôt.

L'import manuel de fichiers (.FIT, .GPX, .TCX, .CSV, .zip) reste disponible dans l'onglet Activités.

python-garminconnect est une bibliothèque non officielle : Garmin peut modifier son accès à tout moment.
