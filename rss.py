import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


WEB_URL = "https://soltec.com/es/sala-prensa/"
BASE_URL = "https://soltec.com"
ARCHIVO_RSS = Path("soltec.xml")

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def limpiar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def escapar_xml(texto):
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def descargar_pagina():
    ultimo_error = None

    for intento in range(1, 4):
        try:
            respuesta = requests.get(
                WEB_URL,
                headers=CABECERAS,
                timeout=90,
                allow_redirects=True,
            )
            respuesta.raise_for_status()

            if len(respuesta.text.strip()) < 500:
                raise RuntimeError(
                    "Soltec devolvió una página incompleta"
                )

            return respuesta.text

        except (
            requests.RequestException,
            RuntimeError,
        ) as error:
            ultimo_error = error
            print(f"Intento {intento} fallido: {error}")

            if intento < 3:
                time.sleep(5 * intento)

    raise RuntimeError(
        f"No se pudo descargar la Sala de Prensa: {ultimo_error}"
    )


def convertir_fecha(texto):
    texto = limpiar_texto(texto).lower()

    coincidencia = re.search(
        r"\b(\d{1,2})\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s+(\d{4})\b",
        texto,
    )

    if not coincidencia:
        return None

    dia = int(coincidencia.group(1))
    mes = MESES[coincidencia.group(2)]
    anio = int(coincidencia.group(3))

    try:
        return datetime(
            anio,
            mes,
            dia,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def es_enlace_de_noticia(url):
    ruta = urlparse(url).path.lower().rstrip("/")

    prefijo = "/es/sala-prensa/"

    if not ruta.startswith(prefijo):
        return False

    resto = ruta.removeprefix(prefijo)

    if not resto:
        return False

    if resto.startswith("page/"):
        return False

    if resto.startswith("filter/"):
        return False

    if "/" in resto:
        return False

    return True


def buscar_contenedor(enlace):
    actual = enlace

    for _ in range(10):
        actual = actual.parent

        if actual is None:
            break

        texto = limpiar_texto(
            actual.get_text(" ", strip=True)
        )

        if convertir_fecha(texto) is not None:
            if len(texto) <= 2500:
                return actual

    return None


def obtener_titulo(enlace, contenedor):
    for etiqueta in ["h1", "h2", "h3", "h4", "h5"]:
        encabezado = contenedor.find(etiqueta)

        if encabezado:
            titulo = limpiar_texto(
                encabezado.get_text(" ", strip=True)
            )

            if len(titulo) >= 15:
                return titulo

    titulo = limpiar_texto(
        enlace.get_text(" ", strip=True)
    )

    if titulo.lower() in {
        "ver más",
        "ver mas",
        "leer más",
        "leer mas",
    }:
        return ""

    return titulo


def obtener_descripcion(contenedor, titulo):
    texto = limpiar_texto(
        contenedor.get_text(" ", strip=True)
    )

    texto = texto.replace(titulo, " ")

    texto = re.sub(
        r"\b(?:ver|leer)\s+m[aá]s\b",
        " ",
        texto,
        flags=re.IGNORECASE,
    )

    texto = re.sub(
        r"\b\d{1,2}\s+"
        r"(?:enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s+\d{4}\b",
        " ",
        texto,
        flags=re.IGNORECASE,
    )

    texto = limpiar_texto(texto)

    if texto == titulo:
        return ""

    return texto[:900]


def obtener_noticias(html):
    soup = BeautifulSoup(html, "html.parser")
    noticias = []
    enlaces_vistos = set()

    for enlace in soup.find_all("a", href=True):
        url = urljoin(
            BASE_URL,
            enlace.get("href"),
        )
        url = url.split("#")[0].split("?")[0].rstrip("/")

        if not es_enlace_de_noticia(url):
            continue

        if url in enlaces_vistos:
            continue

        contenedor = buscar_contenedor(enlace)

        if contenedor is None:
            continue

        texto_contenedor = limpiar_texto(
            contenedor.get_text(" ", strip=True)
        )
        fecha = convertir_fecha(texto_contenedor)

        if fecha is None:
            continue

        titulo = obtener_titulo(
            enlace,
            contenedor,
        )

        if len(titulo) < 15:
            continue

        # Excluye publicaciones protegidas con contraseña.
        if titulo.lower().startswith("protegido:"):
            continue

        descripcion = obtener_descripcion(
            contenedor,
            titulo,
        )

        noticias.append(
            {
                "titulo": titulo,
                "url": url,
                "fecha": fecha,
                "descripcion": descripcion,
            }
        )

        enlaces_vistos.add(url)

    noticias.sort(
        key=lambda noticia: noticia["fecha"],
        reverse=True,
    )

    if not noticias:
        raise RuntimeError(
            "No se encontraron noticias en la Sala de Prensa "
            "de Soltec"
        )

    return noticias[:50]


def crear_rss(noticias):
    ahora = datetime.now(timezone.utc)

    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>Soltec - Sala de Prensa</title>",
        f"<link>{escapar_xml(WEB_URL)}</link>",
        (
            "<description>Últimas noticias y comunicados "
            "oficiales de Soltec</description>"
        ),
        "<language>es</language>",
        f"<lastBuildDate>{format_datetime(ahora)}</lastBuildDate>",
        "<ttl>60</ttl>",
    ]

    for noticia in noticias:
        partes.extend(
            [
                "<item>",
                f"<title>{escapar_xml(noticia['titulo'])}</title>",
                f"<link>{escapar_xml(noticia['url'])}</link>",
                (
                    f'<guid isPermaLink="true">'
                    f"{escapar_xml(noticia['url'])}</guid>"
                ),
                (
                    f"<pubDate>"
                    f"{format_datetime(noticia['fecha'])}"
                    f"</pubDate>"
                ),
                (
                    f"<description>"
                    f"{escapar_xml(noticia['descripcion'])}"
                    f"</description>"
                ),
                "</item>",
            ]
        )

    partes.extend(
        [
            "</channel>",
            "</rss>",
        ]
    )

    return "\n".join(partes)


def guardar_rss(contenido):
    archivo_temporal = ARCHIVO_RSS.with_suffix(
        ".xml.tmp"
    )
    archivo_temporal.write_text(
        contenido,
        encoding="utf-8",
    )
    archivo_temporal.replace(ARCHIVO_RSS)


def main():
    html = descargar_pagina()
    noticias = obtener_noticias(html)
    contenido_rss = crear_rss(noticias)
    guardar_rss(contenido_rss)

    print(
        f"RSS de Soltec actualizada: "
        f"{len(noticias)} noticias"
    )

    for noticia in noticias[:5]:
        print(
            noticia["fecha"].strftime("%d/%m/%Y"),
            "-",
            noticia["titulo"],
        )


if __name__ == "__main__":
    main()
