# Clasificación del éxito comercial de películas

> Proyecto integrador de Machine Learning. Compara modelos de clasificación para estudiar el desempeño comercial de películas de TMDb.

## Objetivo

La etiqueta operativa es `éxito = 1` cuando la recaudación reportada es al menos dos veces el presupuesto; en caso contrario, `éxito = 0`. Este umbral sirve para el ejercicio, pero no mide la ganancia neta: no incluye marketing, distribución ni la parte de taquilla que recibe el estudio.

El notebook principal compara SVM, Árbol de Decisión, Random Forest y Gradient Boosting. Usa variables de producción disponibles en los CSV históricos, valida los modelos y guarda métricas y gráficos. El resultado describe la colección analizada; no demuestra que el modelo prediga películas futuras.

## Archivos principales

- `Proyecto_Integrador_Peliculas.ipynb`: análisis y entrenamiento principal.
- `requirements.txt`: dependencias para ejecutar el notebook.
- `build_movies_dataset.py` y `generar_dataset_tmdb.cmd`: generador opcional de una colección más reciente desde la API de TMDb.
- `output/pdf/Informe_Proyecto_Peliculas.pdf`: informe del proyecto.
- `output/presentacion/Presentacion_Proyecto_Peliculas.pptx`: diapositivas.
- `experimentos/`: pruebas exploratorias separadas del análisis principal.

## Datos necesarios para el notebook principal

Descargá el conjunto **TMDB 5000 Movie Dataset** desde [Kaggle](https://www.kaggle.com/datasets/tmdb/tmdb-movie-metadata) y extraé estos dos archivos en una carpeta local:

```text
Proyecto-Integrador/
└── dataset/
    ├── tmdb_5000_movies.csv
    └── tmdb_5000_credits.csv
```

Esa carpeta está ignorada por Git porque los CSV son datos descargados y no deben subirse al repositorio. El notebook busca los archivos ahí cuando se ejecuta desde la raíz del proyecto. No se debe subir una clave de TMDb ni incluirla en el notebook.

## Ejecución local en Windows

Abrí PowerShell en la carpeta del proyecto y ejecutá:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jupyter lab Proyecto_Integrador_Peliculas.ipynb
```

En Jupyter, ejecutá las celdas en orden con **Run All**. El notebook guarda tablas, métricas y gráficos en `resultados/`, que se vuelve a crear al ejecutar y no se versiona.

Si `py -3` no está disponible, usá `python` para crear el entorno. También podés abrir el notebook con VS Code si tenés instalada su extensión de Jupyter y seleccionás el intérprete de `.venv`.

## Ejecución en Google Colab

Subí el notebook y los dos CSV a Colab. Colocá ambos CSV en `/content` y ejecutá todas las celdas en orden. El notebook también busca esos archivos en esa ubicación.

## Generador opcional de datos 2010–2026

El generador es independiente del notebook TMDb 5000. En Windows, ejecutá:

```powershell
.\generar_dataset_tmdb.cmd
```

El programa solicita la credencial de TMDb de forma interactiva y la descarga no la guarda en el proyecto. La carpeta `datos_actualizados/` contiene el CSV nuevo y la caché reanudable; todo ese directorio está excluido de Git. Si la descarga se interrumpe, se puede volver a ejecutar el mismo archivo para continuar.

La descarga requiere conexión a la API y puede demorar. Las cifras y etiquetas dependen de los datos reportados por TMDb; revisá el resumen generado y la cantidad de casos completos antes de entrenar. Las películas de 2026 aún pueden tener resultados incompletos. El notebook principal no consume automáticamente ese CSV.

## Qué se sube al repositorio

Subí el notebook, los scripts, `requirements.txt`, este README y las entregas finales del informe y las diapositivas. No subas CSV de datos, cachés, credenciales, entornos virtuales ni resultados intermedios regenerables. El `.gitignore` de esta carpeta aplica a este repositorio.

## Fuente y atribución

Los CSV TMDb 5000 se obtienen del [conjunto publicado en Kaggle](https://www.kaggle.com/datasets/tmdb/tmdb-movie-metadata). Atribuí a TMDb el uso de sus datos según sus condiciones y conserva el enlace de la fuente en la entrega.
