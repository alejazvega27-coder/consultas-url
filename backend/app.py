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

import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from scraper import (
    BIBLIOTECA_IMPOSITIVA,
    BIBLIOTECA_ADUANERA,
    descargar_archivo,
    ejecutar_busqueda,
    nombre_archivo,
    obtener_fuente,
)

st.set_page_config(layout="wide", page_title="Buscador DNIT")

LOGO_URL = "https://www.dnit.gov.py/documents/d/global/logo-light-svg-1?download=true"

# Sincronizar parámetros de URL para permitir clics directos en los iconos
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


def volver_inicio():
    st.session_state.seccion = None
    st.session_state.tipo = None
    st.query_params.clear()


# ---------------------------------------------------------------------------
# DATOS (con cache para que navegar no vuelva a descargar todo)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cargar(termino: str, fuente_id: str | None):
    _, df = ejecutar_busqueda(termino, fuente_id)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_instructivos(url: str):
    """Extrae las filas de la página oficial de instructivos para mostrarlas como tabla."""
    respuesta = requests.get(
        url,
        timeout=25,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BuscadorDNIT/1.0)"},
    )
    respuesta.raise_for_status()
    soup = BeautifulSoup(respuesta.text, "html.parser")
    registros = []
    vistos = set()

    # Primero se leen las tablas que publica el portal, conservando encabezados y filas.
    for tabla in soup.select("table"):
        filas = tabla.select("tr")
        if not filas:
            continue
        encabezados = [" ".join(c.get_text(" ", strip=True).split()) for c in filas[0].select("th,td")]
        for fila in filas[1:] if any(filas[0].select("th")) else filas:
            celdas = fila.select("th,td")
            if not celdas:
                continue
            textos = [" ".join(c.get_text(" ", strip=True).split()) for c in celdas]
            anclas = fila.select("a[href]")
            if not any(textos) and not anclas:
                continue
            enlace = anclas[0] if anclas else None
            titulo = " ".join(enlace.get_text(" ", strip=True).split()) if enlace else (textos[0] if textos else "")
            href = urllib.parse.urljoin(url, enlace.get("href", "")) if enlace else ""
            if not titulo and textos:
                titulo = " | ".join(t for t in textos if t)
            if not titulo:
                continue
            fecha = ""
            descripcion = ""
            for i, valor in enumerate(textos):
                if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:19|20)\d{2}\b", valor):
                    fecha = valor
                elif valor and valor != titulo and valor not in encabezados:
                    descripcion = valor if not descripcion else f"{descripcion} · {valor}"
            clave = (titulo, href)
            if clave not in vistos:
                vistos.add(clave)
                registros.append({"Sección": "Instructivos", "Fecha": fecha, "Título": titulo,
                                  "Descripción": descripcion, "Enlace Descargar": href,
                                  "Enlace Ver": href or url})

    # Algunos listados de Liferay no usan <table>; se extraen enlaces de la zona de contenido.
    if not registros:
        contenido = soup.select_one("main") or soup.select_one(".portlet-body") or soup.body or soup
        for a in contenido.select("a[href]"):
            titulo = " ".join(a.get_text(" ", strip=True).split())
            href = urllib.parse.urljoin(url, a.get("href", ""))
            if not titulo or len(titulo) < 3 or href.startswith(("javascript:", "mailto:", "#")):
                continue
            texto_norm = normalizar(titulo + " " + href)
            # Evitar enlaces de navegación genéricos y priorizar instructivos/documentos.
            if not any(p in texto_norm for p in ("instructivo", "formulario", "guia", "manual", ".pdf", ".doc", ".xls", "descargar", "ver archivo")):
                continue
            clave = (titulo, href)
            if clave in vistos:
                continue
            vistos.add(clave)
            registros.append({"Sección": "Instructivos", "Fecha": "", "Título": titulo,
                              "Descripción": "", "Enlace Descargar": href,
                              "Enlace Ver": href})

    return pd.DataFrame(registros, columns=["Sección", "Fecha", "Título", "Descripción", "Enlace Descargar", "Enlace Ver"])


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
    if "Enlace Ver" in df.columns:
        opciones["column_config"] = COLUMN_CONFIG_NORMAS

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


def filtrar_df(df, texto: str):
    tokens = normalizar(texto).split()
    if df.empty or not tokens:
        return df
    filas = df.astype(str).apply(lambda col: col.map(normalizar)).apply(" ".join, axis=1)
    mascara = filas.apply(lambda fila: all(t in fila for t in tokens))
    return df[mascara].reset_index(drop=True)


def obtener_df(item: dict, filtro: str):
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

def mostrar_destino(seccion: str, tipo: str, item: dict, filtro_base: str = "",
                    con_titulo: bool = True, clave: str = "sec"):
    if con_titulo:
        st.markdown(f"##### {item['icono']} {tipo}")

    if item["fuente"] is None:
        st.info(f"**{tipo}** de **{seccion}** todavía no tiene una fuente configurada (próximamente).")
        return

    # Instructivos institucionales separados por área: impositiva y aduanera.
    fuentes_instructivos = {
        "dnit_instructivos_imp": (
            "https://www.dnit.gov.py/en/web/portal-institucional/instructivos",
            "Instructivos Impositivos",
        ),
        "dnit_instructivos_adu": (
            "https://www.dnit.gov.py/en/web/portal-institucional/instructivos1",
            "Instructivos Aduaneros",
        ),
    }
    if item["fuente"] in fuentes_instructivos:
        url_instructivos, titulo_instructivos = fuentes_instructivos[item["fuente"]]
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
        st.link_button("🌐 Abrir fuente oficial de instructivos", url_instructivos, use_container_width=True)
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
        st.link_button("🌐 Abrir página oficial de PVAA", url_pvaa, use_container_width=True)
        with st.expander("Vista integrada del portal oficial"):
            st.components.v1.iframe(url_pvaa, height=650, scrolling=True)
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

    detalle = f"  ·  Filtro del buscador: «{filtro_base}»" if filtro_base else ""
    st.caption(f"Fuente: {fuente['nombre']} ({len(df)} resultados) - {fuente['url']}{detalle}")
    mostrar_tabla(df, fuente, clave=f"tabla_{clave}_{seccion}_{tipo}")


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

termino = st.text_input(
    "Buscar",
    key="termino_general",
    placeholder="Escribe una palabra clave (ej: dnit, dolar, decreto, ley)...",
    label_visibility="collapsed",
)

if termino.strip():
    secciones, tipos, resto = interpretar(termino)

    if secciones or tipos:
        por_seccion = {}
        for seccion, tipo, item in destinos(secciones, tipos):
            por_seccion.setdefault(seccion, []).append((tipo, item))

        st.caption("Borra el texto del buscador para volver al inicio.")

        def pintar(seccion, lista):
            for tipo, item in lista:
                mostrar_destino(seccion, tipo, item, resto, con_titulo=True, clave="busq")

        if len(por_seccion) > 1:
            pestanias = st.tabs(
                [f"{NAVEGACION[sec]['icono']} {sec}" for sec in por_seccion]
            )
            for pestania, (seccion, lista) in zip(pestanias, por_seccion.items()):
                with pestania:
                    pintar(seccion, lista)
        else:
            for seccion, lista in por_seccion.items():
                st.subheader(f"{NAVEGACION[seccion]['icono']} {seccion}")
                pintar(seccion, lista)
    else:
        with st.spinner("Procesando consulta y extrayendo datos..."):
            fuente, df_resultado = ejecutar_busqueda(termino)
        filtro_local = st.text_input(
            "Filtrar dentro de los resultados",
            key="filtro_busq_general",
            placeholder="Número, año, palabra clave...",
        )
        df_resultado = filtrar_df(df_resultado, filtro_local)
        st.caption(
            f"Fuente: {fuente['nombre']} ({len(df_resultado)} filas) - {fuente['url']}  ·  "
            "Borra el texto del buscador para volver al inicio."
        )
        mostrar_tabla(df_resultado, fuente, clave="tabla_busq_general")

else:
    # Si se eligió una categoría, mostrar primero los resultados: evita que
    # queden debajo de todo el catálogo de opciones.
    if st.session_state.seccion and st.session_state.tipo:
        seccion, tipo = st.session_state.seccion, st.session_state.tipo
        item = NAVEGACION[seccion]["items"][tipo]
        col_titulo, col_accion = st.columns([5, 1])
        with col_titulo:
            st.subheader(f"{NAVEGACION[seccion]['icono']} Resultados: {seccion} › {tipo}")
        with col_accion:
            st.button("✖ Limpiar", on_click=volver_inicio, width="stretch")
        mostrar_destino(seccion, tipo, item, "", con_titulo=False, clave="sec")
        st.markdown("---")

    col_imp, col_adu = st.columns(2, gap="large")

    for columna, nombre in ((col_imp, "Normativa Impositiva"), (col_adu, "Normativa Aduanera")):
        datos = NAVEGACION[nombre]
        with columna, st.container(border=True):
            st.markdown(
                f"<p class='tarjeta-titulo'>{datos['icono']} {nombre}</p>"
                f"<p class='tarjeta-desc'>{datos['descripcion']}</p>",
                unsafe_allow_html=True,
            )
            items_normales = {
                t: i for t, i in datos["items"].items()
                if "logo" not in i and "filtro_fijo" not in i
            }
            items_logo = {t: i for t, i in datos["items"].items() if "logo" in i}

            botones = st.columns(2)
            for i, (tipo, item) in enumerate(items_normales.items()):
                etiqueta = f"{item['icono']} {tipo}"
                if item["fuente"] is None:
                    etiqueta += " (próximamente)"
                botones[i % 2].button(
                    etiqueta,
                    key=f"btn_{nombre}_{tipo}",
                    on_click=ir_a,
                    args=(nombre, tipo),
                    width="stretch",
                )
# Biblioteca donde cada tarjeta de color e imagen es directamente el enlace/botón de acceso
            if items_logo:
                st.caption("Biblioteca")
                columnas_logo = st.columns(4)
                for i, (tipo, item) in enumerate(items_logo.items()):
                    with columnas_logo[i % 4]:
                        color_fondo = item.get("color", "#1e293b")
                        url_destino = f"?sec={urllib.parse.quote(nombre)}&tip={urllib.parse.quote(tipo)}"
                        
                        st.markdown(
                            f"""
                            <a href="{url_destino}" target="_self" style="text-decoration: none;">
                                <div style="background-color: {color_fondo}; padding: 12px; border-radius: 8px; 
                                text-align: center; margin-bottom: 10px; display: flex; align-items: center; justify-content: center; height: 65px; transition: filter 0.2s ease;"
                                onmouseover="this.style.filter='brightness(1.15)'" onmouseout="this.style.filter='brightness(1)'" title="{tipo}">
                                    <img src="{item['logo']}" style="max-height: 40px; max-width: 100%; object-fit: contain;">
                                </div>
                            </a>
                            """,
                            unsafe_allow_html=True,
                        )

    datos = NAVEGACION["Cotizaciones"]
    with st.container(border=True):
        st.markdown(
            f"<p class='tarjeta-titulo'>{datos['icono']} Cotizaciones</p>"
            f"<p class='tarjeta-desc'>{datos['descripcion']}</p>",
            unsafe_allow_html=True,
        )
        st.button(
            "📈 Ver historial de cotizaciones",
            key="btn_cotizaciones",
            on_click=ir_a,
            args=("Cotizaciones", "Historial"),
        )
