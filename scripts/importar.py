#!/usr/bin/env python3
"""Migración única: de los ficheros del proyecto viejo (garmin-ai/) a la BD.

    py scripts/importar.py --origen C:/Proyectos/IA/run-ai            # BD local
    py scripts/importar.py --origen ... --db "$SUPABASE_DB_URL"       # la de la nube

Es una recarga completa: vacía las tablas y las vuelve a llenar en una sola
transacción. Si algo falla a medias, la BD se queda como estaba.

El parser de comidas/*.md es un port de parseSemana/parseDia de app/app.js (la
referencia), y el de instrucciones-semana.md, de _parse_plan de plan_comidas.py.
verificar() compara el resultado con lo que saca el parser de JS sobre los mismos
ficheros: si no cuadran, no se da por buena la importación.

shortcut: las series por segundo (garmin/raw/) no se suben aquí, van a Storage con el
sync de la fase 4.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DB_LOCAL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
RE_SEMANA_MD = re.compile(r"^\d{4}-W\d{2}\.md$")
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


# ---------------------------------------------------------------- utilidades
def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower().strip()


def num_es(s):
    """'1.988' -> 1988, '3,3' -> 3.3 (como numEs de app.js)."""
    if s is None:
        return None
    return float(str(s).replace(".", "").replace(",", "."))


def entero(s):
    n = num_es(s)
    return None if n is None else round(n)


def m1(txt, patron):
    m = re.search(patron, txt, re.M)
    return m.group(1) if m else None


def semanas_md(carpeta: Path):
    return sorted(p for p in carpeta.glob("*.md") if RE_SEMANA_MD.match(p.name)) if carpeta.is_dir() else []


# ---------------------------------------------------------------- comidas/*.md
RE_MACROS_OPCION = re.compile(r"\s*\(P ([\d.,]+) · CH ([\d.,]+) · G ([\d.,]+)\)\s*$")


def parse_semana(txt: str) -> dict:
    txt = txt.replace("\r\n", "\n")
    corte = re.search(r"^## \d{4}-\d{2}-\d{2}", txt, re.M)
    cab = txt[:corte.start()] if corte else txt
    cabecera = {
        "peso": num_es(m1(cab, r"Peso \*\*([\d.,]+) kg\*\*")),
        "basal": num_es(m1(cab, r"test T0\s*\*\*([\d.,]+) kcal\*\*")),
        "base": num_es(m1(cab, r"gasto de base sin ejercicio \*\*([\d.,]+) kcal\*\*")),
        "deficit": num_es(m1(cab, r"D[ée]ficit objetivo: ([\d.,]+) kcal")),
        "coste": (m1(cab, r"^- (Coste medido[^\n]*)") or ""),
    }
    cierre = re.split(r"\n---\n", txt)[-1]
    resumen = cierre.strip() if re.search(r"D[ée]ficit real", cierre) and not re.search(r"^## ", cierre, re.M) else ""

    marcas = list(re.finditer(r"^## (\d{4}-\d{2}-\d{2}) (\S+) — (\S+)\s*$", txt, re.M))
    dias = []
    for i, mk in enumerate(marcas):
        cuerpo = txt[mk.end():marcas[i + 1].start() if i + 1 < len(marcas) else len(txt)]
        cuerpo = re.split(r"\n---\n", cuerpo)[0]
        dias.append({"fecha": mk.group(1), "nombre": mk.group(2), "tipo": sin_tildes(mk.group(3)),
                     **parse_dia(cuerpo)})
    return {"cabecera": cabecera, "dias": dias, "resumen": resumen}


def parse_dia(cuerpo: str) -> dict:
    partes = re.split(r"^### Recetas\s*$", cuerpo, maxsplit=1, flags=re.M)
    antes, recetas = partes[0], (partes[1] if len(partes) > 1 else "")

    sesiones = [{"min": int(s[1]), "tipo": s[2], "que": s[3], "kcal": num_es(s[4]), "hora": s[5] or ""}
                for s in re.finditer(r"^- (\d+)['′] (\S+): `(.+?)` ≈([\d.,]+) kcal(?: · a las (\d\d:\d\d))?",
                                     antes, re.M)]
    mant = re.search(r"^- Mantenimiento ([\d.,]+) kcal · objetivo \*\*([\d.,]+) kcal\*\* \(d[ée]ficit ([\d.,-]+)\)",
                     antes, re.M)
    mac = re.search(r"^- \*\*P (\d+) g · CH (\d+) g \(([\d,]+) g/kg\) · G (\d+) g\*\*", antes, re.M)
    avisos = [re.sub(r"^- ⚠\s*", "", a) for a in re.findall(r"^- ⚠[^\n]*", antes, re.M)]
    notas = [a[2:] for a in re.findall(r"^- sin carrera[^\n]*", antes, re.M)]

    comidas = [{"nombre": t[1].strip(), "kcal": entero(t[2]), "p": int(t[3]), "ch": int(t[4]), "g": int(t[5]),
                "meta": "", "opciones": []}
               for t in re.finditer(r"^\|\s*([^|\d\-][^|]*?)\s*\|\s*([\d.,]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
                                    antes, re.M)]

    bloques = list(re.finditer(r"^\*\*(.+?)\*\*\s*(?:—|-)?\s*(.*)$", recetas, re.M))
    for i, b in enumerate(bloques):
        trozo = recetas[b.end():bloques[i + 1].start() if i + 1 < len(bloques) else len(recetas)]
        opciones = []
        for o in re.finditer(r"^\s*(\d+)\.\s+(.*)$", trozo, re.M):
            texto, elegida = o.group(2), o.group(2).startswith("(E) ")
            texto = re.sub(r"^\(E\)\s*", "", texto)
            mm = RE_MACROS_OPCION.search(texto)
            opciones.append({"numero": int(o.group(1)), "elegida": elegida,
                             "texto": texto[:mm.start()] if mm else texto,
                             "p": num_es(mm[1]) if mm else None, "ch": num_es(mm[2]) if mm else None,
                             "g": num_es(mm[3]) if mm else None})
        nombre, meta = b.group(1).strip(), b.group(2).strip()
        c = next((c for c in comidas if sin_tildes(c["nombre"]) == sin_tildes(nombre)), None)
        if c:
            c["opciones"], c["meta"] = opciones, meta
        elif opciones:
            comidas.append({"nombre": nombre, "kcal": None, "p": None, "ch": None, "g": None,
                            "meta": meta, "opciones": opciones})

    return {
        "sesiones": sesiones, "avisos": avisos, "notas": notas, "comidas": comidas,
        "mantenimiento": entero(mant[1]) if mant else None,
        "objetivo": entero(mant[2]) if mant else None,
        "deficit": entero(mant[3]) if mant else None,
        "macros": {"p": int(mac[1]), "ch": int(mac[2]), "chgkg": num_es(mac[3]), "g": int(mac[4])} if mac else None,
    }


def lunes_de_semana(semana: str) -> dt.date:
    a, w = semana.split("-W")
    return dt.date.fromisocalendar(int(a), int(w), 1)


# ---------------------------------------------------------------- compras/*.md
def parse_compra(txt: str) -> dict:
    txt = txt.replace("\r\n", "\n")
    items, seccion, orden = [], "", 0
    for linea in txt.split("\n"):
        if (h := re.match(r"^## (.+)$", linea)):
            seccion = h.group(1).strip()
            continue
        # filas de producto: tabla con un precio en euros; la cabecera y los guiones no lo llevan
        if linea.startswith("|") and re.search(r"\d[\d.,]*\s*€", linea) and not re.match(r"^\|[\s:|-]+\|", linea):
            celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
            if len(celdas) < 3:
                continue
            orden += 1
            items.append({"seccion": seccion, "orden": orden, "producto": celdas[0], "cantidad": celdas[1],
                          "precio_eur": num_es(m1(celdas[2], r"([\d.,]+)\s*€")),
                          "para": celdas[3] if len(celdas) > 3 else ""})
    return {"md": txt, "total_eur": num_es(m1(txt, r"\*\*Total:\s*([\d.,]+)\s*€")), "items": items}


# ---------------------------------------------------------------- instrucciones-semana.md
def parse_instrucciones(txt: str) -> list[dict]:
    """[{fecha, tipo, texto, orden}]. Mismas reglas que _parse_plan de plan_comidas.py:
    "Martes - 6K@R1" es actividad; un guion indentado bajo el día es nutrición salvo
    que caiga en la sección "Actividades". Lo demás dentro de una semana (mensajes del
    entrenador) se guarda como nota del lunes para no perderlo."""
    out, lunes, dia, seccion = [], None, None, None

    def add(fecha, tipo, texto):
        out.append({"fecha": fecha, "tipo": tipo, "texto": texto, "orden": len(out)})

    for linea in txt.replace("\r\n", "\n").split("\n"):
        plano = sin_tildes(linea)
        if (m := re.match(r"##\s*Semana\s+de\s*\[?\s*(\d+)/(\d+)/(\d+)", linea, re.I)):
            d, mes, a = (int(x) for x in m.groups())
            inicio = dt.date(a, mes, d)
            lunes, dia = inicio - dt.timedelta(days=inicio.weekday()), None
            continue
        if linea.startswith(("#", "---")):
            dia = None
            if linea.startswith("#"):
                lunes = None   # otra cabecera: se acaba la semana
            continue
        if lunes and (m := re.match(rf"({'|'.join(DIAS)})\s*(?:[-–:]\s*(.+))?$", plano, re.I)):
            dia, seccion = lunes + dt.timedelta(days=DIAS.index(m.group(1).lower())), None
            if m.group(2):
                add(dia, "actividad", linea.strip()[m.start(2):].strip())
            continue
        if dia and (m := re.fullmatch(r"(actividades|nutricion)\s*:?", plano, re.I)):
            seccion = m.group(1).lower()
            continue
        if dia and linea[:1] in " \t" and (m := re.match(r"[-*]\s*(.+)", linea.strip())):
            add(dia, "actividad" if seccion == "actividades" else "nutricion", m.group(1).strip())
            continue
        if lunes and linea.strip():
            add(lunes, "nota", linea.strip())
    return out


def separar_hora(titulo: str) -> tuple[str, str | None]:
    """'7K@R1 [9:00]' -> ('7K@R1', '09:00')."""
    m = re.search(r"\s*\[(\d{1,2}):(\d{2})\]\s*$", titulo)
    if not m:
        return titulo.strip(), None
    return titulo[:m.start()].strip(), f"{int(m[1]):02d}:{m[2]}"


# ---------------------------------------------------------------- carga
TABLAS = ["opcion", "comida", "plan_dia", "compra_item", "compra", "revision", "plan_semana",
          "entreno_planificado", "instruccion_dia", "actividad", "garmin_dia", "zonas", "perfil"]


def importar(origen: Path, conn) -> dict:
    ga = origen / "garmin-ai"
    cuenta = {}
    with conn.transaction():
        conn.execute("truncate " + ", ".join(TABLAS) + " restart identity cascade")

        # perfil y zonas
        conn.execute("insert into perfil (md) values (%s)", ((ga / "perfil.md").read_text(encoding="utf-8"),))
        conn.execute("insert into zonas (datos) values (%s)",
                     (Jsonb(json.loads((ga / "zonas.json").read_text(encoding="utf-8"))),))

        # lo del reloj
        data = json.loads((ga / "garmin" / "data.json").read_text(encoding="utf-8"))
        for w in data.get("wellness", {}).values():
            conn.execute(
                """insert into garmin_dia (fecha, resting_hr, hrv_ms, sleep_hours, sleep_score,
                   body_battery_low, body_battery_high, stress_avg, steps, todo)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (w["date"], w.get("resting_hr"), w.get("hrv_ms"), w.get("sleep_hours"), w.get("sleep_score"),
                 w.get("body_battery_low"), w.get("body_battery_high"), w.get("stress_avg"), w.get("steps"),
                 Jsonb(w["all"]) if w.get("all") else None))
        for a in data.get("activities", {}).values():
            conn.execute(
                """insert into actividad (id, nombre, tipo, inicio, duracion_min, distancia_km,
                   fc_media, fc_max, kcal, todo) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (a["id"], a.get("name"), a["type"], a["start"], a.get("duration_min"), a.get("distance_km"),
                 a.get("avg_hr"), a.get("max_hr"), a.get("calories"), Jsonb(a["all"]) if a.get("all") else None))
        cuenta["garmin_dia"], cuenta["actividad"] = len(data.get("wellness", {})), len(data.get("activities", {}))

        # entrenos del calendario de Garmin
        n = 0
        for f in sorted((ga / "garmin" / "entrenos").glob("*.json")):
            for e in json.loads(f.read_text(encoding="utf-8")):
                titulo, hora = separar_hora(e["titulo"])
                conn.execute(
                    """insert into entreno_planificado (fecha, titulo, deporte, hora, workout_id,
                       estimado_s, estimado_m, pasos) values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (e["fecha"], titulo, e.get("deporte") or "running", hora, e.get("workoutId"),
                     e.get("estimado_s"), e.get("estimado_m"), Jsonb(e.get("pasos"))))
                n += 1
        cuenta["entreno_planificado"] = n

        # instrucciones
        ins = parse_instrucciones((ga / "instrucciones-semana.md").read_text(encoding="utf-8"))
        for i in ins:
            conn.execute("insert into instruccion_dia (fecha, tipo, texto, orden) values (%s,%s,%s,%s)",
                         (i["fecha"], i["tipo"], i["texto"], i["orden"]))
        cuenta["instruccion_dia"] = len(ins)

        # plan de comidas
        cuenta.update(plan_dia=0, comida=0, opcion=0)
        for f in semanas_md(ga / "comidas"):
            semana = f.stem
            sem = parse_semana(f.read_text(encoding="utf-8"))
            lunes = lunes_de_semana(semana)
            conn.execute("insert into plan_semana (semana, desde, hasta, cabecera, resumen_md) values (%s,%s,%s,%s,%s)",
                         (semana, lunes, lunes + dt.timedelta(days=6), Jsonb(sem["cabecera"]), sem["resumen"]))
            for d in sem["dias"]:
                mac = d["macros"] or {}
                conn.execute(
                    """insert into plan_dia (fecha, semana, tipo, mantenimiento, objetivo, deficit,
                       p, ch, ch_gkg, g, sesiones, avisos, notas) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (d["fecha"], semana, d["tipo"], d["mantenimiento"], d["objetivo"], d["deficit"],
                     mac.get("p"), mac.get("ch"), mac.get("chgkg"), mac.get("g"),
                     Jsonb(d["sesiones"]), d["avisos"], d["notas"]))
                cuenta["plan_dia"] += 1
                for orden, c in enumerate(d["comidas"], 1):
                    cid = conn.execute(
                        """insert into comida (fecha, orden, nombre, kcal, p, ch, g, meta)
                           values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (d["fecha"], orden, c["nombre"], c["kcal"], c["p"], c["ch"], c["g"], c["meta"])).fetchone()[0]
                    cuenta["comida"] += 1
                    for o in c["opciones"]:
                        conn.execute(
                            """insert into opcion (comida_id, numero, texto, p, ch, g, elegida)
                               values (%s,%s,%s,%s,%s,%s,%s)""",
                            (cid, o["numero"], o["texto"], o["p"], o["ch"], o["g"], o["elegida"]))
                        cuenta["opcion"] += 1

        # compras: la semana tiene que existir en plan_semana
        cuenta.update(compra=0, compra_item=0)
        for f in semanas_md(ga / "compras"):
            c = parse_compra(f.read_text(encoding="utf-8"))
            conn.execute("insert into compra (semana, md, total_eur) values (%s,%s,%s)", (f.stem, c["md"], c["total_eur"]))
            for it in c["items"]:
                conn.execute(
                    """insert into compra_item (semana, seccion, orden, producto, cantidad, precio_eur, para)
                       values (%s,%s,%s,%s,%s,%s,%s)""",
                    (f.stem, it["seccion"], it["orden"], it["producto"], it["cantidad"], it["precio_eur"], it["para"]))
            cuenta["compra"] += 1
            cuenta["compra_item"] += len(c["items"])

        # revisiones
        revs = semanas_md(ga / "revision")
        for f in revs:
            conn.execute("insert into revision (semana, md) values (%s,%s)", (f.stem, f.read_text(encoding="utf-8")))
        cuenta["revision"] = len(revs)
    return cuenta


# ---------------------------------------------------------------- verificación
def verificar(origen: Path, conn) -> list[str]:
    """Compara la BD con lo que saca el parser de JS (app/app.js) de los mismos .md."""
    js = Path(__file__).with_name("volcar_comidas.js")
    ref = json.loads(subprocess.run(["node", str(js), str(origen)], capture_output=True, text=True,
                                    encoding="utf-8", check=True).stdout)
    errores = []
    for fecha, r in ref.items():
        d = conn.execute("select tipo, objetivo from plan_dia where fecha = %s", (fecha,)).fetchone()
        if not d:
            errores.append(f"{fecha}: no está en la BD")
            continue
        if (d[0], d[1]) != (r["tipo"], r["objetivo"]):
            errores.append(f"{fecha}: tipo/objetivo {d} != {(r['tipo'], r['objetivo'])}")
        bd = conn.execute(
            """select c.nombre, c.kcal, count(o.id),
                      coalesce(min(o.numero) filter (where o.elegida), 0)
               from comida c left join opcion o on o.comida_id = c.id
               where c.fecha = %s group by c.id order by c.orden""", (fecha,)).fetchall()
        esperado = [(c["nombre"], c["kcal"], c["opciones"], c["elegida"]) for c in r["comidas"]]
        if [tuple(x) for x in bd] != [tuple(x) for x in esperado]:
            errores.append(f"{fecha}: comidas {bd} != {esperado}")
    return errores


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origen", type=Path, required=True, help="raíz del proyecto viejo (la que tiene garmin-ai/)")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", DB_LOCAL))
    a = ap.parse_args()
    if not (a.origen / "garmin-ai").is_dir():
        sys.exit(f"{a.origen} no tiene garmin-ai/")

    with psycopg.connect(a.db) as conn:
        for tabla, n in importar(a.origen, conn).items():
            print(f"{tabla:22} {n}")
        errores = verificar(a.origen, conn)
    if errores:
        print(f"\n{len(errores)} DIFERENCIAS con el parser de app.js:", *errores[:20], sep="\n  ")
        sys.exit(1)
    print("\nverificado: cuadra día a día con el parser de app.js")


if __name__ == "__main__":
    main()
