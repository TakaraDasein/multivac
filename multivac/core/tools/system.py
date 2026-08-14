"""Estado del sistema: batería, recursos, hora."""

from __future__ import annotations

import datetime as dt
import shutil

import psutil

from .registry import tool

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@tool()
def hora_y_fecha() -> str:
    """Dice la hora y la fecha actuales."""
    ahora = dt.datetime.now()
    return (
        f"Son las {ahora.hour} y {ahora.minute:02d}, "
        f"{DIAS[ahora.weekday()]} {ahora.day} de {MESES[ahora.month - 1]} de {ahora.year}."
    )


@tool()
def estado_bateria() -> str:
    """Consulta el nivel de batería y si está cargando."""
    bat = psutil.sensors_battery()
    if bat is None:
        return "Este equipo no reporta batería."
    if bat.power_plugged:
        return f"Batería al {round(bat.percent)} por ciento, conectado a la corriente."
    if bat.secsleft and bat.secsleft > 0:
        horas, minutos = divmod(bat.secsleft // 60, 60)
        return (
            f"Batería al {round(bat.percent)} por ciento, "
            f"quedan unas {horas} horas y {minutos} minutos."
        )
    return f"Batería al {round(bat.percent)} por ciento."


@tool()
def estado_sistema() -> str:
    """Informa de uso de CPU, memoria y disco."""
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    disco = shutil.disk_usage("/")
    return (
        f"CPU al {cpu:.0f} por ciento. "
        f"Memoria: {mem.used / 2**30:.1f} de {mem.total / 2**30:.0f} gigas usados. "
        f"Disco: {disco.free / 2**30:.0f} gigas libres."
    )


@tool()
def procesos_pesados() -> str:
    """Lista los procesos que más CPU están consumiendo."""
    psutil.cpu_percent(interval=None)  # primera lectura: siempre 0, se descarta
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(interval=None)
        except psutil.Error:
            pass

    import time

    time.sleep(0.5)
    medidos: list[tuple[float, str]] = []
    for p in procs:
        try:
            medidos.append((p.cpu_percent(interval=None), p.info["name"] or "?"))
        except psutil.Error:
            continue
    medidos.sort(reverse=True)
    top = [f"{nombre} al {uso:.0f} por ciento" for uso, nombre in medidos[:3] if uso > 1]
    return "Lo que más consume: " + ", ".join(top) if top else "Nada consume CPU ahora."
