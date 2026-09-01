# agent/tools.py — Herramientas del agente
# Generado por AgentKit

"""
Herramientas especificas del negocio.

OJO: estas funciones NO se ejecutan solas todavia. La informacion del negocio le llega
al agente por el system prompt (config/prompts.yaml), asi que para CONTESTAR preguntas
no hace falta nada de aca. Este archivo es el lugar para las ACCIONES —registrar un
lead, escalarlo a un asesor— y conectarlas al ciclo de tool use de Claude es un paso
aparte.

Casos de uso de Zavala Seppey: preguntas frecuentes + calificacion de leads.
"""

import logging
from pathlib import Path

import yaml

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
