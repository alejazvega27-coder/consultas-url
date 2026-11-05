"""
Aplicación principal de Streamlit para el buscador DNIT.
Ejecución: streamlit run app.py

Estructura:
  - Buscador general (siempre arriba): detecta la fuente según lo escrito.
  - Pantalla de inicio: Normativa Impositiva, Normativa Aduanera y Cotizaciones.
  - Resultados de cada categoría debajo del menú, sin navegar a otra vista.
"""

import difflib
import pathlib
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

# ─── NUEVO: ruta del Manual de Usuario (PDF) que se sirve desde la propia app ───
# Colocar el archivo "Manual_Usuario_Buscador_DNIT.pdf" en la misma carpeta que
# este app.py (o en una subcarpeta "assets/").
RUTA_MANUAL = pathlib.Path(__file__).parent / "Manual_Usuario_Buscador_DNIT.pdf"
RUTA_MANUAL_ALT = pathlib.Path(__file__).parent / "assets" / "Manual_Usuario_Buscador_DNIT.pdf"


@st.cache_data(show_spinner=False)
def cargar_manual() -> bytes | None:
    """Lee el PDF del manual desde el disco (caché en memoria). Devuelve None si no existe."""
    for ruta in (RUTA_MANUAL, RUTA_MANUAL_ALT):
        if ruta.exists():
            return ruta.read_bytes()
    return None


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
    # y mantiene la normativa/listados de la fuente
