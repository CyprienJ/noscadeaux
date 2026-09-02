
# 🎁 nosCadeaux

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)

## 🏠 Accès
🌐 Site web : [noscadeaux.fr](https://noscadeaux.fr)

---

Une application web Django moderne pour organiser et partager des listes de cadeaux entre proches. Créez des groupes, partagez vos souhaits et coordonnez-vous facilement pour les occasions spéciales !

---
## Fonctionnalités

- 👥 **Création de groupes** : pour partager ces cadeaux dans différents cercles sociaux (familles, amis, ...)
- 🎯 **Gestion de listes de souhaits** : Ajoutez, modifiez et organisez des idées cadeaux
- 🔗 **Partage par invitation** : Invitez facilement des membres via des liens sécurisés
- 📧 **Notifications email** : Recevez une notification quand un membre choisis ajoute quelque chose à ça liste
- 🌍 **Interface multilingue** : Support français et anglais
- 🔐 **Authentification sécurisée** : Système complet de gestion d'utilisateurs
- 🦊 **Ajout rapide Firefox** : Extraction d'un produit depuis une boutique avec validation avant ajout

Le prototype de l'extension Firefox et ses instructions de développement se trouvent dans
[`firefox-extension/`](firefox-extension/).

---
## 🛠 Stack Technique

- **Framework** : Django 6.0 avec Python 3.12+
- **Frontend** : HTML5, CSS3, JavaScript
- **Base de données** : PostgreSQL (production), SQLite (développement)
- **Gestionnaire de paquets** : UV
- **Déploiement** : Docker + Nginx + Gunicorn
- **CI/CD** : GitHub Actions

## Notes de développement

- À chaque ajout ou modification de texte visible par les utilisateurs, penser à mettre à jour les traductions Django (`locale/*/LC_MESSAGES/django.po`) puis recompiler les messages.

### Versionnement

La version applicative suit le format `X.Y.Z` et sa source de vérité est le champ
`project.version` de `pyproject.toml`. Avant d'ouvrir une pull request, l'incrémenter
selon la nature du changement :

```bash
uv version --bump patch  # correction
uv version --bump minor  # fonctionnalité rétrocompatible
uv version --bump major  # changement incompatible
```

Le check CI `Version incremented` échoue si la version d'une pull request n'est pas
strictement supérieure à celle de la branche cible. Ce check doit être déclaré
obligatoire dans le ruleset GitHub de `main`. Lors du build, le workflow injecte
automatiquement cette version et le hash complet du commit dans l'unique image
Docker `latest`.

### Notes de version

Les nouveautés visibles par les utilisateurs sont définies dans
`gifts/release_notes/X.Y.Z.toml`. Le nom du fichier et son champ `version`
doivent correspondre à la version concernée. Une traduction française est
obligatoire et une traduction anglaise peut être ajoutée :

```toml
version = "1.2.0"
date = 2026-08-18

[fr]
title = "Titre de la nouveauté"
content = "Description affichée dans la modale et le changelog."

[en]
title = "Update title"
content = "Description shown in the modal and changelog."
```

La CI valide automatiquement ces fichiers. La commande peut aussi être lancée
localement avec `uv run python manage.py validate_release_notes`.

### Inscription, invitations et personnes sans compte (1.4.0)

L’inscription demande l’e-mail et le mot de passe avec confirmation. Après validation
de l’adresse, le pseudo est obligatoire ; la photo et le jour/mois d’anniversaire
sont facultatifs. Le choix suivant permet de créer un groupe, d’en rejoindre un par
code ou de continuer sans groupe. Le profil reste modifiable dans « Mon compte ».
Le parcours et l’invitation en attente sont conservés sur le compte pour la reprise
après reconnexion. Ouvrir l’e-mail sur un autre appareil valide l’adresse, mais ne
connecte pas automatiquement la personne : elle doit s’authentifier sur cet appareil.

Après création d’un groupe, l’écran d’invitations permet de copier/partager un lien
privé distinct du code court, d’envoyer un message individuel à chaque destinataire
et d’ajouter zéro, une ou plusieurs personnes sans compte. Il suffit de renseigner
leur nom. Chaque ajout affiche immédiatement un lien vers sa liste. Tous les membres
peuvent gérer ces souhaits ; le renommage synchronise le profil et la liste.
La suppression est définitive et emporte le profil technique, ses souhaits et leurs
données associées. La suppression du groupe supprime aussi ses personnes sans compte,
sans supprimer les comptes réels de ses membres. La revendication d’un profil reste
une évolution future, non disponible dans cette version.

Configuration et exploitation :

- `PUBLIC_BASE_URL` définit le domaine public des liens d’invitation. Vérifier aussi
  le domaine Django Sites pour les e-mails de vérification. Les liens gardent la langue
  du parcours ; tester les préfixes français et anglais avec le backend réel.
- Par défaut : 10 destinataires par envoi, 30 par utilisateur et 100 par groupe sur
  une fenêtre de 3 600 secondes. Les variables `GROUP_INVITATION_MAX_RECIPIENTS_PER_REQUEST`,
  `GROUP_INVITATION_MAX_RECIPIENTS_PER_USER_WINDOW`,
  `GROUP_INVITATION_MAX_RECIPIENTS_PER_GROUP_WINDOW` et
  `GROUP_INVITATION_RATE_WINDOW_SECONDS` permettent de régler ces limites.
- Le renvoi d’une vérification est limité côté serveur à une demande par minute
  et par compte. `/register` et le renvoi de vérification sont aussi limités par
  adresse IP sur une fenêtre glissante (`AUTH_RATE_WINDOW_SECONDS`, 900 s par
  défaut ; `REGISTRATION_MAX_ATTEMPTS_PER_IP_WINDOW` et
  `VERIFICATION_RESEND_MAX_ATTEMPTS_PER_IP_WINDOW`, 10 par défaut). L’IP n’est
  jamais stockée en clair : seul un HMAC salé par `DJANGO_SECRET_KEY` est
  conservé, et `cleanup_auth_throttle_events` (service `scheduler`) purge les
  lignes hors fenêtre. Le message affiché en cas de dépassement est neutre.
  Les inscriptions non vérifiées expirent après 30 minutes. La commande
  `cleanup_unverified_users` supprime ces comptes ; elle tourne toutes les heures
  dans le service `scheduler` du `docker-compose.yml`, plus jamais pendant une
  requête d’internaute. Elle effectue un `DELETE` en cascade des comptes
  `is_verified=False` de plus de 30 minutes n’ayant pas commencé leur onboarding ;
  elle ne supprime pas un compte ayant terminé son onboarding qui vérifie une
  nouvelle adresse. Une panne d’envoi laisse le compte reprenable et affiche une
  action de renvoi.
- `OnboardingDailyCount` contient uniquement `day`, `event`, `count` et une clé technique.
  Les événements sont `registered`, `verified`, `profile_completed`, `group_created`,
  `group_joined`, `group_skipped`. La démo est exclue. Ce sont des volumes quotidiens,
  pas un suivi individuel ni des cohortes : ils ne permettent pas de calculer un délai
  médian ou un taux exact d’abandon. Aucun e-mail, pseudo, token, IP ou identifiant
  utilisateur n’y est stocké. `GroupInvitationDispatch` reste le registre distinct
  de quotas/envois du lot 4, sans adresses destinataires.
- Ne pas activer les logs détaillés du backend e-mail en production ; masquer les
  chemins contenant des secrets dans les logs d’accès du proxy et de l’hébergement.

#### Livraison groupée et migration

Les cinq lots doivent être déployés ensemble, après sauvegarde et recette.
Les migrations `0039` à `0042` conservent les utilisateurs vérifiés déjà configurés
et les codes courts existants. `0043` ajoute les compteurs anonymes. `0044` répare
les anciennes identités gérées sans `ManagedMember`, et les profils sans utilisateur
technique, en conservant les souhaits et les adhésions.

Un utilisateur géré sans profil qui est actif ou appartient à zéro/plusieurs groupes
fait **échouer explicitement `0044` avant réparation** : un rattachement manuel doit
être décidé après audit, sans suppression automatique. Les corrections sont
transactionnelles et leur relance est idempotente. Le retour de `0044` est un no-op
sur les données : les liens réparés ne sont pas défaits.

**Avant déploiement**, lancer `python manage.py audit_managed_members` (lecture
seule) sur une copie récente de la production, hors de la fenêtre de déploiement.
La commande liste les identités gérées ambiguës qui feraient échouer `0044` ; si
elle en trouve, les traiter à la main avant d'appliquer les migrations. Le
`RuntimeError` de `0044` reste en place comme filet de sécurité.

La suite `gifts.test_onboarding_delivery` répète la migration du schéma `0038` à `0044`,
un retour de schéma puis une nouvelle migration sur une base de test jetable avec
données historiques synthétiques. **Ce n’est pas une recette sur une copie de production.**
Avant livraison : répéter sur une copie récente PostgreSQL, contrôler utilisateurs,
groupes et souhaits, puis vérifier les e-mails réels et les six parcours du `plan.md`
sur mobile/ordinateur, au clavier et avec lecteur d’écran.

En cas de retour arrière réel, privilégier un ancien code compatible avec le schéma
étendu, ou restaurer la sauvegarde complète avec une procédure validée. Ne pas utiliser
la suppression des colonnes ou la rotation des invitations comme rollback : elles
perdraient l’état du parcours ou invalideraient les liens déjà partagés.

### Signalement public des bugs

Le formulaire `/bug-report/` crée directement une issue GitHub et ne stocke aucun
signalement dans la base de données. Configurez le service avec :

- `BUG_REPORT_REPOSITORY` : dépôt public au format `propriétaire/dépôt` ;
- `BUG_REPORT_TOKEN` : jeton finement limité à ce dépôt avec `Issues: write` ;
- `BUG_REPORT_LABELS` : labels séparés par des virgules (facultatif).

Le jeton doit rester exclusivement côté serveur. Le dépôt doit être public pour que
les visiteurs sans compte GitHub puissent consulter le ticket créé. Le fichier
`.env.example` documente les variables sans contenir de secret. La version et la
révision publiées dans le ticket proviennent automatiquement de l’image déployée.

## Licence

Ce projet est distribué sous licence GNU Affero General Public License v3.0 ou ultérieure (`AGPL-3.0-or-later`). Consultez le fichier [LICENSE](LICENSE) pour le texte complet de la licence.
