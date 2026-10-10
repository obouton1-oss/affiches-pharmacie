"""Habillage de l'interface : feuille de style, en-tête, titres d'étapes, écran de connexion, aide.

Ne contient que de la présentation : aucune règle de l'outil ni aucun rendu d'affiche n'en dépend.
"""
import base64
from html import escape

import streamlit as st

COULEUR = "#2F6FEB"  # accent neutre (le même que .streamlit/config.toml)

# Pictogrammes dessinés en CSS (st.html retire les balises <svg> : ils sont donnés en image de fond)
_SVG_ETIQUETTE = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2' "
    "stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V4a1 1 0 0 1 1-1h9l7.6 7.6a2 2 0 0 1 0 2.8z'/>"
    "<circle cx='7.6' cy='7.6' r='1.4' fill='white' stroke='none'/></svg>")
_SVG_AFFICHE_VIDE = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 150' fill='none'>"
    "<rect x='6' y='4' width='108' height='142' rx='8' fill='white' stroke='#C9D3E6' stroke-width='2.5'/>"
    "<rect x='38' y='16' width='44' height='52' rx='8' fill='#EEF2FA'/>"
    "<rect x='26' y='78' width='68' height='8' rx='4' fill='#DCE4F3'/>"
    "<rect x='36' y='92' width='48' height='6' rx='3' fill='#E7ECF6'/>"
    "<rect x='24' y='108' width='72' height='26' rx='6' fill='#FFE27A'/>"
    "<rect x='42' y='116' width='36' height='10' rx='4' fill='#F2C94C'/></svg>")


def _image_css(svg):
    return "url(data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii") + ")"


_CSS = """
<style>
:root{
  --accent:#2F6FEB; --accent-fonce:#2459C9; --accent-doux:#EAF1FE; --texte:#1F2430; --muet:#6B7280;
  --bord:#DDE1E8; --bord-doux:#E8EBF0; --fond-page:#F4F5F8; --rayon:14px; --ok:#16A34A; --ok-doux:#DCFCE7;
  --ombre:0 1px 2px rgba(16,24,40,.05),0 8px 24px -12px rgba(16,24,40,.12);
}
/* Page */
.stApp{background:linear-gradient(180deg,#F8F9FC 0,#F2F4F8 100%) fixed}
[data-testid="stHeader"]{background:#F7F8FB;border-bottom:1px solid rgba(221,225,232,.6)}
[data-testid="stMainBlockContainer"]{max-width:1640px;padding-top:4.2rem;padding-bottom:.75rem}
/* marge sous le formulaire : donne à la colonne d'aperçu la place de rester entièrement visible en bas de page */
[data-testid="stColumn"]:has(.st-key-etape_1){padding-bottom:3.5rem}
h1{font-size:1.55rem !important;font-weight:700 !important;letter-spacing:-.01em;padding:0 !important}
@keyframes hab-apparait{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}

/* En-tête */
.hab-entete{display:flex;align-items:center;gap:.75rem;margin:0 0 .25rem 0}
.hab-marque{flex:none;width:2.3rem;height:2.3rem;border-radius:10px;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#4C8BF5 0,var(--accent) 55%,#2459C9 100%);box-shadow:0 4px 12px -4px rgba(47,111,235,.6)}
.hab-marque::before{content:"";width:1.25rem;height:1.25rem;background:__ETIQUETTE__ center/contain no-repeat}
.hab-entete .titre{font-size:1.5rem;font-weight:750;letter-spacing:-.015em;color:var(--texte);line-height:1.1}
.hab-entete .sous-titre{font-size:.85rem;color:var(--muet);background:#fff;border:1px solid var(--bord-doux);
  border-radius:999px;padding:.18rem .75rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:22rem}

/* Onglets */
[data-baseweb="tab-list"]{gap:.25rem;border-bottom:1px solid var(--bord-doux)}
[data-baseweb="tab-list"] button{padding:.6rem 1.05rem;border-radius:10px 10px 0 0;transition:background .15s}
[data-baseweb="tab-list"] button:hover{background:rgba(47,111,235,.06)}
[data-baseweb="tab-list"] button p{font-size:1rem;font-weight:600}
[data-baseweb="tab-list"] button[aria-selected="true"] p{color:var(--accent)}
[data-baseweb="tab-highlight"]{height:3px !important;border-radius:3px 3px 0 0;background:var(--accent) !important}
[data-baseweb="tab-border"]{display:none}

/* Étapes du formulaire */
[class*="st-key-etape_"]{background:#fff;border:1px solid var(--bord-doux);border-radius:16px;
  padding:1.15rem 1.35rem 1.35rem;box-shadow:var(--ombre);gap:.85rem}
.hab-etape{display:flex;align-items:center;gap:.7rem;min-height:2rem}
.hab-etape .num{flex:none;width:1.95rem;height:1.95rem;border-radius:50%;background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.95rem;
  box-shadow:0 0 0 4px var(--accent-doux);transition:background .2s,box-shadow .2s}
.hab-etape .num.fait{background:var(--ok);box-shadow:0 0 0 4px var(--ok-doux)}
.hab-etape .titre{font-size:1.15rem;font-weight:700;color:var(--texte);letter-spacing:-.005em}
.hab-etape-aide{color:var(--muet);font-size:.9rem;margin:-.35rem 0 .1rem 2.65rem;line-height:1.35}
.hab-sous-titre{font-weight:650;font-size:.95rem;color:var(--texte);margin:.25rem 0 -.2rem}
.hab-petit-titre{font-size:.82rem;font-weight:600;color:var(--muet);margin:.1rem 0 -.55rem .1rem}
/* Réglages d'un texte (police, style, couleur, taille), juste sous son champ de saisie */
[class*="st-key-reglages_texte_"]{background:#F6F8FB;border:1px solid var(--bord-doux);border-radius:10px;
  padding:.3rem .55rem .25rem;margin-top:-.45rem}
[class*="st-key-reglages_texte_"] [data-testid="stSlider"]{padding:0 .35rem}
[class*="st-key-reglages_texte_"] [data-baseweb="select"] > div{min-height:2.1rem}
[class*="st-key-reglages_texte_"] [data-testid="stColorPicker"] > div{justify-content:center}

/* Aperçu : colonne qui reste visible pendant la saisie */
@media (min-width:700px){
  [data-testid="stColumn"]:has(.st-key-apercu_fixe),[data-testid="stColumn"]:has(.st-key-mer_apercu){position:sticky;top:4.2rem;align-self:flex-start}
}
.st-key-apercu_fixe,.st-key-mer_apercu{background:#fff;border:1px solid var(--bord-doux);border-radius:16px;
  padding:.9rem 1rem 1rem;box-shadow:0 2px 6px rgba(16,24,40,.05),0 16px 36px -16px rgba(16,24,40,.22);gap:.6rem;
  max-height:calc(100vh - 5rem);overflow-y:auto}
.hab-vide{border:2px dashed var(--bord);border-radius:14px;padding:1.6rem 1.4rem 1.8rem;text-align:center;color:var(--muet);
  background:linear-gradient(180deg,#FBFCFE,#F6F8FC);animation:hab-apparait .35s ease-out}
.hab-vide::before{content:"";display:block;width:84px;height:105px;margin:0 auto .9rem;
  background:__AFFICHE__ center/contain no-repeat}
.hab-vide .gros{font-size:1.05rem;font-weight:650;color:var(--texte);margin-bottom:.5rem}
.hab-vide ul{list-style:none;padding:0;margin:.6rem 0 0;text-align:left;display:inline-block}
.hab-vide li{padding:.15rem 0}
.hab-vide li::before{content:"○";color:var(--accent);margin-right:.5rem;font-weight:700}

/* Boutons, champs, volets */
.stButton button,.stDownloadButton button,.stLinkButton a{border-radius:10px;font-weight:550;min-height:2.6rem;height:auto;
  transition:transform .12s ease,box-shadow .12s ease,background .12s ease,border-color .12s ease}
.stButton button p,.stDownloadButton button p,.stLinkButton a p{white-space:normal;line-height:1.25}
.stButton button[kind="secondary"]:hover,.stDownloadButton button[kind="secondary"]:hover,.stLinkButton a:hover{
  border-color:var(--accent);background:var(--accent-doux);color:var(--accent-fonce)}
.stDownloadButton button[kind="primary"],.stButton button[kind="primary"],.stFormSubmitButton button[kind="primaryFormSubmit"]{
  font-weight:650;box-shadow:0 1px 2px rgba(47,111,235,.35),0 8px 16px -8px rgba(47,111,235,.6)}
.stDownloadButton button[kind="primary"]:hover,.stButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primaryFormSubmit"]:hover{background:var(--accent-fonce);border-color:var(--accent-fonce);transform:translateY(-1px)}
.stDownloadButton button[kind="primary"]:active,.stButton button[kind="primary"]:active{transform:none}
.st-key-apercu_fixe .stDownloadButton button{min-height:3rem;font-size:1.02rem}
.st-key-apercu_fixe .stButton button{min-height:2.5rem}
[data-testid="stExpander"] details{border-radius:10px;border-color:var(--bord-doux);background:#FBFBFD;transition:background .15s}
[data-testid="stExpander"] details:hover{background:#F7F9FD}
[data-testid="stExpander"] summary{font-weight:550}
[data-testid="stFileUploaderDropzone"]{border-radius:10px}
[data-testid="stCaptionContainer"]{color:var(--muet)}
[data-baseweb="input"],[data-baseweb="select"] > div,[data-baseweb="textarea"]{border-radius:10px !important}
[data-testid="stForm"]{max-width:440px;background:#fff;border:1px solid var(--bord-doux);border-radius:var(--rayon);padding:1.2rem 1.3rem}
/* champ « Affiner la recherche » : formulaire sans cadre, sur toute la largeur */
.st-key-zone_affiner [data-testid="stForm"]{max-width:none;background:transparent;border:none;padding:0}

/* Raccourcis de dates */
.st-key-raccourcis_dates .stButton button{min-height:2.1rem;font-size:.88rem;border-radius:999px;padding:.1rem .6rem}
.st-key-raccourcis_dates p{margin:0}

/* Types d'affiche (barre en haut de « Créer une affiche ») */
.st-key-barre_types{background:#fff;border:1px solid var(--bord-doux);border-radius:14px;padding:.6rem 1rem .75rem;gap:.3rem;
  box-shadow:var(--ombre);margin-bottom:.5rem}
.st-key-barre_types [data-testid="stPills"] button{border-radius:999px}
.st-key-barre_types [data-testid="stCaptionContainer"] p{margin:0}

/* Produits repris de l'historique */
.st-key-recents .stButton button{min-height:2.1rem;font-size:.88rem;border-radius:999px;padding:.1rem .6rem}

/* Premiers pas */
.st-key-premiers_pas{background:linear-gradient(135deg,#F3F7FF 0,#fff 70%);border:1px solid #D6E3FB;border-radius:16px;
  padding:1.1rem 1.35rem 1.2rem;box-shadow:var(--ombre);animation:hab-apparait .35s ease-out;margin-bottom:.4rem}
.hab-pas{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.9rem;margin:.2rem 0 .3rem}
.hab-pas > div{display:flex;gap:.6rem;align-items:flex-start}
.hab-pas .n{flex:none;width:1.7rem;height:1.7rem;border-radius:50%;background:var(--accent);color:#fff;font-weight:700;
  font-size:.85rem;display:flex;align-items:center;justify-content:center}
.hab-pas b{display:block;color:var(--texte);margin-bottom:.1rem}
.hab-pas span.t{color:var(--muet);font-size:.9rem;line-height:1.35}
.hab-pas-titre{font-size:1.1rem;font-weight:700;color:var(--texte);margin-bottom:.5rem}
@media (max-width:900px){.hab-pas{grid-template-columns:repeat(2,minmax(0,1fr))}}

/* Écran de connexion */
.st-key-connexion{max-width:440px;margin:6vh auto 0;animation:hab-apparait .4s ease-out}
.st-key-connexion [data-testid="stForm"]{max-width:none;box-shadow:var(--ombre)}
.hab-connexion{text-align:center;margin-bottom:1.3rem}
.hab-connexion .marque{width:3.6rem;height:3.6rem;border-radius:16px;margin:0 auto .9rem;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#4C8BF5 0,var(--accent) 55%,#2459C9 100%);box-shadow:0 10px 24px -8px rgba(47,111,235,.65)}
.hab-connexion .marque::before{content:"";width:1.9rem;height:1.9rem;background:__ETIQUETTE__ center/contain no-repeat}
.hab-connexion .titre{font-size:1.8rem;font-weight:750;letter-spacing:-.02em;color:var(--texte)}
.hab-connexion .sous{color:var(--muet);margin-top:.25rem}
.hab-connexion-pied{text-align:center;color:var(--muet);font-size:.85rem;margin-top:.9rem}

.st-key-apercu_polices{position:absolute;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none}

@media (max-width:699px){
  .st-key-apercu_fixe,.st-key-mer_apercu{max-height:none;overflow-y:visible}
}
</style>
"""

CSS = _CSS.replace("__ETIQUETTE__", _image_css(_SVG_ETIQUETTE)).replace("__AFFICHE__", _image_css(_SVG_AFFICHE_VIDE))

AIDE_MARKDOWN = """
**Pour créer une affiche**
1. **Chercher le produit** par son nom ou son code CIP13 / EAN : l'outil cherche le visuel (à vérifier).
2. **Saisir le prix promo** (7,90 ou 7.9). Prix barré et dates sont facultatifs ; des raccourcis proposent « Ce mois-ci » ou « Mois prochain ».
3. **Retoucher sur l'aperçu**, à droite : un clic sur un élément (marque, prix, photo…) affiche ses réglages juste au-dessus de l'affiche : texte, couleur, police, gras, italique, souligné, taille. On le fait glisser pour le déplacer ; il « colle » au centre de l'affiche (trait rouge).
4. **Télécharger le PDF** : l'affiche est aussi gardée dans l'historique.

**Astuces**
- **Annuler** une retouche : flèche ↶ au-dessus de l'aperçu (ou Ctrl + Z après un clic sur l'affiche) ; ↷ pour rétablir.
- **Au clavier**, après un clic sur un élément : flèches pour le déplacer finement (Maj + flèche : plus vite), + / − pour la taille, Suppr pour retirer un élément ajouté, Ctrl + D pour le dupliquer, Échap pour désélectionner. Un double-clic sur un texte permet de l'écrire directement.
- Sans élément sélectionné, la barre au-dessus de l'aperçu ajoute un texte, un prix ou une forme, et change la police ou le thème de couleurs de toute l'affiche.
- Pas de visuel ? Copier une image sur le web, cliquer dans le cadre « Coller une image » et coller (Ctrl + V, ou ⌘ + V sur Mac). On peut aussi y coller l'adresse de la page du produit : sa photo principale est prise. Dans tous les cas, l'outil va chercher la photo en grand quand le site la propose (les sites montrent souvent une miniature).
- Les photos proposées indiquent leur netteté réelle (« Très nette », « Nette », « Correcte », « Petite ») : une miniature agrandie par un site est grande mais floue, elle est signalée et classée après.
- Sous chaque texte (marque, détail, prix), une ligne règle sa police, gras / italique / souligné, sa couleur et sa taille.
- Autres offres (« LE 2e À –50 % »…) : le petit texte au-dessus de l'offre a ses propres réglages ; déplacé sur l'aperçu, il quitte le bandeau.
- **Enregistrer et passer à la suivante** prépare l'affiche d'après : produit et prix repartent à zéro, le format, le style et les dates sont gardés.
- **Reprendre une affiche récente** (sous la recherche) rouvre une affiche déjà faite pour la modifier.
- **Types d'affiche** (en haut de la page) : un type garde un format, un logo, une photo (ou non), une police et des couleurs, par exemple « Petite affiche de rayon ». **Gérer les types › Créer pas à pas** pose les mêmes questions qu'à la première visite (avec import d'une affiche existante, image ou PDF, pour en relever les couleurs) ; « Créer tout de suite » reprend simplement les réglages de l'affiche en cours. Ensuite, un clic suffit pour passer d'un type à l'autre.
- **Logo de la marque** (SVR, Avène…) : sous le nom de la marque, importer ou coller son logo une fois ; il remplace ensuite le nom sur chaque affiche de cette marque.
- Dans l'**Historique**, chaque affiche indique le nombre de jours avant sa suppression et se **télécharge en PDF** pour être gardée sur l'ordinateur.
- L'onglet **Page A4 regroupée** met jusqu'à 6 affiches sur une feuille.
- L'onglet **Ma pharmacie** règle le nom, le logo, les couleurs et l'ordre des éléments du type d'affiche choisi, et range les logos de marques.
"""


def appliquer():
    """Injecte la feuille de style (à appeler une fois par exécution, tout au début de la page)."""
    st.html(CSS)


def entete(titre="Affiches promo", sous_titre="Pharmacie Bouton"):
    marque = '<span class="hab-marque"></span>'
    pastille = f'<span class="sous-titre">{escape(sous_titre)}</span>' if sous_titre else ""
    st.html(f'<div class="hab-entete">{marque}<span class="titre">{escape(titre)}</span>{pastille}</div>')


def connexion_entete(titre="Affiches promo", sous="Créez vos affiches de promotions en quelques clics."):
    st.html('<div class="hab-connexion"><div class="marque"></div>'
            f'<div class="titre">{escape(titre)}</div><div class="sous">{escape(sous)}</div></div>')


def connexion_pied(texte):
    st.html(f'<div class="hab-connexion-pied">{escape(texte)}</div>')


def titre_etape(numero, titre, aide="", fait=False):
    """Titre numéroté d'une étape ; fait=True : la pastille devient verte avec une coche (étape complète)."""
    pastille = '<span class="num fait">✓</span>' if fait else f'<span class="num">{int(numero)}</span>'
    html = f'<div class="hab-etape">{pastille}<span class="titre">{escape(titre)}</span></div>'
    if aide:
        html += f'<div class="hab-etape-aide">{escape(aide)}</div>'
    st.html(html)


def sous_titre(texte):
    st.html(f'<div class="hab-sous-titre">{escape(texte)}</div>')


def petit_titre(texte):
    """Petit intitulé discret au-dessus d'une ligne de réglages."""
    st.html(f'<div class="hab-petit-titre">{escape(texte)}</div>')



def etat_vide(manquants):
    """Cadre affiché à la place de l'aperçu tant que l'affiche n'est pas complète."""
    items = "".join(f"<li>{escape(m)}</li>" for m in manquants)
    st.html('<div class="hab-vide"><div class="gros">Votre affiche apparaîtra ici</div>'
            "<div>Il reste à renseigner :</div>"
            f"<ul>{items}</ul></div>")


def premiers_pas():
    """Les quatre gestes de base (affichés tant que la pharmacie n'a pas cliqué sur « J'ai compris »)."""
    pas = (("Cherchez le produit", "Par son nom ou son code CIP13\u00a0/\u00a0EAN, l'outil cherche le visuel (à vérifier)."),
           ("Saisissez le prix", "Prix promo, prix barré et dates si besoin. Des raccourcis proposent les dates du mois."),
           ("Retouchez l'aperçu", "À droite : un clic sur un élément pour changer son texte, sa couleur ou sa police ; glisser pour le déplacer."),
           ("Téléchargez le PDF", "Il est aussi gardé dans l'historique. «\u00a0Passer à la suivante\u00a0» prépare l'affiche d'après."))
    cases = "".join(f'<div><span class="n">{i}</span><div><b>{escape(t)}</b><span class="t">{escape(d)}</span></div></div>'
                    for i, (t, d) in enumerate(pas, 1))
    st.html(f'<div class="hab-pas-titre">Premiers pas : une affiche en 4 gestes</div><div class="hab-pas">{cases}</div>')


def contenu_aide():
    """Texte du bouton « Aide » (en haut de la page)."""
    st.markdown(AIDE_MARKDOWN)
