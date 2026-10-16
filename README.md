# AC Track Mapper / AC Track Studio

Application web pour dessiner un tracé avec Google Maps et générer un pack de piste pour Assetto Corsa.

## Déploiement complet (interface + serveur Python)

GitHub Pages ne peut exécuter que des fichiers statiques. Pour activer la génération de pack avec le serveur Python, ce dépôt contient un fichier `render.yaml` pour Render.

1. Connecte-toi à Render : https://render.com/
2. Choisis **New + → Blueprint**.
3. Connecte GitHub et sélectionne le dépôt `valouddsite/mon-site-web`.
4. Vérifie le service `ac-track-studio`, puis clique sur **Apply**.
5. Attends que le déploiement soit terminé et ouvre l'URL Render du service.
6. Dans la page, ajoute une clé Google Maps API valide dans **Connecter Google Maps**.

Le service Render sert la page et son API sur le même domaine. Les déploiements suivants se feront automatiquement après les commits sur `main`.

## Google Maps

Dans Google Cloud Console, active la facturation si Google la demande, puis les API utilisées par l'application : **Maps JavaScript API**, **Directions API**, **Elevation API** et **Geocoding API**. Restreins la clé HTTP aux domaines utilisés (URL Render du service et, si nécessaire, URL GitHub Pages). Ne publie pas une clé non restreinte dans le code.

## Développement local

- Windows : double-clique sur `start.bat`.
- macOS/Linux : exécute `./start.sh` ou `python3 server.py`.

Puis ouvre http://localhost:8765 si le navigateur ne s'ouvre pas automatiquement.

## Limites du déploiement hébergé

Le service Render ne comprend pas Blender par défaut. Le ZIP peut contenir le modèle de route et le script `source/build_fbx.py`, mais pour produire automatiquement un fichier FBX, il faut installer/configurer Blender sur le serveur, ou exécuter le script avec Blender sur un ordinateur local. Le plan gratuit Render peut aussi mettre le service en veille après une période sans activité.
