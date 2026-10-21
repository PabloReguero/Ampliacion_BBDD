import math
import random
import sys
from datetime import datetime, timedelta, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

URL = "http://localhost:8086"
TOKEN = "z091WTUTs-WwQeMAJYCTxM8QDfsvDYuTWDY4Pg_-_iBgKfidbng1tIXPmmgXTD0DotJ9C4kGTutSRPgTEzsP9w=="
ORG = "loyola_karting"
BUCKET = "karting_telemetry"

MEASUREMENT = "telemetria_kart"
TANDA_ID = "test_setup_a"
HZ = 10                      # muestras por segundo
DT = 1.0 / HZ
SECTOR_S = 20                # duracion de cada sector (s)
LAP_S = 3 * SECTOR_S         # vuelta de 60 s
TURNO_S = 360                # cada turno dura 6 min = 6 vueltas
N_TURNOS = 5                 # 5 turnos x 6 min = 30 min
CHUNK = 5000                 # puntos por lote de escritura
SEED = 2026

KARTS = ["kart_01", "kart_02", "kart_03", "kart_04", "kart_05"]
PILOTOS = ["P1", "P2", "P3", "P4", "P5"]
# Factor de habilidad del piloto (variacion de pilotaje: +-3 %)
HABILIDAD = {"P1": 1.00, "P2": 1.01, "P3": 0.97, "P4": 1.02, "P5": 0.98}
# Pequena diferencia propia de cada kart (+-1 %)
FACTOR_KART = {"kart_01": 1.00, "kart_02": 1.005, "kart_03": 1.00, "kart_04": 0.995, "kart_05": 1.01}

# Perfil de la vuelta: (inicio_fraccion, fin_fraccion, velocidad_base_kmh, gas_pct, freno_bar) por sector
# Cada sector se divide en 3 tramos.
PERFIL = {
    "sector_1": [(0.0, 0.3, 70, 85, 0.0), (0.3, 0.6, 52, 35, 6.0), (0.6, 1.0, 66, 80, 0.0)],
    "sector_2": [(0.0, 0.2, 72, 90, 0.0), (0.2, 0.75, 96, 100, 0.0), (0.75, 1.0, 58, 20, 9.0)],
    "sector_3": [(0.0, 0.15, 64, 75, 0.0), (0.15, 0.7, 104, 100, 0.0), (0.7, 1.0, 60, 25, 8.0)],
}
SECTORES = ["sector_1", "sector_2", "sector_3"]
# Anomalia de chasis: kart_03 en sector_2 pierde un 12 % de velocidad con cualquier piloto
ANOMALIA_KART, ANOMALIA_SECTOR, ANOMALIA_FACTOR = "kart_03", "sector_2", 0.88


def tramo(sector, frac):
    for ini, fin, vel, gas, freno in PERFIL[sector]:
        if ini <= frac < fin:
            return vel, gas, freno
    return PERFIL[sector][-1][2:]


def piloto_de(kart_idx, turno):
    """Rotacion tipo cuadrado latino: en cada turno cada piloto conduce un kart distinto."""
    return PILOTOS[(kart_idx - turno) % 5]


def generar_puntos(t0, rng):
    puntos = []
    for turno in range(N_TURNOS):
        for k_idx, kart in enumerate(KARTS):
            piloto = piloto_de(k_idx, turno)
            vel = 40.0
            temp = 470.0 + rng.uniform(-5, 5)
            for i in range(TURNO_S * HZ):
                t_rel = i * DT
                t_vuelta = t_rel % LAP_S
                sector = SECTORES[int(t_vuelta // SECTOR_S)]
                frac = (t_vuelta % SECTOR_S) / SECTOR_S
                vel_base, gas_base, freno_base = tramo(sector, frac)

                factor = HABILIDAD[piloto] * FACTOR_KART[kart]
                if kart == ANOMALIA_KART and sector == ANOMALIA_SECTOR:
                    factor *= ANOMALIA_FACTOR
                objetivo = vel_base * factor
                vel += (objetivo - vel) * 0.12 + rng.gauss(0, 0.35)
                vel = max(25.0, vel)

                gas = min(100.0, max(0.0, gas_base + rng.gauss(0, 2.0)))
                freno = max(0.0, freno_base + rng.gauss(0, 0.4)) if freno_base > 0 else 0.0
                rpm = int(min(13500, max(4500, 4500 + vel * 78 + gas * 18 + rng.gauss(0, 60))))
                temp_objetivo = 400 + 1.95 * gas + 0.008 * (rpm - 9000)
                temp += (temp_objetivo - temp) * 0.035 + rng.gauss(0, 0.8)

                ts = t0 + timedelta(seconds=turno * TURNO_S + t_rel)
                puntos.append(
                    Point(MEASUREMENT)
                    .tag("kart_id", kart).tag("piloto_id", piloto)
                    .tag("tanda_id", TANDA_ID).tag("sector", sector)
                    .field("velocidad_kmh", float(round(vel, 2)))
                    .field("rpm", int(rpm))
                    .field("temp_escape_c", float(round(temp, 1)))
                    .field("apertura_gas_pct", float(round(gas, 1)))
                    .field("presion_freno_bar", float(round(freno, 2)))
                    .time(ts, WritePrecision.MS)
                )
    return puntos


def main():
    dry = "--dry-run" in sys.argv
    rng = random.Random(SEED)
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    t0 = ahora - timedelta(minutes=N_TURNOS * TURNO_S / 60)   # empieza hace 30 min
    puntos = generar_puntos(t0, rng)
    print(f"Puntos generados: {len(puntos)}  (esperados: {N_TURNOS*TURNO_S*HZ*len(KARTS)})")

    if not dry:
        with InfluxDBClient(url=URL, token=TOKEN, org=ORG, timeout=60_000) as client:
            write_api = client.write_api(write_options=SYNCHRONOUS)
            for i in range(0, len(puntos), CHUNK):
                write_api.write(bucket=BUCKET, org=ORG, record=puntos[i:i + CHUNK])
                print(f"  lote {i // CHUNK + 1}/{math.ceil(len(puntos) / CHUNK)} escrito")
        print("Ingesta completada.")

    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("\n=== VENTANAS PARA LAS CONSULTAS FLUX ===")
    print(f"Inicio sesion : {fmt(t0)}")
    print(f"Fin sesion    : {fmt(t0 + timedelta(seconds=N_TURNOS * TURNO_S))}")
    v0 = t0 + timedelta(seconds=LAP_S)          # vuelta 2 del turno 1
    print(f"Vuelta 2 turno 1 (P1 vs P2) -> start: {fmt(v0)}  stop: {fmt(v0 + timedelta(seconds=LAP_S))}")
    print("Turno 1: P1 conduce kart_01 y P2 conduce kart_02")


if __name__ == "__main__":
    main()
