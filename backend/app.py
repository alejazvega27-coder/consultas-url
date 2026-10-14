def filtrar_df(df, texto: str):
    tokens = normalizar(texto).split()
    if df.empty or not tokens:
        return df
    columnas_texto = [c for c in df.columns if not str(c).startswith("Enlace")]
    if not columnas_texto:
        return df
    filas = df[columnas_texto].astype(str).apply(lambda col: col.map(normalizar)).apply(" ".join, axis=1)
    mascara = filas.apply(lambda fila: all(t in fila for t in tokens))
    return df[mascara].reset_index(drop=True)