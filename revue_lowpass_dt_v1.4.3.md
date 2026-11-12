# Rapport de revue — lowpass_dt, branche `v1.4-dev` (commit a5c27d5, version 1.4.3)

Intégration Home Assistant (HACS) : filtre passe-bas du premier ordre tenant compte du temps réel entre échantillons. Code dans `custom_components/lowpass_dt/`. La revue a été faite en lecture seule : les tests temporaires ont été supprimés et `git status` est propre.

Ce document est destiné à être transmis tel quel au modèle chargé des corrections. Chaque constat porte une **proposition de correction** : une direction, pas un correctif testé. Aucune de ces propositions n'a été implémentée ni exécutée.

## 1. Synthèse

**Suite de tests au départ** (Python 3.14, Home Assistant 2026.10.0, `pytest -q -rsxX -p no:cacheprovider`) : 177 réussis, 0 échec, 0 ignoré, 0 xfail, 54 s.

**Lu en entier** :
- Documentation : `README.md`, `CHANGELOG.md`, `ARCHITECTURE.md`, `BRANCH.md`, `CLAUDE.md`, `tests/README.md`.
- Code : `__init__.py`, `config_flow.py`, `const.py`, `config.py`, `loader.py`, `sensor.py`, `attributes.py`, `filter.py`, `injector.py`, `publisher.py`, `manifest.json`, `hacs.json`.
- Tests : `conftest.py`, `test_entity.py`, `test_config.py`.

**Lu en partie** : `test_patterns.py` et `test_behavior.py` (helpers et la majorité des tests). Pour `test_attributes.py`, `test_core.py`, `test_parsing.py` et `test_entity_id.py`, seuls les noms des tests ont été lus, pas leurs assertions. Une affirmation « aucun test » ci-dessous repose sur une recherche par mots-clés dans `tests/`.

**Non examiné** :
- `.github/workflows/validate.yml`, `pytest.ini`, `requirements_test.txt`.
- Les versions de Home Assistant autres que 2026.10.
- Le code de history-explorer-card : la conformité de la logique partagée n'a été vérifiée que contre la description de `CLAUDE.md`.
- Le comportement en production (Recorder réel).

**Méthode de preuve** : tests temporaires dans une vraie instance Home Assistant de test, avec les helpers de `tests/test_entity.py` (`Clock`, `feed`, `recorded`) et de `tests/test_patterns.py` (`start`, `restart`). Le harnais déclenche les minuteries jusqu'à 0,5 s en avance ; les comptages sont donc approximatifs à ±10 %.

Les décisions marquées « décidé par l'utilisateur » dans `CLAUDE.md` ne sont pas remises en cause : faux silences au démarrage, marge de 0,1 s, 2 décimales avec `deadband: 0`, `state_reported` non écouté.

### Vue d'ensemble des constats

| Id | Sujet | Catégorie | Statut | Décision de l'utilisateur requise |
|---|---|---|---|---|
| R01 | Écritures pendant un silence : `max_rate_dt` contourné | logique | prouvé | non |
| R02 | Deux entrées `sensors:` sur la même source | logique | prouvé | oui (deux entités ou avertissement) |
| R03 | Collision de `unique_id` entre domaines | logique | prouvé | oui (migration ou avertissement) |
| R04 | Valeur de silence écrite à la reprise | logique | prouvé | non |
| R05 | `silence: zero` : saut de 0 à l'ancienne valeur | logique | comportement prouvé, faute suspectée | oui |
| R06 | `silence: zero` sur `total` → *unknown* | écart doc-code | prouvé | oui (doc ou code) |
| R07 | Capteur texte `unknown` au démarrage : entité orpheline permanente | logique | prouvé | non |
| R08 | Valeurs invalides sans avertissement | logique / doc | prouvé | non |
| R09 | Battement `min_rate_dt` sans écriture au Recorder | écart doc-code | prouvé | oui (doc ou code) |
| R10 | Limite de 100 entités par pattern | logique / doc | suspecté | non |
| R11 | Écarts avec `BRANCH.md` / `ARCHITECTURE.md` | architecture | certain | non |
| R12 | Comportements sans test | test manquant | certain | non |
| R13 | Tests faibles | test faible | certain | non |
| R14 | Changement d'attribut seul = échantillon | logique | comportement prouvé, faute suspectée | oui |
| R15 | Écarts de documentation mineurs | écart doc-code | certain | non |
| R16 | Plancher absolu de 0,001 pour la deadband | logique | suspecté | oui |

### Ordre de correction conseillé

1. R01, puis R04 (même zone de `publisher.py` ; R04 supprime la branche morte citée en R11d).
2. R08, R07, R10 (indépendants, sans décision à prendre).
3. R02, R03 (après décision).
4. R11 (remaniement sans changement de comportement ; à faire après les corrections ci-dessus pour ne pas les mélanger).
5. R05, R06, R09, R14, R16 (après décision), R15 (docs).
6. R12 et R13 au fil des corrections : chaque correction arrive avec son test.

### Logique partagée avec history-explorer-card

Aucune proposition ne touche aux trois logiques partagées (valeurs de `circular`, règle de détection de silence, unités et angles des attributs). Seule exception à surveiller : R11e (fusion des deux analyseurs « nombre + unité »), qui doit garder à l'identique la règle de lecture des attributs, commune avec la carte.

### Rappel des règles de version du dépôt

Corrections à faire sur `claude/v1.4-dev-port`, version = dernière release + 1 (à vérifier sur GitHub avant le merge), section de CHANGELOG au numéro de la version, pas de `[Unreleased]`, pas de numéro de build dans les fichiers. Les fins de ligne CRLF de `__init__.py`, `config.py`, `config_flow.py`, `const.py`, `injector.py` et `loader.py` sont à conserver.

---

## 2. Constats

### R01 — Pendant un silence, chaque échantillon injecté contourne `max_rate_dt`

- **Fichiers** : `publisher.py` l.129 (`or self.output_just_resumed`), l.167 (`if injected: self.output_just_resumed = True`), l.220 ; `injector.py` `_start_periodic_injection` / `_tick`.
- **Catégorie** : logique.
- **Description** : `output_just_resumed` passe à vrai à chaque publication injectée et ne revient à faux qu'à la publication d'un échantillon réel ou suivi. Tant que dure l'injection (jusqu'à la convergence, plusieurs `tau`), toute valeur injectée qui dépasse la deadband est donc écrite, à la cadence d'injection. Cette cadence est l'intervalle moyen de la source (plancher 1 s), quel que soit `max_rate_dt`.
- **Attendu** : pas de flot d'écritures (`CLAUDE.md`, « What the filter is for »). README « Rate limits » : « at most one write per this interval — except right after a silence ». L'exception documentée est « juste après » un silence, pas pendant toute la convergence.
- **Statut** : prouvé par un test.
- **Preuve** : source toutes les 5 s environ, `tau: 120`, puis échelon 0 → 1000 et silence. Écritures comptées sur 120 s de silence :

  | Configuration | Écritures en 120 s de silence |
  |---|---|
  | `deadband: 0`, `max_rate_dt` par défaut (10 s) | 23 à 26 |
  | deadband adaptative, `max_rate_dt` par défaut | 23 à 26 |
  | `deadband: 0`, `max_rate_dt: 30` | 23 à 26 |

  Un autre essai, avec une source à 1 s environ, a donné 237 écritures en 120 s de silence (comptage gonflé par l'avance des minuteries du harnais), contre 11 quand la même source continue de parler après le même échelon.
- **Vérification attendue** : après un échelon suivi d'un silence, le nombre d'écritures pendant la convergence ne dépasse pas environ durée / `max_rate_dt`, plus l'écriture de convergence finale. La sortie atteint toujours la dernière valeur. La première écriture après la reprise n'est pas retardée.
- **Dépendances** : R12 (test existant trop étroit).

**Proposition de correction**

- Séparer les deux usages de `output_just_resumed`, aujourd'hui confondus :
  1. « la prochaine publication d'un échantillon réel n'est pas retenue par `max_rate_dt` » (l'exception documentée) ;
  2. « ne pas compter cet intervalle dans les statistiques `dt_output` » (l.77).
- Dans `should_publish`, n'accorder le contournement de `max_rate_dt` qu'aux publications non injectées (échantillon réel ou suivi). Le plus simple : passer `injected` à `should_publish` et n'appliquer `or self.output_just_resumed` que si `injected` est faux. Les publications injectées suivent alors la règle ordinaire (`now − time_last_pub > max_rate_dt`).
- Ne rien changer à la publication de convergence : elle ne passe pas par `should_publish` (`converged` court-circuite le test), donc la dernière valeur reste écrite dès qu'elle est atteinte.
- Ne pas ralentir l'injection elle-même : le calcul du filtre doit continuer à la cadence actuelle, seule l'écriture est limitée (règle 2 d'`ARCHITECTURE.md` : le résultat ne dépend pas du nombre de pas).
- À re-vérifier après correction : `test_edge_battery_at_rest_reaches_its_value_in_each_silence` (attend la valeur 12 dans les 60 s après chaque repos, avec `tau: 5`), `test_deadband_zero_silence_converges_and_stops`, `test_silence_converges_to_last_value`, et les tests de courbe redessinée de `test_behavior.py` (moins de points pendant la convergence : l'erreur de la courbe en lignes droites peut augmenter un peu).
- README « Rate limits » : la phrase « except right after a silence » devient exacte, rien à changer.

---

### R02 — Deux entrées `sensors:` sur la même source : seule la dernière est créée, sans message

- **Fichier** : `loader.py` l.163 (`explicit[cfg.key] = cfg`).
- **Catégorie** : logique.
- **Description** : les configurations explicites sont rangées par clé « source (+ attribut) ». Une seconde entrée sur la même source écrase la première, même avec `name` et `unique_id` distincts, et aucun avertissement n'est émis.
- **Attendu** : soit deux entités, soit un avertissement (`ARCHITECTURE.md` règle 4 : une option invalide donne un avertissement). Le README ne dit pas qu'une source ne peut avoir qu'un filtre.
- **Statut** : prouvé par un test.
- **Preuve** : deux entrées sur `sensor.src` (`tau: 30, name: Fast, unique_id: a` et `tau: 600, name: Slow, unique_id: b`). Résultat : une seule entité, `sensor.slow`, et aucun avertissement de lowpass_dt.
- **Vérification attendue** : le comportement choisi (deux entités, ou une seule avec un avertissement nommant l'entrée ignorée) est testé.
- **Dépendances** : R03.

**Proposition de correction** (décision de l'utilisateur : option A ou B)

- **Option A — autoriser plusieurs filtres par source.** Ranger les configurations explicites par `unique_id` calculé (`make_meta`) au lieu de la clé de source, et garder à part l'ensemble des clés de source explicites (il sert aux tests `key in explicit` des deux chemins de patterns, qui doivent continuer à exclure une source déjà filtrée explicitement). Deux entrées qui donnent le même `unique_id` (même source, même `prefix`, pas de `unique_id:`) : avertissement, la première est gardée. Avec le mode par défaut (sans `name`), les deux entités auraient aussi le même `entity_id` : exiger un `unique_id:` ou un `prefix` distinct pour la seconde, sinon avertissement. Documenter dans le README (un exemple « deux constantes de temps sur la même source »).
- **Option B — refuser, avec un avertissement.** Garder une entité par source ; à la seconde entrée, avertir (« sensors[] : source … déjà filtrée, entrée ignorée ») et garder la **première** (cohérent avec « le premier pattern gagne »). Attention : aujourd'hui c'est la dernière qui gagne ; garder la première change l'entité effective des configurations qui ont déjà un doublon. Si l'on veut zéro changement, garder la dernière et seulement avertir.
- Recommandation : option B « avertir et garder la dernière » si l'on veut le risque minimal ; option A si la fonction est souhaitée (le README propose aujourd'hui le chaînage comme seul moyen d'avoir deux constantes de temps).

---

### R03 — Collision de `unique_id` et d'`entity_id` entre sources de domaines différents

- **Fichiers** : `config.py` l.413-424 (`make_meta`), `sensor.py` constructeur.
- **Catégorie** : logique.
- **Description** : pour une source sans attribut, la graine du `unique_id` est `prefix + object_id`, sans le domaine. `sensor.x` et `input_number.x`, tous deux en `sensors:`, donnent `lowpass_dt::lp_x` et `sensor.lp_x`. Avec un attribut, la graine contient l'entity_id complet.
- **Attendu** : une entité filtrée par source, ou un avertissement de l'intégration.
- **Statut** : prouvé par un test.
- **Preuve** : une seule entité créée (source `sensor.x`). Home Assistant journalise « Platform lowpass_dt does not generate unique IDs. ID lowpass_dt::lp_x is already used by sensor.lp_x - ignoring sensor.lp_x ».
- **Vérification attendue** : deux sources de même `object_id` et de domaines différents donnent deux entités distinctes, ou un avertissement explicite. Les `unique_id` des entités existantes (sources `sensor.*`) ne changent pas.
- **Dépendances** : R02.

**Proposition de correction** (décision de l'utilisateur : option A ou B)

- **Option A — détecter et avertir (sans changer les identifiants).** Dans `loader.py`, en construisant les entités explicites, tenir l'ensemble des `unique_id` déjà attribués ; à la seconde occurrence, avertir en nommant les deux sources et le remède (`unique_id:` ou `prefix:` sur l'une des deux), et ne pas créer la seconde. Aucun identifiant existant ne change. C'est le correctif minimal ; il s'appuie sur le même ensemble que l'option A de R02.
- **Option B — graine avec le domaine pour les sources hors `sensor`.** `prefix + object_id` reste la graine des sources `sensor.*` (identifiants inchangés pour presque tous les utilisateurs) ; pour un autre domaine, graine `prefix + entity_id complet` (comme pour les attributs) et object_id `lp_<domaine>_<object_id>`. Il faut alors migrer les entités déjà enregistrées avec l'ancienne graine pour une source hors `sensor` (registre des entités : mise à jour du `unique_id` quand l'ancien existe et que le nouveau n'existe pas), sinon elles seraient marquées absentes puis recréées sous un autre nom. Plus propre, mais plus risqué.
- Recommandation : option A.

---

### R04 — `silence: zero` / `unknown` : la valeur de silence est écrite au moment où la source reprend

- **Fichiers** : `publisher.py` l.185-215 (`was_clamped_to_source`), `sensor.py` l.661-681 (marqueur).
- **Catégorie** : logique.
- **Description** : en mode `zero` ou `unknown`, la convergence publie d'abord la dernière valeur, puis l'injection suivante (un intervalle plus tard) affiche 0 ou *unknown*. Si la source reprend dans cette fenêtre, la publication du marqueur de fin de silence prend la branche « afficher la valeur de silence ». L'entité écrit alors 0 ou *unknown* à l'instant de la reprise, alors que cette valeur n'a jamais été affichée pendant le silence.
- **Attendu** : README, `silence` : « value published after the filter converges during silence ». Rien ne prévoit de l'écrire à la reprise.
- **Statut** : prouvé par un test.
- **Preuve** : `tau: 30`, `deadband: 0.01`, source à 10 s. On avance jusqu'à `publisher.clamped_to_source == True` (état affiché `19.999`), puis la source envoie 80. Écritures : `['0.0', '19.991']` en mode `zero`, `['unknown', '19.991']` en mode `unknown`.
- **Vérification attendue** : une reprise entre la convergence et l'affichage de la valeur de silence n'écrit ni 0 ni *unknown*.
- **Dépendances** : R05, R06.

**Proposition de correction**

- Dans `publish`, n'afficher la valeur de silence que pour une publication injectée : la branche l.206 ne doit s'appliquer ni au marqueur (`force`) ni à un échantillon réel. Condition à ajouter : la publication est injectée.
- Dans `_marker_value`, choisir la valeur du marqueur d'après ce qui est **réellement affiché** (`silence_shown`), pas d'après l'option `silence` :
  - valeur de silence pas encore affichée (palier à la dernière valeur) : marqueur = palier + 10^-(décimales+1), comme en mode `last` ;
  - 0 affiché (mode `zero`) : marqueur = 10^-(décimales+1), comme aujourd'hui ;
  - *unknown* affiché : pas de marqueur (c'est déjà le cas, l'état valant `None`).
- La branche `unknown` de `_marker_value` (valeur 0.0) disparaît alors : elle ne produit jamais la valeur écrite (voir R11d).
- Tests à ajouter : les deux cas de la preuve ci-dessus (reprise dans la fenêtre, modes `zero` et `unknown`), en vérifiant la liste complète des écritures.

---

### R05 — `silence: zero` : à la reprise, la sortie saute de 0 à l'ancienne valeur

- **Fichiers** : `publisher.py` (`publish`, `_marker_value`), `sensor.py` `_handle_source_event`.
- **Catégorie** : logique (intention à trancher).
- **Description** : pendant que 0 est affiché, l'état interne du filtre reste à la dernière valeur de la source. À la reprise, l'entité écrit le marqueur (≈ 0), puis aussitôt l'ancienne valeur du palier, puis monte vers la nouvelle. En mode `unknown`, l'ancienne valeur est écrite à l'instant de la reprise.
- **Attendu** : non spécifié. Le README dit d'utiliser `zero` quand le silence signifie que l'appareil est éteint ; un palier à l'ancienne valeur daté de la reprise contredit cette lecture. Aucune source ne dit de quelle valeur la courbe doit repartir.
- **Statut** : comportement prouvé par un test ; son caractère fautif est suspecté.
- **Preuve** : silence long (affichage `0.0`), puis source à 80. Écritures : `['0.0001', '19.999', '37.007', '49.195']`. En mode `unknown` : `['19.999', '37.007', '49.195']`.
- **Vérification attendue** : une fois le comportement décidé (repartir de 0 ou de l'ancienne valeur), un test fixe la suite des valeurs écrites à la reprise pour `zero` et `unknown`.
- **Dépendances** : R04, R06.

**Proposition de correction** (décision de l'utilisateur requise)

- **Option A — garder le comportement, le documenter.** Dire dans le README que `zero` / `unknown` ne changent que la valeur **affichée** pendant le silence : le filtre reste à la dernière valeur et repart d'elle à la reprise. Ajouter le test qui fige la suite d'écritures.
- **Option B — en mode `zero`, repartir de 0.** Si « silence = appareil éteint », la valeur en vigueur pendant le silence est 0. Au moment où 0 est affiché, mettre l'état du filtre à 0 : la sortie (`core.y`) et la valeur tenue (`core.x_prev`), de sorte qu'à la reprise le bloqueur d'ordre zéro pondère le silence par 0 et non par l'ancienne valeur. Points à traiter :
  - la valeur injectée pendant un silence vient de `sensor._last_source_value` : elle ne doit pas ramener le filtre vers l'ancienne valeur (l'injection est déjà arrêtée à ce moment, à vérifier après un redémarrage) ;
  - la restauration (`sensor.py`, « SILENCE VALUE SHOWN BEFORE THE RESTART ») compare `_last_source_value` à `core.x_prev` pour savoir si la source est toujours sur la valeur du silence : il faut alors mémoriser à part la valeur sur laquelle la source s'est tue (nouveau champ dans `extra_restore_state_data`, avec repli pour les états sauvés avant) ;
  - le marqueur à la reprise reste ≈ 0, puis la courbe monte de 0 vers la nouvelle valeur.
- En mode `unknown`, écrire l'ancienne valeur à l'instant de la reprise n'a pas de meilleure alternative évidente (il faut un point de départ) : le garder et le documenter.
- Recommandation : trancher d'abord ; l'option B est la lecture la plus naturelle de la phrase du README, mais elle ajoute un champ persistant.

---

### R06 — `silence: zero` sur une source `total` / `total_increasing` affiche *unknown*

- **Fichier** : `publisher.py` l.206-210.
- **Catégorie** : écart doc-code.
- **Description** : pour une `state_class` `total` ou `total_increasing`, `silence: zero` met l'état à *unknown* (pas à 0) après convergence.
- **Attendu** : README : « For `total` and `total_increasing` sensors this parameter has low effect ». Le passage à *unknown* n'est pas documenté.
- **Statut** : prouvé par un test.
- **Preuve** : source `state_class: total`, `silence: zero`, silence long. État final `unknown` ; dernières écritures `['20.967', '20.983', '21.0', 'unknown']`.
- **Vérification attendue** : la doc et le code s'accordent (lequel a tort n'est pas présumé) ; un test couvre `zero` sur `total` et `total_increasing`.
- **Dépendances** : R04, R05.

**Proposition de correction** (décision de l'utilisateur : doc ou code)

- **Option A — corriger la doc.** Le code a une raison : un 0 sur un compteur serait lu comme une remise à zéro par les statistiques de Home Assistant. Écrire dans le README que, sur `total` / `total_increasing`, `silence: zero` affiche *unknown* (jamais 0) et qu'aucun marqueur de fin de silence n'est écrit.
- **Option B — ignorer `silence` sur les compteurs.** Sur `total` / `total_increasing`, traiter `zero` et `unknown` comme `last`, avec un avertissement au chargement quand la classe est connue (elle ne l'est parfois qu'au premier échantillon : l'avertissement doit alors être émis une seule fois, à l'exécution). Correspond mieux à « low effect », mais retire un comportement existant.
- Recommandation : option A, avec les deux tests.

---

### R07 — Un capteur non numérique en état `unknown` au démarrage reçoit une entité filtrée qui ne disparaît jamais

- **Fichier** : `loader.py` l.54-58 (`_is_measurement` accepte `unknown`), l.230-250 (« KEEP » sur la seule existence), l.345 (le chemin dynamique refuse `unknown`).
- **Catégorie** : logique.
- **Description** : le scan de démarrage crée une entité pour tout capteur correspondant dont l'état est `unknown`, y compris un capteur texte pas encore initialisé. Le chemin dynamique, lui, n'en crée pas. Quand la source devient non numérique, l'entité n'est plus fournie aux démarrages suivants, mais le nettoyage la garde indéfiniment car sa source existe.
- **Attendu** : CHANGELOG v1.4.0 : « Patterns only create filtered entities for numeric sources ».
- **Statut** : prouvé par un test.
- **Preuve** : `sensor.power_mode` enregistré, état `unknown`, pattern `sensor.power_*`. `sensor.lp_power_mode` est créée. La source passe à `eco`. Après trois redémarrages simulés (runs 2, 3, 4), l'entité est toujours au registre, à l'état `unavailable` (`restored=True`).
- **Vérification attendue** : soit un capteur `unknown` ne crée rien avant d'avoir une valeur numérique, soit une entité dont la source s'avère non numérique est retirée selon la règle des deux redémarrages. Le cas « source en retard au démarrage » des tests existants doit rester vrai.
- **Dépendances** : aucune.

**Proposition de correction** (deux changements complémentaires)

- **Création.** Au scan de démarrage, une source en état `unknown` ne crée une entité que si son entité filtrée existe déjà au registre (elle a donc déjà été numérique : c'est le cas « source en retard » que le code veut protéger). Sinon, attendre : le chemin dynamique la créera à sa première valeur numérique, comme pour toute source apparue après le démarrage.
- **Nettoyage.** Dans le calcul de `keep_cfgs`, ne garder « sur la seule existence » que les sources dont on ne sait pas encore si elles sont numériques : pas d'état, `unknown` ou `unavailable`. Une source dont l'état est présent et non numérique (texte, date) ne protège plus son entité filtrée : elle suit la règle ordinaire (marquée au premier démarrage, retirée au suivant).
- Tests à vérifier : `test_source_back_at_the_next_restart_clears_the_mark`, `test_source_without_unique_id_is_kept_at_start`, `test_non_numeric_source_is_not_filtered` doivent rester verts. Test à ajouter : le scénario de la preuve (création refusée ; puis, pour une entité déjà existante dont la source est devenue du texte, retrait au second redémarrage).
- La même règle s'applique aux patterns avec `attribute:` (l'attribut absent ou non numérique sur une source présente).

---

### R08 — Valeurs invalides acceptées ou corrigées sans avertissement

- **Fichier** : `config.py` l.149-153 (`tau`), l.190, l.199-207, l.211, l.219-221, l.180-188 (`deadband`).
- **Catégorie** : logique / écart doc-code.
- **Description** :
  - Une valeur non numérique pour `tau`, `deadband_k_sigma`, `deadband_tau_sigma`, `max_rate_dt` ou `min_rate_dt` prend la valeur par défaut sans avertissement. L'avertissement n'existe que pour les valeurs numériques hors plage.
  - `tau: .nan`, `tau: .inf` et `deadband: .nan` sont acceptés tels quels.
  - `round: 2.7` devient 2 et `round: true` devient 1, sans message.
  - `unique_id` dans un pattern est ignoré sans message.
  - `silence` accepte `none` et `0`, absents du README.
- **Attendu** : `ARCHITECTURE.md` règle 4 : une mauvaise option donne un avertissement et sa valeur par défaut.
- **Statut** : prouvé par un test pour le silence des journaux et l'acceptation de NaN / inf ; certain à la lecture pour les effets en aval (avec `tau = inf`, `alpha = 0` et la minuterie de silence reçoit un délai infini).
- **Preuve** : `build_cfg({"tau": "abc"}, …)` donne 60.0 avec 0 enregistrement de journal. `build_cfg({"tau": nan})` garde `nan`, `inf` reste `inf`, `deadband: nan` reste `nan`.
- **Vérification attendue** : chaque valeur invalide ci-dessus produit un avertissement et la valeur par défaut ; aucune valeur non finie n'atteint `LowpassCfg`.
- **Dépendances** : R13.

**Proposition de correction**

- Remplacer `_float_or_default` par un seul helper de lecture d'un nombre, utilisé pour toutes les options numériques : il reçoit l'item, la clé, la valeur par défaut, la fonction d'avertissement et la borne (`> 0` ou `≥ 0`). Il avertit et rend la valeur par défaut quand la valeur est illisible, booléenne, non finie (`nan`, `inf`) ou hors borne. Il n'avertit pas quand la clé est absente.
- `deadband` : même helper, avec « absente = adaptative » ; `nan` / `inf` → avertissement et deadband adaptative.
- `circular` : déjà protégé contre les valeurs non finies, rien à changer (logique partagée avec la carte).
- `round` : accepter un entier, ou un nombre à valeur entière (`2.0`) ; refuser avec avertissement un booléen et un nombre non entier. Garder les valeurs négatives (arrondi aux dizaines) si elles sont voulues, sinon les refuser : à préciser dans le README.
- `unique_id` dans un pattern : avertissement « ignoré, réservé à `sensors:` », comme pour `name` (émis une fois, au chargement, grâce au mode `quiet` existant).
- README, ligne `silence` : documenter `none` (= `last`) et `0` (= `zero`), ou cesser de les accepter. Corriger aussi le message d'avertissement, qui cite « None/Last/Zero/Unknown ».
- Tests : paramétrer `test_invalid_tau_falls_back` et ses semblables sur toutes les options numériques avec `"abc"`, `nan`, `inf`, `True`, et vérifier **l'avertissement** (`caplog`) en plus de la valeur.

---

### R09 — Le battement `min_rate_dt` n'écrit rien quand la valeur arrondie n'a pas changé

- **Fichier** : `publisher.py` l.146-149, l.222-229.
- **Catégorie** : écart doc-code.
- **Description** : le battement appelle `async_write_ha_state()` avec un état et des attributs identiques. Home Assistant n'émet alors pas `state_changed`, donc le Recorder ne reçoit rien.
- **Attendu** : README : « `min_rate_dt` (3600 s): at least one write per this interval, even when nothing changes ».
- **Statut** : prouvé par un test.
- **Preuve** : `tau: 60, deadband: 5, round: 0, min_rate_dt: 60, max_rate_dt: 10`, source alternant 19,9 / 20,1 chaque seconde pendant 600 s. Résultat : 10 appels à `async_write_ha_state`, 0 événement `state_changed`.
- **Vérification attendue** : la doc et le comportement s'accordent ; un test du battement existe (aucun aujourd'hui).
- **Dépendances** : R12.

**Proposition de correction** (décision de l'utilisateur : doc ou code)

- **Option A — corriger la doc (recommandée).** Le but du projet est de peu écrire : une valeur identique n'apporte rien au Recorder. Reformuler le README : `min_rate_dt` garantit que la valeur publiée est **réévaluée** au moins à cet intervalle tant que la source parle ; une valeur qui, arrondie, n'a pas changé n'est pas enregistrée de nouveau par Home Assistant. Rappeler que le battement ne s'applique pas pendant un silence (déjà dit).
- **Option B — forcer une écriture.** Si un point périodique est réellement voulu (par exemple pour des outils qui tracent en lignes droites), il faudrait une valeur différente de la précédente, comme le fait le marqueur de fin de silence (± 10^-(décimales+1)). Cela ajoute des écritures artificielles : contraire à l'esprit du filtre, à ne faire que sur demande.
- Dans les deux cas : ajouter un test du battement (valeur qui change sous la deadband → au moins une écriture par `min_rate_dt` ; valeur arrondie inchangée → comportement choisi).

---

### R10 — Limite de 100 entités par pattern : sémantique différente au démarrage et en dynamique

- **Fichier** : `loader.py` l.243-247 (compteur par pattern, `break`), l.372-376 (compteur global `pattern_dynamic_created`).
- **Catégorie** : logique / écart doc-code.
- **Description** : au démarrage la limite est par pattern, et le `break` empêche les sources suivantes d'entrer dans `keep_cfgs`. En dynamique la limite est de 100 créations au total, tous patterns confondus, en plus de celles du démarrage. Des entités créées dynamiquement au-delà de la centième source (ordre alphabétique) seraient marquées absentes au redémarrage suivant, supprimées au second, puis recréées dynamiquement.
- **Attendu** : README : « Creation is limited to 100 entities per match string ».
- **Statut** : suspecté (lecture seule, non testé).
- **Preuve** : `if local_count > MAX_PATTERN_ENTITIES: … break`, placé après `keep_cfgs[key] = …`, et `if len(pattern_dynamic_created) >= MAX_PATTERN_ENTITIES`.
- **Vérification attendue** : avec plus de 100 sources pour un pattern, aucune entité existante n'est marquée ou supprimée alors que sa source existe ; la limite documentée est celle appliquée sur les deux chemins.
- **Dépendances** : aucune.

**Proposition de correction**

- Commencer par un test qui reproduit le soupçon (il est seulement lu, pas prouvé) : un pattern, 100 sources au démarrage, quelques sources créées ensuite en dynamique, puis deux redémarrages ; vérifier qu'aucune entité dont la source existe n'est marquée.
- Un seul compteur **par pattern**, partagé par le scan de démarrage et le chemin dynamique (par exemple un dictionnaire indexé par la position du pattern dans la liste).
- Au scan de démarrage, à la limite : arrêter de **créer**, mais continuer à parcourir les sources pour remplir `keep_cfgs` (remplacer le `break` par la poursuite de la boucle sans création). Une entité existante dont la source existe n'est ainsi jamais prise pour une orpheline.
- Avertissement de limite émis une fois par pattern sur les deux chemins.

---

### R11 — Écarts entre le code et les contraintes de `BRANCH.md` / `ARCHITECTURE.md`

- **Catégorie** : architecture / écart doc-code. **Statut** : certain à la lecture.
- **a. Configuration modifiée après analyse.** `sensor.py` l.288, l.581-582 écrit `self.cfg.circular` et `self.cfg.circular_auto`. `BRANCH.md` : « the configuration read-only once parsed ».
- **b. Accès aux membres privés d'un autre module.** `BRANCH.md` : « no reaching into another module's private attributes ».
  - `sensor.py` l.425 : `injector._on_silence_detected`.
  - `sensor.py` l.489 : `inj._compute_limits`.
  - `sensor.py` l.666-667 : `publisher._check_convergence`, `publisher._diff`.
  - `publisher.py` : 11 accès à `sensor._attr_native_value`, `_last_source_value`, `_attr_state_class`, `_unique_id_seed`.
  - `tests/test_entity.py` lit `ent.injector._stopped`.
- **c. Terme intégral mal situé dans la doc.** `ARCHITECTURE.md` place « the deadband … with its integral term » dans `filter.py`. Le terme intégral est calculé dans `publisher.py` `should_publish`. `core.err`, `core.err_i` et `core.time_last_pub` sont stockés dans `LowpassCore` mais écrits seulement par `Publisher`. La docstring de `LowpassCore` annonce « rounding », qui est dans `publisher.py`.
- **d. Code mort.** `BRANCH.md` : « dead code removed ».
  - `filter.py` l.102-104 : branche inatteignable (`t_sigma_start` est affecté l.96-97).
  - `filter.py` : `tau_lp` duplique `tau`.
  - `publisher.py` l.258-259 : la branche `unknown` de `_marker_value` ne produit jamais la valeur écrite. Soit le marqueur n'est pas émis (état `None`), soit la valeur est remplacée l.206-210 (voir R04).
  - `sensor.py` `_parse_source_value` : simple délégation.
- **e. Deux analyseurs « nombre + unité » aux règles différentes.** `config.py` `_NUMBER_UNIT` refuse la virgule décimale et les unités avec espace ; `attributes.py` `_NUMBER_UNIT` accepte les deux. La différence est voulue pour la virgule (CHANGELOG v1.4.0) et non documentée pour le reste.
- **f. Attribut de debug mal nommé.** `publisher.py` l.316 : l'attribut `silent` expose `output_just_resumed`, pas l'état de silence.
- **Vérification attendue** : après mise en conformité, aucun module n'appelle un membre `_privé` d'un autre, et `LowpassCfg` n'est plus écrit hors de `config.py`.
- **Dépendances** : R04 (branche du marqueur).

**Proposition de correction** (remaniement sans changement de comportement ; à faire en commits séparés des corrections de logique, la suite devant rester verte à chaque étape)

- **a.** Sortir la période circulaire résolue de la configuration. Elle est un état d'exécution : la faire porter par `LowpassCore` (attribut public, par exemple `period`, initialisé depuis `cfg.circular`), modifiée par une méthode publique que `sensor.py` appelle à la détection automatique et à la désactivation pour `total_increasing`. `publisher.py` et `injector.py` lisent la période du cœur au lieu de `cfg.circular`. `LowpassCfg` peut alors devenir un dataclass gelé (`frozen=True`), ce qui fait respecter la règle par construction. La clé `circular` de `extra_restore_state_data` prend sa valeur au même endroit.
- **b.** Donner une petite interface publique à chaque module :
  - `TauInjector` : une méthode pour « démarrer comme après un silence » (remplace l'appel à `_on_silence_detected` depuis `sensor.py` ; elle peut garder le rendez-vous annulable dans `stop()`), et une paire export / restauration des statistiques d'intervalle (remplace l'écriture directe de `dt_mean`, `dt_m2`, `t_last_source` puis `_compute_limits`).
  - Différence circulaire : la déplacer dans `LowpassCore` (méthode publique `diff(a, b)`), utilisée par le cœur, l'injecteur (qui la recalcule aujourd'hui à la main dans `_on_track`) et le publisher. Trois copies de la même formule deviennent une.
  - `Publisher` : méthode publique pour le test de convergence, utilisée par `sensor.py`.
  - Entité → publisher : exposer par des propriétés publiques de `LowpassDtSensor` ce que le publisher lit (valeur publiée, dernière valeur de la source, classe d'état, graine du `unique_id`) et une méthode publique pour écrire la valeur publiée ; ou passer ces accès au constructeur du publisher, comme `get_last_source` l'est déjà pour l'injecteur.
  - Test : remplacer la lecture de `injector._stopped` par une propriété publique ou par l'effet observable (la convergence après un silence, déjà vérifiée dans ce même test).
- **c.** Déplacer `err`, `err_i` et `time_last_pub` dans `Publisher` (seul à les écrire). La clé de persistance `low_pass.time_last_pub` reste la même pour ne pas perdre l'état sauvé. Corriger `ARCHITECTURE.md` (terme intégral dans `publisher.py`) et la docstring de `LowpassCore`.
- **d.** Supprimer la branche inatteignable et `tau_lp` dans `filter.py`, la branche `unknown` de `_marker_value` (avec R04), et `_parse_source_value`.
- **e.** Un seul analyseur « nombre + unité » dans un seul module, avec un paramètre explicite pour la virgule décimale (refusée pour un état, acceptée pour un attribut). Garder à l'identique la règle des attributs (partagée avec history-explorer-card). Décider si une unité contenant un espace est acceptée pour un état ; documenter la différence restante dans un commentaire.
- **f.** Faire exposer à l'attribut de debug `silent` l'état réel de l'injecteur (`injector.silent`). Changement visible seulement en mode `debug: true` : le signaler dans le CHANGELOG.

---

### R12 — Comportements sans test

- **Catégorie** : test manquant. **Statut** : certain (recherche par mots-clés dans `tests/`).
- `min_rate_dt` : seule l'analyse de l'option est testée (R09).
- Nombre d'écritures pendant un silence : `test_max_rate_dt_limits_writes` ne couvre qu'une source qui parle (R01).
- Limite `MAX_PATTERN_ENTITIES` (R10).
- Changement de période `circular` sur un état restauré (`_reset_circular_stats`), et détection d'angle en cours d'exécution.
- Option `round` de bout en bout ; valeur du marqueur selon les décimales.
- Options `prefix` (dont vide) et `suffix` de bout en bout ; `unique_id` explicite.
- Copie de l'`icon` de la source ; `state_class` déduite de la `device_class`.
- `silence` avec `total` / `total_increasing` (R06) ; marqueur en mode `zero` / `unknown` (R04, R05).
- Changement du YAML entre deux démarrages (`async_setup` : mise à jour de l'entrée) et suppression de la section YAML.
- Sources en double (R02) et collision entre domaines (R03).
- Valeurs non finies dans la configuration (R08).
- **Vérification attendue** : un test par ligne ci-dessus.

**Proposition de correction**

- Écrire chaque test avec la correction du constat correspondant, dans le fichier que `tests/README.md` lui attribue (`test_entity.py` pour l'entité, `test_patterns.py` pour le chargeur, `test_config.py` pour les options, `test_behavior.py` pour les comptages d'écritures).
- Pour les comportements sans constat associé (changement de période circulaire restaurée, `round`, `prefix` vide, `suffix`, `icon`, `state_class` déduite, changement et suppression du YAML) : tests de caractérisation du comportement actuel, sans modification du code.
- Suivre la convention du dépôt : un comportement voulu mais pas encore atteint est marqué `xfail(strict=True)` avec sa raison ; un comportement fautif figé porte une docstring commençant par « BUG ».
- Utiliser le helper `tick()` (horloge légèrement irrégulière) pour toute source régulière, afin d'éviter les faux silences d'une horloge parfaite.

---

### R13 — Tests faibles

- **Catégorie** : test faible. **Statut** : certain à la lecture.
- `tests/test_config.py::test_invalid_tau_falls_back` vérifie la valeur de repli, pas l'avertissement. Il passe alors que `tau: "abc"` ne journalise rien (R08).
- `tests/test_config.py::test_config_schema_passes_options_through` utilise `tau: "abc"` sans vérifier ce que `build_cfg` en fait.
- `tests/test_entity.py::test_silence_modes` ne vérifie que l'état final, pas la suite des écritures ni la reprise.
- `tests/test_entity.py::test_integral_term_publishes_noise_under_the_deadband` fige par `>= 5` un comportement que sa docstring décrit comme hors du régime normal, sans borne haute.
- `tests/test_behavior.py`, `sound()` : tolérance de 1e-6. Le marqueur vaut « palier + 10^-(décimales+1) » et peut sortir de la plage de la source ; aucun test ne place un silence sur le maximum de la source.
- **Vérification attendue** : chaque test ci-dessus échoue si le comportement décrit par son nom est cassé.
- **Dépendances** : R08, R04, R05.

**Proposition de correction**

- `test_invalid_tau_falls_back` : ajouter l'assertion sur l'avertissement (`caplog`), et l'étendre aux autres options numériques (voir R08).
- `test_config_schema_passes_options_through` : garder l'assertion sur le schéma, et la compléter par ce que `build_cfg` rend pour cet item (valeur par défaut et avertissement).
- `test_silence_modes` : enregistrer les écritures (`recorded`) et vérifier la suite complète — palier, puis valeur de silence, une seule fois — et la reprise (voir R04, R05).
- `test_integral_term_publishes_noise_under_the_deadband` : ajouter une borne haute (le nombre d'écritures attendu d'après la formule, avec une marge), pour qu'un emballement soit détecté.
- `test_behavior.py` : ajouter un scénario où le silence tombe sur le maximum de la source, et décider si le marqueur a le droit de dépasser la plage de 10^-(décimales+1). Si oui, l'écrire dans `sound()` comme tolérance explicite ; sinon, le marqueur doit s'écarter vers l'intérieur de la plage.

---

### R14 — Un changement d'attribut seul compte comme un échantillon pour une source d'état

- **Fichier** : `sensor.py` l.618-634.
- **Catégorie** : logique (incohérence à trancher).
- **Description** : pour une source avec `attribute:`, seule une nouvelle valeur de l'attribut est un échantillon. Pour une source d'état, un `state_changed` dû à un autre attribut, état inchangé, est traité comme un échantillon : il alimente les statistiques d'intervalle et arrête le silence.
- **Attendu** : `CLAUDE.md` : une source qui répète la même valeur est traitée comme silencieuse. Aucun texte ne traite le cas « même état, attribut changé ».
- **Statut** : comportement prouvé par un test ; son caractère fautif est suspecté.
- **Preuve** : source à `21`, puis même état avec un attribut `rssi` ajouté 10 s plus tard. `injector.t_last_source` avance de 10,0 s.
- **Vérification attendue** : le comportement choisi est testé pour les deux types de source.

**Proposition de correction** (décision de l'utilisateur requise)

- **Recommandation : ne pas changer le code, documenter.** Un `state_changed` à état identique vient soit d'un attribut qui change, soit d'un capteur déclaré en `force_update`, qui signale ainsi une vraie mesure de même valeur. Ignorer ces événements ferait perdre de vrais échantillons aux capteurs `force_update` et les ferait passer pour silencieux. Dans les deux cas, l'événement prouve que la source est vivante, ce qui est le sens de la détection de silence.
- Écrire la règle là où sont les décisions (`CLAUDE.md`, revue de la détection de silence) : « pour une source d'état, tout `state_changed` est un échantillon, même à valeur égale ; pour un attribut, seule une nouvelle valeur l'est », avec la raison (`force_update`).
- Si l'utilisateur préfère l'alignement strict (même état = pas d'échantillon) : l'appliquer seulement quand l'état **et** l'heure de dernière mise à jour sont inchangés n'est pas possible avec `state_changed` seul ; il faudrait alors accepter la perte des échantillons `force_update`. Déconseillé.
- Dans tous les cas, ajouter le test qui fige le comportement pour les deux types de source.

---

### R15 — Écarts de documentation mineurs

- **Catégorie** : écart doc-code. **Statut** : certain à la lecture.
- `tests/README.md` : le tableau des fichiers omet `test_attributes.py`. Le texte ne mentionne pas son `skipif` (`wind_direction`).
- `hacs.json` déclare `homeassistant: 2024.1.0`. README et CI ne couvrent que 2025.1 et plus.
- README, tableau des paramètres : `silence` a pour défaut « None » et la description dit « `last` (default) ». `circular` est typé « string » alors qu'il accepte booléen et nombre.
- README « Performance » : « Fixed cost per update, regardless of sensor count ». Avec des patterns, `loader.py` écoute tous les `state_changed` de Home Assistant et teste chaque pattern à chaque événement.
- README « Silence detection » : « When the source resumes, an end-of-silence marker is published ». Le marqueur est conditionnel : convergence atteinte, nouvelle valeur hors deadband, pas de `total` / `total_increasing`.

**Proposition de correction**

- `tests/README.md` : ajouter la ligne `test_attributes.py` (attributs et sous-attributs comme source : valeur, unité, classes, angles) et mentionner le test ignoré quand `wind_direction` n'existe pas dans la version de Home Assistant.
- `hacs.json` : porter `homeassistant` à la plus ancienne version testée par la CI (`2025.1.0`), ou dire dans le README que les versions 2024.x sont acceptées par HACS sans être testées. À décider par l'utilisateur : relever le minimum empêche l'installation sur 2024.x.
- README, tableau : `silence` — défaut `last` ; valeurs `last`, `zero`, `unknown` (plus `none` et `0` si elles restent acceptées, voir R08). `circular` — type « nombre, chaîne ou `false` ».
- README « Performance » : préciser que, avec des `patterns`, chaque changement d'état de Home Assistant est comparé aux patterns (coût faible, mais proportionnel à l'activité totale), et que le coût du filtrage lui-même est fixe par mise à jour d'une source filtrée.
- README « Silence detection » : écrire les conditions du marqueur (la sortie a rejoint la dernière valeur, la nouvelle valeur s'en écarte d'au moins la deadband, pas sur `total` / `total_increasing`).
- Ces changements de docs sont visibles par l'utilisateur : selon les règles du dépôt, ils entrent dans la version et dans le CHANGELOG (pas dans « Version highlights »).

---

### R16 — Plancher absolu de 0,001 pour la deadband adaptative

- **Fichier** : `filter.py` l.164.
- **Catégorie** : logique. **Statut** : suspecté.
- **Description** : la deadband adaptative vaut au moins 0,001 dans l'unité de la source, quelle que soit son échelle. Pour une source de très petite amplitude (variations inférieures à 0,001 unité), seul le terme intégral laisse passer les changements, et les décimales dérivées plafonnent à 6.
- **Attendu** : `CLAUDE.md` : la deadband se règle seule sur l'amplitude des variations du système.
- **Preuve** : `return max(0.001, float(self.cfg.deadband_k_sigma) * sigma)`.
- **Vérification attendue** : une source d'amplitude 1e-4 voit ses changements d'état écrits sans attendre le terme intégral, ou la limite est documentée.

**Proposition de correction** (décision de l'utilisateur requise ; priorité basse)

- Commencer par un test qui mesure le cas : source autour de 2e-4 avec des variations de 1e-5 et un changement d'état de 1e-4 ; noter le délai d'écriture.
- **Option A — documenter.** Écrire dans le README (« Known Limitations » ou description de `deadband`) que la deadband automatique ne descend pas sous 0,001 unité, et que pour une source de très petite amplitude il faut fixer `deadband` (et `round`) à la main, ou changer l'unité de la source. Aucun risque, conforme à « garder la machinerie en proportion ».
- **Option B — plancher relatif.** Remplacer la constante par un plancher lié à l'échelle de la source (par exemple une fraction de la valeur absolue moyenne déjà suivie par le cœur, avec un minimum absolu très bas). Cela change la deadband et le nombre de décimales de **toutes** les sources immobiles (σ = 0) : à mesurer avec les scénarios de `test_behavior.py` avant de décider, et à annoncer dans le CHANGELOG.
- Recommandation : option A, sauf si un utilisateur a réellement une source de ce type.

---

## 3. Points examinés sans constat

- **Mathématique du filtre** (`filter.py`) : `alpha = 1 − exp(−dt/tau)`, bloqueur d'ordre zéro sur `x_prev`, moyenne circulaire, indépendance au découpage du temps. Conforme au README et au CHANGELOG v1.4.0.
- **Détection de silence** (`injector.py`) : EMA alpha 0,1, limite `mean + 3σ + 0,1 s`, plancher 1 s, plafond `tau`, premier échantillon après silence compté comme la limite, départ depuis le premier intervalle. Conforme à `CLAUDE.md` section 2.
- **Minuteries** : toutes en `@callback`, annulées dans `stop()`, relancées par `start()` après un renommage d'entity_id.
- **Double chargement au démarrage après un changement du YAML** (`__init__.py` : `async_update_entry` + `async_reload`) : testé, un seul appel du chargeur et une seule création d'état.
- **Remise à zéro `total_increasing`** : en marche et pendant l'arrêt, `reset_pending` est bien consommé (testé : la sortie suit `3.0`, `4.264`, `6.626` et `reset_pending` revient à faux).
- **Nettoyage en deux redémarrages** (`loader.py`) : marque `absent_since_run`, rechargement non compté, marque effacée au retour de la source, capteurs explicites jamais marqués. Conforme au CHANGELOG v1.4.3.
- **Garde anti-récursion** : fondée sur le registre des entités, pas sur le préfixe.
- **Valeurs de `circular`** (`config.py`) et détection automatique (`sensor.py` `_resolve_auto_circular`, `attributes.py` `is_circular_attribute`) : conformes à `CLAUDE.md` sections 1 et 3.
- **Attributs** (`attributes.py`) : chemin de sous-attribut, nom de feuille, tables d'unités, facteurs (luminosité, volume), dérivation des classes. Conformes à la description de `CLAUDE.md` section 3.
- **Restauration** (`sensor.py`) : pleine précision de `y`, temps d'arrêt pondéré sur la valeur d'avant l'arrêt, source indisponible au démarrage, valeur de silence conservée.
- **Versions** : `manifest.json` 1.4.3, section `## v1.4.3 (beta)`, pas de `[Unreleased]` ni de numéro de build dans les fichiers lus. La cohérence avec les releases et tags GitHub n'a pas été vérifiée.
- **« Version highlights » du README** : ne liste que des fonctionnalités.

---

## 4. Annexe — tests temporaires utilisés comme preuves

Ces tests ont été écrits dans `tests/` le temps de la revue, puis supprimés. Ils sont décrits ici pour être réécrits comme tests de non-régression. Tous utilisent les helpers existants de `tests/test_entity.py` (`Clock`, `clock`, `feed`, `recorded`, `tick`, `jitter`, `ATTRS`, `SRC = "sensor.src"`, `OUT = "sensor.lp_src"`) et de `tests/test_patterns.py` (`register_source`, `start`, `restart`, `lowpass_ids`). L'entité filtrée est pré-enregistrée au registre (fixture `registered`) quand le test porte sur un silence.

| Constat | Scénario | Mesure |
|---|---|---|
| R01 | `tau: 120` (+ `deadband: 0`, ou rien, ou `max_rate_dt: 30`). Source toutes les 5 s ± 0,3 s pendant 200 s autour de 0, puis 1000 à t = 200,3 et 1000,5 à t = 205, puis silence ; horloge avancée de 206 à 326 s par pas de 0,25 à 1 s. | Nombre d'événements `state_changed` de la sortie entre 206 et 326 s : 23 à 26 dans les trois configurations. |
| R02 | `sensors:` avec deux entrées sur `sensor.src` (`tau: 30, name: Fast, unique_id: a` ; `tau: 600, name: Slow, unique_id: b`). | Entités de l'intégration au registre : `['sensor.slow']` ; aucun avertissement de lowpass_dt. |
| R03 | États `sensor.x = 10` et `input_number.x = 20` ; `sensors:` avec les deux sources. | Une entité (`sensor.lp_x`, source `sensor.x`) ; erreur de Home Assistant « does not generate unique IDs ». |
| R04 | `tau: 30, deadband: 0.01, max_rate_dt: 1, silence: zero` (puis `unknown`). Source à 20 ± 0,001 toutes les 10 s jusqu'à t = 100, puis silence ; horloge avancée seconde par seconde jusqu'à `publisher.clamped_to_source == True` (t ≈ 242), puis source à 80 une seconde après. | Écritures à la reprise : `['0.0', '19.991']` (`zero`), `['unknown', '19.991']` (`unknown`). |
| R05 | Même configuration ; silence jusqu'à t = 1500 (état `0.0` ou `unknown`), puis source à 80 (t = 1505), 80,001 (1515), 80,002 (1525). | Écritures : `['0.0001', '19.999', '37.007', '49.195']` (`zero`), `['19.999', '37.007', '49.195']` (`unknown`). |
| R06 | Source `state_class: total`, unité kWh, valeurs croissantes toutes les 10 s jusqu'à t = 100 ; `tau: 30, deadband: 0.01, max_rate_dt: 1, silence: zero` ; silence jusqu'à t = 1500. | État final `unknown` ; dernières écritures `['20.967', '20.983', '21.0', 'unknown']`. |
| R07 | `sensor.power_mode` enregistré au registre, état `unknown`, sans attributs ; pattern `sensor.power_*` ; `start()` ; la source passe à `eco` ; `restart()` trois fois (runs 2, 3, 4). | `sensor.lp_power_mode` présente au registre après chaque redémarrage, état `unavailable`, `restored=True`. |
| R08 | Appels directs à `build_cfg` avec `tau` = `"abc"`, `nan`, `inf` ; `deadband_k_sigma`, `max_rate_dt`, `min_rate_dt`, `deadband_tau_sigma` = `"abc"` ; `round` = `2.7`, `True` ; `deadband` = `nan` ; `unique_id` dans un pattern. | Nombre d'enregistrements de journal : 0 dans tous ces cas ; `tau` reste `nan` / `inf`, `deadband` reste `nan`, `round` vaut 2 / 1. |
| R09 | `tau: 60, deadband: 5, round: 0, min_rate_dt: 60, max_rate_dt: 10` ; source alternant 19,9 / 20,1 chaque seconde (avec `tick()`), 30 s de mise en route puis 600 s mesurées. | 10 appels à `async_write_ha_state`, 0 événement `state_changed`. |
| R14 | `tau: 60` ; source à 21 à t = 10 ; à t = 20, même état `21` avec un attribut `rssi: -60` ajouté. | `injector.t_last_source` avance de 10,0 s. |

Vérifications sans constat : changement du YAML entre deux démarrages (une entrée de configuration préexistante avec d'autres données, puis `async_setup_component`) — un seul appel du chargeur ; remise à zéro `total_increasing` (1000 → 1010 → 1020 → 3 → 5 → 8 → 12) — sorties `1000.0, 1006.321, 3.0, 3.0, 4.264, 6.626`, `reset_pending` à faux à la fin.
