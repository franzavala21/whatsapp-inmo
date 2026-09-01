# agent/brain.py — Cerebro del agente: conexion con Claude
# Generado por AgentKit

"""
Logica de IA del agente. Lee el system prompt de config/prompts.yaml y genera las
respuestas con la API de Anthropic.
"""

import logging
import os

import yaml
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from agent.tools import buscar_propiedades

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Unica herramienta conectada al ciclo de tool use de Claude por ahora: el
# listado de propiedades de Tokko Broker, porque es informacion que cambia
# todo el tiempo y no puede vivir pegada en el system prompt.
TOOLS_ANTHROPIC = [
    {
        "name": "buscar_propiedades",
        "description": (
            "Busca propiedades disponibles ahora mismo en el CRM de Zavala Seppey "
            "(Tokko Broker). Usar SIEMPRE que el cliente pregunte por propiedades "
            "disponibles, pida ver opciones, o pregunte el precio de algo puntual: "
            "nunca inventar propiedades ni precios sin llamar a esta herramienta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operacion": {
                    "type": "string",
                    "enum": ["venta", "alquiler"],
                    "description": "Que operacion busca el cliente.",
                },
                "tipo": {
                    "type": "string",
                    "enum": ["casa", "departamento", "terreno", "oficina"],
                    "description": "Tipo de propiedad. Omitir si el cliente no lo especifico.",
                },
                "zona": {
                    "type": "string",
                    "description": "Zona, barrio o direccion que menciono el cliente. Omitir si no dio ninguna.",
                },
                "precio_max": {
                    "type": "number",
                    "description": "Presupuesto maximo del cliente, si lo menciono. Omitir si no dio uno.",
                },
            },
            "required": ["operacion"],
        },
    }
]


async def _ejecutar_herramienta(nombre: str, entrada: dict) -> str:
    """Despacha una tool_use de Claude a la funcion real de agent/tools.py."""
    if nombre == "buscar_propiedades":
        return await buscar_propiedades(
            operacion=entrada.get("operacion", ""),
            tipo=entrada.get("tipo"),
            zona=entrada.get("zona"),
            precio_max=entrada.get("precio_max"),
        )
    logger.error(f"Claude pidio una herramienta que no existe: {nombre}")
    return "Esa herramienta no esta disponible."

# El modelo se cambia desde .env, sin tocar el codigo.
#   claude-opus-5     el mas capaz             $5 / $25 por millon de tokens
#   claude-sonnet-5   el balanceado (default)  $3 / $15
#   claude-haiku-4-5  el mas barato y rapido   $1 / $5
# El "or" y no el default de os.getenv: una variable declarada vacia en el .env
# devuelve "" y dejaria al agente sin modelo.
MODELO = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5"

# Es un bot de respuestas cortas: con esfuerzo bajo contesta mas rapido y mas barato.
# Dejalo vacio en el .env para no mandar el parametro.
ESFUERZO = os.getenv("ANTHROPIC_EFFORT", "low").strip()

# WhatsApp son mensajes cortos, pero este tope NO es solo la respuesta: en los modelos
# actuales el razonamiento interno tambien cuenta contra el. Con el margen justo, una
# pregunta que exija pensar un poco deja al agente sin espacio para contestar.
MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS") or "4096")

# Los modelos mas viejos no aceptan output_config. Si la primera llamada falla por eso,
# se reintenta sin el parametro y se recuerda para las siguientes.
_soporta_esfuerzo = True


def cargar_config_prompts() -> dict:
    """Lee toda la configuracion desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """El system prompt: quien es el agente y que sabe del negocio."""
    return cargar_config_prompts().get(
        "system_prompt", "Eres un asistente util. Responde siempre en espanol."
    )


def obtener_mensaje_error() -> str:
    """Que decirle al cliente cuando algo falla de nuestro lado."""
    return cargar_config_prompts().get(
        "error_message",
        "Lo siento, estoy teniendo problemas tecnicos. Por favor intenta de nuevo en unos minutos.",
    )


def obtener_mensaje_fallback() -> str:
    """Que decirle al cliente cuando no se entendio el mensaje."""
    return cargar_config_prompts().get(
        "fallback_message", "Disculpa, no entendi tu mensaje. Podrias reformularlo?"
    )


def _extraer_texto(respuesta) -> str:
    """
    Junta el texto de la respuesta de Claude.

    Ojo: NO se puede hacer respuesta.content[0].text. La respuesta es una lista de
    bloques y el primero no siempre es texto (los modelos que razonan devuelven
    primero un bloque de pensamiento). Hay que filtrar por tipo.
    """
    partes = [bloque.text for bloque in respuesta.content if bloque.type == "text"]
    return "\n".join(p for p in partes if p).strip()


def _es_error_de_esfuerzo(error: Exception) -> bool:
    """
    True solo si el modelo rechazo la llamada POR el parametro output_config/effort.

    Se exige que sea un 400 de peticion invalida y no cualquier error que mencione la
    palabra: un 529 de sobrecarga que la nombre de paso no debe apagar el parametro
    para todo el proceso.
    """
    if getattr(error, "status_code", None) != 400:
        return False
    texto = str(error).lower()
    return "output_config" in texto or "effort" in texto


async def generar_respuesta(mensaje: str, historial: list[dict]) -> tuple[str, bool]:
    """
    Genera una respuesta con Claude.

    Args:
        mensaje: el mensaje nuevo del cliente
        historial: los mensajes anteriores, [{"role": "user"|"assistant", "content": "..."}]

    Returns:
        (texto, es_respuesta_real)

        "es_respuesta_real" es False cuando lo que se devuelve es un aviso tecnico
        (error o fallback) y no una respuesta del agente. main.py lo usa para no
        guardar esos avisos en el historial: si se guardaran, quedarian contaminando
        el contexto de todos los mensajes siguientes.
    """
    global _soporta_esfuerzo

    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback(), False

    mensajes = [{"role": m["role"], "content": m["content"]} for m in historial]
    mensajes.append({"role": "user", "content": mensaje})

    system_prompt = cargar_system_prompt()

    def _extras() -> dict:
        return {"output_config": {"effort": ESFUERZO}} if (_soporta_esfuerzo and ESFUERZO) else {}

    async def _llamar():
        # "mensajes" se muta en el ciclo de tool use de abajo (append), por eso
        # esta funcion no recibe la lista por parametro: siempre lee la ultima version.
        return await client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=mensajes,
            tools=TOOLS_ANTHROPIC,
            **_extras(),
        )

    try:
        respuesta = await _llamar()
    except Exception as e:  # noqa: BLE001
        if _extras() and _es_error_de_esfuerzo(e):
            logger.warning(
                f"El modelo {MODELO} no acepta output_config.effort; se reintenta sin ese parametro."
            )
            _soporta_esfuerzo = False
            try:
                respuesta = await _llamar()
            except Exception as e2:  # noqa: BLE001
                logger.error(f"Error llamando a Claude: {e2}")
                return obtener_mensaje_error(), False
        else:
            logger.error(f"Error llamando a Claude: {e}")
            return obtener_mensaje_error(), False

    # Ciclo de tool use: antes de contestar, Claude puede pedir que se ejecute
    # buscar_propiedades una o mas veces. El tope de vueltas evita quedar en un
    # loop infinito si algo sale mal (ej. el resultado de la herramienta no le
    # alcanza y la vuelve a pedir sin parar).
    vueltas = 0
    while getattr(respuesta, "stop_reason", None) == "tool_use" and vueltas < 3:
        vueltas += 1
        mensajes.append({"role": "assistant", "content": respuesta.content})

        resultados = [
            {
                "type": "tool_result",
                "tool_use_id": bloque.id,
                "content": await _ejecutar_herramienta(bloque.name, bloque.input),
            }
            for bloque in respuesta.content
            if bloque.type == "tool_use"
        ]
        mensajes.append({"role": "user", "content": resultados})

        try:
            respuesta = await _llamar()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error llamando a Claude durante el tool use: {e}")
            return obtener_mensaje_error(), False

    if getattr(respuesta, "stop_reason", None) == "max_tokens":
        logger.warning(
            f"La respuesta se corto por llegar al tope de {MAX_TOKENS} tokens. "
            "Si pasa seguido, sube ANTHROPIC_MAX_TOKENS o acorta el system prompt."
        )

    texto = _extraer_texto(respuesta)
    if not texto:
        logger.warning("Claude devolvio una respuesta sin texto")
        return obtener_mensaje_fallback(), False

    logger.info(
        f"Respuesta generada con {MODELO} "
        f"({respuesta.usage.input_tokens} in / {respuesta.usage.output_tokens} out)"
    )
    return texto, True
