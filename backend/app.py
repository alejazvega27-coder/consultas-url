"""
Aplicación principal de Streamlit para el buscador DNIT.
Ejecución: streamlit run app.py

Estructura:
  - Buscador general (siempre arriba): detecta la fuente según lo escrito.
  - Pantalla de inicio: Normativa Impositiva, Normativa Aduanera y Cotizaciones.
  - Vista de sección: tabla de resultados + filtro propio de esa sección.
"""

import difflib
import unicodedata

import requests
import streamlit as st
from scraper import (
    BIBLIOTECA_IMPOSITIVA,
    descargar_archivo,
    ejecutar_busqueda,
    nombre_archivo,
    obtener_fuente,
)

st.set_page_config(layout="wide", page_title="Buscador DNIT")

LOGO_URL = "https://www.dnit.gov.py/documents/d/global/logo-light-svg-1?download=true"

# ---------------------------------------------------------------------------
# MAPA DE NAVEGACION
# ---------------------------------------------------------------------------
NAVEGACION = {
    "Normativa Impositiva": {
        "icono": "🧾",
        "descripcion": "Leyes, decretos, resoluciones y digesto en materia tributaria.",
        "items": {
            "Leyes":        {"icono": "⚖️", "fuente": "dnit_leyes_imp",        "prefijo": None},
            "Decretos":     {"icono": "📜", "fuente": "dnit_decretos_imp",     "prefijo": None},
            "Resoluciones": {"icono": "📑", "fuente": "dnit_resoluciones_imp", "prefijo": None},
            "Digesto":      {"icono": "📚", "fuente": "digesto_tributario",    "prefijo": None},
            # Biblioteca impositiva integrada de forma directa
            "IVA":          {"icono": "📖", "fuente": "dnit_biblioteca_iva",   "prefijo": None},
            "IRP":          {"icono": "📖", "fuente": "dnit_biblioteca_irp",   "prefijo": None},
            "IRE":          {"icono": "📖", "fuente": "dnit_biblioteca_ire",   "prefijo": None},
            "IDU":          {"icono": "📖", "fuente": "dnit_biblioteca_idu",   "prefijo": None},
            "INR":          {"icono": "📖", "fuente": "dnit_biblioteca_inr",   "prefijo": None},
            "ISC":          {"icono": "📖", "fuente": "dnit_biblioteca_isc",   "prefijo": None},
            "IRE RESIMPLE": {"icono": "📖", "fuente": "dnit_biblioteca_ire_resimple", "prefijo": None},
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


def ir_a(seccion: str, tipo: str):
    st.session_state.seccion = seccion
    st.session_state.tipo = tipo


def volver_inicio():
    st.session_state.seccion = None
    st.session_state.tipo = None


# ---------------------------------------------------------------------------
# DATOS (con cache para que navegar no vuelva a descargar todo)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cargar(termino: str, fuente_id: str | None):
    _, df = ejecutar_busqueda(termino, fuente_id)
    return df


def panel_descarga(fila, clave: str):
    """Boton de descarga real (no previsualizacion) para la fila seleccionada."""
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

    # Con filas seleccionables: al elegir una aparece su boton de descarga
    evento = st.dataframe(
        df, key=clave, on_select="rerun", selection_mode="single-row", **opciones
    )
    filas = evento.selection.rows
    if filas and filas[0] < len(df):
        panel_descarga(df.iloc[filas[0]], clave)
    else:
        st.caption("💡 Selecciona una fila (casilla de la izquierda) para descargar su archivo.")


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes (resolución -> resolucion)."""
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def filtrar_df(df, texto: str):
    """Deja las filas donde TODAS las palabras aparecen en alguna columna
    (sin distinguir mayúsculas ni tildes)."""
    tokens = normalizar(texto).split()
    if df.empty or not tokens:
        return df
    filas = df.astype(str).apply(lambda col: col.map(normalizar)).apply(" ".join, axis=1)
    mascara = filas.apply(lambda fila: all(t in fila for t in tokens))
    return df[mascara].reset_index(drop=True)


def obtener_df(item: dict, filtro: str):
    """Carga la fuente completa (cacheada), aplica el prefijo del item
    (ej. solo 'Ley ...') y después el filtro de texto."""
    df = cargar("", item["fuente"])
    if item["prefijo"] and "Título" in df.columns:
        df = df[
            df["Título"].str.lower().str.startswith(item["prefijo"].lower())
        ].reset_index(drop=True)
    return filtrar_df(df, filtro)


def mostrar_destino(seccion: str, tipo: str, item: dict, filtro_base: str = "",
                    con_titulo: bool = True, clave: str = "sec"):
    """Muestra un par sección/tipo: filtro propio + tabla (o aviso 'próximamente')."""
    if con_titulo:
        st.markdown(f"##### {item['icono']} {tipo}")

    if item["fuente"] is None:
        st.info(f"**{tipo}** de **{seccion}** todavía no tiene una fuente configurada (próximamente).")
        return

    fuente = obtener_fuente(item["fuente"])
    local = st.text_input(
        f"Filtrar dentro de {tipo}",
        key=f"filtro_{clave}_{seccion}_{tipo}",
        placeholder="Número, año, palabra clave...",
    )
    filtro = f"{filtro_base} {local}".strip()

    with st.spinner("Extrayendo datos..."):
        df = obtener_df(item, filtro)

    detalle = f"  ·  Filtro del buscador: «{filtro_base}»" if filtro_base else ""
    st.caption(f"Fuente: {fuente['nombre']} ({len(df)} filas) - {fuente['url']}{detalle}")
    mostrar_tabla(df, fuente, clave=f"tabla_{clave}_{seccion}_{tipo}")


# ---------------------------------------------------------------------------
# INTERPRETACION DEL BUSCADOR GENERAL
# ---------------------------------------------------------------------------
ALIAS_TIPO = {
    "ley": "Leyes", "leyes": "Leyes",
    "decreto": "Decretos", "decretos": "Decretos",
    "resolucion": "Resoluciones", "resoluciones": "Resoluciones",
    "digesto": "Digesto", "digestos": "Digesto", "digesta": "Digesto", "digestas": "Digesto",
    "iva": "IVA", "irp": "IRP", "ire": "IRE", "idu": "IDU",
    "inr": "INR", "isc": "ISC", "resimple": "IRE RESIMPLE",
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
# ESTILOS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .cabecera { text-align: center; margin: 0.5rem 0 1.5rem 0; }
      .cabecera img { max-height: 70px; margin-bottom: 0.6rem; }
      .cabecera h1 { margin: 0; padding: 0; }
      .cabecera p { color: #64748b; margin: 0.3rem 0 0 0; }
      .tarjeta-titulo { font-size: 1.3rem; font-weight: 600; margin: 0; }
      .tarjeta-desc { color: #64748b; font-size: 0.92rem; margin: 0.2rem 0 0.8rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# CABECERA + BUSCADOR GENERAL (siempre visible)
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="cabecera">
      <img src="{LOGO_URL}" alt="Logo DNIT">
      <h1>Buscador General</h1>
      <p>Sistema de consulta y filtrado de datos institucionales</p>
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

# ---------------------------------------------------------------------------
# 1) BUSQUEDA GENERAL
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 2) VISTA DE SECCION
# ---------------------------------------------------------------------------
elif st.session_state.seccion:
    seccion, tipo = st.session_state.seccion, st.session_state.tipo
    item = NAVEGACION[seccion]["items"][tipo]

    st.button("← Volver al inicio", on_click=volver_inicio)
    st.subheader(f"{NAVEGACION[seccion]['icono']} {seccion} › {tipo}")
    mostrar_destino(seccion, tipo, item, "", con_titulo=False, clave="sec")

# ---------------------------------------------------------------------------
# 3) PANTALLA DE INICIO
# ---------------------------------------------------------------------------
else:
    col_imp, col_adu = st.columns(2, gap="large")

    # Columna de Normativa Impositiva
    with col_imp, st.container(border=True):
        datos = NAVEGACION["Normativa Impositiva"]
        st.markdown(
            f"<p class='tarjeta-titulo'>{datos['icono']} Normativa Impositiva</p>"
            f"<p class='tarjeta-desc'>{datos['descripcion']}</p>",
            unsafe_allow_html=True,
        )
        
        # Botones principales (Leyes, Decretos, Resoluciones, Digesto)
        principales = ["Leyes", "Decretos", "Resoluciones", "Digesto"]
        botones = st.columns(2)
        for i, tipo in enumerate(principales):
            item = datos["items"][tipo]
            etiqueta = f"{item['icono']} {tipo}"
            botones[i % 2].button(
                etiqueta,
                key=f"btn_Normativa Impositiva_{tipo}",
                on_click=ir_a,
                args=("Normativa Impositiva", tipo),
                width="stretch",
            )
        
        # Biblioteca de impuestos desplegada directamente con sus logos
        st.markdown("---")
        st.markdown("##### 📖 Biblioteca de Normativas por Impuesto")
        impuestos_biblio = ["IVA", "IRP", "IRE", "IDU", "INR", "ISC", "IRE RESIMPLE"]
        cols_biblio = st.columns(4)
        for idx, tipo in enumerate(impuestos_biblio):
            cat = next((c for c in BIBLIOTECA_IMPOSITIVA if c["nombre"] == tipo), None)
            with cols_biblio[idx % 4]:
                if cat and "logo" in cat:
                    st.image(cat["logo"], width=100)
                st.button(
                    tipo,
                    key=f"btn_biblio_Normativa Impositiva_{tipo}",
                    on_click=ir_a,
                    args=("Normativa Impositiva", tipo),
                    width="stretch",
                )

    # Columna de Normativa Aduanera
    with col_adu, st.container(border=True):
        datos = NAVEGACION["Normativa Aduanera"]
        st.markdown(
            f"<p class='tarjeta-titulo'>{datos['icono']} Normativa Aduanera</p>"
            f"<p class='tarjeta-desc'>{datos['descripcion']}</p>",
            unsafe_allow_html=True,
        )
        botones = st.columns(2)
        for i, (tipo, item) in enumerate(datos["items"].items()):
            etiqueta = f"{item['icono']} {tipo}"
            botones[i % 2].button(
                etiqueta,
                key=f"btn_Normativa Aduanera_{tipo}",
                on_click=ir_a,
                args=("Normativa Aduanera", tipo),
                width="stretch",
            )

    # Tarjeta de Cotizaciones
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
