const input = document.getElementById("input-busqueda");
const boton = document.getElementById("boton-buscar");
const estado = document.getElementById("estado");
const encabezado = document.getElementById("encabezado");
const cuerpo = document.getElementById("cuerpo");

async function buscar() {
  const termino = input.value.trim();
  if (!termino) return;

  estado.textContent = "Buscando...";
  encabezado.innerHTML = "";
  cuerpo.innerHTML = "";
  boton.disabled = true;

  try {
    const resp = await fetch(`/api/buscar?q=${encodeURIComponent(termino)}`);
    const data = await resp.json();

    if (!resp.ok) {
      estado.textContent = `Error: ${data.error}`;
      return;
    }

    if (data.filas.length === 0) {
      estado.textContent = `"${data.fuente.nombre}" no devolvio datos.`;
      return;
    }

    estado.textContent =
      `Fuente: ${data.fuente.nombre} (${data.filas.length} filas) - ${data.fuente.url}`;

    data.columnas.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      encabezado.appendChild(th);
    });

    data.filas.forEach((fila) => {
      const tr = document.createElement("tr");
      fila.forEach((valor) => {
        const td = document.createElement("td");
        td.textContent = valor === null || valor === undefined ? "" : valor;
        tr.appendChild(td);
      });
      cuerpo.appendChild(tr);
    });
  } catch (err) {
    estado.textContent = "Error de conexion con el backend.";
  } finally {
    boton.disabled = false;
  }
}

boton.addEventListener("click", buscar);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") buscar();
});
