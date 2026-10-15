"""
Aplicación principal de Streamlit para el buscador DNIT.
Ejecución: streamlit run app.py

Estructura:
  - Buscador general (siempre arriba): detecta la fuente según lo escrito.
  - Pantalla de inicio: Normativa Impositiva, Normativa Aduanera y Cotizaciones.
  - Resultados de cada categoría debajo del menú, sin navegar a otra vista.
"""

import difflib
import re
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from scraper import (
    BIBLIOTECA_IMPOSITIVA,
    BIBLIOTECA_ADUANERA,
    descargar_archivo,
    ejecutar_busqueda,
    diagnosticar_pagina,
    extraer_tablas,
    nombre_archivo,
    obtener_fuente,
    obtener_html,
    parser_instructivos,
)

LOGO_URL = "https://www.dnit.gov.py/documents/d/global/logo-light-svg-1?download=true"
st.set_page_config(layout="wide", page_title="Buscador DNIT", page_icon=LOGO_URL)

# Sincronizar parámetros de URL para permitir clics directos en los iconos
if "sec" in st.query_params and "seccion" not in st.session_state:
    st.session_state.scroll_resultados = True   # enlace directo: baja a los resultados
if "sec" in st.query_params:
    st.session_state.seccion = st.query_params["sec"]
if "tip" in st.query_params:
    st.session_state.tipo = st.query_params["tip"]

# ---------------------------------------------------------------------------
# MAPA DE NAVEGACION
# ---------------------------------------------------------------------------
ITEMS_BIBLIOTECA_IMP = {
    cat["nombre"]: {"icono": "📖", "fuente": cat["fuente"], "prefijo": None, "logo": cat["logo"],"color": cat.get("color", "")}
    for cat in BIBLIOTECA_IMPOSITIVA
}

ITEMS_BIBLIOTECA_ADU = {
    cat["nombre"]: {"icono": "📖", "fuente": cat["fuente"], "prefijo": None, "logo": cat["logo"],"color": cat.get("color","")}
    for cat in BIBLIOTECA_ADUANERA
}

# Instructivos institucionales: no están en FUENTES (se extraen aparte con
# cargar_instructivos), pero deben poder buscarse igual que el resto.
FUENTES_INSTRUCTIVOS = {
    "dnit_instructivos_imp": (
        "https://www.dnit.gov.py/en/web/portal-institucional/instructivos",
        "Instructivos Impositivos",
    ),
    "dnit_instructivos_adu": (
        "https://www.dnit.gov.py/en/web/portal-institucional/instructivos1",
        "Instructivos Aduaneros",
    ),
}

NAVEGACION = {
    "Normativa Impositiva": {
        "icono": "🧾",
        "descripcion": "Leyes, decretos, resoluciones y digesto en materia tributaria.",
        "items": {
            "Leyes":        {"icono": "⚖️", "fuente": "dnit_leyes_imp",        "prefijo": None},
            "Decretos":     {"icono": "📜", "fuente": "dnit_decretos_imp",     "prefijo": None},
            "Resoluciones": {"icono": "📑", "fuente": "dnit_resoluciones_imp", "prefijo": None},
            "Digesto":      {"icono": "📚", "fuente": "digesto_tributario",    "prefijo": None},
            "Instructivos": {"icono": "📘", "fuente": "dnit_instructivos_imp", "prefijo": None},
            **ITEMS_BIBLIOTECA_IMP,
        },
    },
    "Normativa Aduanera": {
        "icono": "🚢",
        "descripcion": "Leyes, decretos, resoluciones y digesto en materia aduanera.",
        "items": {
            "Leyes":        {"icono": "⚖️", "fuente": "dnit_leyes_adu",        "prefijo": None},
            "Decretos":     {"icono": "📜", "fuente": "dnit_decretos_adu",     "prefijo": None},
            "Resoluciones": {"icono": "📑", "fuente": "dnit_resoluciones_adu", "prefijo": None},
            "Digesto":      {"icono": "📚", "fuente": "digesto_aduanero",    "prefijo": None},
            "Instructivos": {"icono": "📘", "fuente": "dnit_instructivos_adu", "prefijo": None},
            "Dictámenes":   {"icono": "🗂️", "fuente": "dnit_dictamenes_adu",   "prefijo": None, "tablas": True},
            "VII Enmienda": {"icono": "🔢", "fuente": "dnit_enmienda_vii",     "prefijo": None, "tablas": True},
            **ITEMS_BIBLIOTECA_ADU,
        },
    },
    "Cotizaciones": {
        "icono": "💱",
        "descripcion": "Historial de tipos de cambio publicados por la DNIT.",
        "items": {
            "Historial": {"icono": "📈", "fuente": "dnit_cotizaciones", "prefijo": None},
        },
    },
}

COLUMN_CONFIG_NORMAS = {
    "Sección": st.column_config.TextColumn("Sección", width="small"),
    "Fecha": st.column_config.TextColumn("Fecha", width="small"),
    "Título": st.column_config.TextColumn("Título", width="medium"),
    "Descripción": st.column_config.TextColumn("Descripción", width="large"),
    "Enlace Descargar": st.column_config.LinkColumn(
        "Enlace Descargar", display_text="📄 Abrir archivo"
    ),
    "Enlace Ver": st.column_config.LinkColumn(
        "Enlace Ver", display_text="🌐 Ver Detalle"
    ),
}

# ---------------------------------------------------------------------------
# ESTADO Y NAVEGACION
# ---------------------------------------------------------------------------
st.session_state.setdefault("seccion", None)
st.session_state.setdefault("tipo", None)
st.session_state.setdefault("modal_pvaa_url", None)
st.session_state.setdefault("modal_pvaa_titulo", None)


def ir_a(seccion: str, tipo: str):
    st.session_state.seccion = seccion
    st.session_state.tipo = tipo
    st.query_params["sec"] = seccion
    st.query_params["tip"] = tipo
    st.session_state.scroll_resultados = True   # tras el clic, deslizar a "Resultados"


def volver_inicio():
    st.session_state.seccion = None
    st.session_state.tipo = None
    st.query_params.clear()


def limpiar_todo():
    """Botón Limpiar del buscador general: borra el texto y vuelve al inicio (sin Enter)."""
    st.session_state.termino_general = ""
    volver_inicio()


def scroll_a_resultados():
    """Desliza la página hasta el contenedor de resultados (una sola vez por clic)."""
    if not st.session_state.pop("scroll_resultados", False):
        return
    st.components.v1.html(
        """<script>
        setTimeout(function () {
          var el = window.parent.document.querySelector('.st-key-resultados');
          if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
        }, 250);
        </script>""",
        height=0,
    )


# ---------------------------------------------------------------------------
# DATOS (con cache para que navegar no vuelva a descargar todo)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cargar(termino: str, fuente_id: str | None):
    _, df = ejecutar_busqueda(termino, fuente_id)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_instructivos(url: str):
    """Instructivos con título y descripción de cada apartado (ver scraper.parser_instructivos)."""
    return parser_instructivos(obtener_html(url), url)


@st.cache_data(ttl=600, show_spinner=False)
def cargar_html(url: str) -> str:
    return obtener_html(url)


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_presentacion_biblioteca(url: str):
    """Extrae una descripción introductoria y accesos a sistemas desde la fuente oficial."""
    html = obtener_html(url)
    soup = BeautifulSoup(html, "html.parser")
    for nodo in soup(["script", "style", "noscript", "nav", "footer"]):
        nodo.decompose()

    zona = soup.select_one("main, #main-content, [role='main'], #content") or soup
    descripcion = []
    for p in zona.find_all(["p", "blockquote"]):
        texto = " ".join(p.get_text(" ", strip=True).split())
        if len(texto) < 45 or len(texto) > 650:
            continue
        if texto not in descripcion:
            descripcion.append(texto)
        if len(descripcion) >= 3:
            break

    accesos, vistos = [], set()
    palabras_acceso = (
        "consulta", "sistema", "acceder", "ingresar", "solicitud",
        "registro", "tramite", "trámite", "portal", "aplicacion",
        "aplicación", "servicio en linea", "servicio en línea", "reenvio", "reenvío"
    )
    for a in zona.select("a[href]"):
        titulo = " ".join(a.get_text(" ", strip=True).split())
        href = urllib.parse.urljoin(url, (a.get("href") or "").strip())
        if not titulo or len(titulo) < 4 or href.startswith(("javascript:", "mailto:", "#")):
            continue
        if any(ext in urllib.parse.urlparse(href).path.lower() for ext in
               (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".png", ".jpg", ".svg")):
            continue
        if not any(p in normalizar(titulo) for p in palabras_acceso):
            continue
        if href.rstrip("/") == url.rstrip("/") or href in vistos:
            continue
        vistos.add(href)
        accesos.append({"titulo": titulo, "url": href})
        if len(accesos) >= 8:
            break
    return descripcion, accesos


MUESTRAS_DIAGNOSTICO = {
    "dnit_dictamenes_adu": ["Congeladoras conservadoras para helados"],
    "dnit_enmienda_vii": ["0305.10.00"],
}


def mostrar_diagnostico(url: str, clave: str, muestras=()):
    """Panel para ver qué trae realmente la página (y poder compartir el HTML)."""
    with st.expander("🛠 Diagnóstico de la página", expanded=False):
        try:
            html = cargar_html(url)
        except (requests.RequestException, ValueError) as e:
            st.error(f"No se pudo descargar la página: {e}")
            return
        st.json(diagnosticar_pagina(html, url, muestras))
        st.download_button("⬇ Descargar HTML recibido", html, file_name="pagina_dnit.html",
                           mime="text/html", key=f"diag_{clave}")


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_tablas(url: str):
    """Todas las tablas <table> que contiene una página: [(titulo, DataFrame), ...]."""
    return extraer_tablas(obtener_html(url), url)


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_recursos_pvaa():
    """Recupera enlaces públicos de avisos, guías, instructivos y formularios PVAA."""
    url = "https://www.dnit.gov.py/en/web/portal-institucional/personas-vinculadas-a-la-actividad-aduanera"
    respuesta = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BuscadorDNIT/1.0)"},
    )
    respuesta.raise_for_status()
    soup = BeautifulSoup(respuesta.text, "html.parser")
    recursos = []
    vistos = set()
    for a in soup.select("a[href]"):
        titulo = " ".join(a.get_text(" ", strip=True).split())
        href = urllib.parse.urljoin(url, a.get("href", ""))
        if not titulo or len(titulo) < 5 or href.startswith(("javascript:", "mailto:")):
            continue
        if "dnit.gov.py" not in urllib.parse.urlparse(href).netloc and "aduana.gov.py" not in urllib.parse.urlparse(href).netloc:
            continue
        t = normalizar(titulo)
        if any(x in t for x in ("comunicado", "aviso", "guia", "instructivo", "formulario", "solicitud", "modelo para adjuntar")):
            clave = (titulo, href)
            if clave not in vistos:
                vistos.add(clave)
                if any(x in t for x in ("comunicado", "aviso")):
                    grupo = "Avisos importantes"
                elif "guia" in t:
                    grupo = "Guías"
                else:
                    grupo = "Instructivos y formularios"
                recursos.append({"titulo": titulo, "url": href, "grupo": grupo})
    return recursos


def panel_descarga(fila, clave: str):
    url = fila.get("Enlace Descargar", "")
    ver = fila.get("Enlace Ver", "")
    titulo = fila.get("Título", "archivo")
    st.markdown(f"**{titulo}**")

    if not url:
        st.info("Este registro no tiene archivo para descargar.")
        if ver:
            st.link_button("🌐 Ver detalle", ver)
        return

    nombre = nombre_archivo(url, titulo)
    try:
        with st.spinner("Preparando archivo..."):
            contenido, tipo_mime = descargar_archivo(url)
    except (requests.RequestException, ValueError) as error:
        st.error(f"No se pudo preparar la descarga ({error}).")
        st.link_button("Abrir el enlace directo", url)
        return

    st.download_button(
        f"📥 Descargar {nombre}",
        data=contenido,
        file_name=nombre,
        mime=tipo_mime,
        key=f"dl_{clave}",
    )


def mostrar_tabla(df, fuente, clave: str = "tabla"):
    if df.empty:
        st.warning("No se encontraron resultados para la búsqueda ingresada.")
        return

    opciones = {"width": "stretch", "hide_index": True}
    config = {k: v for k, v in COLUMN_CONFIG_NORMAS.items() if k in df.columns}
    if config:
        opciones["column_config"] = config

    if "Enlace Descargar" not in df.columns:
        st.dataframe(df, **opciones)
        return

    evento = st.dataframe(
        df, key=clave, on_select="rerun", selection_mode="single-row", **opciones
    )
    filas = evento.selection.rows
    if filas and filas[0] < len(df):
        panel_descarga(df.iloc[filas[0]], clave)
    else:
        st.caption("💡 Selecciona una fila (casilla de la izquierda) para descargar su archivo.")


def aplicar_filtros_columnas(df, clave: str, max_opciones: int = 80):
    """Filtros de selección (multiselect) por cada columna con pocos valores distintos."""
    if df.empty:
        return df
    columnas = []
    for c in df.columns:
        if str(c).startswith("Enlace"):
            continue
        valores = [v for v in df[c].astype(str).unique() if v.strip()]
        if 1 < len(valores) <= max_opciones:
            columnas.append((c, sorted(valores, key=lambda v: normalizar(v))))
    if not columnas:
        return df
    base = df
    with st.expander("🔎 Filtros por columna", expanded=False):
        celdas = st.columns(min(3, len(columnas)))
        for i, (c, opciones) in enumerate(columnas):
            with celdas[i % len(celdas)]:
                elegidos = st.multiselect(str(c), opciones, key=f"{clave}_col_{c}")
            if elegidos:
                df = df[base[c].astype(str).isin(elegidos)]
    return df.reset_index(drop=True)


def mostrar_tablas_pagina(url: str, clave: str):
    """Muestra las tablas de la página oficial, cada una con filtro de texto y por columna."""
    try:
        tablas = cargar_tablas(url)
    except (requests.RequestException, ValueError):
        return
    if not tablas:
        return
    st.markdown(f"#### 📊 Tablas de la página ({len(tablas)})")
    for i, (titulo, tabla) in enumerate(tablas):
        etiqueta = f"{titulo or f'Tabla {i + 1}'} · {len(tabla)} filas"
        with st.expander(etiqueta, expanded=(len(tablas) == 1)):
            texto = st.text_input("Filtrar esta tabla", key=f"{clave}_t{i}_txt",
                                  placeholder="Palabra clave...")
            vista = aplicar_filtros_columnas(tabla, f"{clave}_t{i}")
            vista = filtrar_df(vista, texto)
            st.caption(f"{len(vista)} de {len(tabla)} filas")
            mostrar_tabla(vista, None, clave=f"{clave}_t{i}")


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def anios_de_texto(texto: str, permitir_anio_corto: bool = False) -> set[str]:
    """Extrae años completos y, en títulos, años abreviados como /25 o /92.

    Convención para sufijos de dos dígitos: 00-29 => 2000-2029;
    30-99 => 1930-1999. Así /25 es 2025 y /92 es 1992.
    """
    texto = str(texto or "")
    anios = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", texto))
    if permitir_anio_corto:
        for sufijo in re.findall(r"/(\d{2})(?![\d/])", texto):
            numero = int(sufijo)
            anios.add(str(2000 + numero if numero <= 29 else 1900 + numero))
    return anios


def anios_fila(fila, columnas_texto: list[str]) -> set[str]:
    anios = set()
    for columna in columnas_texto:
        anios.update(anios_de_texto(fila.get(columna, ""), permitir_anio_corto=(columna == "Título")))
    return anios


def columnas_buscables(df):
    """Columnas donde se busca texto.
    - Si la tabla tiene 'Título' (normativa): solo Título y Descripción.
    - Si no (cotizaciones, dictámenes, tablas de páginas): todas menos los enlaces.
    """
    if "Título" in df.columns:
        return [c for c in ("Título", "Descripción") if c in df.columns]
    return [c for c in df.columns if not str(c).startswith("Enlace")]


def filtrar_df(df, texto: str):
    tokens = normalizar(texto).split()
    if df.empty or not tokens:
        return df
    cols = columnas_buscables(df)
    if not cols:
        return df
    filas = df[cols].astype(str).apply(lambda col: col.map(normalizar)).apply(" ".join, axis=1)
    mascara = filas.apply(lambda fila: all(t in fila for t in tokens))
    return df[mascara].reset_index(drop=True)

def obtener_df(item: dict, filtro: str):
    if item["fuente"] in FUENTES_INSTRUCTIVOS:
        url_instructivos, _ = FUENTES_INSTRUCTIVOS[item["fuente"]]
        df = cargar_instructivos(url_instructivos)
        return filtrar_df(df, filtro)
    df = cargar("", item["fuente"])
    if item["prefijo"] and "Título" in df.columns:
        df = df[
            df["Título"].str.lower().str.startswith(item["prefijo"].lower())
        ].reset_index(drop=True)
    return filtrar_df(df, filtro)



@st.dialog("Consulta externa", width="large")
def mostrar_modal_pvaa(titulo: str, url: str):
    st.markdown(f"### {titulo}")
    st.caption("Si el sistema externo bloquea la visualización integrada, usá el enlace para abrirlo en una pestaña nueva.")
    st.components.v1.iframe(url, height=650, scrolling=True)
    st.link_button("Abrir sistema en una pestaña nueva", url, use_container_width=True)
    if st.button("Cerrar ventana", key="cerrar_modal_pvaa", use_container_width=True):
        st.session_state["modal_pvaa_url"] = None
        st.session_state["modal_pvaa_titulo"] = None
        st.rerun()

def mostrar_tabla_fuente(item: dict, filtro_base: str, clave: str, selects=(), textos=(),
                         col_fecha: str = None, anchos: dict = None, detalle: bool = False,
                         nota: str = ""):
    """Muestra la tabla de la página DNIT tal cual la publica, con filtros:
    búsqueda libre, selección múltiple por columna, 'contiene' por columna y orden por fecha."""
    fuente = obtener_fuente(item["fuente"])
    with st.spinner("Extrayendo datos..."):
        try:
            df = obtener_df(item, filtro_base)
        except (requests.RequestException, ValueError) as e:
            st.error(f"No se pudo descargar la página: {e}")
            df = pd.DataFrame()
    if df.empty:
        st.warning("No se pudo leer la tabla de la página")
        if st.button("🔄 Reintentar (limpiar caché)", key=f"tf_retry_{clave}"):
            cargar.clear()
            st.rerun()
        if item["fuente"] == "dnit_biblioteca_regimenes":
            mostrar_diagnostico(fuente["url"], clave, ("DSEM", "EC01"))
        # mostrar_diagnostico(fuente["url"], clave, MUESTRAS_DIAGNOSTICO.get(item["fuente"], ()))
        return

    total = len(df)
    if nota:
        st.caption(nota)
    texto = st.text_input("Buscar en la tabla", key=f"tf_txt_{clave}",
                          placeholder="Palabra clave en cualquier columna...")
    df = filtrar_df(df, texto)

    controles = [("sel", c) for c in selects if c in df.columns] + \
                [("txt", c) for c in textos if c in df.columns]
    if col_fecha and col_fecha in df.columns:
        controles.append(("ord", col_fecha))
    orden = "Como en la fuente"
    base = df
    for inicio in range(0, len(controles), 4):
        celdas = st.columns(4)
        for celda, (tipo_ctl, col) in zip(celdas, controles[inicio:inicio + 4]):
            with celda:
                if tipo_ctl == "sel":
                    valores = sorted({v for v in base[col].astype(str) if v.strip()},
                                     key=lambda v: (not v.isdigit(), -int(v) if v.isdigit() else 0, normalizar(v)))
                    elegidos = st.multiselect(col, valores, key=f"tf_sel_{clave}_{col}")
                    if elegidos:
                        df = df[df[col].astype(str).isin(elegidos)]
                elif tipo_ctl == "txt":
                    valor = st.text_input(f"{col} contiene", key=f"tf_txt_{clave}_{col}")
                    if valor.strip():
                        buscado = normalizar(valor)
                        df = df[df[col].astype(str).map(normalizar).str.contains(buscado, regex=False)]
                else:
                    orden = st.selectbox(f"Orden por {col.lower()}",
                                         ["Como en la fuente", "Más reciente primero", "Más antiguo primero"],
                                         key=f"tf_ord_{clave}")
    if orden != "Como en la fuente":
        fechas = pd.to_datetime(df[col_fecha], dayfirst=True, errors="coerce")
        df = df.assign(_f=fechas).sort_values(
            "_f", ascending=(orden == "Más antiguo primero"), na_position="last", kind="stable"
        ).drop(columns="_f")
    df = df.reset_index(drop=True)

    st.caption(f"Fuente: {fuente['nombre']} ({len(df)} de {total} filas) - {fuente['url']}")
    if df.empty:
        st.warning("Ninguna fila cumple los filtros elegidos.")
        return

    config = {k: v for k, v in COLUMN_CONFIG_NORMAS.items() if k in df.columns}
    for col, ancho in (anchos or {}).items():
        if col in df.columns:
            config[col] = st.column_config.TextColumn(col, width=ancho)
    opciones = {"width": "stretch", "hide_index": True, "column_config": config}
    if detalle:
        evento = st.dataframe(df, key=f"tf_df_{clave}", on_select="rerun",
                              selection_mode="single-row", **opciones)
        filas = evento.selection.rows
        if filas and filas[0] < len(df):
            fila = df.iloc[filas[0]]
            with st.container(border=True):
                st.markdown("**Detalle de la fila seleccionada**")
                for col in df.columns:
                    if str(fila[col]).strip() and not str(col).startswith("Enlace"):
                        st.markdown(f"**{col}:** {fila[col]}")
                if "Enlace Descargar" in df.columns:
                    panel_descarga(fila, f"tf_{clave}")
        else:
            st.caption("💡 Selecciona una fila (casilla de la izquierda) para ver su detalle completo.")
    else:
        st.dataframe(df, **opciones)
    st.download_button("⬇ Descargar CSV (filas filtradas)", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{item['fuente']}.csv", mime="text/csv", key=f"tf_csv_{clave}")
    #mostrar_diagnostico(fuente["url"], clave, MUESTRAS_DIAGNOSTICO.get(item["fuente"], ()))


MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
MONEDAS_COT = ["DOLAR", "REAL", "PESO_ARG", "YEN", "EURO", "LIBRA"]


def filtrar_cotizaciones(df, texto="", anio="Todos", mes="Todos", campo=None,
                         vmin=None, vmax=None, descendente=True, monedas=None):
    """Filtra y ordena el historial de cotizaciones (función pura, sin widgets)."""
    if df.empty:
        return df
    df = filtrar_df(df, texto)
    fechas = pd.to_datetime(df["fecha"], errors="coerce")
    mascara = pd.Series(True, index=df.index)
    if anio != "Todos":
        mascara &= fechas.dt.year == int(anio)
    if mes != "Todos":
        mascara &= fechas.dt.month == MESES.index(mes) + 1
    if campo and campo in df.columns:
        if vmin is not None:
            mascara &= df[campo] >= vmin
        if vmax is not None:
            mascara &= df[campo] <= vmax
    df = df[mascara].assign(_f=fechas[mascara]).sort_values("_f", ascending=not descendente)
    df = df.drop(columns="_f").reset_index(drop=True)
    if monedas is not None:
        df = df[["fecha"] + [c for c in df.columns if c != "fecha" and c.rsplit("_", 1)[0] in monedas]]
    return df


def mostrar_cotizaciones(filtro_base: str, clave: str):
    with st.spinner("Extrayendo datos..."):
        df = obtener_df({"fuente": "dnit_cotizaciones", "prefijo": None}, filtro_base)
    if df.empty:
        st.warning("No se encontraron cotizaciones.")
        return

    fechas = pd.to_datetime(df["fecha"], errors="coerce")
    anios = [str(a) for a in sorted(fechas.dt.year.dropna().astype(int).unique(), reverse=True)]
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1.5])
    with c1:
        texto = st.text_input("Palabra clave o fecha", key=f"cot_txt_{clave}",
                              placeholder="Ej: 2026-04, 7.5...")
    with c2:
        anio = st.selectbox("Año", ["Todos"] + anios, key=f"cot_anio_{clave}")
    with c3:
        mes = st.selectbox("Mes", ["Todos"] + MESES, key=f"cot_mes_{clave}")
    with c4:
        orden = st.selectbox("Orden por fecha", ["Más reciente primero", "Más antigua primero"],
                             key=f"cot_orden_{clave}")

    columnas_valor = [c for c in df.columns if c != "fecha"]
    campo, vmin, vmax, monedas = None, None, None, None
    with st.expander("🔎 Filtros por valor", expanded=False):
        monedas = st.multiselect("Monedas a mostrar", MONEDAS_COT, default=MONEDAS_COT,
                                 key=f"cot_mon_{clave}")
        f1, f2, f3 = st.columns(3)
        with f1:
            campo = st.selectbox("Columna a filtrar", ["(ninguna)"] + columnas_valor,
                                 key=f"cot_campo_{clave}")
        with f2:
            vmin = st.number_input("Valor mínimo", value=None, format="%.2f",
                                   key=f"cot_min_{clave}")
        with f3:
            vmax = st.number_input("Valor máximo", value=None, format="%.2f",
                                   key=f"cot_max_{clave}")
    if campo == "(ninguna)":
        campo = None

    vista = filtrar_cotizaciones(df, texto, anio, mes, campo, vmin, vmax,
                                 descendente=(orden == "Más reciente primero"),
                                 monedas=monedas)
    fuente = obtener_fuente("dnit_cotizaciones")
    st.caption(f"Fuente: {fuente['nombre']} ({len(vista)} de {len(df)} registros) - {fuente['url']}")
    if vista.empty:
        st.warning("Ninguna cotización cumple los filtros elegidos.")
        return
    config = {"fecha": st.column_config.TextColumn("Fecha", width="small")}
    for c in vista.columns:
        if c != "fecha":
            config[c] = st.column_config.NumberColumn(c.replace("_", " "), format="%.2f")
    st.dataframe(vista, width="stretch", hide_index=True, column_config=config)


def mostrar_presentacion_biblioteca(item: dict, tipo: str, clave: str):
    """Muestra una ficha institucional integrada, inspirada en la página oficial."""
    fuente = obtener_fuente(item["fuente"])
    if not fuente or not fuente.get("url"):
        return
    url_oficial = fuente["url"]
    try:
        descripcion, accesos = cargar_presentacion_biblioteca(url_oficial)
    except (requests.RequestException, ValueError):
        descripcion, accesos = [], []

    # Ficha IVA: replica en Streamlit la jerarquía visual de la página institucional
    # y mantiene la normativa/listados de la fuente debajo de esta presentación.
    if normalizar(tipo) == "iva":
        url_iva = "https://www.dnit.gov.py/en/web/portal-institucional/iva"
        st.markdown("""
        <style>
        .iva-hero {background:linear-gradient(110deg,#1e7197,#258ec0);color:white;padding:28px 30px;border-radius:8px;margin:8px 0 24px 0;}
        .iva-hero h2 {color:white!important;font-size:2.25rem;margin:0 0 14px 0;}
        .iva-hero p {color:white!important;font-size:1rem;line-height:1.55;margin-bottom:0;}
        .iva-registro {background:#1b5875;padding:22px 24px;border-radius:6px;color:white;height:100%;}
        .iva-registro h4 {color:white!important;margin-top:0;}
        .iva-registro p {color:white!important;}
        .iva-seccion {color:#5790f0;font-size:1.55rem;font-weight:500;margin:12px 0 14px 0;}
        .iva-caracteristicas {background:#090718;padding:14px 18px;color:#5790f0;border-radius:2px;margin-bottom:12px;}
        .iva-dato {padding:3px 5px 10px 5px;}
        </style>
        """, unsafe_allow_html=True)
        hero_a, hero_b = st.columns([1.05, 1], gap="large")
        with hero_a:
            st.markdown("<div class='iva-hero'><h2>IVA ▤</h2><p>El Impuesto al Valor Agregado (IVA) es un impuesto a las enajenaciones (compra/venta) de bienes situados en el país, las prestaciones de servicios realizadas en el territorio nacional (excluidos los prestados en relación de dependencia) y la importación de bienes.</p></div>", unsafe_allow_html=True)
        with hero_b:
            st.markdown("<div class='iva-hero iva-registro'><h4>Inscripción al IVA</h4><p>Si ya cumples con todos los requisitos para inscribirte al IVA puedes registrarte desde la página oficial de la DNIT.</p></div>", unsafe_allow_html=True)
            st.link_button("📝 Inscribirme / consultar requisitos", url_iva, use_container_width=True)
        st.markdown("<div class='iva-seccion'>Principales características</div>", unsafe_allow_html=True)
        st.markdown("<div class='iva-caracteristicas'><b>Tipo de Impuesto:</b> Al Consumo &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <b>Vencimiento:</b> Mensual<br><b>Tasas:</b> 5% y 10% &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <b>Obligación:</b> 211 - IVA GENERAL</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.markdown("**Regímenes**  \nGeneral  \nRégimen de Turismo")
            st.markdown("**Principales obligaciones**  \nEmitir comprobantes, comunicar la numeración utilizada, registrar comprobantes, presentar la declaración jurada y pagar los tributos.")
        with c2:
            st.markdown("**Obligados**  \nServicios personales fuera de relación de dependencia, alquileres, empresas unipersonales y privadas, sucursales y establecimientos permanentes, estructuras jurídicas transparentes, importadores, contribuyentes del INR y entidades públicas indicadas por la DNIT.")
        st.link_button("🌐 Abrir ficha oficial completa del IVA", url_iva, use_container_width=True)
        st.markdown("---")
        st.markdown("#### 📚 Recursos oficiales relacionados")
        st.caption("A continuación se muestran los documentos y las normativas que la aplicación consigue recuperar de la fuente institucional.")
        return

    with st.container(border=True):
        st.markdown(f"#### ℹ️ ¿Qué encontrarás en {tipo}?")
        if descripcion:
            for parrafo in descripcion:
                st.write(parrafo)
        else:
            st.write(
                f"Esta sección reúne la información que la DNIT publica en la fuente oficial "
                f"de **{fuente.get('nombre', tipo)}**. El listado que sigue se obtiene de esa página "
                "y puede incluir documentos, normativas, guías u otros recursos según el contenido disponible."
            )
        st.caption(f"Fuente institucional: {url_oficial}")
        if accesos:
            st.markdown("**Accesos y servicios detectados en esta fuente**")
            for i, acceso in enumerate(accesos):
                with st.expander(f"🔗 {acceso['titulo']}", expanded=False):
                    st.components.v1.iframe(acceso["url"], height=560, scrolling=True)
                    st.link_button(
                        "Abrir sistema en una pestaña nueva",
                        acceso["url"],
                        key=f"bib_acceso_{clave}_{i}",
                        use_container_width=True,
                    )


def mostrar_destino(seccion: str, tipo: str, item: dict, filtro_base: str = "",
                    con_titulo: bool = True, clave: str = "sec"):
    if con_titulo:
        st.markdown(f"##### {item['icono']} {tipo}")

    if item["fuente"] is None:
        st.info(f"**{tipo}** de **{seccion}** todavía no tiene una fuente configurada (próximamente).")
        return

    # No mostrar descripciones introductorias de cada biblioteca; mantener
    # las tablas, documentos y accesos funcionales de cada sección.
    tipo_normalizado = normalizar(tipo)

    # Instructivos institucionales separados por área: impositiva y aduanera.
    if item["fuente"] in FUENTES_INSTRUCTIVOS:
        url_instructivos, titulo_instructivos = FUENTES_INSTRUCTIVOS[item["fuente"]]
        st.markdown(f"### 📘 {titulo_instructivos}")
        st.caption(f"Listado extraído de la página oficial de la DNIT · {url_instructivos}")
        col_buscar, col_orden = st.columns([3, 1])
        with col_buscar:
            filtro_instructivos = st.text_input(
                "Filtrar instructivos", key=f"filtro_{clave}_{item['fuente']}",
                placeholder="Título, fecha o palabra clave..."
            )
        with st.spinner("Cargando instructivos oficiales..."):
            try:
                df_instructivos = cargar_instructivos(url_instructivos)
            except (requests.RequestException, ValueError) as error:
                st.error(f"No se pudieron extraer los instructivos de la DNIT: {error}")
                st.link_button("🌐 Abrir instructivos en una pestaña nueva", url_instructivos, use_container_width=True)
                return
        if filtro_instructivos.strip():
            df_instructivos = filtrar_df(df_instructivos, filtro_instructivos)
        with col_orden:
            orden_instructivos = st.selectbox(
                "Orden", ["Predeterminado", "Título A-Z", "Título Z-A"],
                key=f"orden_{clave}_{item['fuente']}"
            )
        if orden_instructivos != "Predeterminado" and not df_instructivos.empty:
            df_instructivos = df_instructivos.sort_values(
                "Título", ascending=(orden_instructivos == "Título A-Z"),
                key=lambda col: col.astype(str).str.lower()
            ).reset_index(drop=True)
        st.caption(f"{len(df_instructivos)} instructivos encontrados")
        if df_instructivos.empty:
            st.warning("No se encontraron instructivos en la estructura pública de la página. Podés consultar la fuente oficial directamente.")
        else:
            mostrar_tabla(df_instructivos, {"nombre": titulo_instructivos, "url": url_instructivos},
                          clave=f"tabla_{clave}_{item['fuente']}")
        #st.link_button("🌐 Abrir fuente oficial de instructivos", url_instructivos, use_container_width=True)
        return

    if item["fuente"] == "dnit_biblioteca_mesa":
        url_mesa = "https://secure.aduana.gov.py/dna-apps/public/consultaExpediente"
        st.markdown("**Consulta de expedientes — Mesa de Entrada**")
        st.caption("Si el portal permite mostrar contenido integrado, la consulta aparecerá aquí. Si no carga, utiliza el enlace directo.")
        try:
            st.components.v1.iframe(url_mesa, height=680, scrolling=True)
        except Exception:
            st.link_button("Abrir Mesa de Entrada", url_mesa)
        st.link_button("Abrir portal en una pestaña", url_mesa)
        return

    # PVAA: dos accesos de sistema y la información institucional publicada por DNIT.
    if normalizar(tipo) == "pvaa" or "pvaa" in normalizar(tipo):
        url_registro = "https://secure.aduana.gov.py/dna-apps/public/solicitudRegistroPvaa"
        url_reenvio = "https://secure.dnit.gov.py/presupuesto/app/#/reenviarSolicitud"
        url_pvaa = "https://www.dnit.gov.py/en/web/portal-institucional/personas-vinculadas-a-la-actividad-aduanera"

        st.markdown("### Acceso al Sistema")
        st.caption("Seleccioná uno de los accesos para abrir el sistema en una ventana emergente dentro de esta aplicación.")
        col_registro, col_reenvio = st.columns(2, gap="medium")
        with col_registro:
            st.markdown("**📝 Solicitud de Registro de Personas Vinculadas a la Actividad Aduanera**")
            if st.button("Abrir solicitud de registro", key=f"pvaa_registro_{clave}", use_container_width=True):
                st.session_state["modal_pvaa_url"] = url_registro
                st.session_state["modal_pvaa_titulo"] = "Acceso al Sistema — Solicitud de Registro"
        with col_reenvio:
            st.markdown("**🔁 Reenvío de solicitud PVAA**")
            if st.button("Abrir reenvío de solicitud", key=f"pvaa_reenvio_{clave}", use_container_width=True):
                st.session_state["modal_pvaa_url"] = url_reenvio
                st.session_state["modal_pvaa_titulo"] = "Reenvío de solicitud PVAA"

        # Modal renderizado tras la selección de uno de los accesos.
        if st.session_state.get("modal_pvaa_url"):
            mostrar_modal_pvaa(st.session_state["modal_pvaa_titulo"], st.session_state["modal_pvaa_url"])

        st.markdown("---")
        st.markdown("### 📢 Avisos importantes, guías, instructivos y formularios")
        st.caption("Enlaces recuperados de la página institucional de Personas Vinculadas a la Actividad Aduanera.")
        try:
            recursos = cargar_recursos_pvaa()
            if recursos:
                for grupo in ("Avisos importantes", "Guías", "Instructivos y formularios"):
                    grupo_recursos = [r for r in recursos if r["grupo"] == grupo]
                    if grupo_recursos:
                        with st.expander(f"{grupo} ({len(grupo_recursos)})", expanded=(grupo == "Avisos importantes")):
                            for recurso in grupo_recursos:
                                st.markdown(f"- [{recurso['titulo']}]({recurso['url']})")
            else:
                st.info("No se pudieron detectar recursos individuales automáticamente. Consultá la página oficial.")
        except (requests.RequestException, ValueError) as error:
            st.warning(f"No se pudieron cargar los recursos automáticamente: {error}")
        #mostrar_tablas_pagina(url_pvaa, f"pg_{clave}_pvaa")
        return

    if item["fuente"] == "dnit_cotizaciones":
        mostrar_cotizaciones(filtro_base, f"{clave}_{seccion}_{tipo}")
        return

    if item["fuente"] == "dnit_dictamenes_adu":
        mostrar_tabla_fuente(
            item, filtro_base, f"{clave}_{seccion}_{tipo}",
            selects=("Año", "Resolución", "País", "Capítulo"),
            textos=("Dictamen", "Mercadería", "Clasificación"),
            col_fecha="Fecha", detalle=True,
            anchos={"Dictamen": "medium", "Mercadería": "medium", "Descripción": "large",
                    "Fundamento Legal": "medium"})
        return

    if item["fuente"] == "dnit_enmienda_vii":
        mostrar_tabla_fuente(
            item, filtro_base, f"{clave}_{seccion}_{tipo}",
            selects=("Capítulo", "Correlación"),
            textos=("NCM 2017 (VI Enmienda)", "NCM 2022 (VII Enmienda)"),
            nota=("Tabla de correlación entre la NCM 2017 y la NCM 2022 (VII Enmienda). "
                  "«EX» indica que la correlación no es plena: los códigos alcanzados "
                  "corresponden a más de un ítem."),
            anchos={"NCM 2017 (VI Enmienda)": "medium", "NCM 2022 (VII Enmienda)": "large"})
        return

    if item["fuente"] == "dnit_biblioteca_regimenes":
        mostrar_tabla_fuente(
            item, filtro_base, f"{clave}_{seccion}_{tipo}",
            selects=("Tipo", "Obs.", "Import/Export", "Cancela DDT"),
            textos=("Código", "Descripción"),
            nota="Destinaciones de Importación y Exportación (suspensivos, definitivos y temporales). "
                 "«*» indica que la fuente no informa ese dato.",
            anchos={"N°": "small", "Código": "small", "Descripción": "large",
                    "Tipo": "small", "Obs.": "medium"})
        return

    fuente = obtener_fuente(item["fuente"])
    clave_filtro = f"filtro_{clave}_{seccion}_{tipo}"
    if item.get("filtro_fijo") and clave_filtro not in st.session_state:
        st.session_state[clave_filtro] = item["filtro_fijo"]
    col_texto, col_anio, col_orden = st.columns([3, 1, 1])
    with col_texto:
        local = st.text_input(
            "Palabra clave o número",
            key=clave_filtro,
            placeholder="Título, número, descripción...",
        )
    with st.spinner("Extrayendo datos..."):
        df = obtener_df(item, f"{filtro_base} {local}".strip())

    # El año de la norma se determina prioritariamente desde el TÍTULO.
    # Ej.: "Decreto N° 8635/22" => 2022, aunque la descripción mencione 1931.
    # Solo si el título no contiene año, se recurre a la columna Fecha.
    def anio_norma(fila):
        titulo = str(fila.get("Título", "") or "")
        # Un número aislado en el título (p. ej. "Decreto 2063") NO es un año.
        # Se interpreta como año solo si aparece como sufijo /YY o /YYYY.
        sufijos = re.findall(r"/((?:19|20)?\d{2})(?!\d)", titulo)
        if sufijos:
            sufijo = sufijos[-1]
            if len(sufijo) == 4:
                return sufijo
            numero = int(sufijo)
            return str(2000 + numero if numero <= 29 else 1900 + numero)
        fecha = str(fila.get("Fecha", "") or "")
        if item.get("tablas") and not fecha:
            fecha = " ".join(str(v) for k, v in fila.items() if not str(k).startswith("Enlace"))
        completos_fecha = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", fecha)
        return completos_fecha[-1] if completos_fecha else ""

    anios_por_fila = df.apply(anio_norma, axis=1) if not df.empty else []
    anios = sorted({anio for anio in anios_por_fila if anio}, reverse=True) if not df.empty else []
    with col_anio:
        anio_sel = st.selectbox(
            "Año",
            ["Todos"] + anios,
            key=f"anio_{clave}_{seccion}_{tipo}",
        )
    with col_orden:
        orden = st.selectbox(
            "Orden",
            ["Predeterminado", "Título A-Z", "Título Z-A"],
            key=f"orden_{clave}_{seccion}_{tipo}",
        )

    if anio_sel != "Todos" and not df.empty:
        df = df[anios_por_fila == anio_sel].reset_index(drop=True)
    if orden in ("Título A-Z", "Título Z-A") and "Título" in df.columns:
        df = df.sort_values("Título", ascending=(orden == "Título A-Z"), key=lambda col: col.astype(str).str.lower()).reset_index(drop=True)

    if item.get("tablas"):
        df = aplicar_filtros_columnas(df, f"colf_{clave}_{seccion}_{tipo}")

    detalle = f"  ·  Filtro del buscador: «{filtro_base}»" if filtro_base else ""
    st.caption(f"Fuente: {fuente['nombre']} ({len(df)} resultados) - {fuente['url']}{detalle}")
    if df.empty and item.get("logo"):
        st.caption("Sin documentos en el listado para este filtro (o la página no los publica como lista).")
    else:
        mostrar_tabla(df, fuente, clave=f"tabla_{clave}_{seccion}_{tipo}")
    if item.get("logo"):
        mostrar_tablas_pagina(fuente["url"], f"pg_{clave}_{seccion}_{tipo}")
    if item.get("tablas"):
        mostrar_diagnostico(fuente["url"], f"{clave}_{seccion}_{tipo}")


# ---------------------------------------------------------------------------
# INTERPRETACION DEL BUSCADOR GENERAL
# ---------------------------------------------------------------------------
ALIAS_TIPO = {
    "ley": "Leyes", "leyes": "Leyes",
    "decreto": "Decretos", "decretos": "Decretos",
    "resolucion": "Resoluciones", "resoluciones": "Resoluciones",
    "digesto": "Digesto", "digestos": "Digesto", "digesta": "Digesto", "digestas": "Digesto",
    "iva": "IVA", "irp": "IRP", "ire": "IRE", "idu": "IDU", "inr": "INR", "isc": "ISC",
    "iresimple": "IRE RESIMPLE",
}
ALIAS_SECCION = {
    "impositiva": "Normativa Impositiva", "impositivo": "Normativa Impositiva",
    "tributaria": "Normativa Impositiva", "tributario": "Normativa Impositiva",
    "impuesto": "Normativa Impositiva", "impuestos": "Normativa Impositiva",
    "aduanera": "Normativa Aduanera", "aduanero": "Normativa Aduanera",
    "aduana": "Normativa Aduanera", "aduanas": "Normativa Aduanera",
    "cotizacion": "Cotizaciones", "cotizaciones": "Cotizaciones",
    "dolar": "Cotizaciones", "divisas": "Cotizaciones", "guaranies": "Cotizaciones",
    "cambio": "Cotizaciones", "tipo": "Cotizaciones",
}
ALIAS_TODOS = {
    **{a: ("tipo", v) for a, v in ALIAS_TIPO.items()},
    **{a: ("seccion", v) for a, v in ALIAS_SECCION.items()},
}
PALABRAS_RELLENO = {"de", "del", "la", "el", "los", "las", "en", "y", "dnit",
                    "legislativo", "norma", "normas", "normativa"}
MIN_PREFIJO = 3   
MIN_TIPEO = 4     


def reconocer(palabra: str):
    if palabra in ALIAS_TODOS:
        return ALIAS_TODOS[palabra]

    if len(palabra) >= MIN_PREFIJO:
        candidatos = {v for alias, v in ALIAS_TODOS.items() if alias.startswith(palabra)}
        if len(candidatos) == 1:
            return candidatos.pop()
        if len(candidatos) > 1:
            return None  

    if len(palabra) >= MIN_TIPEO:
        parecidos = difflib.get_close_matches(palabra, ALIAS_TODOS.keys(), n=1, cutoff=0.8)
        if parecidos:
            return ALIAS_TODOS[parecidos[0]]
    return None


def interpretar(termino: str):
    secciones, tipos, resto = [], [], []
    for original in termino.lower().split():
        norm = normalizar(original)
        if norm in PALABRAS_RELLENO:
            continue
        hallado = reconocer(norm)
        if hallado is None:
            resto.append(original)
        elif hallado[0] == "seccion":
            if hallado[1] not in secciones:
                secciones.append(hallado[1])
        else:
            if hallado[1] not in tipos:
                tipos.append(hallado[1])
    return secciones, tipos, " ".join(resto)


def destinos(secciones: list, tipos: list):
    salida = []
    for seccion, datos in NAVEGACION.items():
        if secciones and seccion not in secciones:
            continue
        if seccion == "Cotizaciones" and seccion not in secciones:
            continue  
        for tipo, item in datos["items"].items():
            if tipos and seccion != "Cotizaciones" and tipo not in tipos:
                continue
            salida.append((seccion, tipo, item))
    return salida


def destinos_con_resultados(secciones: list, tipos: list, filtro_texto: str):
    """Recorre en paralelo todos los apartados candidatos (los indicados por
    alias, o TODOS si no se reconoció ninguno) y devuelve solo los que
    realmente tienen coincidencias para filtro_texto. Así el buscador
    general ya no se limita a las palabras clave de cada sección: cualquier
    texto libre ("relativa a la...") se busca en cada apartado."""
    candidatos = [
        (seccion, tipo, item)
        for seccion, tipo, item in destinos(secciones, tipos)
        if item["fuente"] is not None
    ]

    def probar(candidato):
        _, _, item = candidato
        try:
            df = obtener_df(item, filtro_texto)
        except Exception:
            df = pd.DataFrame()
        return candidato, not df.empty

    encontrados = []
    if candidatos:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for candidato, tiene_resultados in pool.map(probar, candidatos):
                if tiene_resultados:
                    encontrados.append(candidato)
    return encontrados


def css_tarjeta_biblioteca(clave: str, logo: str, color: str, estado: str) -> str:
    """Convierte un st.button en una tarjeta con logo; toda su superficie es clickeable."""
    sel = f".st-key-{clave} button"
    css = (
        f"{sel}, {sel}:hover, {sel}:focus, {sel}:active {{"
        f' background-color: {color} !important; background-image: url("{logo}") !important;'
        " background-repeat: no-repeat !important; background-position: center !important;"
        " background-size: contain !important; background-origin: content-box !important;"
        " height: 65px !important; padding: 12px !important; color: transparent !important;"
        " border: 3px solid transparent !important; transition: all 0.2s ease; }"
        f" {sel} p {{ font-size: 0 !important; }}"
        f" {sel}:hover {{ filter: brightness(1.15); }}"
    )
    if estado == "palida":
        css += (f" {sel} {{ opacity: 0.3; filter: grayscale(0.6); }}"
                f" {sel}:hover {{ opacity: 0.8; filter: none; }}")
    elif estado == "activa":
        css += (f" {sel}, {sel}:hover {{ border-color: #ffffff !important;"
                " transform: translateY(-2px) scale(1.04);"
                " box-shadow: 0 0 0 3px #2563eb, 0 8px 18px rgba(0,0,0,0.35); }")
    return css


# ---------------------------------------------------------------------------
# ESTILOS (Actualizados con los colores institucionales de la DNIT)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .header-dnit {
        background-color: #0b132b;
        padding: 10px 18px;
        border-radius: 10px;
        margin-bottom: 0.65rem;
        border-bottom: 3px solid #3b82f6;
        display: flex;
        align-items: center;
        gap: 16px;
      }
      .header-dnit img { max-height: 58px; max-width: 155px; object-fit: contain; }
      .header-dnit h1 { color: #ffffff; margin: 0; padding: 0; font-size: 1.85rem; font-weight: 700; }
      .header-dnit p { color: #cbd5e1; margin: 2px 0 0 0; font-size: 0.82rem; }
      .tarjeta-titulo { font-size: 1.1rem; font-weight: 600; margin: 0; }
      .tarjeta-desc { color: #64748b; font-size: 0.84rem; margin: 0.15rem 0 0.45rem 0; }
      div[data-testid="stVerticalBlock"] { gap: 0.55rem; }
      div.stButton > button { padding-top: 0.35rem; padding-bottom: 0.35rem; }
      section[data-testid="stSidebar"] div[data-testid="stExpander"] { margin-bottom: 0.4rem; }
      /* Botones del menú lateral: texto alineado a la izquierda */
      [data-testid="stSidebar"] [data-testid^="stBaseButton"],
      section[data-testid="stSidebar"] div.stButton > button {
        justify-content: flex-start !important; text-align: left !important;
      }
      [data-testid="stSidebar"] [data-testid^="stBaseButton"] > div,
      [data-testid="stSidebar"] [data-testid^="stBaseButton"] [data-testid="stMarkdownContainer"],
      [data-testid="stSidebar"] [data-testid^="stBaseButton"] p {
        justify-content: flex-start !important; text-align: left !important; width: 100%;
      }
      /* Tarjetas de la biblioteca: la seleccionada resalta, el resto se ve más pálido */
      .card-biblio {
        padding: 12px; border-radius: 8px; height: 65px; margin-bottom: 10px;
        display: flex; align-items: center; justify-content: center;
        border: 3px solid transparent; transition: all 0.2s ease;
      }
      .card-biblio img { max-height: 40px; max-width: 100%; object-fit: contain; }
      .card-biblio:hover { filter: brightness(1.15); }
      .card-biblio.palida { opacity: 0.3; filter: grayscale(0.6); }
      .card-biblio.palida:hover { opacity: 0.8; filter: none; }
      .card-biblio.activa {
        border-color: #ffffff; transform: translateY(-2px) scale(1.04);
        box-shadow: 0 0 0 3px #2563eb, 0 8px 18px rgba(0, 0, 0, 0.35);
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# CABECERA + BUSCADOR GENERAL
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="header-dnit">
      <img src="{LOGO_URL}" alt="Logo DNIT">
      <div>
        <h1>Buscador General</h1>
        <p>Sistema de consulta y filtrado de datos institucionales</p>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

URL_MARANGATU = "https://marangatu.set.gov.py/eset/login"

col_buscador, col_limpiar, col_marangatu = st.columns([4, 1, 1.4], gap="medium")
with col_buscador:
    termino = st.text_input(
        "Buscar",
        key="termino_general",
        placeholder="Escribe una palabra clave (ej: dnit, dolar, decreto, ley)...",
        label_visibility="collapsed",
    )
with col_limpiar:
    st.button("✖ Limpiar", key="limpiar_general", on_click=limpiar_todo, width="stretch")
with col_marangatu:
    st.markdown(
        f"""
        <a href="{URL_MARANGATU}" target="_blank" rel="noopener" style="text-decoration: none;">
            <div style="background: #0e1830; border: 1px solid #223150; border-radius: 10px;
            padding: 6px 14px; height: 44px; display: flex; align-items: center; justify-content: center;
            gap: 10px; transition: filter 0.2s ease;"
            onmouseover="this.style.filter='brightness(1.2)'" onmouseout="this.style.filter='brightness(1)'"
            title="Ingresar a Marangatu">
                <svg width="22" height="22" viewBox="0 0 100 100" fill="none" stroke="#8bc34a" stroke-width="3" aria-hidden="true">
                    <circle cx="50" cy="28" r="20"/><circle cx="50" cy="72" r="20"/>
                    <circle cx="31" cy="39" r="20"/><circle cx="69" cy="39" r="20"/>
                    <circle cx="31" cy="61" r="20"/><circle cx="69" cy="61" r="20"/>
                </svg>
                <span style="color: #e7ebf3; font-size: 0.9rem; font-weight: 600; white-space: nowrap;">Ingresar a Marangatu</span>
            </div>
        </a>
        """,
        unsafe_allow_html=True,
    )

if termino.strip():
    # secciones/tipos: solo restringen la búsqueda si el usuario escribió un
    # alias reconocido (p. ej. "decreto", "aduanera"). Si no reconoce nada,
    # ambas listas quedan vacías y destinos_con_resultados recorre TODO.
    secciones, tipos, resto = interpretar(termino)

    with st.spinner(f"Buscando «{termino}» en los apartados relevantes..."):
        coincidencias = destinos_con_resultados(secciones, tipos, resto)

    if coincidencias:
        por_seccion = {}
        for seccion, tipo, item in coincidencias:
            por_seccion.setdefault(seccion, []).append((tipo, item))

        total = sum(len(lista) for lista in por_seccion.values())
        st.caption(
            f"{total} apartado(s) con coincidencias en {len(por_seccion)} sección(es)."
            "  ·  Usa «✖ Limpiar» para volver al inicio."
        )

        def pintar(seccion, lista):
            for tipo, item in lista:
                mostrar_destino(seccion, tipo, item, resto, con_titulo=True, clave="busq")

        if len(por_seccion) > 1:
            pestanias = st.tabs(
                [f"{NAVEGACION[sec]['icono']} {sec} ({len(lista)})" for sec, lista in por_seccion.items()]
            )
            for pestania, (seccion, lista) in zip(pestanias, por_seccion.items()):
                with pestania:
                    pintar(seccion, lista)
        else:
            for seccion, lista in por_seccion.items():
                st.subheader(f"{NAVEGACION[seccion]['icono']} {seccion}")
                pintar(seccion, lista)
    else:
        st.warning(
            f"No se encontraron resultados para «{termino}» en ninguna sección "
            "(Normativa Impositiva, Normativa Aduanera ni sus bibliotecas)."
        )
        st.caption("Usa «✖ Limpiar» para volver al inicio.")

else:
    # -----------------------------------------------------------------
    # BARRA LATERAL DE NAVEGACION (v2): las categorías ya no están en
    # botones centrales, sino agrupadas por sección en el sidebar.
    # -----------------------------------------------------------------
    with st.sidebar:
        st.markdown("###### 📂 Menú de navegación")
        for nombre in ("Normativa Impositiva", "Normativa Aduanera"):
            datos = NAVEGACION[nombre]
            items_normales = {
                t: i for t, i in datos["items"].items()
                if "logo" not in i and "filtro_fijo" not in i
            }
            seccion_activa = st.session_state.seccion == nombre
            with st.expander(f"{datos['icono']} {nombre}", expanded=True):
                for tipo, item in items_normales.items():
                    etiqueta = f"{item['icono']} {tipo}"
                    if item["fuente"] is None:
                        etiqueta += " (próximamente)"
                    activo = seccion_activa and st.session_state.tipo == tipo
                    st.button(
                        etiqueta,
                        key=f"nav_{nombre}_{tipo}",
                        on_click=ir_a,
                        args=(nombre, tipo),
                        width="stretch",
                        type="primary" if activo else "secondary",
                    )
        st.markdown("---")
        activo_cot = st.session_state.seccion == "Cotizaciones"
        st.button(
            "💱 Cotizaciones",
            key="nav_cotizaciones",
            on_click=ir_a,
            args=("Cotizaciones", "Historial"),
            width="stretch",
            type="primary" if activo_cot else "secondary",
        )

    # -----------------------------------------------------------------
    # BIBLIOTECA: se mantiene debajo del buscador general (en la
    # pantalla principal) para acceso directo, agrupada por sección.
    # -----------------------------------------------------------------
    seleccion_actual = (st.session_state.seccion, st.session_state.tipo)
    hay_biblioteca_activa = any(
        "logo" in i and seleccion_actual == (n, t)
        for n, d in NAVEGACION.items() for t, i in d["items"].items()
    )
    estilos_biblioteca = []
    for n_sec, nombre in enumerate(("Normativa Impositiva", "Normativa Aduanera")):
        datos = NAVEGACION[nombre]
        items_logo = {t: i for t, i in datos["items"].items() if "logo" in i}
        if not items_logo:
            continue
        st.caption(f"Biblioteca · {nombre}")
        columnas_logo = st.columns(4)
        for i, (tipo, item) in enumerate(items_logo.items()):
            with columnas_logo[i % 4]:
                clave_btn = f"bib_{n_sec}_{i}"
                st.button(tipo, key=clave_btn, help=tipo, on_click=ir_a,
                          args=(nombre, tipo), width="stretch")
            estado = ""
            if hay_biblioteca_activa:
                estado = "activa" if (nombre, tipo) == seleccion_actual else "palida"
            estilos_biblioteca.append(
                css_tarjeta_biblioteca(clave_btn, item["logo"], item.get("color", "#1e293b"), estado)
            )
    st.markdown("<style>" + "".join(estilos_biblioteca) + "</style>", unsafe_allow_html=True)

    st.markdown("---")

    # -----------------------------------------------------------------
    # RESULTADOS de la sección/tipo elegidos en el sidebar
    # -----------------------------------------------------------------
    if st.session_state.seccion and st.session_state.tipo:
        seccion, tipo = st.session_state.seccion, st.session_state.tipo
        item = NAVEGACION[seccion]["items"][tipo]
        with st.container(key="resultados"):
            col_titulo, col_accion = st.columns([5, 1])
            with col_titulo:
                st.subheader(f"{NAVEGACION[seccion]['icono']} Resultados: {seccion} › {tipo}")
            with col_accion:
                st.button("✖ Limpiar", on_click=volver_inicio, width="stretch")
            mostrar_destino(seccion, tipo, item, "", con_titulo=False, clave="sec")
        scroll_a_resultados()
    else:
        st.info("Selecciona una opción del menú lateral (izquierda) para ver los resultados aquí.")
