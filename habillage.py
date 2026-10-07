"""Habillage de l'interface : feuille de style, en-tête, titres d'étapes.

Ne contient que de la présentation : aucune règle de l'outil ni aucun rendu d'affiche n'en dépend.
"""
from html import escape

import streamlit as st

COULEUR = "#2F6FEB"  # accent neutre (le même que .streamlit/config.toml)

CSS = """
<style>
:root{
  --accent:#2F6FEB; --accent-doux:#EAF1FE; --texte:#1F2430; --muet:#6B7280;
  --bord:#DDE1E8; --bord-doux:#E8EBF0; --fond-page:#F4F5F8; --rayon:14px;
}
/* Page */
.stApp{background:var(--fond-page)}
[data-testid="stHeader"]{background:rgba(244,245,248,.88);backdrop-filter:blur(6px)}
[data-testid="stMainBlockContainer"]{max-width:1640px;padding-top:4.2rem;padding-bottom:.75rem}
/* marge sous le formulaire : donne à la colonne d'aperçu la place de rester entièrement visible en bas de page */
[data-testid="stColumn"]:has(.st-key-etape_1){padding-bottom:3.5rem}
h1{font-size:1.55rem !important;font-weight:700 !important;letter-spacing:-.01em;padding:0 !important}
.hab-entete{display:flex;align-items:baseline;gap:.7rem;margin:0 0 .35rem 0}
.hab-entete .titre{font-size:1.55rem;font-weight:700;letter-spacing:-.01em;color:var(--texte)}
.hab-entete .sous-titre{font-size:.95rem;color:var(--muet)}

/* Onglets */
[data-baseweb="tab-list"]{gap:.35rem}
[data-baseweb="tab-list"] button{padding:.55rem 1rem;border-radius:10px 10px 0 0}
[data-baseweb="tab-list"] button p{font-size:1rem;font-weight:600}

/* Étapes du formulaire */
[class*="st-key-etape_"]{background:#fff;border:1px solid var(--bord-doux);border-radius:var(--rayon);
  padding:1.1rem 1.3rem 1.3rem;box-shadow:0 1px 2px rgba(16,24,40,.04);gap:.85rem}
.hab-etape{display:flex;align-items:center;gap:.65rem;min-height:2rem}
.hab-etape .num{flex:none;width:1.9rem;height:1.9rem;border-radius:50%;background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.95rem}
.hab-etape .titre{font-size:1.15rem;font-weight:700;color:var(--texte)}
.hab-etape-aide{color:var(--muet);font-size:.9rem;margin:-.35rem 0 .1rem 2.55rem;line-height:1.35}
.hab-sous-titre{font-weight:650;font-size:.95rem;color:var(--texte);margin:.25rem 0 -.2rem}

/* Aperçu : colonne qui reste visible pendant la saisie */
@media (min-width:700px){
  [data-testid="stColumn"]:has(.st-key-apercu_fixe){position:sticky;top:4.2rem;align-self:flex-start}
}
.st-key-apercu_fixe{background:#fff;border:1px solid var(--bord-doux);border-radius:var(--rayon);
  padding:.9rem 1rem 1rem;box-shadow:0 2px 10px rgba(16,24,40,.07);gap:.6rem;
  max-height:calc(100vh - 5rem);overflow-y:auto}
.hab-vide{border:2px dashed var(--bord);border-radius:12px;padding:2.2rem 1.4rem;text-align:center;color:var(--muet);
  background:#FAFBFC}
.hab-vide .gros{font-size:1.05rem;font-weight:650;color:var(--texte);margin-bottom:.5rem}
.hab-vide ul{list-style:none;padding:0;margin:.6rem 0 0;text-align:left;display:inline-block}
.hab-vide li{padding:.15rem 0}
.hab-vide li::before{content:"○";color:var(--accent);margin-right:.5rem;font-weight:700}

/* Boutons, champs, volets */
.stButton button,.stDownloadButton button,.stLinkButton a{border-radius:10px;font-weight:550;min-height:2.6rem;height:auto}
.stButton button p,.stDownloadButton button p,.stLinkButton a p{white-space:normal;line-height:1.25}
.stDownloadButton button[kind="primary"],.stButton button[kind="primary"]{font-weight:650}
.st-key-apercu_fixe .stDownloadButton button{min-height:3rem;font-size:1.02rem}
.st-key-apercu_fixe .stButton button{min-height:2.7rem}
[data-testid="stExpander"] details{border-radius:10px;border-color:var(--bord-doux);background:#FBFBFD}
[data-testid="stExpander"] summary{font-weight:550}
[data-testid="stFileUploaderDropzone"]{border-radius:10px}
[data-testid="stCaptionContainer"]{color:var(--muet)}
[data-testid="stForm"]{max-width:440px;background:#fff;border:1px solid var(--bord-doux);border-radius:var(--rayon);padding:1.2rem 1.3rem}
/* champ « Affiner la recherche » : formulaire sans cadre, sur toute la largeur */
.st-key-zone_affiner [data-testid="stForm"]{max-width:none;background:transparent;border:none;padding:0}

@media (max-width:699px){
  .st-key-apercu_fixe{max-height:none;overflow-y:visible}
}
</style>
"""


def appliquer():
    """Injecte la feuille de style (à appeler une fois par exécution, tout au début de la page)."""
    st.html(CSS)


def entete(titre="Affiches promo", sous_titre="Pharmacie Bouton"):
    st.html(f'<div class="hab-entete"><span class="titre">{escape(titre)}</span>'
            f'<span class="sous-titre">{escape(sous_titre)}</span></div>')


def titre_etape(numero, titre, aide=""):
    html = (f'<div class="hab-etape"><span class="num">{int(numero)}</span>'
            f'<span class="titre">{escape(titre)}</span></div>')
    if aide:
        html += f'<div class="hab-etape-aide">{escape(aide)}</div>'
    st.html(html)


def sous_titre(texte):
    st.html(f'<div class="hab-sous-titre">{escape(texte)}</div>')


def etat_vide(manquants):
    """Cadre affiché à la place de l'aperçu tant que l'affiche n'est pas complète."""
    items = "".join(f"<li>{escape(m)}</li>" for m in manquants)
    st.html('<div class="hab-vide"><div class="gros">Votre affiche apparaîtra ici</div>'
            "<div>Il reste à renseigner :</div>"
            f"<ul>{items}</ul></div>")
