import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

PAGINAS = ["https://soltec.com/actualidad/?bpage=1", "https://soltec.com/es/sala-prensa/"]
ARCHIVO_RSS = Path("soltec.xml")
CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36", "Accept-Language": "es-ES,es;q=0.9"}
MESES = {nombre: i for i, nombre in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}
MESES["setiembre"] = 9
PATRON_FECHA = re.compile(r"\b(\d{1,2})\s+(?:de\s+)?(" + "|".join(MESES) + r")\s+(?:de\s+)?(20\d{2})\b", re.I)


def limpiar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def fecha_de(texto):
    match = PATRON_FECHA.search(limpiar(texto))
    if not match:
        return None
    try:
        return datetime(int(match.group(3)), MESES[match.group(2).lower()], int(match.group(1)), 12, tzinfo=timezone.utc)
    except ValueError:
        return None


def url_noticia(url):
    parsed = urlparse(url)
    if parsed.netloc.lower() not in ("soltec.com", "www.soltec.com"):
        return False
    ruta = parsed.path.rstrip("/")
    return any(re.fullmatch(r"/(?:es/)?sala-prensa/[^/]+", ruta, re.I) for _ in (0,)) and not ruta.endswith("/filter")


def descargar(url):
    ultimo = None
    for intento in range(3):
        try:
            r = requests.get(url, headers=CABECERAS, timeout=40)
            r.raise_for_status()
            if len(r.content) < 500:
                raise ValueError("Respuesta demasiado corta")
            return r.text
        except (requests.RequestException, ValueError) as exc:
            ultimo = exc
            print(f"Intento {intento + 1} en {url}: {exc}")
            if intento < 2:
                time.sleep(3 * (intento + 1))
    raise RuntimeError(str(ultimo))


def extraer(html, base):
    soup = BeautifulSoup(html, "html.parser")
    resultados = {}
    for a in soup.select("a[href]"):
        url = urljoin(base, a.get("href", "")).split("#")[0].split("?")[0].rstrip("/") + "/"
        if not url_noticia(url):
            continue
        titulo = limpiar(a.get_text(" ", strip=True))
        if len(titulo) < 15 or titulo.lower() in ("ver más", "leer más"):
            titulo = ""
        mejor = None
        for padre in list(a.parents)[:7]:
            texto = limpiar(padre.get_text(" ", strip=True))
            if len(texto) > 1800:
                break
            fecha = fecha_de(texto)
            if fecha:
                mejor = (padre, fecha)
                break
        if not mejor:
            continue
        contenedor, fecha = mejor
        if not titulo:
            for h in contenedor.find_all(["h2", "h3", "h4", "h5"]):
                candidato = limpiar(h.get_text(" ", strip=True))
                if len(candidato) >= 15:
                    titulo = candidato
                    break
        if not titulo:
            texto = limpiar(contenedor.get_text(" ", strip=True))
            titulo = limpiar(PATRON_FECHA.sub("", texto, count=1).split("Ver más")[0])
        if len(titulo) < 15 or titulo.lower().startswith("protegido:"):
            continue
        descripcion = limpiar(contenedor.get_text(" ", strip=True))[:700]
        resultados[url] = {"titulo": titulo, "url": url, "fecha": fecha, "descripcion": descripcion}
    return list(resultados.values())


def anteriores():
    resultado = {}
    if not ARCHIVO_RSS.exists():
        return resultado
    try:
        raiz = ET.parse(ARCHIVO_RSS).getroot()
        for item in raiz.findall("./channel/item"):
            url = (item.findtext("link") or "").strip()
            titulo = (item.findtext("title") or "").strip()
            fecha = parsedate_to_datetime(item.findtext("pubDate"))
            if url and titulo:
                resultado[url.rstrip("/") + "/"] = {"url": url.rstrip("/") + "/", "titulo": titulo, "fecha": fecha, "descripcion": item.findtext("description") or ""}
    except (ET.ParseError, ValueError, TypeError, OSError) as exc:
        print(f"Aviso: no se pudo leer RSS anterior: {exc}")
    return resultado


def crear_xml(noticias):
    rss = ET.Element("rss", version="2.0")
    canal = ET.SubElement(rss, "channel")
    for etiqueta, valor in (("title", "Soltec - Noticias"), ("link", PAGINAS[0]), ("description", "Noticias y comunicados oficiales de Soltec"), ("language", "es"), ("lastBuildDate", format_datetime(datetime.now(timezone.utc)))):
        ET.SubElement(canal, etiqueta).text = valor
    for noticia in sorted(noticias, key=lambda n: n["fecha"], reverse=True)[:60]:
        item = ET.SubElement(canal, "item")
        for etiqueta, valor in (("title", noticia["titulo"]), ("link", noticia["url"]), ("guid", noticia["url"]), ("pubDate", format_datetime(noticia["fecha"])), ("description", noticia["descripcion"])):
            nodo = ET.SubElement(item, etiqueta)
            nodo.text = valor
            if etiqueta == "guid":
                nodo.set("isPermaLink", "true")
    ET.indent(rss)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    guardadas = anteriores()
    nuevas = {}
    for pagina in PAGINAS:
        try:
            items = extraer(descargar(pagina), pagina)
            print(f"{pagina}: {len(items)} noticias encontradas")
            for noticia in items:
                nuevas[noticia["url"]] = noticia
        except Exception as exc:
            print(f"Aviso: {pagina}: {exc}")
    if not nuevas:
        if guardadas:
            print("No se detectaron noticias nuevas. Se conserva el XML anterior sin modificar.")
            return
        raise RuntimeError("No se encontraron noticias y no existe RSS anterior que conservar.")
    guardadas.update(nuevas)
    contenido = crear_xml(list(guardadas.values()))
    ET.fromstring(contenido)
    temporal = ARCHIVO_RSS.with_suffix(".xml.tmp")
    temporal.write_bytes(contenido)
    temporal.replace(ARCHIVO_RSS)
    print(f"RSS actualizado: {len(nuevas)} noticias detectadas; {len(guardadas)} registros acumulados.")


if __name__ == "__main__":
    main()
