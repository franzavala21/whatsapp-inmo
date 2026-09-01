# agent/tools.py — Herramientas del agente
# Generado por AgentKit

"""
Herramientas especificas del negocio.

La informacion ESTATICA del negocio (horario, zonas, politicas) le llega al agente por
el system prompt (config/prompts.yaml), no por aca. Este archivo es para lo que el
prompt no puede resolver solo:

- buscar_propiedades(): esta SI esta conectada al ciclo de tool use de Claude (ver
  agent/brain.py). Consulta en vivo el CRM Tokko Broker, porque el listado de
  propiedades cambia todo el tiempo y no puede vivir pegado en el prompt.
- registrar_lead() / escalar_a_vendedor(): son funciones listas para usar, pero
  todavia NO estan conectadas al tool use — hoy el agente solo conversa para calificar
  al lead, no lo registra solo. Conectarlas es un paso aparte.

Casos de uso de Zavala Seppey: preguntas frecuentes + calificacion de leads.
"""

import logging
import os
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

CARPETA_KNOWLEDGE = Path("knowledge")


def cargar_info_negocio() -> dict:
    """Carga la informacion del negocio desde config/business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atencion del negocio."""
    info = cargar_info_negocio()
    return {
        "horario": info.get("negocio", {}).get("horario", "No disponible"),
        "esta_abierto": True,  # TODO: calcular segun la hora actual y el horario
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca informacion en los archivos de /knowledge.
    Retorna los fragmentos que coinciden con la consulta.
    """
    if not CARPETA_KNOWLEDGE.is_dir():
        return "No hay archivos de conocimiento disponibles."

    resultados = []
    for ruta in sorted(CARPETA_KNOWLEDGE.iterdir()):
        if ruta.name.startswith(".") or not ruta.is_file():
            continue
        try:
            contenido = ruta.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binarios y archivos ilegibles se saltean
        if consulta.lower() in contenido.lower():
            resultados.append(f"[{ruta.name}]: {contenido[:500]}")

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontre informacion especifica sobre eso en mis archivos."


# ════════════════════════════════════════════════════════════
# Calificacion y atencion de leads
#
# Zavala Seppey todavia no tiene conectado su CRM (Tokko Broker) a estas
# herramientas: por ahora son funciones listas para usar el dia que se
# conecte el tool use de Claude. Registran el lead en un archivo simple
# para no perder informacion mientras tanto.
# ════════════════════════════════════════════════════════════

ARCHIVO_LEADS = Path("knowledge") / "leads.yaml"


def registrar_lead(telefono: str, nombre: str, interes: str) -> dict:
    """
    Guarda los datos de un lead (interesado en comprar, alquilar o vender).

    interes: descripcion libre de lo que busca (ej. "alquilar depto 2 amb en
    Salsipuedes, presupuesto $300.000, para diciembre").
    """
    leads = []
    if ARCHIVO_LEADS.exists():
        leads = yaml.safe_load(ARCHIVO_LEADS.read_text(encoding="utf-8")) or []

    lead = {"telefono": telefono, "nombre": nombre, "interes": interes}
    leads.append(lead)
    ARCHIVO_LEADS.write_text(yaml.safe_dump(leads, allow_unicode=True), encoding="utf-8")

    logger.info(f"Lead registrado: {telefono} — {interes}")
    return lead


def escalar_a_vendedor(telefono: str, contexto: str) -> bool:
    """
    Marca un lead como listo para que un asesor humano lo contacte.

    Por ahora solo lo deja registrado en knowledge/leads.yaml. Conectar esto
    a una notificacion real (WhatsApp interno, email, Slack) es un paso
    aparte que se arma cuando el agente ya este en produccion.
    """
    logger.info(f"Lead escalado a un asesor: {telefono} — {contexto}")
    return True


# ════════════════════════════════════════════════════════════
# Tokko Broker — listado de propiedades en vivo
#
# Esta es la UNICA herramienta conectada al ciclo de tool use de Claude
# (ver TOOLS y ejecutar_herramienta en agent/brain.py). El inventario de
# Zavala Seppey vive en Tokko Broker, no en este repo: se consulta la API
# en cada busqueda para no contestar con precios o disponibilidad vieja.
# ════════════════════════════════════════════════════════════

TOKKO_BASE_URL = "https://www.tokkobroker.com/api/v1/property/"

# IDs internos de la cuenta de Zavala Seppey en Tokko Broker (confirmados
# contra la API real: GET /api/v1/property/ y se releva que valores usa
# cada propiedad). Si el negocio empieza a cargar otros tipos, hay que
# sumarlos aca.
OPERACIONES_TOKKO = {"venta": "Venta", "alquiler": "Alquiler"}
TIPOS_TOKKO = {"terreno": "Terreno", "departamento": "Departamento", "casa": "Casa", "oficina": "Oficina"}


async def buscar_propiedades(
    operacion: str,
    tipo: str | None = None,
    zona: str | None = None,
    precio_max: float | None = None,
) -> str:
    """
    Busca propiedades disponibles en el Tokko Broker de Zavala Seppey.

    Trae el listado completo de la cuenta (hoy son ~70 propiedades, entra en
    una sola pagina) y filtra en Python: la API de busqueda de Tokko exige un
    formato de filtros que no esta documentado publicamente, mientras que el
    listado simple si es estable y esta confirmado contra la cuenta real.

    Args:
        operacion: "venta" o "alquiler"
        tipo: "casa" | "departamento" | "terreno" | "oficina", opcional
        zona: texto libre para buscar en la direccion/barrio/titulo, opcional
        precio_max: precio maximo, opcional. Ojo: compara contra el numero
            de precio tal cual esta cargado, sin distinguir moneda (las
            ventas casi siempre estan en USD, los alquileres en ARS).
    """
    api_key = os.getenv("TOKKO_API_KEY", "")
    if not api_key:
        logger.error("No se puede buscar en Tokko: falta TOKKO_API_KEY")
        return "El sistema de propiedades no esta disponible en este momento."

    operacion_tokko = OPERACIONES_TOKKO.get(operacion.lower().strip())
    if not operacion_tokko:
        return f"Operacion invalida: '{operacion}'. Debe ser 'venta' o 'alquiler'."

    tipo_tokko = TIPOS_TOKKO.get(tipo.lower().strip()) if tipo else None

    try:
        async with httpx.AsyncClient(timeout=15.0) as cliente:
            r = await cliente.get(
                TOKKO_BASE_URL,
                params={"key": api_key, "lang": "es_ar", "format": "json", "limit": 200},
            )
    except httpx.HTTPError as e:
        logger.error(f"Error de red hablando con Tokko: {e}")
        return "No se pudo consultar el sistema de propiedades en este momento."

    if r.status_code != 200:
        logger.error(f"Tokko rechazo la consulta [{r.status_code}]: {r.text[:300]}")
        return "No se pudo consultar el sistema de propiedades en este momento."

    propiedades = r.json().get("objects", [])
    zona_normalizada = zona.lower().strip() if zona else None

    coincidencias = []
    for prop in propiedades:
        operaciones = [op for op in prop.get("operations", []) if op.get("operation_type") == operacion_tokko]
        if not operaciones:
            continue

        if tipo_tokko and (prop.get("type") or {}).get("name") != tipo_tokko:
            continue

        if zona_normalizada:
            texto_ubicacion = " ".join(
                [
                    prop.get("address") or "",
                    prop.get("real_address") or "",
                    (prop.get("location") or {}).get("full_location") or "",
                    prop.get("publication_title") or "",
                ]
            ).lower()
            if zona_normalizada not in texto_ubicacion:
                continue

        precios = operaciones[0].get("prices", [])
        if precio_max is not None and precios and all(p["price"] > precio_max for p in precios):
            continue

        coincidencias.append((prop, precios))

    if not coincidencias:
        return "No se encontraron propiedades disponibles que coincidan con esa busqueda."

    # Se manda un maximo de 5 opciones: es WhatsApp, no un listado interminable
    lineas = []
    for prop, precios in coincidencias[:5]:
        precio_txt = ", ".join(f"{p['currency']} {p['price']:,.0f}" for p in precios) if precios else "Consultar precio"
        ubicacion = (prop.get("location") or {}).get("name") or prop.get("address") or "Zona no especificada"
        lineas.append(
            f"- {prop.get('publication_title', 'Propiedad')} | {(prop.get('type') or {}).get('name', '')} en {ubicacion} "
            f"| {operacion_tokko} {precio_txt} | Ficha: {prop.get('public_url', '')}"
        )

    total = len(coincidencias)
    encabezado = f"Se encontraron {total} propiedad(es)"
    if total > 5:
        encabezado += " (mostrando las primeras 5)"

    return encabezado + ":\n" + "\n".join(lineas)
