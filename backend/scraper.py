"""
Logica de scraping: registro de fuentes, parsers y busqueda.
Reutilizada por app.py (backend/frontend).
"""

import difflib
import re
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

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

def parser_dnit_decretos(html: str, url_base: str = "https://www.dnit.gov.py", max_chars: int = 300) -> pd.DataFrame:
    """Parser optimizado que extrae URLs limpias y descripciones más amplias."""
    soup = BeautifulSoup(html, "html.parser")
    filas_totales = []
    
    bloques = soup.find_all('div', class_='resultado-item')
    
    if not bloques:
        titulos = soup.find_all(['h3', 'h4'])
        bloques = [t.find_parent('div') or t.parent for t in titulos]

    for bloque in bloques:
        if not bloque:
            continue
            
        titulo_elem = bloque.find(['h3', 'h4', 'a'])
        titulo = titulo_elem.get_text(strip=True) if titulo_elem else ""
        
        if not titulo or "Todos los decretos" in titulo:
            continue
            
        desc_elem = bloque.find('p')
        descripcion = desc_elem.get_text(strip=True) if desc_elem else ""
        if len(descripcion) > max_chars:
            descripcion = descripcion[:max_chars].strip() + "..."
        
        link_descargar = ""
        link_ver = ""
        
        for a in bloque.find_all('a', href=True):
            texto_enlace = a.get_text(strip=True).lower()
            href = a['href']
            enlace_absoluto = urljoin(url_base, href)
            
            if "descargar" in texto_enlace:
                link_descargar = enlace_absoluto
            elif "ver" in texto_enlace:
                link_ver = enlace_absoluto
                
        filas_totales.append({
            "Título": titulo,
            "Descripción": descripcion,
            "Enlace Descargar": link_descargar,
            "Enlace Ver": link_ver
        })
        
    df = pd.DataFrame(filas_totales)
    return df

def _limpiar_numero(texto: str):
    texto = (texto or "").strip()
    if not texto or texto.upper() in ("ND", "-", "N/D"):
        return None
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


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
        "parser": parser_dnit,
    },
    {
        "id": "dnit_ruc",
        "nombre": "DNIT - Consulta RUC",
        "alias": ["decreto tributario"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/decretos",
        "parser": parser_dnit_decretos,
    },
]


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


def obtener_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def ejecutar_busqueda(termino: str):
    termino_lower = termino.lower().strip()
    fuente = buscar_fuente(termino)
    
    if fuente is None:
        fuente = FUENTES[0]

    html = obtener_html(fuente["url"])
    df = fuente["parser"](html)

    if df.empty or not termino_lower:
        return fuente, df

    palabras_ignorar = {"dnit", "cotizaciones", "tipo", "de", "cambio", "divisas", "guaranies"}
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