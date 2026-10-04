import os
import gc
import tempfile
import shutil
import subprocess
import threading
import re
import csv
import io
import unicodedata
from urllib.parse import quote
from flask import Flask, render_template_string, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)

CARPETA_PC_ACTUAL = None
CACHE_OPCIONES = {}
RUTA_COOKIES_MEMORIA = None
LOCK_DESCARGA = threading.Lock()

HTML_INTERFAZ = """
<!DOCTYPE html>
<html lang="es" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spotify Local Matcher Web</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #121212; color: #ffffff; }
        .card { background-color: #1e1e1e; border: 1px solid #333; border-radius: 12px; }
        .btn-verde { background-color: #198754; color: #ffffff; font-weight: bold; border: none; }
        .btn-verde:hover { background-color: #157347; color: #ffffff; }
        .btn-verde:disabled { background-color: #115c39; color: #888888; }
        .btn-blanco { background-color: #ffffff; color: #121212; font-weight: bold; border: none; }
        .btn-blanco:hover { background-color: #e0e0e0; color: #000000; }
        .form-control { background-color: #2c2c2c; color: #ffffff; border: 1px solid #555; }
        .form-control:focus { background-color: #333; color: #ffffff; border-color: #198754; box-shadow: none; }
        .form-label { color: #e0e0e0; font-weight: 600; }
        #panelProgreso, #exito { display: none; }
        #barraProgreso { transition: width 0.25s ease; }
        .btn-otra-version { transition: all 0.2s ease; }
    </style>
</head>
<body>
    <div class="container mt-5 px-3 mb-5">
        <div class="row justify-content-center">
            <div class="col-md-7">
                <div class="text-center mb-4">
                    <h2 class="fw-bold">🎵 Spotify Matcher <span class="badge bg-success">Web Edition</span></h2>
                    <p class="text-light opacity-75">Elige tu carpeta una sola vez y descarga tus canciones en .m4a</p>
                </div>
                
                <div class="card p-4 shadow-lg">
                    <form id="formDescarga">
                        <div class="mb-3">
                            <label for="archivo_lista" class="form-label">1. Sube tu archivo (.txt o .csv de Spotify)</label>
                            <input class="form-control" type="file" id="archivo_lista" name="archivo_lista" accept=".txt,.csv">
                        </div>

                        <div class="text-center my-2 text-secondary fw-bold">— O TAMBIÉN PUEDES —</div>

                        <div class="mb-4">
                            <label for="texto_canciones" class="form-label">2. Escribir las canciones directamente (una por línea o separadas por coma)</label>
                            <textarea class="form-control" id="texto_canciones" name="texto_canciones" rows="3" spellcheck="true" lang="es" placeholder="Ej: Rammstein - Du Hast, The Haunting (Somewhere in Time)"></textarea>
                        </div>
                        
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn btn-verde btn-lg" id="btnDescargar">
                                📁 Elegir Carpeta y Descargar
                            </button>
                            <button type="button" class="btn btn-blanco" id="btnLimpiar" onclick="limpiarFormulario()">
                                🧹 Limpiar Todo
                            </button>
                        </div>
                    </form>

                    <div id="panelProgreso" class="mt-4">
                        <div class="d-flex justify-content-between mb-1">
                            <span id="textoEstado" class="text-warning fw-semibold">Preparando descarga...</span>
                            <span id="contadorProgreso" class="text-light fw-bold">0/0</span>
                        </div>
                        <div class="progress" style="height: 20px;">
                            <div id="barraProgreso" class="progress-bar progress-bar-striped progress-bar-animated bg-success" role="progressbar" style="width: 0%;"></div>
                        </div>
                        <ul id="listaResultados" class="list-group list-group-flush mt-3 small"></ul>
                    </div>

                    <div id="exito" class="alert alert-success text-center mt-4 fw-bold" role="alert"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal para 5 opciones de versión -->
    <div class="modal fade" id="modalVersiones" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content bg-dark text-light border-secondary">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title fw-bold">🔄 Escoger otra versión</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <p id="subtituloModal" class="text-secondary small mb-2"></p>
                    <div id="contenedorVersiones" class="list-group"></div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const modalVersiones = new bootstrap.Modal(document.getElementById('modalVersiones'));
        const esClientePC = (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost');
        
        let dirHandleGuardado = null;
        let timerBarra = null;
        let urlsDescargadasPorCancion = {};
        let listaCancionesGlobal = [];
        
        let colaDescargas = [];
        let procesandoDescarga = false;
        let tareaActivaActual = null;

        let totalEncoladasVersiones = 0;
        let procesadasVersiones = 0;
        let modoColaVersiones = false;
        let idItemContador = 1000;

        function escaparHtml(texto) {
            if (!texto) return '';
            return texto.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }

        function actualizarProgresoVisual(posicionActual, totalElementos) {
            const contadorProgreso = document.getElementById('contadorProgreso');
            contadorProgreso.textContent = `${posicionActual}/${totalElementos}`;
        }

        function actualizarResumenContadoresFinal() {
            const contadorProgreso = document.getElementById('contadorProgreso');
            const exito = document.getElementById('exito');

            const itemsTotales = document.querySelectorAll('#listaResultados li').length;
            const itemsExitosos = document.querySelectorAll('#listaResultados li.text-success').length;

            if (itemsTotales > 0) {
                contadorProgreso.textContent = `${itemsExitosos}/${itemsTotales}`;

                exito.style.display = 'block';
                if (itemsExitosos === itemsTotales) {
                    exito.className = "alert alert-success text-center mt-4 fw-bold";
                    exito.textContent = `★ ¡Listo! Se guardaron ${itemsExitosos} de ${itemsTotales} canciones.`;
                } else if (itemsExitosos > 0) {
                    exito.className = "alert alert-warning text-center mt-4 fw-bold";
                    exito.textContent = `⚠️ Se guardaron ${itemsExitosos} de ${itemsTotales} canciones activas.`;
                } else {
                    exito.className = "alert alert-danger text-center mt-4 fw-bold";
                    exito.textContent = `❌ No se pudo guardar ninguna canción (0 de ${itemsTotales}).`;
                }
            }
        }

        function registrarUrlDescargada(cancion, url) {
            if (!url) return;
            if (!urlsDescargadasPorCancion[cancion]) {
                urlsDescargadasPorCancion[cancion] = [];
            }
            if (!urlsDescargadasPorCancion[cancion].includes(url)) {
                urlsDescargadasPorCancion[cancion].push(url);
            }
        }

        function iniciarAvanceBarra(desdePct, hastaPct) {
            const barra = document.getElementById('barraProgreso');
            if (timerBarra) clearInterval(timerBarra);
            barra.classList.add('progress-bar-animated');
            let actual = Math.max(desdePct, 12);
            barra.style.width = `${actual}%`;

            timerBarra = setInterval(() => {
                if (actual < hastaPct) {
                    const paso = Math.max(1, Math.round((hastaPct - actual) * 0.18));
                    actual = Math.min(hastaPct, actual + paso);
                    barra.style.width = `${actual}%`;
                }
            }, 250);
        }

        function detenerAvanceBarra(pctFinal, quitarAnimacion = true) {
            const barra = document.getElementById('barraProgreso');
            if (timerBarra) {
                clearInterval(timerBarra);
                timerBarra = null;
            }
            barra.style.width = `${pctFinal}%`;
            if (quitarAnimacion) {
                barra.classList.remove('progress-bar-animated');
            }
        }

        function limpiarFormulario() {
            if (procesandoDescarga || colaDescargas.length > 0) {
                if (!confirm("Hay canciones en proceso o en cola. ¿Deseas limpiar todo?")) return;
            }
            detenerAvanceBarra(0, true);
            urlsDescargadasPorCancion = {};
            listaCancionesGlobal = [];
            colaDescargas = [];
            procesandoDescarga = false;
            tareaActivaActual = null;
            modoColaVersiones = false;
            totalEncoladasVersiones = 0;
            procesadasVersiones = 0;
            document.getElementById('formDescarga').reset();
            document.getElementById('panelProgreso').style.display = 'none';
            document.getElementById('exito').style.display = 'none';
            document.getElementById('listaResultados').innerHTML = '';
            document.getElementById('btnDescargar').disabled = false;
        }

        function limpiarNombreArchivo(nombre) {
            return nombre.replace(/[<>:"/\\\\|?*]+/g, ' ').replace(/\\s+/g, ' ').trim();
        }

        async function obtenerOVerificarPermisoCarpeta() {
            if (dirHandleGuardado) {
                const opciones = { mode: 'readwrite' };
                if ((await dirHandleGuardado.queryPermission(opciones)) === 'granted') {
                    return dirHandleGuardado;
                }
                if ((await dirHandleGuardado.requestPermission(opciones)) === 'granted') {
                    return dirHandleGuardado;
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
            const punto = nombreBase.lastIndexOf('.');
            const base = punto !== -1 ? nombreBase.substring(0, punto) : nombreBase;
            const ext = punto !== -1 ? nombreBase.substring(punto) : '.m4a';
            let contador = 2;

            while (true) {
                try {
                    await dirHandle.getFileHandle(nombreFinal, { create: false });
                    nombreFinal = `${base} (${contador})${ext}`;
                    contador++;
                } catch (err) {
                    return nombreFinal;
                }
            }
        }

        async function guardarBlobEnDispositivo(blob, nombreArchivo) {
            nombreArchivo = limpiarNombreArchivo(nombreArchivo);

            if (!esClientePC && ('showDirectoryPicker' in window)) {
                if (!dirHandleGuardado) {
                    dirHandleGuardado = await obtenerOVerificarPermisoCarpeta();
                }

                if (dirHandleGuardado) {
                    const nombreUnico = await obtenerNombreUnicoEnCarpetaWeb(dirHandleGuardado, nombreArchivo);
                    const fileHandle = await dirHandleGuardado.getFileHandle(nombreUnico, { create: true });
                    const writable = await fileHandle.createWritable();
                    await writable.write(blob);
                    await writable.close();
                    return nombreUnico;
                }
            }

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

        async function abrirSelectorVersiones(cancionTexto) {
            const contenedor = document.getElementById('contenedorVersiones');
            document.getElementById('subtituloModal').textContent = `Opciones alternativas para: "${cancionTexto}"`;
            contenedor.innerHTML = '<div class="text-center py-3"><div class="spinner-border text-success"></div><p class="mt-2 mb-0">Buscando opciones...</p></div>';
            modalVersiones.show();

            try {
                const excluidas = urlsDescargadasPorCancion[cancionTexto] || [];
                const resp = await fetch('/buscar_versiones', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cancion: cancionTexto, excluidas: excluidas })
                });
                const datos = await resp.json();

                if (!resp.ok || !datos.versiones || datos.versiones.length === 0) {
                    contenedor.innerHTML = '<p class="text-warning mb-0 p-3">No hay más versiones disponibles para esta canción.</p>';
                    return;
                }

                contenedor.innerHTML = '';
                datos.versiones.forEach(v => {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'list-group-item list-group-item-action bg-dark text-light border-secondary d-flex justify-content-between align-items-center';
                    btn.innerHTML = `
                        <div class="me-2 text-start">
                            <span class="badge bg-success me-1">Opción ${v.version}</span>
                            <strong>${v.titulo}</strong><br>
                            <small class="text-secondary">📺 ${v.canal} | ⏱️ ${v.duracion} | 👁 ${v.vistas}</small>
                        </div>
                        <span class="btn btn-sm btn-blanco">Descargar</span>
                    `;
                    btn.onclick = () => encolarVersionEspecifica(cancionTexto, v.url, v.titulo);
                    contenedor.appendChild(btn);
                });
            } catch (e) {
                contenedor.innerHTML = '<p class="text-danger p-3 mb-0">Error al consultar versiones.</p>';
            }
        }

        function encolarVersionEspecifica(cancion, urlVideo, tituloVideo) {
            modalVersiones.hide();

            registrarUrlDescargada(cancion, urlVideo);
            document.getElementById('exito').style.display = 'none';

            modoColaVersiones = true;
            totalEncoladasVersiones++;
            
            idItemContador++;
            const nuevoId = idItemContador;
            const listaResultados = document.getElementById('listaResultados');

            listaResultados.insertAdjacentHTML('beforeend', `
                <li class="list-group-item bg-transparent text-secondary border-secondary d-flex justify-content-between align-items-center" id="fila-${nuevoId}">
                    <span id="item-texto-${nuevoId}" class="me-2 text-warning">⏳ En cola: ${escaparHtml(tituloVideo || cancion)}</span>
                </li>
            `);

            colaDescargas.push({
                cancion: cancion,
                urlVideo: urlVideo,
                itemId: nuevoId,
                nombreDisplay: tituloVideo || cancion,
                esExtra: true
            });

            actualizarProgresoVisual(procesadasVersiones + 1, totalEncoladasVersiones);
            procesarSiguienteEnCola();
        }

        async function procesarSiguienteEnCola() {
            if (procesandoDescarga) return;

            if (colaDescargas.length === 0) {
                detenerAvanceBarra(100, true);
                actualizarResumenContadoresFinal();

                modoColaVersiones = false;
                totalEncoladasVersiones = 0;
                procesadasVersiones = 0;

                document.getElementById('textoEstado').textContent = "¡Proceso completado!";
                document.getElementById('btnDescargar').disabled = false;
                return;
            }

            procesandoDescarga = true;
            document.getElementById('btnDescargar').disabled = true;

            const tarea = colaDescargas.shift();
            tareaActivaActual = tarea;
            const { cancion, urlVideo, itemId, nombreDisplay, esExtra, ordenCancion } = tarea;
            const spanTexto = document.getElementById(`item-texto-${itemId}`);
            const textoEstado = document.getElementById('textoEstado');

            if (esExtra) {
                const numeroActual = procesadasVersiones + 1;
                actualizarProgresoVisual(numeroActual, totalEncoladasVersiones);
                textoEstado.textContent = `🔍 ${nombreDisplay}...`;
                if (spanTexto) spanTexto.innerHTML = `<span class="text-warning">⏳ Descargando: "${escaparHtml(nombreDisplay)}"...</span>`;
            } else {
                actualizarProgresoVisual(ordenCancion, listaCancionesGlobal.length);
                textoEstado.textContent = `🔍 ${cancion}...`;
                if (spanTexto) spanTexto.innerHTML = `<span class="text-warning">⏳ Descargando: "${escaparHtml(cancion)}"...</span>`;
            }

            iniciarAvanceBarra(20, 90);

            try {
                const respAudio = await fetch('/descargar_una', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cancion: cancion,
                        url: urlVideo || "",
                        guardar_en_pc: esClientePC
                    })
                });

                if (respAudio.ok) {
                    let guardadoComo = "";
                    const urlUsada = respAudio.headers.get('X-Youtube-Url') || urlVideo;
                    if (urlUsada) registrarUrlDescargada(cancion, urlUsada);

                    if (esClientePC) {
                        const datosPC = await respAudio.json();
                        guardadoComo = datosPC.guardado_como;
                        if (datosPC.url_usada) registrarUrlDescargada(cancion, datosPC.url_usada);
                    } else {
                        const blob = await respAudio.blob();
                        const nombreCabecera = respAudio.headers.get('X-Filename');
                        const nombreArchivo = nombreCabecera ? decodeURIComponent(nombreCabecera) : `${cancion}.m4a`;
                        guardadoComo = await guardarBlobEnDispositivo(blob, nombreArchivo);
                    }

                    if (esExtra) {
                        procesadasVersiones++;
                    }

                    if (spanTexto) {
                        const liPadre = spanTexto.closest('li');
                        if (liPadre) {
                            liPadre.className = "list-group-item bg-transparent text-success border-secondary d-flex justify-content-between align-items-center";
                        }
                        spanTexto.innerHTML = `✅ ${guardadoComo}`;
                    }
                } else {
                    if (spanTexto) {
                        const liPadre = spanTexto.closest('li');
                        if (liPadre) {
                            liPadre.className = "list-group-item bg-transparent text-danger border-secondary d-flex justify-content-between align-items-center";
                        }
                        spanTexto.innerHTML = `<span class="text-danger">❌ Falló descarga: ${escaparHtml(cancion)}</span>`;
                    }
                }
            } catch (err) {
                if (spanTexto) {
                    const liPadre = spanTexto.closest('li');
                    if (liPadre) {
                        liPadre.className = "list-group-item bg-transparent text-danger border-secondary d-flex justify-content-between align-items-center";
                    }
                    spanTexto.innerHTML = `<span class="text-danger">❌ Error de conexión: ${escaparHtml(cancion)}</span>`;
                }
            } finally {
                procesandoDescarga = false;
                tareaActivaActual = null;
                procesarSiguienteEnCola();
            }
        }

        document.getElementById('formDescarga').addEventListener('submit', async function(e) {
            e.preventDefault();
            if (procesandoDescarga || colaDescargas.length > 0) {
                if (!confirm("Hay descargas en progreso o en cola. ¿Deseas reiniciar la lista?")) return;
            }

            const archivo = document.getElementById('archivo_lista').files.length;
            const texto = document.getElementById('texto_canciones').value.trim();
            if (!archivo && !texto) {
                alert("Por favor sube un archivo (.txt / .csv) o ingresa canciones en el campo de texto.");
                return;
            }

            if (esClientePC) {
                const respCarpeta = await fetch('/elegir_carpeta_pc', { method: 'POST' });
                const datosCarpeta = await respCarpeta.json();
                if (datosCarpeta.cancelado) return;
            } else if ('showDirectoryPicker' in window) {
                dirHandleGuardado = await obtenerOVerificarPermisoCarpeta();
                if (!dirHandleGuardado) {
                    return;
                }
            }

            const btn = document.getElementById('btnDescargar');
            const panelProgreso = document.getElementById('panelProgreso');
            const textoEstado = document.getElementById('textoEstado');
            const listaResultados = document.getElementById('listaResultados');
            const exito = document.getElementById('exito');

            btn.disabled = true;
            exito.style.display = 'none';
            listaResultados.innerHTML = '';
            urlsDescargadasPorCancion = {};
            colaDescargas = [];
            modoColaVersiones = false;
            totalEncoladasVersiones = 0;
            procesadasVersiones = 0;

            panelProgreso.style.display = 'block';
            textoEstado.textContent = "Leyendo lista de canciones...";
            iniciarAvanceBarra(10, 25);

            const formData = new FormData(this);

            try {
                const respLista = await fetch('/obtener_lista', { method: 'POST', body: formData });
                const datosLista = await respLista.json();

                if (!respLista.ok) {
                    detenerAvanceBarra(0, true);
                    alert(datosLista.error || "Error leyendo la lista.");
                    panelProgreso.style.display = 'none';
                    btn.disabled = false;
                    return;
                }

                listaCancionesGlobal = datosLista.canciones;
                actualizarProgresoVisual(0, listaCancionesGlobal.length);

                listaResultados.innerHTML = '';
                listaCancionesGlobal.forEach((cancion, idx) => {
                    listaResultados.insertAdjacentHTML('beforeend', `
                        <li class="list-group-item bg-transparent text-secondary border-secondary d-flex justify-content-between align-items-center" id="fila-${idx}">
                            <span id="item-texto-${idx}" class="me-2">⏳ En cola: ${escaparHtml(cancion)}</span>
                            <button type="button" class="btn btn-sm btn-outline-light flex-shrink-0 btn-otra-version" onclick="abrirSelectorVersiones('${escaparHtml(cancion)}')">
                                🔄 Otra versión
                            </button>
                        </li>
                    `);
                    colaDescargas.push({
                        cancion: cancion,
                        urlVideo: null,
                        itemId: idx,
                        nombreDisplay: cancion,
                        esExtra: false,
                        ordenCancion: idx + 1
                    });
                });

                procesarSiguienteEnCola();

            } catch (err) {
                detenerAvanceBarra(0, true);
                alert("Ocurrió un error conectando con el servidor.");
                btn.disabled = false;
            }
        });
    </script>
</body>
</html>
"""


def preparar_cookies_yt():
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


def normalizar_texto(texto):
    if not texto:
        return ""
    texto_norm = unicodedata.normalize("NFKD", str(texto))
    texto_sin_acentos = "".join([c for c in texto_norm if not unicodedata.combining(c)])
    limpio = re.sub(r"[-_.,()\[\]{}]+", " ", texto_sin_acentos)
    return re.sub(r"\s+", " ", limpio).strip().lower()


def formatear_vistas(num):
    if not num:
        return "N/A"
    num = int(num)
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M vistas"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K vistas"
    return f"{num} vistas"


def obtener_nombre_unico_disco(carpeta, nombre_archivo):
    nombre_limpio = re.sub(r'[<>:"/\\|?*]+', " ", nombre_archivo).strip()
    base, ext = os.path.splitext(nombre_limpio)
    candidato = nombre_limpio
    contador = 2
    while os.path.exists(os.path.join(carpeta, candidato)):
        candidato = f"{base} ({contador}){ext}"
        contador += 1
    return candidato


def desglosar_artista_y_pista(cancion_str):
    stopwords = {
        "the",
        "and",
        "for",
        "from",
        "with",
        "this",
        "that",
        "con",
        "del",
        "las",
        "los",
        "una",
        "por",
        "para",
    }
    str_limpio = cancion_str.strip()
    if " - " in str_limpio:
        partes = [p.strip() for p in str_limpio.split(" - ") if p.strip()]
        artista = partes[0]
        pista = " - ".join(partes[1:])
    else:
        artista = ""
        pista = str_limpio

    palabras_pista = [
        w for w in normalizar_texto(pista).split() if len(w) > 2 and w not in stopwords
    ]
    return artista, pista, palabras_pista


def obtener_opciones_youtube(cancion):
    if cancion in CACHE_OPCIONES and len(CACHE_OPCIONES[cancion]) >= 5:
        return CACHE_OPCIONES[cancion]

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
    archivo_cookies = preparar_cookies_yt()
    if archivo_cookies:
        ydl_opts_search["cookiefile"] = archivo_cookies

    artista_orig, pista_orig, palabras_clave_pista = desglosar_artista_y_pista(cancion)
    norm_cancion = normalizar_texto(cancion)
    busca_en_vivo = any(
        w in norm_cancion for w in ["live", "vivo", "concert", "concierto"]
    )

    if artista_orig:
        query_music = f'ytsearch10:"{pista_orig}" {artista_orig} audio'
    else:
        query_music = f'ytsearch10:"{pista_orig}" audio'

    try:
        with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
            info = ydl.extract_info(query_music, download=False)
            opciones_raw = list(info.get("entries", [])) if info else []
    except Exception as e:
        print(f"⚠️ Error buscando en YT: {e}")
        return []

    if not opciones_raw:
        return []

    opciones_relevantes = []
    for op in opciones_raw:
        if not op:
            continue
        titulo_v = str(op.get("title", ""))
        titulo_v_norm = normalizar_texto(titulo_v)

        if palabras_clave_pista:
            coincidencias = sum(1 for w in palabras_clave_pista if w in titulo_v_norm)
            min_requerido = max(1, round(len(palabras_clave_pista) * 0.7))
            if coincidencias < min_requerido:
                continue

        opciones_relevantes.append(op)

    if not opciones_relevantes:
        opciones_relevantes = [op for op in opciones_raw if op]

    textos_por_video = [
        f" {normalizar_texto(op.get('title', ''))} {normalizar_texto(op.get('uploader') or op.get('channel') or '')} "
        for op in opciones_relevantes
    ]

    def es_canal_oficial_artista(canal_raw):
        canal_low = canal_raw.lower()
        if any(
            bad in canal_low
            for bad in [
                "unofficial",
                "no oficial",
                "fan",
                "tribute",
                "lyrics",
                "letra",
                "radio",
                "amv",
            ]
        ):
            return False

        raiz_artista = re.sub(
            r"\b(official|oficial|vevo|topic|music|band|tv|channel|records)\b",
            "",
            normalizar_texto(canal_low),
        ).strip()
        if len(raiz_artista) < 2:
            return False

        esta_en_busqueda = (
            f" {raiz_artista} " in f" {norm_cancion} " or raiz_artista in norm_cancion
        )
        repeticiones_en_lista = sum(
            1 for txt in textos_por_video if f" {raiz_artista} " in txt
        )
        umbral_minimo = 2 if len(opciones_relevantes) < 4 else 3
        es_artista_implicito = repeticiones_en_lista >= umbral_minimo

        return esta_en_busqueda or es_artista_implicito

    palabras_no_deseadas = [
        "live",
        "en vivo",
        "concert",
        "concierto",
        "festival",
        "wacken",
        "tour",
        "cover",
        "tribute",
        "full album",
        "slowed",
        "nightcore",
        "revisited",
    ]

    opciones_clasificadas = []
    for op in opciones_relevantes:
        titulo_raw = op.get("title", "Desconocido")
        canal_raw = op.get("uploader") or op.get("channel") or "Desconocido"
        titulo = str(titulo_raw).lower()
        canal = str(canal_raw).lower()
        descripcion = str(op.get("description") or "").lower()
        vistas = int(op.get("view_count") or 0)
        url_vid = op.get("url") or (
            f"https://www.youtube.com/watch?v={op.get('id')}" if op.get("id") else ""
        )

        es_no_deseada = (
            any(kw in titulo or kw in canal for kw in palabras_no_deseadas)
            and not busca_en_vivo
        )
        es_oficial = es_canal_oficial_artista(canal)

        if es_no_deseada:
            nivel = -1
        elif es_oficial:
            nivel = 4
        elif (
            "topic" in canal
            or "provided to youtube by" in descripcion
            or "auto-generated by youtube" in descripcion
        ):
            nivel = 3
        elif "official audio" in titulo or "audio oficial" in titulo:
            nivel = 2
        else:
            nivel = 1

        opciones_clasificadas.append(
            {
                "title": titulo_raw,
                "uploader": canal_raw,
                "duration": op.get("duration"),
                "view_count": vistas,
                "url": url_vid,
                "_nivel": nivel,
            }
        )

    opciones_clasificadas.sort(
        key=lambda x: (x["_nivel"], x["view_count"]), reverse=True
    )

    cache_ultraligero = []
    for op in opciones_clasificadas[:5]:
        dur = op.get("duration")
        if dur:
            m, s = divmod(int(dur), 60)
            dur_str = f"{m:02d}:{s:02d}"
        else:
            dur_str = "N/A"

        cache_ultraligero.append(
            {
                "title": str(op.get("title", "Desconocido")),
                "uploader": str(op.get("uploader", "Desconocido")),
                "duration": dur_str,
                "view_count": int(op.get("view_count") or 0),
                "url": str(op.get("url") or ""),
            }
        )

    del opciones_raw, opciones_relevantes, opciones_clasificadas
    if "info" in locals():
        del info
    gc.collect()

    if len(CACHE_OPCIONES) > 25:
        CACHE_OPCIONES.clear()
        gc.collect()

    CACHE_OPCIONES[cancion] = cache_ultraligero
    return cache_ultraligero


def convertir_a_m4a_solo_audio(ruta_entrada, ruta_salida):
    ffmpeg_bin = (
        "ffmpeg.exe" if os.name == "nt" and os.path.exists("ffmpeg.exe") else "ffmpeg"
    )

    cmd_copy = [
        ffmpeg_bin,
        "-y",
        "-i",
        ruta_entrada,
        "-vn",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        ruta_salida,
    ]
    res = subprocess.run(cmd_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if (
        res.returncode == 0
        and os.path.exists(ruta_salida)
        and os.path.getsize(ruta_salida) > 1000
    ):
        return True

    cmd_aac = [
        ffmpeg_bin,
        "-y",
        "-i",
        ruta_entrada,
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-threads",
        "1",
        ruta_salida,
    ]
    res2 = subprocess.run(cmd_aac, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (
        res2.returncode == 0
        and os.path.exists(ruta_salida)
        and os.path.getsize(ruta_salida) > 1000
    )


def ejecutar_descarga_yt(url_video, carpeta_destino):
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

    with yt_dlp.YoutubeDL(ydl_opts_download) as ydl_down:
        ydl_down.download([url_video])

    archivos_descargados = os.listdir(carpeta_destino)
    if not archivos_descargados:
        return None

    ruta_bruta = os.path.join(carpeta_destino, archivos_descargados[0])
    nombre_base = os.path.splitext(archivos_descargados[0])[0]
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


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_INTERFAZ)


@app.route("/elegir_carpeta_pc", methods=["POST"])
def elegir_carpeta_pc():
    global CARPETA_PC_ACTUAL
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        carpeta = filedialog.askdirectory(
            initialdir=CARPETA_PC_ACTUAL or os.path.expanduser("~\\Downloads"),
            title="Selecciona la carpeta donde se guardarán tus canciones (.m4a)",
        )
        root.destroy()
        if not carpeta:
            return jsonify({"cancelado": True})
        CARPETA_PC_ACTUAL = os.path.normpath(carpeta)
        return jsonify({"cancelado": False, "carpeta": CARPETA_PC_ACTUAL})
    except Exception as e:
        return jsonify({"cancelado": False, "modo_web": True, "detalle": str(e)})


@app.route("/obtener_lista", methods=["POST"])
def obtener_lista():
    canciones = []

    texto_directo = request.form.get("texto_canciones", "").strip()
    if texto_directo:
        for linea in texto_directo.splitlines():
            canciones.extend([c.strip() for c in linea.split(",") if c.strip()])

    archivo = request.files.get("archivo_lista")
    if archivo and archivo.filename != "":
        ext = os.path.splitext(archivo.filename)[1].lower()
        contenido_raw = archivo.read().decode("utf-8-sig", errors="ignore")

        if ext == ".csv":
            lector = csv.reader(io.StringIO(contenido_raw), quotechar='"')
            encabezados = next(lector, [])
            try:
                idx_track = encabezados.index("Track Name")
                idx_artist = encabezados.index("Artist Name(s)")
                for fila in lector:
                    if len(fila) > max(idx_track, idx_artist):
                        track = fila[idx_track].strip()
                        artist = fila[idx_artist].strip()
                        if track and artist:
                            canciones.append(f"{artist} - {track}")
            except ValueError:
                for fila in lector:
                    if fila and fila[0].strip():
                        canciones.append(fila[0].strip())
        else:
            canciones.extend(
                [linea.strip() for linea in contenido_raw.splitlines() if linea.strip()]
            )

    if not canciones:
        return jsonify(
            {"error": "No ingresaste canciones ni subiste un archivo válido."}
        ), 400

    print(f"\n🚀 Procesando lista con {len(canciones)} canciones...")
    return jsonify({"canciones": canciones})


@app.route("/buscar_versiones", methods=["POST"])
def buscar_versiones():
    datos = request.get_json() or {}
    cancion = datos.get("cancion", "").strip()
    excluidas = set(datos.get("excluidas") or [])
    if not cancion:
        return jsonify({"error": "Canción vacía"}), 400

    try:
        opciones_cargadas = obtener_opciones_youtube(cancion)
        candidatas = [op for op in opciones_cargadas if op.get("url") not in excluidas]

        if len(candidatas) < 5:
            for op in opciones_cargadas:
                if op not in candidatas and op.get("url") not in excluidas:
                    candidatas.append(op)
                if len(candidatas) >= 5:
                    break

        resultado = []
        for idx, op in enumerate(candidatas[:5]):
            resultado.append(
                {
                    "version": idx + 1,
                    "titulo": op.get("title", "Desconocido"),
                    "canal": op.get("uploader", "Desconocido"),
                    "duracion": op.get("duration", "N/A"),
                    "vistas": formatear_vistas(op.get("view_count")),
                    "url": op.get("url"),
                }
            )
        return jsonify({"versiones": resultado})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/descargar_una", methods=["POST"])
def descargar_una():
    datos = request.get_json() or {}
    cancion = datos.get("cancion", "").strip()
    url_directa = datos.get("url", "").strip()
    guardar_en_pc = datos.get("guardar_en_pc", False)

    with LOCK_DESCARGA:
        temp_dir = tempfile.mkdtemp()
        exito_envio_directo = False
        try:
            ruta_archivo = None
            url_exitosa = None

            if url_directa:
                print(f"⬇️ Descargando versión alternativa: {cancion}...")
                ruta_archivo = ejecutar_descarga_yt(url_directa, temp_dir)
                url_exitosa = url_directa
            else:
                print(f"🔍 Buscando mejor coincidencia oficial para: {cancion}...")
                opciones = obtener_opciones_youtube(cancion)
                if not opciones:
                    return f"No se encontraron resultados válidos para: {cancion}", 404

                for idx_op, op in enumerate(opciones[:3]):
                    canal_elegido = op.get("uploader", "YT")
                    vistas_elegidas = formatear_vistas(op.get("view_count"))
                    print(
                        f"⬇️ Probando #{idx_op + 1}: {op.get('title')} ({canal_elegido} | {vistas_elegidas})..."
                    )
                    try:
                        ruta_archivo = ejecutar_descarga_yt(op.get("url"), temp_dir)
                        if ruta_archivo and os.path.exists(ruta_archivo):
                            url_exitosa = op.get("url")
                            break
                    except Exception:
                        pass

            if not ruta_archivo or not os.path.exists(ruta_archivo):
                return f"No se pudo descargar: {cancion}", 404

            nombre_archivo = os.path.basename(ruta_archivo)

            if guardar_en_pc and CARPETA_PC_ACTUAL and os.path.isdir(CARPETA_PC_ACTUAL):
                nombre_final = obtener_nombre_unico_disco(
                    CARPETA_PC_ACTUAL, nombre_archivo
                )
                ruta_final = os.path.join(CARPETA_PC_ACTUAL, nombre_final)
                shutil.copy2(ruta_archivo, ruta_final)
                shutil.rmtree(temp_dir, ignore_errors=True)
                gc.collect()
                return jsonify(
                    {"guardado_como": nombre_final, "url_usada": url_exitosa}
                )

            respuesta = send_file(
                ruta_archivo,
                mimetype="audio/mp4",
                as_attachment=True,
                download_name=nombre_archivo,
            )
            respuesta.headers["X-Filename"] = quote(nombre_archivo)
            if url_exitosa:
                respuesta.headers["X-Youtube-Url"] = url_exitosa

            @respuesta.call_on_close
            def limpiar_despues():
                shutil.rmtree(temp_dir, ignore_errors=True)
                gc.collect()

            exito_envio_directo = True
            return respuesta
        except Exception as e:
            return str(e), 500
        finally:
            if not exito_envio_directo:
                shutil.rmtree(temp_dir, ignore_errors=True)
                gc.collect()


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=puerto, debug=False)
