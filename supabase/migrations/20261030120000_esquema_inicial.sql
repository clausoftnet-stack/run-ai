-- Esquema inicial de Run-AI. Sustituye a los ficheros de garmin-ai/:
--   garmin/data.json        -> garmin_dia, actividad
--   garmin/entrenos/*.json  -> entreno_planificado
--   instrucciones-semana.md -> instruccion_dia
--   comidas/*.md            -> plan_semana, plan_dia, comida, opcion
--   compras/*.md            -> compra, compra_item
--   revision/*.md           -> revision
--   perfil.md, zonas.json   -> perfil, zonas
--
-- Seguridad: RLS activado en todas las tablas y sin políticas. Ni anon ni authenticated
-- leen nada directamente: todo pasa por la api, que usa la secret key (salta RLS) y
-- comprueba el JWT del usuario.

-- ---------------------------------------------------------------- perfil y zonas
-- una sola fila (id = 1). md: el texto de perfil.md; auto: lo que antes iba entre
-- <!-- garmin:auto --> y lo escribe solo el sync
create table perfil (
  id            smallint primary key default 1 check (id = 1),
  md            text not null default '',
  auto          jsonb not null default '{}',
  actualizado   timestamptz not null default now()
);

-- sale de las tablas del test de lactato T0; no se edita a mano
create table zonas (
  id            smallint primary key default 1 check (id = 1),
  datos         jsonb not null,
  actualizado   timestamptz not null default now()
);

-- ---------------------------------------------------------------- lo que registra el reloj
create table garmin_dia (
  fecha             date primary key,
  resting_hr        smallint,
  hrv_ms            smallint,
  sleep_hours       numeric(4,2),
  sleep_score       smallint,
  body_battery_low  smallint,
  body_battery_high smallint,
  stress_avg        smallint,
  steps             integer,
  -- todo lo que devuelve Garmin aplanado; las entradas de julio de 2026 no lo tienen
  todo              jsonb,
  sincronizado      timestamptz not null default now()
);

create table actividad (
  id            bigint primary key,           -- activityId de Garmin
  nombre        text,
  tipo          text not null,                -- running, soccer, strength_training...
  inicio        timestamp not null,           -- hora local, como la da Garmin
  duracion_min  numeric(6,1),
  distancia_km  numeric(7,2),
  fc_media      numeric(5,1),
  fc_max        numeric(5,1),
  kcal          numeric(7,1),
  todo          jsonb,
  -- ruta en Storage de la serie por segundo (antes garmin/raw/)
  serie_path    text,
  sincronizado  timestamptz not null default now()
);
create index actividad_inicio on actividad (inicio);

-- ---------------------------------------------------------------- lo planificado
-- del calendario de Garmin: el título es la sesión, con la hora opcional "7K@R1 [9:00]"
create table entreno_planificado (
  id            bigint generated always as identity primary key,
  fecha         date not null,
  titulo        text not null,
  deporte       text not null,
  hora          time,
  minutos       smallint,
  workout_id    bigint,
  estimado_s    integer,
  estimado_m    numeric(9,1),
  pasos         jsonb,
  unique (fecha, workout_id)
);
create index entreno_planificado_fecha on entreno_planificado (fecha);

-- instrucciones-semana.md: lo que no está en Garmin
create table instruccion_dia (
  id            bigint generated always as identity primary key,
  fecha         date not null,
  tipo          text not null check (tipo in ('actividad', 'nutricion', 'nota')),
  texto         text not null,
  orden         smallint not null default 0
);
create index instruccion_dia_fecha on instruccion_dia (fecha);

-- ---------------------------------------------------------------- plan de comidas
create table plan_semana (
  semana        text primary key check (semana ~ '^\d{4}-W\d{2}$'),
  desde         date not null,
  hasta         date not null,
  -- la cabecera (peso, basal, gasto de base, déficit) tal cual la escribe plan_comidas
  cabecera      jsonb not null default '{}',
  resumen_md    text not null default '',
  generado      timestamptz not null default now()
);

create table plan_dia (
  fecha          date primary key,
  semana         text not null references plan_semana on delete cascade,
  tipo           text not null,               -- CALIDAD, FACIL, DESCANSO...
  mantenimiento  integer,
  objetivo       integer,
  deficit        integer,
  p              smallint,
  ch             smallint,
  ch_gkg         numeric(3,1),
  g              smallint,
  sesiones       jsonb not null default '[]',  -- [{min, tipo, que, kcal, hora}]
  avisos         text[] not null default '{}', -- las "⚠ nutrición: ..."
  notas          text[] not null default '{}'
);
create index plan_dia_semana on plan_dia (semana);

create table comida (
  id            bigint generated always as identity primary key,
  fecha         date not null references plan_dia on delete cascade,
  orden         smallint not null,
  nombre        text not null,               -- Desayuno, Media mañana, Comida...
  kcal          integer,
  p             smallint,
  ch            smallint,
  g             smallint,
  meta          text not null default '',    -- "regla del plan: ... · pre-entreno ..."
  unique (fecha, nombre)
);

create table opcion (
  id            bigint generated always as identity primary key,
  comida_id     bigint not null references comida on delete cascade,
  numero        smallint not null check (numero > 0),
  texto         text not null,
  p             numeric(5,1),
  ch            numeric(5,1),
  g             numeric(5,1),
  elegida       boolean not null default false,
  unique (comida_id, numero)
);
-- el contrato del (E): como mucho una opción elegida por comida
create unique index opcion_una_elegida on opcion (comida_id) where elegida;

-- ---------------------------------------------------------------- compra
create table compra (
  semana        text primary key references plan_semana on delete cascade,
  -- el documento entero (avisos de despensa, lo que no se elige...); los items
  -- de abajo son solo las filas de las tablas, para la checklist del móvil
  md            text not null default '',
  total_eur     numeric(7,2),
  generado      timestamptz not null default now()
);

create table compra_item (
  id            bigint generated always as identity primary key,
  semana        text not null references compra on delete cascade,
  seccion       text not null,               -- Frutería y verdura, Carnicería...
  orden         smallint not null,
  producto      text not null,
  cantidad      text not null,
  precio_eur    numeric(7,2),
  para          text not null default '',
  comprado      boolean not null default false
);
create index compra_item_semana on compra_item (semana);

-- ---------------------------------------------------------------- revisión
-- prosa: la escriben revision.py y el agente, la app solo la pinta
create table revision (
  semana        text primary key check (semana ~ '^\d{4}-W\d{2}$'),
  md            text not null,
  generado      timestamptz not null default now()
);

-- ---------------------------------------------------------------- RLS
alter table perfil              enable row level security;
alter table zonas               enable row level security;
alter table garmin_dia          enable row level security;
alter table actividad           enable row level security;
alter table entreno_planificado enable row level security;
alter table instruccion_dia     enable row level security;
alter table plan_semana         enable row level security;
alter table plan_dia            enable row level security;
alter table comida              enable row level security;
alter table opcion              enable row level security;
alter table compra              enable row level security;
alter table compra_item         enable row level security;
alter table revision            enable row level security;

-- ---------------------------------------------------------------- elegir opción
-- Mueve la (E). En dos pasos porque el índice parcial no se puede diferir: un solo
-- UPDATE podría pasar por un estado con dos elegidas. Si la opción no existe no toca
-- nada (devuelve false): dejar la comida sin ninguna elegida es peor que no hacer caso.
create function elegir_opcion(p_comida_id bigint, p_numero smallint)
returns boolean
language plpgsql
set search_path = public
as $$
begin
  if not exists (select 1 from opcion where comida_id = p_comida_id and numero = p_numero) then
    return false;
  end if;
  update opcion set elegida = false where comida_id = p_comida_id and elegida;
  update opcion set elegida = true  where comida_id = p_comida_id and numero = p_numero;
  return true;
end;
$$;
revoke execute on function elegir_opcion(bigint, smallint) from public, anon, authenticated;
