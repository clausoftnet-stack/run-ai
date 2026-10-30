"""Tests del esquema contra el Supabase local (npx supabase start).

Cada test corre en una transacción que se deshace al final: no deja datos.
Si la BD local no está levantada, se saltan en vez de fallar.
"""
import os

import psycopg
import pytest

# la BD local de supabase start; en CI se pasa por variable
DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no hay BD local: npx supabase start")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _comida_con_opciones(db, elegida=None):
    db.execute("insert into plan_semana (semana, desde, hasta) values ('2099-W01', '2099-01-05', '2099-01-11')")
    db.execute("insert into plan_dia (fecha, semana, tipo) values ('2099-01-05', '2099-W01', 'FACIL')")
    cid = db.execute("insert into comida (fecha, orden, nombre) values ('2099-01-05', 1, 'Cena') returning id").fetchone()[0]
    for n in (1, 2, 3):
        db.execute("insert into opcion (comida_id, numero, texto, elegida) values (%s, %s, %s, %s)",
                   (cid, n, f"opción {n}", n == elegida))
    return cid


def _elegidas(db, cid):
    return [r[0] for r in db.execute(
        "select numero from opcion where comida_id = %s and elegida order by numero", (cid,))]


def test_solo_una_elegida_por_comida(db):
    cid = _comida_con_opciones(db, elegida=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("update opcion set elegida = true where comida_id = %s and numero = 2", (cid,))


def test_elegir_mueve_la_marca(db):
    cid = _comida_con_opciones(db, elegida=1)
    assert db.execute("select elegir_opcion(%s, 3::smallint)", (cid,)).fetchone()[0] is True
    assert _elegidas(db, cid) == [3]


def test_elegir_opcion_inexistente_no_toca_nada(db):
    cid = _comida_con_opciones(db, elegida=2)
    assert db.execute("select elegir_opcion(%s, 9::smallint)", (cid,)).fetchone()[0] is False
    assert _elegidas(db, cid) == [2]


def test_semana_con_formato_raro(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("insert into plan_semana (semana, desde, hasta) values ('2099-1', '2099-01-05', '2099-01-11')")


@pytest.mark.parametrize("rol", ["anon", "authenticated"])
def test_rls_sin_acceso_directo(db, rol):
    _comida_con_opciones(db, elegida=1)
    db.execute(f"set local role {rol}")
    # RLS sin políticas: no ve filas y no puede escribir
    assert db.execute("select count(*) from opcion").fetchone()[0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("insert into revision (semana, md) values ('2099-W01', 'x')")


@pytest.mark.parametrize("rol", ["anon", "authenticated"])
def test_rls_no_pueden_elegir(db, rol):
    cid = _comida_con_opciones(db, elegida=1)
    db.execute(f"set local role {rol}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("select elegir_opcion(%s, 2::smallint)", (cid,))
