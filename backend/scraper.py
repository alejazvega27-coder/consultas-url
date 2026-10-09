"""
Logica de scraping: registro de fuentes, parsers y busqueda.
Reutilizada por app.py (backend/frontend).
"""

import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote_plus, urljoin, urlparse
import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import streamlit as st

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# PARSERS ESPECIFICOS POR SITIO
# ---------------------------------------------------------------------------

MONEDAS = ["DOLAR", "REAL", "PESO_ARG", "YEN", "EURO", "LIBRA"]
SUBCOLUMNAS = ["Compra", "Venta"]
MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
PATRON_TITULO_DNIT = re.compile(
    r"Tipos de cambios del mes de\s+(\w+)\s+(\d{4})", re.IGNORECASE
)

def parser_dnit(html: str) -> pd.DataFrame:
    """Parser especifico para dnit.gov.py/.../cotizaciones"""
    soup = BeautifulSoup(html, "html.parser")
    filas_totales = []

    for nodo_texto in soup.find_all(string=PATRON_TITULO_DNIT):
        texto = nodo_texto.strip()
        match = PATRON_TITULO_DNIT.search(texto)
        mes_nombre, anio = match.group(1).lower(), int(match.group(2))
        mes_num = MESES_ES.get(mes_nombre)
        if mes_num is None:
            continue

        tabla = nodo_texto.find_next("table")
        if tabla is None:
            continue

        filas = tabla.find_all("tr")
        for fila in filas[2:]:
            celdas = [c.get_text(strip=True) for c in fila.find_all(["td", "th"])]
            if not celdas or not celdas[0].isdigit():
                continue
            dia = int(celdas[0])
            valores = celdas[1:13]
            if len(valores) < 12:
                continue
            try:
                fecha = pd.Timestamp(year=anio, month=mes_num, day=dia)
            except ValueError:
                continue

            registro = {"fecha": fecha}
            idx = 0
            for moneda in MONEDAS:
                for sub in SUBCOLUMNAS:
                    registro[f"{moneda}_{sub}"] = _limpiar_numero(valores[idx])
                    idx += 1
            filas_totales.append(registro)

    df = pd.DataFrame(filas_totales)
    if not df.empty:
        df = df.sort_values("fecha").drop_duplicates(subset="fecha").reset_index(drop=True)
        df["fecha"] = df["fecha"].dt.strftime("%Y-%m-%d")
    return df

_RE_TITULO_GENERAL = re.compile(r"^\s*to(do|da)s\s+(los|las)\b", re.IGNORECASE)


def _tipo_enlace(a) -> str:
    """Clasifica un <a> como 'descargar' o 'ver' (ignora los textos de los iconos)."""
    texto = a.get_text(" ", strip=True).lower()
    for icono in ("download", "visibility"):
        texto = texto.replace(icono, "")
    texto = texto.strip()
    return texto if texto in ("descargar", "ver") else ""


def _recolectar_registro(titulo_tag, tags_corte: tuple, url_base: str):
    """Desde un tag de titulo, junta el texto (descripcion) y clasifica los
    enlaces 'Descargar'/'Ver' que le siguen, hasta el proximo tag cuyo nombre
    este en tags_corte (el siguiente registro, seccion o el pie de pagina)."""
    partes, link_descargar, link_ver, vio_enlace = [], "", "", False
    for el in titulo_tag.next_elements:
        if any(padre is titulo_tag for padre in el.parents):
            continue  # texto del propio titulo
        if isinstance(el, Tag):
            if el.name in tags_corte:
                break
            if el.name == "a":
                vio_enlace = True
                tipo = _tipo_enlace(el)
                href = (el.get("href") or "").strip()
                if tipo and href and not href.startswith(("#", "javascript")):
                    enlace = urljoin(url_base, href)
                    if tipo == "descargar" and not link_descargar:
                        link_descargar = enlace
                    elif tipo == "ver" and not link_ver:
                        link_ver = enlace
        elif type(el) is NavigableString and not vio_enlace and el.find_parent("a") is None:
            texto = " ".join(str(el).split())
            if texto:
                partes.append(texto)
    return " ".join(partes), link_descargar, link_ver


def parser_dnit_normativa(html: str, url_base: str = "https://www.dnit.gov.py",
                          max_chars: int = None) -> pd.DataFrame:
    """Parser unico para las paginas de normativas de dnit.gov.py
    (leyes, decretos, resoluciones; impositivas y aduaneras).

    Cada registro es un <h3> (titulo) seguido de una descripcion y de los enlaces
    'Descargar' y/o 'Ver'. No depende de contenedores: recorre los elementos que
    siguen a cada titulo hasta el proximo titulo, asi tolera registros sin
    descripcion, sin 'Descargar' o con 'Ver' vacio.
    """
    soup = BeautifulSoup(html, "html.parser")
    filas = []

    marcador = soup.find("h3", string=_RE_TITULO_GENERAL)
    encabezados = marcador.find_all_next("h3") if marcador else soup.find_all("h3")

    for encabezado in encabezados:
        titulo = " ".join(encabezado.get_text(" ", strip=True).split())
        if not titulo or _RE_TITULO_GENERAL.match(titulo):
            continue

        descripcion, link_descargar, link_ver = _recolectar_registro(
            encabezado, ("h1", "h2", "h3", "h4", "footer"), url_base
        )
        if not (link_descargar or link_ver):
            continue

        if max_chars and len(descripcion) > max_chars:
            descripcion = descripcion[:max_chars].strip() + "..."

        filas.append({
            "Título": titulo,
            "Descripción": descripcion,
            "Enlace Descargar": link_descargar,
            "Enlace Ver": link_ver,
        })

    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["Título", "Enlace Descargar", "Enlace Ver"]
        ).reset_index(drop=True)
    return df


parser_dnit_decretos = parser_dnit_normativa

# ---------------------------------------------------------------------------
# BIBLIOTECA IMPOSITIVA Y ADUANERA
# ---------------------------------------------------------------------------

SECCIONES_BIBLIOTECA = ("Normativas", "Guías")
_RE_FECHA_BIBLIOTECA = re.compile(r"^\d{2}-\d{2}-\d{4}$")


def _fecha_antes_de(titulo_tag) -> str:
    for el in titulo_tag.previous_elements:
        if isinstance(el, Tag) and el.name in ("h1", "h2", "h3", "h4", "h5", "h6", "a"):
            return ""
        if type(el) is NavigableString:
            texto = str(el).strip()
            if texto:
                return texto if _RE_FECHA_BIBLIOTECA.match(texto) else ""
    return ""


def parser_biblioteca_categoria(html: str, url_base: str = "https://www.dnit.gov.py",
                                max_chars: int = None) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    filas = []

    for etiqueta in SECCIONES_BIBLIOTECA:
        encabezado_seccion = soup.find(
            lambda t: t.name in ("h1", "h2", "h3") and t.get_text(strip=True).lower() == etiqueta.lower()
        )
        if encabezado_seccion is None:
            continue
        limite = encabezado_seccion.find_next(["h1", "h2", "h3"])

        titulos = []
        for el in encabezado_seccion.next_elements:
            if limite is not None and el is limite:
                break
            if isinstance(el, Tag) and el.name in ("h4", "h5", "h6"):
                titulos.append(el)

        for titulo_tag in titulos:
            titulo = " ".join(titulo_tag.get_text(" ", strip=True).split())
            if not titulo:
                continue

            descripcion, link_descargar, link_ver = _recolectar_registro(
                titulo_tag, ("h1", "h2", "h3", "h4", "h5", "h6", "footer"), url_base
            )
            if not (link_descargar or link_ver):
                continue

            if max_chars and len(descripcion) > max_chars:
                descripcion = descripcion[:max_chars].strip() + "..."

            filas.append({
                "Sección": etiqueta,
                "Fecha": _fecha_antes_de(titulo_tag),
                "Título": titulo,
                "Descripción": descripcion,
                "Enlace Descargar": link_descargar,
                "Enlace Ver": link_ver,
            })

    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["Título", "Enlace Descargar", "Enlace Ver"]
        ).reset_index(drop=True)
    return df

def _parser_biblioteca_generico(html: str, url_base: str = "https://www.dnit.gov.py",
                                max_chars: int = None) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    filas = []
    for titulo_tag in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        titulo = " ".join(titulo_tag.get_text(" ", strip=True).split())
        if not titulo or _RE_TITULO_GENERAL.match(titulo):
            continue
        descripcion, link_descargar, link_ver = _recolectar_registro(
            titulo_tag, ("h1", "h2", "h3", "h4", "h5", "h6", "footer", "nav"), url_base
        )
        if not (link_descargar or link_ver):
            continue
        if max_chars and len(descripcion) > max_chars:
            descripcion = descripcion[:max_chars].strip() + "..."
        filas.append({
            "Título": titulo,
            "Descripción": descripcion,
            "Enlace Descargar": link_descargar,
            "Enlace Ver": link_ver,
        })
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["Título", "Enlace Descargar", "Enlace Ver"]
        ).reset_index(drop=True)
    return df


def parser_biblioteca_aduanera(html: str, url_base: str = "https://www.dnit.gov.py",
                               max_chars: int = None) -> pd.DataFrame:
    for estrategia in (parser_dnit_normativa, parser_biblioteca_categoria, _parser_biblioteca_generico):
        df = estrategia(html, url_base, max_chars)
        if not df.empty:
            return df
    return pd.DataFrame()


def _limpiar_numero(texto: str):
    texto = (texto or "").strip()
    if not texto or texto.upper() in ("ND", "-", "N/D"):
        return None
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None

URL_DIGESTO = "https://digestolegislativo.gov.py"
_RE_ENCABEZADO = re.compile(r"^h[1-6]$")
_RE_PROMULGACION = re.compile(r"Promulgaci[oó]n\W*([\d/\-]+)", re.IGNORECASE)
_RE_SANCION = re.compile(r"Sanci[oó]n\W*([\d/\-]+)", re.IGNORECASE)
_RE_SEPARAR_TITULO = re.compile(r"^(.+?\b\d{4})\.\s+(.*)$", re.DOTALL)


def parser_digesto(html: str, url_base: str = URL_DIGESTO, max_chars: int = None) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")

    titulos = [
        a for a in soup.find_all("a", href=True)
        if "detalles" in a["href"] and a.find_parent(_RE_ENCABEZADO)
    ]
    if not titulos:
        titulos = [
            a for a in soup.find_all("a", href=True)
            if "detalles" in a["href"] and len(a.get_text(strip=True)) > 40
        ]
    ids_titulo = {id(a) for a in titulos}

    filas = []
    for ancla in titulos:
        texto_titulo = " ".join(ancla.get_text(" ", strip=True).split())
        if not texto_titulo:
            continue

        textos, link_pdf = [], ""
        for el in ancla.next_elements:
            if any(padre is ancla for padre in el.parents):
                continue
            if isinstance(el, Tag):
                if el.name == "a":
                    if id(el) in ids_titulo:
                        break
                    href = (el.get("href") or "").strip()
                    nombre = el.get_text(strip=True).lower()
                    if not link_pdf and href and (nombre == "pdf" or href.lower().replace(" ", "").endswith(".pdf")):
                        link_pdf = urljoin(url_base, href.replace(" ", "%20"))
            elif type(el) is NavigableString:
                textos.append(str(el))

        bloque = " ".join(" ".join(textos).split())
        prom = _RE_PROMULGACION.search(bloque)
        sanc = _RE_SANCION.search(bloque)

        m = _RE_SEPARAR_TITULO.match(texto_titulo)
        if m:
            titulo, descripcion = m.group(1), m.group(2)
        else:
            titulo, _, descripcion = texto_titulo.partition(". ")
        if max_chars and len(descripcion) > max_chars:
            descripcion = descripcion[:max_chars].strip() + "..."

        filas.append({
            "Título": titulo.strip(),
            "Descripción": descripcion.strip(),
            "Promulgación": prom.group(1) if prom else "",
            "Sanción": sanc.group(1) if sanc else "",
            "Enlace Descargar": link_pdf,
            "Enlace Ver": urljoin(url_base, ancla["href"].strip().replace(" ", "%20")),
        })

    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(subset="Enlace Ver").reset_index(drop=True)
    return df


def parser_generico(html: str) -> pd.DataFrame:
    tablas = pd.read_html(html)
    if not tablas:
        return pd.DataFrame()
    tabla_mas_grande = max(tablas, key=lambda t: t.size)
    return tabla_mas_grande


# ---------------------------------------------------------------------------
# REGISTRO DE FUENTES
# ---------------------------------------------------------------------------

FUENTES = [
    {
        "id": "dnit_cotizaciones",
        "nombre": "DNIT - Historial de Cotizaciones",
        "alias": ["dnit", "cotizaciones", "dolar", "tipo de cambio", "divisas", "guaranies"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/cotizaciones",
        "categoria": "cotizaciones",
        "parser": parser_dnit,
    },
    {
        "id": "dnit_leyes_imp",
        "nombre": "DNIT - Leyes (Impositiva)",
        "alias": ["leyes impositivas"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/leyes",
        "categoria": "impositiva",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "dnit_decretos_imp",
        "nombre": "DNIT - Decretos (Impositiva)",
        "alias": ["decreto tributario", "decretos impositivos"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/decretos",
        "categoria": "impositiva",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "dnit_resoluciones_imp",
        "nombre": "DNIT - Resoluciones (Impositiva)",
        "alias": ["resoluciones impositivas"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/resoluciones",
        "categoria": "impositiva",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "dnit_leyes_adu",
        "nombre": "DNIT - Leyes (Aduanera)",
        "alias": ["leyes aduaneras"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/leyes1",
        "categoria": "aduanera",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "dnit_decretos_adu",
        "nombre": "DNIT - Decretos (Aduanera)",
        "alias": ["decretos aduaneros"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/decretos1",
        "categoria": "aduanera",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "dnit_resoluciones_adu",
        "nombre": "DNIT - Resoluciones (Aduanera)",
        "alias": ["resoluciones aduaneras"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/resoluciones1",
        "categoria": "aduanera",
        "parser": parser_dnit_normativa,
    },
    {
        "id": "digesto_tributario",
        "nombre": "Digesto Legislativo - Tributario en general",
        "alias": ["digesto", "legislativo", "norma", "ley"],
        "url": "https://digestolegislativo.gov.py/9-tributarios/308/91-tributario-en-general",
        "url_datos": "https://digestolegislativo.gov.py/paginacion/interna.php?id=308&action=ajax&page={pagina}",
        "categoria": "impositiva",
        "parser": parser_digesto,
    },
    {
        "id": "digesto_aduanero",
        "nombre": "Digesto Legislativo - Aduanero",
        "alias": ["digesto aduanero"],
        "url": "https://digestolegislativo.gov.py/9-tributarios/309/92-aduanero",
        "url_datos": "https://digestolegislativo.gov.py/paginacion/interna.php?id=309&action=ajax&page={pagina}",
        "categoria": "aduanera",
        "parser": parser_digesto,
    },
    {
        "id": "dnit_biblioteca_iva",
        "nombre": "DNIT - Biblioteca IVA",
        "alias": ["iva"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/iva",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_irp",
        "nombre": "DNIT - Biblioteca IRP",
        "alias": ["irp"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/irp",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_ire",
        "nombre": "DNIT - Biblioteca IRE",
        "alias": ["ire"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/ire",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_idu",
        "nombre": "DNIT - Biblioteca IDU",
        "alias": ["idu"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/idu",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_inr",
        "nombre": "DNIT - Biblioteca INR",
        "alias": ["inr"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/inr",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_isc",
        "nombre": "DNIT - Biblioteca ISC",
        "alias": ["isc"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/isc",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_aduanera",
        "nombre": "DNIT - Biblioteca Aduanera",
        "alias": ["biblioteca aduanera"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/aduanera",
        "categoria": "aduanera",
        "parser": parser_biblioteca_aduanera,
    },
    {
        "id": "dnit_biblioteca_ire_resimple",
        "nombre": "DNIT - Biblioteca IRE RESIMPLE",
        "alias": ["ire resimple", "resimple"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/ire-resimple",
        "categoria": "impositiva",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_pvaa",
        "nombre": "DNIT - Biblioteca PVAA",
        "alias": ["pvaa"],
        "url": "https://www.dnit.gov.py/en/web/portal-institucional/personas-vinculadas-a-la-actividad-aduanera",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_vui",
        "nombre": "DNIT - Biblioteca VUI",
        "alias": ["vui"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/vui",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_kitapp",
        "nombre": "DNIT - Biblioteca KITAPP",
        "alias": ["kitapp"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/kitapp",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_tvf",
        "nombre": "DNIT - Biblioteca TVF",
        "alias": ["tvf"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/trafico-vecinal-fronterizo",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_remesa_expresa",
        "nombre": "DNIT - Biblioteca Remesa Expresa",
        "alias": ["remesa","expresa"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/remesa-expresa",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
    {
        "id": "dnit_biblioteca_remates",
        "nombre": "DNIT - Biblioteca Remates",
        "alias": ["remate","remates"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/remates-y-comercializacion-de-mercaderias",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
   {
        "id": "dnit_biblioteca_regimenes",
        "nombre": "DNIT - Biblioteca Regímenes",
        "alias": ["regimen","regimenes"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/regímenes-aduaneros",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
   {
        "id": "dnit_biblioteca_oea",
        "nombre": "DNIT - Biblioteca OEA",
        "alias": ["oea","operador","económico","autorizado"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/oea-operador-economico-autorizado",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
   {
        "id": "dnit_biblioteca_mesa",
        "nombre": "DNIT - Biblioteca Mesa de Entrada",
        "alias": ["mesa","entrada"],
        "url": "https://secure.aduana.gov.py/dna-apps/public/consultaExpediente",
        "categoria": "aduanera",
        "parser": parser_biblioteca_categoria,
    },
]

# Bibliotecas con sus respectivos colores institucionales
BIBLIOTECA_IMPOSITIVA = [
    {"nombre": "IVA", "fuente": "dnit_biblioteca_iva",
     "logo": "https://www.dnit.gov.py/documents/44828/50401/logo-iva.svg/b5ec899c-8bcd-28fa-5498-b33b11db275e?t=1678484263694",
     "color": "#0284c7"},
    {"nombre": "IRP", "fuente": "dnit_biblioteca_irp",
     "logo": "https://www.dnit.gov.py/documents/44828/50401/logo-irp.svg/bad8c8e3-9a57-e079-4bfe-e7638a4e32f6?t=1678484263863",
     "color": "#eab308"},
    {"nombre": "IRE", "fuente": "dnit_biblioteca_ire",
     "logo": "https://www.dnit.gov.py/documents/44828/50401/logo-ire.svg/54b7b93d-a677-3a4b-42d2-432ec08e5735?t=1678484263808",
     "color": "#0d9488"},
    {"nombre": "IDU", "fuente": "dnit_biblioteca_idu",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-idu+%281%29.svg/f9143016-5782-cc0f-a947-dd8276f97742?t=1678825637846",
     "color": "#9d174d"},
    {"nombre": "INR", "fuente": "dnit_biblioteca_inr",
     "logo": "https://www.dnit.gov.py/documents/44828/50401/logo-inr.svg/86eb7c2e-0a8c-40a5-9ec1-9405c369b6fe?t=1678484263567",
     "color": "#15803d"},
    {"nombre": "ISC", "fuente": "dnit_biblioteca_isc",
     "logo": "https://www.dnit.gov.py/documents/44828/50401/logo-isc.svg/eac5b04c-5626-99d6-9b3a-f525321019d2?t=1678484263637",
     "color": "#4b5563"},
    {"nombre": "IRE RESIMPLE", "fuente": "dnit_biblioteca_ire_resimple",
     "logo": "https://www.dnit.gov.py/documents/20123/251762/logo-ire-simple.svg/d60f9112-3c2c-cef7-bdd0-18742b3c3cce?t=1683762469002",
     "color": "#06b6d4"},
]

BIBLIOTECA_ADUANERA = [
    {"nombre": "PVAA", "fuente": "dnit_biblioteca_pvaa",
     "logo": "https://www.dnit.gov.py/documents/44828/0/BOTON+PVAA+108X48_Mesa+de+trabajo+1.png/af50fc21-805e-602c-9a84-7c463dc58910?t=1753906959193",
     "color": "#6b7280"},
    {"nombre": "VUI", "fuente": "dnit_biblioteca_vui",
     "logo": "https://www.dnit.gov.py/documents/20123/1067002/logo-vui.svg/a2adf0b8-b232-267b-38c4-9fb934ad1c38?t=1726087798981",
     "color": "#7c3aed"},
    {"nombre": "KITAPP", "fuente": "dnit_biblioteca_kitapp",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-regimenes.svg/677fa80b-0709-40df-49fa-6b53af1ed358?t=1727216298868",
     "color": "#059669"},
    {"nombre": "TVF", "fuente": "dnit_biblioteca_tvf",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-tvf.svg/b7312864-7c10-d7a8-76dc-f231056e986d?t=1727215159186",
     "color": "#d97706"},
    {"nombre": "REMESAS", "fuente": "dnit_biblioteca_remesa_expresa",
     "logo": "https://www.dnit.gov.py/documents/44828/0/Logos+Impuestos+2024-09.png/acc51853-a560-1b9a-463b-d52c14ab7220?t=1732132256094",
     "color": "#c026d3"},
    {"nombre": "REMATES", "fuente": "dnit_biblioteca_remates",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-remates.svg/e1c824c6-e5fd-dcc4-dc5a-609ced87cdf2?t=1727216226837",
     "color": "#1d4ed8"},
    {"nombre": "REGIMENES", "fuente": "dnit_biblioteca_regimenes",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-regimenes.svg/677fa80b-0709-40df-49fa-6b53af1ed358?t=1727216298868",
     "color": "#16a34a"},
    {"nombre": "OEA", "fuente": "dnit_biblioteca_oea",
     "logo": "https://www.dnit.gov.py/documents/44828/0/Logos+Impuestos+2024-08+%281%29.png/6702f7ef-7d0e-893d-275b-720d4fa79fc7?t=1732144616029",
     "color": "#dc2626"},
    {"nombre": "MESA DE ENTRADA", "fuente": "dnit_biblioteca_mesa",
     "logo": "https://www.dnit.gov.py/documents/44828/0/logo-mesa-de-entrada.svg/b27b55a8-c48c-35a3-0e99-af73f6371a14?t=1727216341673",
     "color": "#312e81"},
]

def obtener_fuente(fuente_id: str):
    return next((f for f in FUENTES if f["id"] == fuente_id), None)


def buscar_fuente(termino: str, fuentes=FUENTES):
    termino_norm = termino.lower().strip()
    if not termino_norm:
        return None

    for fuente in fuentes:
        textos = [fuente["nombre"].lower()] + [a.lower() for a in fuente["alias"]]
        if any(termino_norm in t or t in termino_norm for t in textos):
            return fuente

    todos_alias = {}
    for fuente in fuentes:
        for alias in fuente["alias"] + [fuente["nombre"]]:
            todos_alias[alias.lower()] = fuente

    coincidencias = difflib.get_close_matches(
        termino_norm, todos_alias.keys(), n=1, cutoff=0.6
    )
    if coincidencias:
        return todos_alias[coincidencias[0]]

    return None

def obtener_html(url: str, extra_headers: dict = None) -> str:
    headers = {**HEADERS, **(extra_headers or {})}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def _tiene_normas(html: str) -> bool:
    return "romulgaci" in html


def _total_paginas(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    numeros = [
        int(a.get_text(strip=True)) for a in soup.find_all("a")
        if a.get_text(strip=True).isdigit() and "javascript" in (a.get("href") or "")
    ]
    return max(numeros, default=1)


@st.cache_data(ttl=3600, show_spinner=False)
def obtener_html_paginado(url_plantilla: str, max_paginas: int = 100) -> str:
    extra = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://digestolegislativo.gov.py/",
    }

    def bajar(pagina):
        try:
            return obtener_html(url_plantilla.format(pagina=pagina), extra)
        except requests.RequestException:
            return ""

    primera = obtener_html(url_plantilla.format(pagina=1), extra)
    if not _tiene_normas(primera):
        return primera

    partes = [primera]
    total = min(_total_paginas(primera), max_paginas)

    if total > 1:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for html in pool.map(bajar, range(2, total + 1)):
                if html and _tiene_normas(html):
                    partes.append(html)

    pagina = total + 1
    while pagina <= max_paginas:
        html = bajar(pagina)
        if not html or not _tiene_normas(html) or html in partes:
            break
        partes.append(html)
        pagina += 1

    return "\n".join(partes)


_RE_EXTENSION = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|csv|txt|zip|rar)$", re.IGNORECASE)
_RE_CARACTERES_INVALIDOS = re.compile(r'[\\/:*?"<>|]+')


def nombre_archivo(url: str, titulo: str = "archivo") -> str:
    ruta = unquote_plus(urlparse(url).path)
    for segmento in reversed(ruta.split("/")):
        if _RE_EXTENSION.search(segmento):
            return _RE_CARACTERES_INVALIDOS.sub("_", segmento).strip()
    base = _RE_CARACTERES_INVALIDOS.sub("_", titulo).strip() or "archivo"
    return base[:120] + ".pdf"


@st.cache_data(ttl=3600, show_spinner=False, max_entries=30)
def descargar_archivo(url: str):
    extra = {"Referer": "https://digestolegislativo.gov.py/"} if "digestolegislativo" in url else None
    headers = {**HEADERS, **(extra or {})}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    tipo = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
    if tipo == "text/html":
        raise ValueError("El servidor devolvió una página web en lugar del archivo.")
    return resp.content, tipo


def ejecutar_busqueda(termino: str, fuente_id: str = None):
    termino_lower = termino.lower().strip()
    fuente = obtener_fuente(fuente_id) if fuente_id else buscar_fuente(termino)

    if fuente is None:
        fuente = FUENTES[0]

    if "url_datos" in fuente:
        html = obtener_html_paginado(fuente["url_datos"])
    else:
        html = obtener_html(fuente["url"])
    df = fuente["parser"](html)

    if df.empty or not termino_lower:
        return fuente, df

    palabras_ignorar = {"dnit", "cotizaciones", "tipo", "de", "cambio", "divisas", "guaranies",
                        "digesto", "legislativo", "norma"}
    tokens = [t for t in termino_lower.split() if t not in palabras_ignorar]

    if not tokens:
        return fuente, df

    df_str = df.astype(str).apply(lambda col: col.str.lower())
    
    filtro = pd.Series([True] * len(df), index=df.index)
    for token in tokens:
        coincide_token = df_str.apply(lambda row: row.str.contains(token, na=False)).any(axis=1)
        filtro = filtro & coincide_token

    df_filtrado = df[filtro].reset_index(drop=True)
    return fuente, df_filtrado
    
