"""
Logica de scraping: registro de fuentes, parsers y busqueda.
Reutilizada por app.py (backend/frontend).
"""

import copy
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
        df = df.sort_values("fecha", ascending=False).drop_duplicates(subset="fecha").reset_index(drop=True)
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
    for estrategia in (parser_dnit_normativa, parser_biblioteca_categoria,
                       _parser_biblioteca_generico, _parser_enlaces_documentos):
        df = estrategia(html, url_base, max_chars)
        if not df.empty:
            return df
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# TABLAS HTML GENERICAS + ESTRATEGIAS DE RESPALDO
# (dictamenes, enmiendas, bibliotecas sin listado, instructivos)
# ---------------------------------------------------------------------------

_RE_ARCHIVO = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|csv|txt|zip|rar)(?=[/?#]|$)", re.IGNORECASE)
_PALABRAS_ACCION = {"descargar", "ver", "download", "visibility", "ver archivo", "abrir"}
_COLS_ENLACE = ("Enlace Descargar", "Enlace Ver")
_RE_FECHA_CELDA = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
COLUMNAS_NORMAS = ["Sección", "Fecha", "Título", "Descripción", "Enlace Descargar", "Enlace Ver"]


_RE_IMAGEN = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico)(?=[/?#]|$)", re.IGNORECASE)


def _es_recurso_del_sitio(href: str) -> bool:
    """Logos, iconos e imagenes del portal (no son documentos de la tabla)."""
    return "/documents/d/global/" in href or bool(_RE_IMAGEN.search(href))


def _contenido_principal(soup):
    """Zona de contenido de la pagina (sin menu, cabecera ni pie)."""
    for selector in ("main", "#main-content", "[role='main']", "#content"):
        zona = soup.select_one(selector)
        if zona is not None and len(_texto(zona)) > 40:
            return zona
    for tag in soup.select("nav, header, footer, aside, [role='navigation'], .navbar, .dropdown-menu"):
        tag.decompose()
    return soup


def _texto(tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def _es_accion(texto: str) -> bool:
    """True si la celda solo contiene textos de botones ('Descargar', 'Ver', iconos)."""
    if not texto.strip():
        return False
    limpio = texto.lower()
    for palabra in ("download", "visibility", "descargar", "archivo", "abrir", "ver"):
        limpio = limpio.replace(palabra, " ")
    return not limpio.strip(" \t·|-")


def _enlaces_de(contenedor, url_base: str):
    """Devuelve (descargar, ver) con los enlaces del contenedor (fila, bloque...)."""
    descargar = ver = otro = ""
    for a in contenedor.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href or href.startswith(("#", "javascript", "mailto", "tel")):
            continue
        if _es_recurso_del_sitio(href):
            continue
        enlace = urljoin(url_base, href.replace(" ", "%20"))
        tipo = _tipo_enlace(a)
        if tipo == "ver":
            ver = ver or enlace
        elif tipo == "descargar" or _RE_ARCHIVO.search(href) or "/documents/" in href:
            descargar = descargar or enlace
        else:
            otro = otro or enlace
    if otro and not descargar:
        descargar = otro
    return descargar, ver


def _nombres_unicos(nombres):
    vistos, salida = {}, []
    for i, nombre in enumerate(nombres, 1):
        nombre = nombre or f"Columna {i}"
        if nombre in vistos:
            vistos[nombre] += 1
            nombre = f"{nombre} ({vistos[nombre]})"
        else:
            vistos[nombre] = 1
        salida.append(nombre)
    return salida


def _titulo_tabla(tabla) -> str:
    cap = tabla.find("caption")
    if cap is not None and _texto(cap):
        return _texto(cap)
    previo = tabla.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
    return _texto(previo) if previo is not None else ""


def extraer_tablas(html: str, url_base: str = "https://www.dnit.gov.py"):
    """Extrae TODAS las <table> de la pagina conservando sus columnas reales.

    Devuelve una lista de (titulo_tabla, DataFrame). Los enlaces de cada fila se
    guardan en 'Enlace Descargar' / 'Enlace Ver'. Las filas de una sola celda
    (subtitulos) se guardan como columna 'Grupo'.
    """
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    resultado = []
    zona = _contenido_principal(soup)
    for tabla in (zona.find_all("table") or soup.find_all("table")):
        if tabla.find_parent("table") is not None:
            continue
        filas = [tr for tr in tabla.find_all("tr") if tr.find_parent("table") is tabla]
        if not filas:
            continue

        def celdas_de(tr):
            return tr.find_all(["th", "td"], recursive=False)

        encabezado = None
        primera = celdas_de(filas[0])
        if primera and (all(c.name == "th" for c in primera) or filas[0].find_parent("thead") is not None):
            encabezado = [_texto(c) for c in primera]
            filas = filas[1:]
            while filas and celdas_de(filas[0]) and all(c.name == "th" for c in celdas_de(filas[0])):
                filas = filas[1:]
        elif (len(primera) >= 2 and not filas[0].find("a")
              and all(_texto(c) for c in primera) and len(filas) > 1):
            encabezado = [_texto(c) for c in primera]
            filas = filas[1:]

        registros, grupo, hay_grupo = [], "", False
        for tr in filas:
            celdas = celdas_de(tr)
            textos = [_texto(c) for c in celdas]
            descargar, ver = _enlaces_de(tr, url_base)
            if not any(textos) and not (descargar or ver):
                continue
            no_vacios = [t for t in textos if t]
            if len(celdas) == 1 and len(no_vacios) == 1 and not (descargar or ver) and (
                    encabezado is None or len(encabezado) > 1):
                grupo, hay_grupo = no_vacios[0], True
                continue
            registros.append((textos, descargar, ver, grupo))

        if not registros:
            continue

        ncols = max(len(encabezado or []), max(len(r[0]) for r in registros))
        nombres = list(encabezado or [])
        nombres += [""] * (ncols - len(nombres))
        nombres = _nombres_unicos(nombres)

        filas_df = []
        for textos, descargar, ver, grupo in registros:
            fila = {}
            if hay_grupo:
                fila["Grupo"] = grupo
            for i, nombre in enumerate(nombres):
                valor = textos[i] if i < len(textos) else ""
                fila[nombre] = "" if _es_accion(valor) else valor
            fila["Enlace Descargar"] = descargar
            fila["Enlace Ver"] = ver
            filas_df.append(fila)

        df = pd.DataFrame(filas_df)
        vacias = [c for c in df.columns if (df[c] == "").all()]
        df = df.drop(columns=vacias)
        if df.empty:
            continue
        resultado.append((_titulo_tabla(tabla), df.reset_index(drop=True)))
    return resultado


def parser_tablas(html: str, url_base: str = "https://www.dnit.gov.py",
                  max_chars: int = None) -> pd.DataFrame:
    """Une las tablas de la pagina en un solo DataFrame (columnas reales)."""
    tablas = extraer_tablas(html, url_base)
    if not tablas:
        return pd.DataFrame()
    partes = []
    for n, (titulo, df) in enumerate(tablas, 1):
        if len(tablas) > 1:
            df = df.copy()
            df.insert(0, "Tabla", titulo or f"Tabla {n}")
        partes.append(df)
    df = pd.concat(partes, ignore_index=True).fillna("")
    datos = [c for c in df.columns if c not in _COLS_ENLACE]
    df = df[datos + [c for c in _COLS_ENLACE if c in df.columns]]
    return df.drop_duplicates().reset_index(drop=True)


def _parser_enlaces_documentos(html: str, url_base: str = "https://www.dnit.gov.py",
                               max_chars: int = None) -> pd.DataFrame:
    """Respaldo: arma registros a partir de los enlaces a documentos de la pagina.
    Titulo = texto del enlace (o el encabezado previo si el enlace es 'Descargar'),
    Descripcion = texto del bloque que lo contiene."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    zona = _contenido_principal(soup)

    registros = {}
    for a in zona.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href or href.startswith(("#", "javascript", "mailto", "tel")):
            continue
        if _es_recurso_del_sitio(href):
            continue
        tipo = _tipo_enlace(a)
        if not (tipo or _RE_ARCHIVO.search(href) or "/documents/" in href):
            continue
        enlace = urljoin(url_base, href.replace(" ", "%20"))
        texto = _texto(a)
        previo = a.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        bloque = a.find_parent(["li", "tr", "article", "p", "div"])
        descripcion = ""
        if bloque is not None:
            copia = copy.copy(bloque)
            for x in copia.find_all("a"):
                x.decompose()
            descripcion = _texto(copia)
        if tipo or len(texto) < 4 or _es_accion(texto):
            titulo = _texto(previo) if previo is not None else ""
            titulo = titulo or nombre_archivo_simple(enlace)
        else:
            titulo = texto
            descripcion = descripcion.replace(titulo, "").strip(" :-·\t")
        if descripcion == titulo:
            descripcion = ""
        if max_chars and len(descripcion) > max_chars:
            descripcion = descripcion[:max_chars].strip() + "..."
        reg = registros.setdefault((titulo, descripcion[:300]), {
            "Título": titulo, "Descripción": descripcion[:600],
            "Enlace Descargar": "", "Enlace Ver": ""})
        if tipo == "ver":
            reg["Enlace Ver"] = reg["Enlace Ver"] or enlace
        else:
            reg["Enlace Descargar"] = reg["Enlace Descargar"] or enlace
    return pd.DataFrame(list(registros.values()))


def nombre_archivo_simple(url: str) -> str:
    ruta = unquote_plus(urlparse(url).path)
    for segmento in reversed(ruta.split("/")):
        if segmento:
            return segmento
    return "Documento"


def parser_biblioteca_robusto(html: str, url_base: str = "https://www.dnit.gov.py",
                              max_chars: int = None) -> pd.DataFrame:
    """Bibliotecas: prueba el parser especifico y, si la pagina no trae ese formato,
    las demas estructuras conocidas. (Las tablas se muestran aparte, ver app.py.)"""
    for estrategia in (parser_biblioteca_categoria, parser_dnit_normativa,
                       _parser_biblioteca_generico, _parser_enlaces_documentos):
        df = estrategia(html, url_base, max_chars)
        if not df.empty:
            return df
    return pd.DataFrame()


def parser_tablas_o_listado(html: str, url_base: str = "https://www.dnit.gov.py",
                            max_chars: int = None) -> pd.DataFrame:
    """Paginas cuyo contenido principal son tablas (dictamenes, enmienda del SA).
    1) tablas <table> del contenido; 2) enlaces a documentos del contenido."""
    df = parser_tablas(html, url_base, max_chars)
    if not df.empty:
        return df
    return _parser_enlaces_documentos(html, url_base, max_chars)


def diagnosticar_pagina(html: str, url_base: str = "https://www.dnit.gov.py", muestras=()) -> dict:
    """Resumen de lo que realmente trae el HTML (para ajustar el parser)."""
    crudo = html
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    total_tablas = len(soup.find_all("table"))
    iframes = [i.get("src", "") for i in soup.find_all("iframe")][:5]
    zona = _contenido_principal(soup)
    docs = [a["href"] for a in zona.find_all("a", href=True)
            if (_RE_ARCHIVO.search(a["href"]) or "/documents/" in a["href"])
            and not _es_recurso_del_sitio(a["href"])]
    info = {
        "bytes_html": len(crudo),
        "tablas_en_pagina": total_tablas,
        "iframes": iframes,
        "enlaces_a_documentos": len(docs),
        "encabezados": [_texto(h) for h in zona.find_all(["h1", "h2", "h3", "h4"])][:12],
    }
    for muestra in muestras:
        entrada = {"en_html_crudo": muestra in crudo, "en_dom_visible": False}
        nodo = soup.find(string=lambda t: t and muestra in t)
        if nodo is not None:
            entrada["en_dom_visible"] = True
            cadena, el = [], nodo.parent
            while el is not None and el.name != "[document]" and len(cadena) < 7:
                padre = el.parent
                iguales = 0
                if padre is not None:
                    firma = (el.name, tuple(sorted(el.get("class", []))))
                    iguales = sum(1 for h in padre.find_all(True, recursive=False)
                                  if (h.name, tuple(sorted(h.get("class", [])))) == firma)
                cadena.append({"etiqueta": el.name, "clase": " ".join(el.get("class", [])),
                               "hijos": len(el.find_all(True, recursive=False)),
                               "hermanos_misma_firma": iguales})
                el = padre
            entrada["ancestros_desde_la_celda"] = cadena
            entrada["fragmento_html"] = str(nodo.parent.parent)[:600] if nodo.parent.parent else ""
        info[f"muestra: {muestra}"] = entrada
    return info


def _tablas_a_registros(tablas) -> pd.DataFrame:
    filas = []
    for titulo, df in tablas:
        datos = [c for c in df.columns if c not in _COLS_ENLACE]
        for _, fila in df.iterrows():
            valores = [(c, str(fila[c]).strip()) for c in datos if str(fila[c]).strip()]
            if not valores:
                continue
            titulo_fila = valores[0][1]
            resto = [f"{c}: {v}" for c, v in valores[1:]]
            fecha = next((v for _, v in valores if _RE_FECHA_CELDA.search(v)), "")
            filas.append({
                "Sección": titulo or "", "Fecha": fecha, "Título": titulo_fila,
                "Descripción": " · ".join(resto),
                "Enlace Descargar": fila.get("Enlace Descargar", ""),
                "Enlace Ver": fila.get("Enlace Ver", ""),
            })
    return pd.DataFrame(filas)


def parser_instructivos(html: str, url_base: str = "https://www.dnit.gov.py") -> pd.DataFrame:
    """Instructivos con titulo y descripcion por apartado (mismas estructuras que
    el resto del portal: titulo + descripcion + Descargar/Ver; luego tablas; luego enlaces)."""
    df = pd.DataFrame()
    for estrategia in (parser_dnit_normativa, parser_biblioteca_categoria, _parser_biblioteca_generico):
        df = estrategia(html, url_base)
        if not df.empty:
            break
    if df.empty:
        tablas = extraer_tablas(html, url_base)
        if tablas:
            df = _tablas_a_registros(tablas)
    if df.empty:
        df = _parser_enlaces_documentos(html, url_base)
    for col in COLUMNAS_NORMAS:
        if col not in df.columns:
            df[col] = ""
    df["Sección"] = df["Sección"].replace("", "Instructivos")
    return df[COLUMNAS_NORMAS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# TABLAS DE LA DNIT CON ENCABEZADO CONOCIDO (Dictamenes y Correlacion NCM)
# Funciona tanto si la pagina usa <table> como si usa <div> en forma de grilla.
# ---------------------------------------------------------------------------
_RE_FECHA_DMY = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")


def _clave_texto(texto: str) -> str:
    """Minusculas, sin acentos, sin espacios y con guiones unificados."""
    import unicodedata
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    t = t.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", t)


def _texto_celda(tag, sep: str = " ") -> str:
    """Texto de una celda; los <br>/<p> se unen con 'sep'."""
    if tag.find(["br", "p", "li", "div"]) is None:
        return " ".join(tag.get_text(" ").split())
    copia = copy.copy(tag)
    for br in copia.find_all("br"):
        br.replace_with("\x00")
    for bloque in copia.find_all(["p", "li", "div"]):
        bloque.append("\x00")
    partes = [" ".join(p.split()) for p in copia.get_text(" ").split("\x00")]
    return sep.join(p for p in partes if p)


def _coincide_etiqueta(clave_texto: str, etiqueta: str) -> bool:
    if clave_texto == etiqueta:
        return True
    return len(etiqueta) >= 8 and etiqueta in clave_texto and len(clave_texto) <= len(etiqueta) + 25


def _ubicaciones_encabezado(cadenas, claves, limite: int = 8):
    """Genera TODOS los grupos de textos consecutivos que contienen las etiquetas, en orden
    (la pagina puede tener un encabezado oculto/duplicado ademas del visible)."""
    cantidad = 0
    for i, (s, k) in enumerate(cadenas):
        if not _coincide_etiqueta(k, claves[0]):
            continue
        encontrados, j, completo = [s], i, True
        for etiqueta in claves[1:]:
            hallado = None
            for m in range(j + 1, min(j + 60, len(cadenas))):
                if _coincide_etiqueta(cadenas[m][1], etiqueta):
                    hallado = m
                    break
            if hallado is None:
                completo = False
                break
            encontrados.append(cadenas[hallado][0])
            j = hallado
        if completo:
            yield encontrados
            cantidad += 1
            if cantidad >= limite:
                return


def _ancestro_comun(tags):
    comun = [tags[0]] + list(tags[0].parents)
    for t in tags[1:]:
        ids = {id(x) for x in [t] + list(t.parents)}
        comun = [a for a in comun if id(a) in ids]
    return comun[0] if comun else None


def _celdas_desde_encabezado(ubicados, ncols: int):
    """Lista de filas (cada una, lista de elementos-celda) que siguen al encabezado."""
    lca = _ancestro_comun([s.parent for s in ubicados])
    if lca is None or lca.name == "[document]":
        return []
    hijos = lca.find_all(True, recursive=False)
    if len(hijos) > 3 * ncols:                         # grilla plana
        primero = ubicados[0]
        i0 = next((i for i, h in enumerate(hijos)
                   if any(p is h for p in primero.parents) or primero.parent is h), 0)
        datos = hijos[i0 + ncols:]
        return [datos[k:k + ncols] for k in range(0, len(datos), ncols)]

    clave_enc = _clave_texto(_texto(lca))
    if lca.name == "tr":
        tabla = lca.find_parent("table")
        filas_el = [tr for tr in tabla.find_all("tr")
                    if tr is not lca and tr.find_parent("table") is tabla] if tabla else []
    else:
        nodo, hermanos = lca, []
        for _ in range(3):
            hermanos = nodo.find_next_siblings(True)
            if len(hermanos) == 1 and len(hermanos[0].find_all(True, recursive=False)) >= 2:
                hermanos = hermanos[0].find_all(True, recursive=False)   # cuerpo envuelto
            if len(hermanos) >= 2:
                break
            nodo = nodo.parent
            if nodo is None:
                break
        filas_el = []
        for h in hermanos:
            if h.name in ("tbody", "tfoot"):
                filas_el.extend(h.find_all("tr"))
            else:
                filas_el.append(h)
    resultado = []
    for fila in filas_el:
        if _clave_texto(_texto(fila)) == clave_enc:
            continue
        celdas = fila.find_all(["td", "th"], recursive=False) or fila.find_all(True, recursive=False)
        resultado.append(celdas or [fila])
    return resultado


def _df_desde_celdas(celdas_por_fila, columnas, url_base: str, sep: str = " ") -> pd.DataFrame:
    ncols = len(columnas)
    registros, hay_enlaces = [], False
    for celdas in celdas_por_fila:
        textos = [_texto_celda(c, sep) for c in celdas]
        if not any(textos):
            continue
        if all(t.strip().lower() in ("", "oculto", "hidden") for t in textos):
            continue                                    # filas ocultas del portal
        if len(textos) > ncols:
            textos = textos[:ncols - 1] + [" ".join(textos[ncols - 1:])]
        textos += [""] * (ncols - len(textos))
        descargar = ver = ""
        for c in celdas:
            d, v = _enlaces_de(c, url_base)
            descargar, ver = descargar or d, ver or v
        hay_enlaces = hay_enlaces or bool(descargar or ver)
        registros.append(textos + [descargar, ver])
    if not registros:
        return pd.DataFrame()
    df = pd.DataFrame(registros, columns=list(columnas) + list(_COLS_ENLACE))
    if not hay_enlaces:
        df = df.drop(columns=list(_COLS_ENLACE))
    else:
        df = df.drop(columns=[c for c in _COLS_ENLACE if (df[c] == "").all()])
    return df.reset_index(drop=True)


def extraer_tabla_por_encabezado(html: str, etiquetas, columnas, url_base: str = "https://www.dnit.gov.py",
                                 sep: str = " ") -> pd.DataFrame:
    """Busca la tabla por su encabezado (sin depender de <table>). Prueba todas las
    ubicaciones del encabezado y devuelve la que aporta MAS filas."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    claves = [_clave_texto(e) for e in etiquetas]
    cadenas = [(s, _clave_texto(str(s))) for s in soup.find_all(string=True)
               if s.__class__.__name__ == "NavigableString" and str(s).strip()]
    cadenas = [(s, k) for s, k in cadenas if k]
    mejor = pd.DataFrame()
    for ubicados in _ubicaciones_encabezado(cadenas, claves):
        df = _df_desde_celdas(_celdas_desde_encabezado(ubicados, len(columnas)), columnas, url_base, sep)
        if len(df) > len(mejor):
            mejor = df
    return mejor


def extraer_tabla_por_ancla(html: str, patron: str, columnas_por_ncols: dict,
                            url_base: str = "https://www.dnit.gov.py", sep: str = " ",
                            min_celdas: int = 2, min_filas: int = 10) -> pd.DataFrame:
    """Sin usar encabezados: parte de un dato conocido (patron) y sube hasta encontrar el
    elemento que se repite como fila (>= min_filas hermanos con la misma etiqueta/clase)."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    rx = re.compile(patron)
    firma = lambda e: (e.name, tuple(sorted(e.get("class", []))))
    mejor = pd.DataFrame()
    for n, ancla in enumerate(soup.find_all(string=rx)):
        if n >= 4:
            break
        el, filas = ancla.parent, None
        while el is not None and el.name != "[document]" and el.parent is not None:
            iguales = [h for h in el.parent.find_all(True, recursive=False) if firma(h) == firma(el)]
            if len(iguales) >= min_filas and len(el.find_all(True, recursive=False)) >= min_celdas:
                filas = iguales
                break
            el = el.parent
        if not filas:
            continue
        celdas_filas = []
        for f in filas:
            celdas = f.find_all(["td", "th"], recursive=False) or f.find_all(True, recursive=False)
            claves_celdas = {_clave_texto(_texto(c)) for c in celdas}
            if {"fecha", "pais"} <= claves_celdas:
                continue                                  # fila de encabezado
            celdas_filas.append(celdas)
        if not celdas_filas:
            continue
        conteos = [len(c) for c in celdas_filas]
        ncols = max(set(conteos), key=conteos.count)
        columnas = columnas_por_ncols.get(ncols) or [f"Columna {i + 1}" for i in range(ncols)]
        df = _df_desde_celdas(celdas_filas, columnas, url_base, sep)
        if len(df) > len(mejor):
            mejor = df
    return mejor


_PAT_FECHA = r"(?<!\d)\d{1,2}/\d{1,2}/\d{4}(?!\d)"
_PAT_CODIGO = r"\d{4}\.\d{2}\.\d{2}"


def _puntaje_dictamenes(df: pd.DataFrame) -> int:
    """Filas con aspecto de dictamen: traen una fecha dd/mm/aaaa y un codigo NCM."""
    filas = df.drop_duplicates().astype(str).agg(" ".join, axis=1)
    return int((filas.str.contains(_PAT_FECHA) & filas.str.contains(_PAT_CODIGO)).sum())


def _puntaje_correlacion(df: pd.DataFrame) -> int:
    col = COLUMNAS_CORRELACION[0]
    return int(df[col].astype(str).str.match(r"\s*" + _PAT_CODIGO).sum()) if col in df.columns else 0


def _elegir_mejor(generadores, puntaje, suficiente: int = 5) -> pd.DataFrame:
    """Prueba estrategias en orden; devuelve la de mayor puntaje (vacia si ninguna es valida)."""
    mejor, mejor_p = pd.DataFrame(), 0
    for generar in generadores:
        df = generar()
        if df.empty:
            continue
        p = puntaje(df)
        if p > mejor_p:
            mejor, mejor_p = df, p
        if p >= suficiente and p >= 0.7 * len(df.drop_duplicates()):
            break
    return mejor


_ALIAS_COLUMNAS = {
    "nro.res.": "Resolución", "nro.res": "Resolución", "resolucion": "Resolución",
    "resoluciondan/mercosur": "Resolución DAN / MERCOSUR", "pais": "País",
    "mercaderia": "Mercadería", "descripcion": "Descripción", "clasif.": "Clasificación",
    "clasificacion": "Clasificación", "fundamentolegal": "Fundamento Legal",
    "obs.": "Observaciones", "observaciones": "Observaciones",
}


def _mejor_tabla_html(html: str, url_base: str, min_columnas: int) -> pd.DataFrame:
    """Respaldo: la <table> con mas filas (si la pagina si usa <table>), columnas renombradas."""
    mejor = pd.DataFrame()
    for _, df in extraer_tablas(html, url_base):
        datos = [c for c in df.columns if c not in _COLS_ENLACE]
        if len(datos) >= min_columnas and len(df) > len(mejor):
            mejor = df.rename(columns=lambda c: _ALIAS_COLUMNAS.get(_clave_texto(str(c)), c))
    return mejor


COLUMNAS_DICTAMENES_9 = ["Resolución", "Fecha", "País", "Dictamen", "Mercadería", "Descripción",
                         "Clasificación", "Fundamento Legal", "Observaciones"]
COLUMNAS_DICTAMENES = ["Resolución", "Resolución DAN / MERCOSUR", "Fecha", "País", "Dictamen",
                       "Mercadería", "Descripción", "Clasificación", "Fundamento Legal", "Observaciones"]


def parser_dictamenes(html: str, url_base: str = "https://www.dnit.gov.py",
                      max_chars: int = None) -> pd.DataFrame:
    """Tabla 'Dictamenes de Clasificacion' tal como la publica la DNIT (+ Año y Capítulo).
    Soporta el formato actual (9 columnas: Nro. Res., ..., Clasif., ..., Obs.) y el anterior (10)."""
    df = _elegir_mejor([
        lambda: extraer_tabla_por_encabezado(
            html, ["Nro. Res.", "Fecha", "País", "Dictamen", "Mercadería", "Clasif."],
            COLUMNAS_DICTAMENES_9, url_base),
        lambda: extraer_tabla_por_ancla(
            html, r"Dictamen\s+de\s+Clasificaci[oó]n",
            {9: COLUMNAS_DICTAMENES_9, 10: COLUMNAS_DICTAMENES}, url_base, min_celdas=5),
        lambda: extraer_tabla_por_encabezado(
            html, ["Resolución", "Fecha", "País", "Dictamen", "Mercadería", "Clasificación"],
            COLUMNAS_DICTAMENES, url_base),
        lambda: _mejor_tabla_html(html, url_base, 7),
    ], _puntaje_dictamenes)
    if df.empty or "Fecha" not in df.columns:
        return df

    # La fuente tiene filas con Fecha/País invertidos o con la etiqueta pegada en 'Dictamen'.
    patron = r"(?<!\d)\d{1,2}/\d{1,2}/\d{4}(?!\d)"
    if "País" in df.columns:
        pais_es_fecha = df["País"].str.contains(patron) & ~df["Fecha"].str.contains(patron)
        df.loc[pais_es_fecha, ["Fecha", "País"]] = df.loc[pais_es_fecha, ["País", "Fecha"]].values
    if "Dictamen" in df.columns:
        df["Dictamen"] = df["Dictamen"].str.replace(r"^\s*Fecha\s+\S+\s+País\s+", "", regex=True)

    def anio(fecha):
        m = _RE_FECHA_DMY.search(fecha or "")
        return m.group(3) if m else ""

    df.insert(df.columns.get_loc("Fecha") + 1, "Año", df["Fecha"].map(anio))
    if "Clasificación" in df.columns:
        def capitulo(clasif):
            m = re.match(r"\s*(\d{2})", clasif or "")
            return m.group(1) if m else ""
        df.insert(df.columns.get_loc("Clasificación") + 1, "Capítulo", df["Clasificación"].map(capitulo))
    return df.drop_duplicates().reset_index(drop=True)


COLUMNAS_CORRELACION = ["NCM 2017 (VI Enmienda)", "NCM 2022 (VII Enmienda)"]


def parser_correlacion_ncm(html: str, url_base: str = "https://www.dnit.gov.py",
                           max_chars: int = None) -> pd.DataFrame:
    """Tabla de Correlacion NCM 2017 -> NCM 2022 (VII Enmienda) (+ Capítulo y tipo de correlación)."""
    def desde_tablas_html():
        mejor_html = _mejor_tabla_html(html, url_base, 2)
        if mejor_html.empty:
            return pd.DataFrame()
        datos = [c for c in mejor_html.columns if c not in _COLS_ENLACE][:2]
        t = mejor_html[datos].copy()
        t.columns = COLUMNAS_CORRELACION
        return t

    df = _elegir_mejor([
        lambda: extraer_tabla_por_encabezado(html, ["NCM - 2017", "NCM - 2022"], COLUMNAS_CORRELACION,
                                             url_base, sep=" · "),
        lambda: extraer_tabla_por_ancla(html, r"^\s*\d{4}\.\d{2}\.\d{2}\w?\s*$",
                                        {2: COLUMNAS_CORRELACION}, url_base, sep=" · ",
                                        min_celdas=2, min_filas=20),
        desde_tablas_html,
    ], _puntaje_correlacion)
    if df.empty:
        return df
    c17, c22 = COLUMNAS_CORRELACION

    def capitulo(valor):
        m = re.match(r"\s*(\d{2})\d{2}\.", valor or "")
        return m.group(1) if m else ""

    def tipo(valor):
        v = (valor or "").strip()
        if not v or v == "-":
            return "Sin correspondencia"
        return "Parcial (EX)" if re.search(r"\bex\b", v, re.IGNORECASE) else "Plena"

    df = df.copy()
    df.insert(0, "Capítulo", df[c17].map(capitulo))
    df.insert(3, "Correlación", df[c22].map(tipo))
    return df.drop_duplicates().reset_index(drop=True)


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
    {
        "id": "dnit_dictamenes_adu",
        "nombre": "DNIT - Dictámenes de Clasificación (Aduanera)",
        "alias": ["dictamen", "dictamenes", "clasificacion"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/dictamenes-de-clasificacion",
        "categoria": "aduanera",
        "parser": parser_dictamenes,
    },
    {
        "id": "dnit_enmienda_vii",
        "nombre": "DNIT - VII Enmienda del Sistema Armonizado (NCM)",
        "alias": ["enmienda", "vii enmienda", "sistema armonizado", "ncm"],
        "url": "https://www.dnit.gov.py/web/portal-institucional/vii-enmienda-del-sistema-armonizado-a-la-nomenclatura-com%C3%BAn-del-mercosur",
        "categoria": "aduanera",
        "parser": parser_correlacion_ncm,
    },
]

# Las bibliotecas con el parser base prueban ademas otras estructuras de pagina
for _f in FUENTES:
    if _f["parser"] is parser_biblioteca_categoria:
        _f["parser"] = parser_biblioteca_robusto

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
