// Banc de captures de la direction artistique : un vrai navigateur (Alice)
// et des bots WebSocket jouent une partie ; on photographie l'écran
// d'Alice à intervalles réguliers et dès qu'une annonce ou un écran de
// résultat apparaît. Sert à vérifier qu'un jeu est lisible et que rien ne
// se superpose, en portrait, en paysage et sur grand écran.
//
// Prérequis : python tests/serveur_essai.py (127.0.0.1:8000) et le site
// servi sur http://localhost:8765 (dossier table-de-jeu).
// Usage : node tests/captures_da.js <jeu> <nb_bots> <dossier> [secondes] [tailles]
//   tailles : "390x844,844x390,1280x800" (défaut : 390x844)
//   LEGER=1 : version légère ; DSF=2 : captures haute définition.
const { chromium } = require('playwright');
const fs = require('fs');

const [jeu, nbBotsTxt, dossier, secondesTxt, taillesTxt] = process.argv.slice(2);
const NB_BOTS = +nbBotsTxt || 3;
const DUREE = (+secondesTxt || 90) * 1000;
const TAILLES = (taillesTxt || '390x844').split(',');
const API = 'http://127.0.0.1:8000', WS = 'ws://127.0.0.1:8000', SITE = 'http://localhost:8765/';
const PAGES = { kinito: 'kinito', beerbattle: 'beerbattle', buckshot: 'buckshot', picolopoly: 'picolopoly', menteur: 'menteur', casino: 'casino', undercover: 'undercover' };
const DEMARRER = {
  kinito: { type: 'demarrer', difficulte: 'normal' }, beerbattle: { type: 'bb_demarrer' }, buckshot: { type: 'bt_demarrer' },
  picolopoly: { type: 'pp_demarrer', difficulte: 'normal' }, menteur: { type: 'mt_demarrer', mode: 'classique' },
  casino: { type: 'cs_demarrer' }, undercover: { type: 'uc_demarrer', famille: 'classique' },
};
const attendre = ms => new Promise(r => setTimeout(r, ms));
const hasard = l => l[Math.floor(Math.random() * l.length)];
fs.mkdirSync(dossier, { recursive: true });

// ---------------------------------------------------------------------
// Bots : la même logique que tests/banc_reconnexions.py, en plus simple.
// ---------------------------------------------------------------------
function bot(code, nom) {
  const ws = new WebSocket(`${WS}/ws/${code}/${encodeURIComponent(nom)}?bat=1&page=${jeu}`);
  const b = { nom, index: null, ws, prev: null };
  const envoyer = (m, d = 400 + Math.random() * 900) => setTimeout(() => { try { ws.send(JSON.stringify(m)); } catch (e) { /* fermé */ } }, d);
  setInterval(() => { try { ws.send('{"type":"ping"}'); } catch (e) { /* fermé */ } }, 4000);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    const t = m.type;
    if (t === 'ton_index') b.index = m.index;
    if ((t === 'mt_main' || t === 'uc_mot') && m.index != null) b.index = m.index;
    if (t === 'bt_mon_index') b.index = m.index;
    if (t === 'bb_ta_main') b.index = m.mon_index;
    const i = b.index;
    if (i == null) return;
    if (jeu === 'menteur' && t === 'mt_etat' && i < m.vivants.length) {
      if (m.phase === 'jeu' && m.courant === i) {
        const nb = m.nb_cartes[i];
        if (m.pose && (nb === 0 || Math.random() < 0.35)) envoyer({ type: 'mt_menteur' });
        else if (nb) envoyer({ type: 'mt_poser', cartes: [Math.floor(Math.random() * nb)] });
      } else if (m.phase === 'cible' && m.cibles && m.cibles.tireurs.includes(i) && !m.cibles.choisis.includes(i)) {
        envoyer({ type: 'mt_cible', cible: hasard(m.vivants.map((v, k) => v && k !== i ? k : -1).filter(k => k >= 0)) });
      }
    } else if (jeu === 'undercover' && t === 'uc_etat' && i < m.vivants.length) {
      if (m.phase === 'lecture' && !m.prets[i]) envoyer({ type: 'uc_pret' });
      else if (m.phase === 'indices' && m.orateur === i) envoyer({ type: 'uc_fini' }, 1500);
      else if (m.phase === 'vote' && m.vivants[i] && !m.a_vote[i]) {
        const c = (m.departage || m.vivants.map((v, k) => v ? k : -1).filter(k => k >= 0)).filter(k => k !== i);
        if (c.length) envoyer({ type: 'uc_voter', cible: hasard(c) }, 2000);
      } else if (m.phase === 'mrwhite' && m.elimination && m.elimination.joueur === i) envoyer({ type: 'uc_deviner', mot: 'truc' }, 2500);
    } else if (jeu === 'kinito') {
      const etat = t === 'effet_special' ? m.etat : (t === 'etat' ? m : null);
      if (etat) { if (etat.phase === 'lancer' && etat.joueur_courant === i) envoyer({ type: 'lancer' }); }
      else if (t === 'ton_score') {
        const possibles = m.scores_possibles || [];
        if (m.premiere_annonce || Math.random() < 0.85) envoyer({ type: 'annoncer', score: possibles.includes(m.score) ? m.score : hasard(possibles) });
        else envoyer({ type: 'abandonner' });
      } else if (t === 'a_toi_de_reagir') envoyer({ type: 'reaction', choix: Math.random() < 0.6 ? 'prends' : 'menteur' });
      else if (t === 'fin_manche') envoyer({ type: 'nouvelle_manche' }, 2500);
      else if (t === 'erreur') envoyer({ type: 'abandonner' });
    } else if (jeu === 'beerbattle' && t === 'bb_etat') {
      if (i >= m.elimine.length || m.elimine[i]) return;
      if (m.phase === 'placement' && m.joueur_en_placement === i) {
        const prises = new Set(m.positions.filter(Boolean).map(p => p.ligne + ',' + p.colonne));
        const taille = m.taille || 6, libres = [];
        for (let l = 0; l < taille; l++) for (let c = 0; c < taille; c++) if (!prises.has(l + ',' + c)) libres.push([l, c]);
        const [l, c] = hasard(libres);
        envoyer({ type: 'bb_placer', ligne: l, colonne: c });
      } else if (m.phase === 'jeu' && m.joueur_courant === i) {
        if (!m.action_faite) {
          const autres = m.elimine.map((e2, k) => !e2 && k !== i ? k : -1).filter(k => k >= 0);
          if (autres.length && Math.random() < 0.6) envoyer({ type: 'bb_attaquer', cible: hasard(autres), index_carte: null, type_attaque: 'cc_mains_nues' });
          else envoyer({ type: 'bb_ne_rien_faire' });
        } else envoyer({ type: 'bb_ramasser', decision: 'prendre', index_echange: null });
      }
    } else if (jeu === 'picolopoly' && t === 'pp_etat') {
      if (m.attente) { if (m.attente.joueur === i) envoyer({ type: 'pp_choix', valeur: null }); return; }
      if (m.dette != null || m.courant !== i) return;
      if (m.phase === 'tour') envoyer({ type: 'pp_lancer' });
      else if (m.phase === 'fin_tour') envoyer({ type: 'pp_terminer' });
    } else if (jeu === 'casino' && t === 'cs_etat' && i < m.jetons.length) {
      const tb = m.table || {};
      if (m.phase === 'hall') envoyer({ type: 'cs_choisir', jeu: hasard(['roulette', 'blackjack', 'poker']) }, 3000);
      else if (m.phase === 'mises' && !(tb.prets || [])[i]) {
        if (m.jeu === 'roulette') envoyer({ type: 'cs_rl_miser', pari: hasard(['rouge', 'pair', 'n7']), montant: 10 });
        else envoyer({ type: 'cs_bj_miser', montant: 10 });
        envoyer({ type: 'cs_pret' }, 1500);
      } else if (m.phase === 'jeu' && tb.courant === i) {
        if (m.jeu === 'blackjack') envoyer({ type: hasard(['cs_bj_tirer', 'cs_bj_rester']) });
        else if (m.jeu === 'poker') envoyer({ type: hasard(['cs_pk_suivre', 'cs_pk_suivre', 'cs_pk_coucher']) });
      }
    } else if (jeu === 'buckshot' && t === 'bt_etat' && i < m.gorgees.length) {
      if (m.phase === 'composition' && m.chargeur === i) envoyer({ type: 'bt_composer', total: 5, reel: 2 });
      else if (m.phase === 'objets' && m.courant === i) envoyer({ type: 'bt_tirer', cible: Math.floor(Math.random() * m.gorgees.length) });
    }
  };
  return b;
}

// Alice clique comme une joueuse : d'abord les actions évidentes du jeu,
// sinon un bouton du panneau au hasard (jamais ceux qui font quitter).
async function jouerAlice(p) {
  return p.evaluate((jeu) => {
    const panneau = document.getElementById('panneau');
    if (!panneau) return null;
    const ok = b => !b.disabled && b.offsetParent !== null && !/Tous les jeux|Quitter|Règles|Autre salon|Serveur|Copier|Revenir|Rejouer|Reconnecter/i.test(b.textContent);
    const boutons = [...panneau.querySelectorAll('button')].filter(ok);
    const clic = (el) => { el.click(); return el.textContent.trim().slice(0, 30); };
    if (jeu === 'menteur') {
      const carte = panneau.querySelector('.main.jouable .carte:not(.selection)');
      if (carte && !panneau.querySelector('.carte.selection')) return clic(carte);
    }
    if (jeu === 'undercover') {
      const m = panneau.querySelector('.mot-secret.voile');
      if (m && /lu/.test(panneau.innerText)) clic(m);
      const champ = panneau.querySelector('#devine');
      if (champ) { champ.value = 'truc'; }
      if (panneau.querySelector('button.danger.choisi')) return null;
    }
    const prefere = boutons.find(b => /j.ai lu|J.ai donn|^Poser|Lancer|Annoncer|Je prends|Prêt|Miser|Charger|Valider|Démarrer|Nouvelle manche|Continuer|Terminer|Placer|Ne rien faire/i.test(b.textContent));
    if (prefere) return clic(prefere);
    if (boutons.length) return clic(boutons[Math.floor(Math.random() * boutons.length)]);
    return null;
  }, jeu);
}

(async () => {
  const navigateur = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  for (const taille of TAILLES) {
    const [w, h] = taille.split('x').map(Number);
    const code = (await (await fetch(API + '/creer-salon')).json()).code;
    const ctx = await navigateur.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: +(process.env.DSF || 1), hasTouch: true });
    await ctx.addInitScript(([c, leger]) => {
      localStorage.setItem('shield.nom', JSON.stringify('Alice'));
      localStorage.setItem('shield.soiree', JSON.stringify({ code: c, table: c, serveur: 'local', vu: Date.now() }));
      localStorage.setItem('shield.leger.propose', '1');
      if (leger) localStorage.setItem('shield.leger', 'true');
    }, [code, !!process.env.LEGER]);
    const p = await ctx.newPage();
    const erreurs = [];
    p.on('pageerror', e => erreurs.push(e.message));
    await p.goto(`${SITE}${PAGES[jeu]}-3d-jeu.html?salon=${code}`);
    await attendre(2500);
    const bots = [];
    for (let k = 0; k < NB_BOTS; k++) { bots.push(bot(code, ['Bruno', 'Chloé', 'Dylan', 'Emma', 'Farid', 'Gaëlle', 'Hugo'][k])); await attendre(300); }
    await attendre(2500);
    await p.screenshot({ path: `${dossier}/${jeu}-${taille}-attente.png` });
    // Lancer : bouton Démarrer de la page (Soiree.lancer peut demander confirmation).
    await p.evaluate(() => { const b = [...document.querySelectorAll('#panneau button')].find(x => /D.marrer|Ouvrir|Lancer la partie/.test(x.textContent) && !x.disabled); if (b) b.click(); });
    await attendre(1500);
    await p.evaluate(() => { const b = [...document.querySelectorAll('[role=dialog] button')].find(x => /Lancer|Oui|Confirmer/.test(x.textContent)); if (b) b.click(); });
    const fin = Date.now() + DUREE;
    let n = 0, dernier = 0, vuAnnonce = 0, vuResultat = 0;
    while (Date.now() < fin) {
      await attendre(700);
      const vu = await p.evaluate(() => ({ a: !!document.querySelector('.res-annonce, .annonce-mt, .annonce-uc, .annonce-cs'), r: !!document.querySelector('.res-voile') }));
      const t = Date.now();
      if (vu.r && t - vuResultat > 8000) { vuResultat = t; await attendre(1300); await p.screenshot({ path: `${dossier}/${jeu}-${taille}-resultat-${n++}.png` }); continue; }
      if (vu.a && t - vuAnnonce > 6000) { vuAnnonce = t; await attendre(400); await p.screenshot({ path: `${dossier}/${jeu}-${taille}-annonce-${n++}.png` }); continue; }
      if (t - dernier > 12000) { dernier = t; await p.screenshot({ path: `${dossier}/${jeu}-${taille}-jeu-${n++}.png` }); }
      if (Math.random() < 0.6) await jouerAlice(p);
    }
    console.log(`${jeu} ${taille} : ${n} captures, ${erreurs.length ? 'ERREURS JS : ' + [...new Set(erreurs)].join(' | ') : 'aucune erreur JS'}`);
    bots.forEach(b => { try { b.ws.close(); } catch (e) { /* déjà fermé */ } });
    await ctx.close();
  }
  await navigateur.close();
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
