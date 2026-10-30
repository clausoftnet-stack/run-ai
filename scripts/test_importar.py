"""Tests de los parsers de la migración, sin BD. Los casos son trozos reales de los .md."""
import datetime as dt

from importar import parse_compra, parse_instrucciones, parse_semana, separar_hora

SEMANA = """# Plan de 5 comidas · 2026-10-26 → 2026-11-01

- Peso **89,6 kg** · basal medido en el test T0 **1.558 kcal** · gasto de base sin ejercicio **1.988 kcal**
- Déficit objetivo: 493 kcal/día (0,5 % del peso por semana)

## 2026-10-26 lunes — CALIDAD

- 62' running: `6'@R1-4x(2000m@R2&3'@R1)` ≈831 kcal · a las 15:30
- ⚠ nutrición: Comida -> Almuerzo (Fajitas pollo y queso)
- Mantenimiento 2.819 kcal · objetivo **2.552 kcal** (déficit 267)
- **P 179 g · CH 297 g (3,3 g/kg) · G 72 g**

| Comida | kcal | P (g) | CH (g) | G (g) |
|---|---:|---:|---:|---:|
| Desayuno | 523 | 36 | 59 | 16 |
| Comida | 621 | 36 | 77 | 19 |

### Recetas

**Desayuno** — 523 kcal · P 36 · CH 59 · G 16
1. Tostada A: pan 70 g (P 36 · CH 57 · G 16)
2. (E) Tostada B: pan 60 g (P 37 · CH 58 · G 16)

**Comida** — 621 kcal · P 36 · CH 77 · G 19 · *regla del plan: cuscús*
1. (E) Cuscús con salmón (P 38 · CH 74 · G 18)
"""


def test_parse_semana():
    s = parse_semana(SEMANA.replace("\n", "\r\n"))   # Windows: \r\n no debe romper nada
    assert s["cabecera"]["peso"] == 89.6 and s["cabecera"]["basal"] == 1558
    [d] = s["dias"]
    assert (d["fecha"], d["tipo"], d["objetivo"], d["deficit"]) == ("2026-10-26", "calidad", 2552, 267)
    assert d["macros"] == {"p": 179, "ch": 297, "chgkg": 3.3, "g": 72}
    assert d["sesiones"] == [{"min": 62, "tipo": "running", "que": "6'@R1-4x(2000m@R2&3'@R1)", "kcal": 831, "hora": "15:30"}]
    assert d["avisos"] == ["nutrición: Comida -> Almuerzo (Fajitas pollo y queso)"]
    desayuno, comida = d["comidas"]
    assert [o["elegida"] for o in desayuno["opciones"]] == [False, True]
    # los macros de la opción salen del texto
    assert desayuno["opciones"][1] == {"numero": 2, "elegida": True, "texto": "Tostada B: pan 60 g",
                                       "p": 37, "ch": 58, "g": 16}
    assert comida["meta"].endswith("*regla del plan: cuscús*")


def test_parse_compra():
    c = parse_compra("""# Compra

## Carnicería

| Producto | Cantidad | Precio | Para |
|---|---:|---:|---|
| Solomillo de cerdo (0,67 kg) | 1 pieza | 5,23 € | cena sáb (130 g) |

**Total: 1.005,23 €**
""")
    assert c["total_eur"] == 1005.23
    assert c["items"] == [{"seccion": "Carnicería", "orden": 1, "producto": "Solomillo de cerdo (0,67 kg)",
                           "cantidad": "1 pieza", "precio_eur": 5.23, "para": "cena sáb (130 g)"}]


def test_parse_instrucciones_formatos_viejo_y_nuevo():
    ins = parse_instrucciones("""# Instrucciones

Texto de cabecera que no es de ninguna semana.

## Semana de [14/9/2026 al 20/9/2026]

Martes - 6K@R1
  - Cena fuera

## Semana de [26/10/2026 al 01/11/2026]

Notas Semana -> media maratón el 8
Lunes
  Actividades
    - 8K@R1
  Nutricion
    - Comida -> Cuscús
""")
    assert [(i["fecha"], i["tipo"], i["texto"]) for i in ins] == [
        (dt.date(2026, 9, 15), "actividad", "6K@R1"),
        (dt.date(2026, 9, 15), "nutricion", "Cena fuera"),
        (dt.date(2026, 10, 26), "nota", "Notas Semana -> media maratón el 8"),
        (dt.date(2026, 10, 26), "actividad", "8K@R1"),
        (dt.date(2026, 10, 26), "nutricion", "Comida -> Cuscús"),
    ]


def test_separar_hora():
    assert separar_hora("7K@R1 [9:00]") == ("7K@R1", "09:00")
    assert separar_hora("Futbol Sala (60')") == ("Futbol Sala (60')", None)
