"""
Aplicación principal de Streamlit para el buscador DNIT.
Ejecución: streamlit run app.py
"""

import streamlit as st
from scraper import ejecutar_busqueda

# 1. Configuración de la página en modo ancho 
# (Debe ser obligatoriamente la primera línea de Streamlit)
st.set_page_config(layout="wide", page_title="Buscador DNIT")

# Interfaz visual
st.title("Buscador General")
st.markdown("Sistema de consulta y filtrado de datos institucionales")

# Campo de búsqueda
termino = st.text_input("Buscar...", "decreto")

# Ejecutar búsqueda al presionar enter o cambiar el texto
if termino:
    with st.spinner("Procesando consulta y extrayendo datos..."):
        fuente, df_resultado = ejecutar_busqueda(termino)
    
    st.caption(f"Fuente: {fuente['nombre']} ({len(df_resultado)} filas) - {fuente['url']}")
    
    if not df_resultado.empty:
        # Validamos si es la fuente de decretos para aplicar el formato avanzado de columnas y enlaces
        if fuente["id"] == "dnit_ruc":
            st.dataframe(
                df_resultado,
                use_container_width=True,  # Ocupa todo el ancho de la pantalla
                column_config={
                    "Título": st.column_config.TextColumn("Título", width="medium"),
                    "Descripción": st.column_config.TextColumn(
                        "Descripción", 
                        width="large"  # Amplía el espacio visual para leer mejor los textos largos
                    ),
                    "Enlace Descargar": st.column_config.LinkColumn(
                        "Enlace Descargar",
                        display_text="📥 Descargar PDF"  # Transforma la URL en un botón interactivo y limpio
                    ),
                    "Enlace Ver": st.column_config.LinkColumn(
                        "Enlace Ver",
                        display_text="🌐 Ver Detalle"   # Transforma la URL en un botón interactivo y limpio
                    )
                }
            )
        else:
            # Para otras fuentes de datos (ej. Cotizaciones)
            st.dataframe(df_resultado, use_container_width=True)
    else:
        st.warning("No se encontraron resultados para la búsqueda ingresada.")