// Referencia para verificar la importación: lo que entiende app/app.js (el parser de
// la app vieja) de comidas/*.md, día a día, en JSON por stdout.
//   node scripts/volcar_comidas.js C:/Proyectos/IA/run-ai
// Carga app.js en un contexto falso, como kiosko-app/build.js del proyecto viejo.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const origen = process.argv[2];
if (!origen) { console.error('uso: node volcar_comidas.js <raiz del proyecto viejo>'); process.exit(2); }

const falso = new Proxy(function () {}, {
  get: (t, k) => (k === 'then' ? undefined : falso),
  set: () => true, apply: () => falso, construct: () => falso,
});
const ctx = {
  document: { querySelector: () => falso, querySelectorAll: () => [], createElement: () => falso },
  fetch: () => Promise.reject(new Error('sin red')),
  Date, Object, Number, String, Math, JSON, RegExp, Array, isNaN, parseFloat,
  console: { ...console, error: () => {} }, setTimeout,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(origen, 'app', 'app.js'), 'utf8'), ctx);

const carpeta = path.join(origen, 'garmin-ai', 'comidas');
const dias = {};
for (const f of fs.readdirSync(carpeta).filter(f => /^\d{4}-W\d{2}\.md$/.test(f)).sort()) {
  for (const d of ctx.parseSemana(fs.readFileSync(path.join(carpeta, f), 'utf8')).dias) {
    dias[d.fecha] = {
      tipo: d.tipo, objetivo: d.objetivo,
      comidas: d.comidas.map(c => ({
        nombre: c.nombre, kcal: c.kcal ?? null, opciones: (c.opciones || []).length,
        elegida: (c.opciones || []).findIndex(o => o.elegida) + 1,
      })),
    };
  }
}
process.stdout.write(JSON.stringify(dias));
