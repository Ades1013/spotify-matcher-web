import os
import re
import csv
import json
import uuid
import shutil
import tempfile
import threading
import subprocess
from flask import Flask, request, jsonify, send_file, render_template_string
import yt_dlp

app = Flask(__name__)

# Configuración global
RUTA_COOKIES_MEMORIA = None
CARPETA_DESCARGAS_TEMP = os.path.join(tempfile.gettempdir(), "spotify_downloads_temp")
os.makedirs(CARPETA_DESCARGAS_TEMP, exist_ok=True)

# Cache y control de versiones
CACHE_VERSIONES = {}
ESTADO_DESCARGAS = {}


def preparar_cookies_yt():
    """Detecta y prepara las cookies activas de YouTube en local o en Render."""
    global RUTA_COOKIES_MEMORIA
    if RUTA_COOKIES_MEMORIA and os.path.exists(RUTA_COOKIES_MEMORIA):
        return RUTA_COOKIES_MEMORIA

    rutas_posibles = [
        "/etc/secrets/cookies_incognito_mayo26.txt",
        "/etc/secrets/cookies.txt",
        "/app/cookies_incognito_mayo26.txt",
        "/app/cookies.txt",
        "cookies_incognito_mayo26.txt",
        "cookies.txt",
    ]
    ruta_origen = next((r for r in rutas_posibles if os.path.exists(r)), None)
    if not ruta_origen:
        return None

    try:
        ruta_temp = os.path.join(tempfile.gettempdir(), "yt_cookies_active.txt")
        shutil.copyfile(ruta_origen, ruta_temp)
        RUTA_COOKIES_MEMORIA = ruta_temp
        return RUTA_COOKIES_MEMORIA
    except OSError:
        return ruta_origen


def limpiar_nombre_archivo(nombre):
    """Elimina caracteres incompatibles con nombres de archivo en Windows y Unix."""
    nombre_limpio = re.sub(r'[\\/*?:"<>|]', "", nombre)
    return nombre_limpio.strip()


def convertir_a_m4a_solo_audio(ruta_origen, ruta_destino):
    """Extrae o recodifica directamente a formato AAC/M4A limpio con FFmpeg."""
    cmd = ["ffmpeg", "-y", "-i", ruta_origen, "-vn", "-c:a", "copy", ruta_destino]
    resultado = subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if (
        resultado.returncode == 0
        and os.path.exists(ruta_destino)
        and os.path.getsize(ruta_destino) > 0
    ):
        return True

    # Fallback con transcodificación AAC
    cmd_transcode = [
        "ffmpeg",
        "-y",
        "-i",
        ruta_origen,
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        ruta_destino,
    ]
    res_transcode = subprocess.run(
        cmd_transcode, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return (
        res_transcode.returncode == 0
        and os.path.exists(ruta_destino)
        and os.path.getsize(ruta_destino) > 0
    )


def buscar_coincidencia_yt(query):
    """Busca en YouTube utilizando cliente web directo con soporte de cookies y Deno."""
    archivo_cookies = preparar_cookies_yt()
    cache_ytdlp = os.path.join(tempfile.gettempdir(), "ytdlp_cache")

    ydl_opts_search = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "cachedir": cache_ytdlp,
        "js_runtimes": {"deno": {}},
        "extractor_args": {"youtube": {"player_client": ["web", "web_embedded"]}},
        "noplaylist": True,
        "retries": 1,
    }
    if archivo_cookies:
        ydl_opts_search["cookiefile"] = archivo_cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
            resultado = ydl.extract_info(f"ytsearch5:{query}", download=False)
            if not resultado or "entries" not in resultado or not resultado["entries"]:
                return None, []

            entradas = [e for e in resultado["entries"] if e]
            if not entradas:
                return None, []

            mejor = entradas[0]
            url_mejor = f"https://www.youtube.com/watch?v={mejor.get('id')}"
            titulo_mejor = mejor.get("title", query)

            versiones = []
            for entrada in entradas:
                duracion_seg = entrada.get("duration")
                if duracion_seg:
                    minutos = int(duracion_seg // 60)
                    segundos = int(duracion_seg % 60)
                    duracion_txt = f"{minutos}:{segundos:02d}"
                else:
                    duracion_txt = "Duración N/D"

                versiones.append(
                    {
                        "id": entrada.get("id"),
                        "titulo": entrada.get("title", "Sin título"),
                        "url": f"https://www.youtube.com/watch?v={entrada.get('id')}",
                        "duracion": duracion_txt,
                    }
                )

            return {"url": url_mejor, "titulo": titulo_mejor}, versiones
    except Exception as e:
        print(f"Error en búsqueda YouTube: {e}")
        return None, []


def ejecutar_descarga_yt(url_video, carpeta_destino):
    """Descarga de YouTube en streaming directo al disco con yt-dlp."""
    archivo_cookies = preparar_cookies_yt()
    cache_ytdlp = os.path.join(tempfile.gettempdir(), "ytdlp_cache")

    for f in os.listdir(carpeta_destino):
        try:
            os.remove(os.path.join(carpeta_destino, f))
        except OSError:
            pass

    ydl_opts_download = {
        "quiet": True,
        "no_warnings": True,
        "nopart": True,
        "windowsfilenames": True,
        "nocheckcertificate": True,
        "cachedir": cache_ytdlp,
        "concurrent_fragment_downloads": 1,
        "js_runtimes": {"deno": {}},
        "extractor_args": {"youtube": {"player_client": ["web", "web_embedded"]}},
        "format": "140/ba[ext=m4a]/ba/b",
        "outtmpl": os.path.join(carpeta_destino, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "retries": 1,
        "fragment_retries": 1,
    }
    if archivo_cookies:
        ydl_opts_download["cookiefile"] = archivo_cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl_down:
            ydl_down.download([url_video])

        archivos = os.listdir(carpeta_destino)
        if not archivos:
            return None

        ruta_bruta = os.path.join(carpeta_destino, archivos[0])
        nombre_base = os.path.splitext(archivos[0])[0]
        ruta_m4a_limpia = os.path.join(carpeta_destino, f"{nombre_base}_clean.m4a")
        ruta_m4a_final = os.path.join(carpeta_destino, f"{nombre_base}.m4a")

        if convertir_a_m4a_solo_audio(ruta_bruta, ruta_m4a_limpia):
            try:
                os.remove(ruta_bruta)
            except OSError:
                pass
            os.replace(ruta_m4a_limpia, ruta_m4a_final)
            return ruta_m4a_final

        return None
    except Exception as e:
        print(f"Error en descarga: {e}")
        return None


# Plantilla HTML con persistencia de permisos en navegador
PLANTILLA_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spotify Matcher - Web Edition</title>
    <style>
        :root {
            --bg-color: #121212;
            --surface-color: #1e1e1e;
            --card-color: #282828;
            --primary-color: #1db954;
            --primary-hover: #1ed760;
            --text-main: #ffffff;
            --text-sub: #b3b3b3;
            --border-color: #333333;
            --accent-orange: #ff9800;
            --accent-blue: #2196f3;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 25px 15px; }
        .contenedor { width: 100%; max-width: 780px; }
        .cabecera { text-align: center; margin-bottom: 25px; }
        .cabecera h1 { font-size: 2rem; font-weight: 700; display: inline-flex; align-items: center; gap: 10px; }
        .badge { background: #198754; color: #fff; font-size: 0.85rem; padding: 4px 10px; border-radius: 4px; font-weight: 600; margin-left: 8px; vertical-align: middle; }
        .cabecera p { color: var(--text-sub); margin-top: 6px; font-size: 0.95rem; }
        .panel { background-color: var(--surface-color); border: 1px solid var(--border-color); border-radius: 10px; padding: 22px; margin-bottom: 22px; }
        .seccion-label { font-size: 0.95rem; font-weight: 600; color: var(--text-main); margin-bottom: 8px; display: block; }
        .input-archivo { width: 100%; padding: 10px; background-color: var(--card-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-sub); font-size: 0.9rem; cursor: pointer; }
        .separador { text-align: center; margin: 18px 0; color: var(--text-sub); font-size: 0.8rem; letter-spacing: 1px; font-weight: bold; }
        textarea { width: 100%; height: 95px; background-color: var(--card-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-main); padding: 12px; font-size: 0.9rem; resize: vertical; outline: none; }
        textarea:focus { border-color: var(--primary-color); }
        .acciones-form { display: flex; flex-direction: column; gap: 10px; margin-top: 20px; }
        .btn-principal { width: 100%; padding: 14px; background-color: #2e7d32; color: #fff; font-size: 1.05rem; font-weight: 700; border: none; border-radius: 8px; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 8px; transition: background 0.2s ease; }
        .btn-principal:hover { background-color: #388e3c; }
        .btn-limpiar { width: 100%; padding: 11px; background-color: #ffffff; color: #000000; font-size: 1rem; font-weight: 700; border: none; border-radius: 8px; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 6px; transition: opacity 0.2s ease; }
        .btn-limpiar:hover { opacity: 0.9; }
        .contenedor-progreso { display: flex; align-items: center; gap: 15px; margin: 20px 0 10px 0; }
        .progreso-barra-bg { flex-grow: 1; height: 18px; background-color: #333333; border-radius: 10px; overflow: hidden; }
        .progreso-barra-fill { height: 100%; width: 0%; background: repeating-linear-gradient(45deg, #1db954, #1db954 10px, #179644 10px, #179644 20px); transition: width 0.3s ease; }
        .progreso-contador { font-size: 1.15rem; font-weight: 700; color: #ffffff; min-width: 45px; text-align: right; }
        .resumen-final { display: none; background-color: #0d2818; border: 1px solid #198754; color: #75b798; font-weight: 700; text-align: center; padding: 14px; border-radius: 6px; margin: 20px 0 10px 0; font-size: 1.05rem; }
        .lista-resultados { display: flex; flex-direction: column; }
        .fila-cancion { display: flex; justify-content: space-between; align-items: center; padding: 12px 10px; border-bottom: 1px solid var(--border-color); font-size: 0.95rem; }
        .fila-cancion:last-child { border-bottom: none; }
        .info-cancion { flex: 1; margin-right: 15px; word-break: break-word; color: #ffffff; }
        .acciones-cancion { min-width: 140px; text-align: right; }
        .btn-version { background-color: #0d6efd; color: #ffffff; border: 1px solid #0d6efd; padding: 7px 12px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
        .btn-version:hover { background-color: #0b5ed7; }
        .selector-version { background-color: #212529; color: #ffffff; border: 1px solid #495057; padding: 6px 10px; border-radius: 6px; font-size: 0.85rem; max-width: 250px; outline: none; }
        .cargando { color: var(--accent-orange); font-size: 0.95rem; font-weight: 600; }
        .error { color: #dc3545; font-size: 0.95rem; font-weight: 600; }
    </style>
</head>
<body>
    <div class="contenedor">
        <header class="cabecera">
            <h1>🎵 Spotify Matcher <span class="badge">Web Edition</span></h1>
            <p>Elige tu carpeta una sola vez y descarga tus canciones en .m4a</p>
        </header>

        <section class="panel">
            <form id="formularioDescarga">
                <label class="seccion-label" for="archivoEntrada">1. Sube tu archivo (.txt o .csv de Spotify)</label>
                <input type="file" id="archivoEntrada" name="archivo" accept=".txt,.csv" class="input-archivo">

                <div class="separador">— O TAMBIÉN PUEDES —</div>

                <label class="seccion-label" for="textoEntrada">2. Escribir las canciones directamente (una por línea o separadas por coma)</label>
                <textarea id="textoEntrada" name="texto" placeholder="Ej: Rammstein - Du Hast, The Haunting (Somewhere in Time)"></textarea>

                <div class="acciones-form">
                    <button type="submit" class="btn-principal" id="btnAccionPrincipal">
                        📁 Elegir Carpeta y Descargar
                    </button>
                    <button type="button" class="btn-limpiar" id="btnLimpiar">
                        🧹 Limpiar Todo
                    </button>
                </div>
            </form>
        </section>

        <div class="contenedor-progreso" id="bloqueProgreso" style="display: none;">
            <div class="progreso-barra-bg">
                <div class="progreso-barra-fill" id="barraProgreso"></div>
            </div>
            <div class="progreso-contador" id="textoProgreso">0/0</div>
        </div>

        <div class="resumen-final" id="mensajeFinal"></div>

        <section class="panel" id="panelResultados" style="display: none;">
            <div class="lista-resultados" id="contenedorResultados"></div>
        </section>
    </div>

    <script>
        const formulario = document.getElementById('formularioDescarga');
        const inputArchivo = document.getElementById('archivoEntrada');
        const textareaTexto = document.getElementById('textoEntrada');
        const btnLimpiar = document.getElementById('btnLimpiar');
        const bloqueProgreso = document.getElementById('bloqueProgreso');
        const barraProgreso = document.getElementById('barraProgreso');
        const textoProgreso = document.getElementById('textoProgreso');
        const mensajeFinal = document.getElementById('mensajeFinal');
        const panelResultados = document.getElementById('panelResultados');
        const contenedorResultados = document.getElementById('contenedorResultados');

        // Variable global en memoria para persistir la autorización de la carpeta durante la sesión
        let dirHandleGuardado = null;
        let contadorGuardadas = 0;
        let totalCanciones = 0;

        btnLimpiar.addEventListener('click', () => {
            inputArchivo.value = '';
            textareaTexto.value = '';
            bloqueProgreso.style.display = 'none';
            panelResultados.style.display = 'none';
            mensajeFinal.style.display = 'none';
            contenedorResultados.innerHTML = '';
            barraProgreso.style.width = '0%';
            textoProgreso.textContent = '0/0';
            contadorGuardadas = 0;
            totalCanciones = 0;
        });

        function limpiarNombreArchivo(nombre) {
            return nombre.replace(/[\\\\/:*?"<>|]/g, "_").trim();
        }

        async function obtenerOVerificarPermisoCarpeta() {
            if (dirHandleGuardado) {
                const opciones = { mode: 'readwrite' };
                try {
                    if ((await dirHandleGuardado.queryPermission(opciones)) === 'granted') {
                        return dirHandleGuardado;
                    }
                    if ((await dirHandleGuardado.requestPermission(opciones)) === 'granted') {
                        return dirHandleGuardado;
                    }
                } catch (e) {
                    // Fallback si el navegador restringe la consulta directa
                }
            }

            try {
                dirHandleGuardado = await window.showDirectoryPicker({ mode: 'readwrite' });
                return dirHandleGuardado;
            } catch (err) {
                return null;
            }
        }

        async function obtenerNombreUnicoEnCarpetaWeb(dirHandle, nombreBase) {
            let nombreFinal = nombreBase;
            let contador = 1;
            const nombreSinExt = nombreBase.replace(/\\.[^/.]+$/, "");
            const ext = nombreBase.split('.').pop();

            while (true) {
                try {
                    await dirHandle.getFileHandle(nombreFinal);
                    nombreFinal = `${nombreSinExt} (${contador}).${ext}`;
                    contador++;
                } catch (e) {
                    return nombreFinal;
                }
            }
        }

        async function guardarBlobEnDispositivo(blob, nombreArchivo) {
            nombreArchivo = limpiarNombreArchivo(nombreArchivo);

            if ('showDirectoryPicker' in window) {
                if (!dirHandleGuardado) {
                    dirHandleGuardado = await obtenerOVerificarPermisoCarpeta();
                }

                if (dirHandleGuardado) {
                    try {
                        const nombreUnico = await obtenerNombreUnicoEnCarpetaWeb(dirHandleGuardado, nombreArchivo);
                        const fileHandle = await dirHandleGuardado.getFileHandle(nombreUnico, { create: true });
                        const writable = await fileHandle.createWritable();
                        await writable.write(blob);
                        await writable.close();
                        return nombreUnico;
                    } catch (err) {
                        console.warn("No se pudo escribir en la carpeta autorizada, recurriendo a descarga tradicional:", err);
                    }
                }
            }

            // Descarga normal por Blob en navegadores sin soporte o si se cancela la carpeta
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = nombreArchivo;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            return nombreArchivo;
        }

        function actualizarResumenFinal() {
            if (mensajeFinal) {
                mensajeFinal.style.display = 'block';
                mensajeFinal.innerHTML = `★ ¡Listo! Se guardaron ${contadorGuardadas} de ${totalCanciones} canciones.`;
            }
        }

        formulario.addEventListener('submit', async (e) => {
            e.preventDefault();

            // Pedir autorización de la carpeta una única vez antes de empezar el lote
            if ('showDirectoryPicker' in window) {
                dirHandleGuardado = await obtenerOVerificarPermisoCarpeta();
                if (!dirHandleGuardado) {
                    alert("Debes autorizar una carpeta para guardar las canciones.");
                    return;
                }
            }

            const formData = new FormData(formulario);
            panelResultados.style.display = 'block';
            bloqueProgreso.style.display = 'flex';
            mensajeFinal.style.display = 'none';
            contenedorResultados.innerHTML = '<p class="cargando">Procesando lista y localizando canciones...</p>';
            barraProgreso.style.width = '0%';
            textoProgreso.textContent = '...';
            contadorGuardadas = 0;

            try {
                const res = await fetch('/procesar', {
                    method: 'POST',
                    body: formData
                });

                if (!res.ok) throw new Error('Error al procesar la lista en el servidor');

                const datos = await res.json();
                const canciones = datos.canciones || [];
                totalCanciones = canciones.length;

                if (totalCanciones === 0) {
                    contenedorResultados.innerHTML = '<p class="error">No se encontraron títulos válidos para procesar.</p>';
                    return;
                }

                contenedorResultados.innerHTML = '';

                for (let i = 0; i < canciones.length; i++) {
                    const item = canciones[i];
                    const fila = document.createElement('div');
                    fila.className = 'fila-cancion';
                    fila.innerHTML = `
                        <div class="info-cancion">
                            <span class="estado-fila">⏳ Descargando: "${item.titulo}"...</span>
                        </div>
                        <div class="acciones-cancion">
                            <button type="button" class="btn-version" onclick="solicitarVersiones('${encodeURIComponent(item.busqueda)}', this.closest('.fila-cancion'))">🔄 Otra versión</button>
                        </div>
                    `;
                    contenedorResultados.appendChild(fila);

                    try {
                        const resAudio = await fetch('/descargar_directo', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ url: item.url_video, titulo: item.titulo })
                        });

                        if (resAudio.ok) {
                            const blob = await resAudio.blob();
                            const nombreGuardado = await guardarBlobEnDispositivo(blob, `${item.titulo}.m4a`);
                            contadorGuardadas++;
                            fila.querySelector('.estado-fila').innerHTML = `✅ ${nombreGuardado}`;
                        } else {
                            fila.querySelector('.estado-fila').textContent = `❌ Falló: "${item.titulo}"`;
                        }
                    } catch (err) {
                        fila.querySelector('.estado-fila').textContent = `❌ Error de red: "${item.titulo}"`;
                    }

                    const porcentaje = Math.round(((i + 1) / totalCanciones) * 100);
                    barraProgreso.style.width = `${porcentaje}%`;
                    textoProgreso.textContent = `${i + 1}/${totalCanciones}`;
                }

                actualizarResumenFinal();

            } catch (error) {
                contenedorResultados.innerHTML = `<p class="error">Ocurrió un error: ${error.message}</p>`;
            }
        });

        async function solicitarVersiones(busquedaCodificada, elementoFila) {
            const query = decodeURIComponent(busquedaCodificada);
            const contenedorAcciones = elementoFila.querySelector('.acciones-cancion');
            contenedorAcciones.innerHTML = '<span>Buscando...</span>';

            try {
                const respuesta = await fetch('/obtener_versiones', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query })
                });

                const datos = await respuesta.json();
                const versiones = datos.versiones || [];

                if (versiones.length === 0) {
                    contenedorAcciones.innerHTML = '<span>Sin opciones</span>';
                    return;
                }

                let selectHtml = `<select class="selector-version" onchange="if(this.value){ totalCanciones++; descargarVersionAlternativa(this.value, this.options[this.selectedIndex].text, this.closest('.fila-cancion')); }">`;
                selectHtml += `<option value="">-- Elige versión --</option>`;
                versiones.forEach(v => {
                    selectHtml += `<option value="${v.url}">${v.titulo} (${v.duracion})</option>`;
                });
                selectHtml += `</select>`;

                contenedorAcciones.innerHTML = selectHtml;

            } catch (e) {
                contenedorAcciones.innerHTML = '<span>Error al cargar</span>';
            }
        }

        async function descargarVersionAlternativa(urlVideo, tituloSugerido, elementoFila) {
            if (!dirHandleGuardado && ('showDirectoryPicker' in window)) {
                dirHandleGuardado = await obtenerOVerificarPermisoCarpeta();
            }

            const filaNueva = document.createElement('div');
            filaNueva.className = 'fila-cancion';
            filaNueva.innerHTML = `
                <div class="info-cancion">
                    <span class="estado-fila">⏳ Descargando: "${tituloSugerido}"...</span>
                </div>
                <div class="acciones-cancion">
                    <span>En cola</span>
                </div>
            `;
            elementoFila.after(filaNueva);

            try {
                const respuesta = await fetch('/descargar_directo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: urlVideo, titulo: tituloSugerido })
                });

                if (!respuesta.ok) throw new Error('Error al descargar versión');

                const blob = await respuesta.blob();
                const nombreArchivoFinal = `${tituloSugerido}.m4a`;
                const nombreGuardado = await guardarBlobEnDispositivo(blob, nombreArchivoFinal);

                contadorGuardadas++;
                actualizarResumenFinal();

                filaNueva.querySelector('.estado-fila').innerHTML = `✅ ${nombreGuardado}`;
                filaNueva.querySelector('.acciones-cancion').innerHTML = `<span>Listo</span>`;
            } catch (error) {
                filaNueva.querySelector('.estado-fila').textContent = `❌ Falló la descarga alternativa`;
                filaNueva.querySelector('.acciones-cancion').innerHTML = `<span>Error</span>`;
            }
        }
    </script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PLANTILLA_HTML)


@app.route("/procesar", methods=["POST"])
def procesar():
    lineas = []

    if "archivo" in request.files and request.files["archivo"].filename:
        archivo = request.files["archivo"]
        contenido = archivo.read().decode("utf-8", errors="ignore")
        if archivo.filename.endswith(".csv"):
            lector = csv.reader(contenido.splitlines())
            for fila in lector:
                if fila:
                    lineas.append(" - ".join(fila[:2]))
        else:
            lineas.extend([l.strip() for l in contenido.splitlines() if l.strip()])

    texto = request.form.get("texto", "").strip()
    if texto:
        for elemento in re.split(r"[,\n]+", texto):
            if elemento.strip():
                lineas.append(elemento.strip())

    lineas_filtradas = []
    for l in lineas:
        if l and not l.startswith("#") and l not in lineas_filtradas:
            lineas_filtradas.append(l)

    resultados = []
    for busqueda in lineas_filtradas:
        mejor, versiones = buscar_coincidencia_yt(busqueda)
        if mejor:
            CACHE_VERSIONES[busqueda] = versiones
            resultados.append(
                {
                    "busqueda": busqueda,
                    "titulo": mejor["titulo"],
                    "url_video": mejor["url"],
                }
            )

    return jsonify({"canciones": resultados})


@app.route("/obtener_versiones", methods=["POST"])
def obtener_versiones():
    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if query in CACHE_VERSIONES:
        return jsonify({"versiones": CACHE_VERSIONES[query]})

    _, versiones = buscar_coincidencia_yt(query)
    CACHE_VERSIONES[query] = versiones
    return jsonify({"versiones": versiones})


@app.route("/descargar_directo", methods=["POST"])
def descargar_directo():
    data = request.get_json() or {}
    url_video = data.get("url")
    titulo_sugerido = data.get("titulo", "cancion")

    if not url_video:
        return jsonify({"error": "URL no proporcionada"}), 400

    id_descarga = str(uuid.uuid4())
    subcarpeta_temp = os.path.join(CARPETA_DESCARGAS_TEMP, id_descarga)
    os.makedirs(subcarpeta_temp, exist_ok=True)

    ruta_m4a = ejecutar_descarga_yt(url_video, subcarpeta_temp)
    if not ruta_m4a or not os.path.exists(ruta_m4a):
        shutil.rmtree(subcarpeta_temp, ignore_errors=True)
        return jsonify({"error": "Fallo al procesar el audio"}), 500

    nombre_descarga = f"{limpiar_nombre_archivo(titulo_sugerido)}.m4a"

    def remover_al_terminar():
        try:
            shutil.rmtree(subcarpeta_temp, ignore_errors=True)
        except Exception:
            pass

    threading.Timer(60.0, remover_al_terminar).start()

    return send_file(
        ruta_m4a,
        as_attachment=True,
        download_name=nombre_descarga,
        mimetype="audio/mp4",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
