#!/usr/bin/env bash
# Tests navigateur enchaînés pour la CI (.github/workflows/tests.yml), utilisable aussi en local.
# Usage : bash tests/ci_navigateur.sh <dossier du site table-de-jeu> [test ...]
# Lance le serveur d'essai (127.0.0.1:8000) et le site (localhost:8765), joue chaque test,
# puis donne la liste des échecs. Code de sortie 1 si au moins un test échoue.
set -u
SITE_DIR=$(cd "${1:?dossier du site table-de-jeu}" && pwd); shift
cd "$(dirname "$0")/.."

# Tests au délai de silence normal (12 s), puis ceux qui demandent DELAI_SILENCE=30 ENQ_RAPIDE=1.
NORMAUX="presents_navigateur mode_test_navigateur menteur_annonces_navigateur solo_navigateur undercover_navigateur conquete_navigateur telephone_navigateur telephone_modes_navigateur justepinte_navigateur quidenous_navigateur tournee_navigateur loupgarou_navigateur navigateur"
LENTS="soiree_navigateur enquete_navigateur"
if [ $# -gt 0 ]; then CHOIX=" $* "; else CHOIX=""; fi
garde() { [ -z "$CHOIX" ] || [[ "$CHOIX" == *" $1 "* ]]; }

python -m http.server 8765 --directory "$SITE_DIR" > /tmp/site.log 2>&1 &
SITE_PID=$!
SRV_PID=""
arreter() { [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; SRV_PID=""; }
trap 'arreter; kill $SITE_PID 2>/dev/null' EXIT

demarrer() {
  arreter
  env "$@" python tests/serveur_essai.py > /tmp/serveur.log 2>&1 &
  SRV_PID=$!
  for _ in $(seq 60); do curl -s -o /dev/null --max-time 2 http://127.0.0.1:8000/salon/ESSAI && return 0; sleep 1; done
  echo "Le serveur d'essai ne démarre pas :"; cat /tmp/serveur.log; exit 1
}

ECHECS=()
jouer() {
  garde "$1" || return 0
  echo "::group::$1"
  local debut=$SECONDS
  if timeout 900 node "tests/$1.js"; then echo "-> $1 OK ($((SECONDS - debut)) s)"
  else echo "-> $1 ÉCHEC ($((SECONDS - debut)) s)"; ECHECS+=("$1"); fi
  echo "::endgroup::"
}

phase() {  # phase "<tests>" VAR=valeur... : démarre le serveur seulement si un test de la phase est choisi
  local tests=$1; shift
  local un=""; for t in $tests; do garde "$t" && un=1; done
  [ -z "$un" ] && return 0
  demarrer "$@"
  for t in $tests; do jouer "$t"; done
}
phase "$NORMAUX" DELAI_SILENCE=12
phase "$LENTS" DELAI_SILENCE=30 ENQ_RAPIDE=1

if [ ${#ECHECS[@]} -gt 0 ]; then echo "Échecs : ${ECHECS[*]}"; exit 1; fi
echo "Tous les tests navigateur passent."
