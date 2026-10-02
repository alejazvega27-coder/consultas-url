"""
Backend Flask.

Requisitos:
    pip install flask requests beautifulsoup4 pandas lxml

Uso:
    cd backend
    python app.py
    # abrir http://localhost:5000
"""

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

from backend.scraper import FUENTES, ejecutar_busqueda

app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "index.html"
    )  # Esto buscará automáticamente en backend/templates/


@app.route("/api/fuentes")
def listar_fuentes():
    """Devuelve las fuentes disponibles, util para mostrar sugerencias en el frontend."""
    return jsonify([
        {"id": f["id"], "nombre": f["nombre"], "alias": f["alias"]}
        for f in FUENTES
    ])


@app.route("/api/buscar")
def buscar():
    termino = request.args.get("q", "").strip()
    if not termino:
        return jsonify({"error": "Falta el parametro 'q'"}), 400

    try:
        fuente, df = ejecutar_busqueda(termino)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"No se pudo descargar la pagina: {exc}"}), 502
    except Exception as exc:  # noqa: BLE001 - devolvemos cualquier error al frontend
        return jsonify({"error": f"Error inesperado: {exc}"}), 500

    info_fuente = {"id": fuente["id"], "nombre": fuente["nombre"], "url": fuente["url"]}

    if df.empty:
        return jsonify({"fuente": info_fuente, "columnas": [], "filas": []})

    df_limpio = df.where(pd.notna(df), None)
    return jsonify({
        "fuente": info_fuente,
        "columnas": df_limpio.columns.tolist(),
        "filas": df_limpio.values.tolist(),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)