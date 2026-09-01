# Alerta Netsubasta

Script en Python para monitorizar [netsubasta.com/vehiculos](https://netsubasta.com/vehiculos) y avisar por terminal cuando aparecen **vehículos nuevos** que cumplen unos filtros configurables (tipo, estado, ubicación, etc.).

Pensado para ejecutarse manualmente o de forma automática con **crontab**.

## Requisitos

- **Python 3.6+** (solo biblioteca estándar, no hace falta instalar paquetes con pip)
- Conexión a internet

Comprobar que Python está disponible:

```bash
python3 --version
```

## Estructura del proyecto

```
Subastas/
├── alerta_subastas.py   # Script principal
├── config.env           # Configuración de filtros (editar aquí)
├── data/
│   └── seen_ids.json    # Historial de vehículos ya vistos (se crea solo)
└── README.md
```

## Configuración (`config.env`)

Todos los filtros se definen en el archivo **`config.env`**, en la raíz del proyecto. Ábrelo con cualquier editor de texto:

```bash
nano config.env
```

### Opciones disponibles

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `BASE_URL` | URL del sitio | `https://netsubasta.com` |
| `TIPOS` | IDs de tipo de vehículo (separados por coma) | `10,13` |
| `ESTADOS` | Estados permitidos (nombres como en la web) | `Averiado,Sin daños` |
| `UBICACIONES` | Provincias permitidas (como aparecen en el listado) | `León,Madrid,Valladolid` |
| `SEEN_FILE` | Ruta al archivo de historial | `data/seen_ids.json` |
| `DETAIL_DELAY` | Segundos de pausa entre peticiones de detalle | `0.5` |
| `FETCH_DETAILS` | Obtener comentarios de la ficha (`true` / `false`) | `true` |

### Valores de referencia (netsubasta.com)

**Tipos de vehículo** (`typologies_id`):

| ID | Tipo |
|----|------|
| `10` | Motos y scooters |
| `13` | Turismos y vehículos comerciales |
| `7` | Camiones y tractoras |

**Estados** (`sources_id`):

| ID | Estado |
|----|--------|
| `13` | Averiado |
| `15` | Con daños internos |
| `6` | Con daños exteriores |
| `17` | Falta de uso |
| `7` | Sin daños |
| `10` | Accidentado |
| `14` | Con exceso de kilometraje |
| `12` | Incendiado |
| `18` | Inundado |

En `config.env` se usan los **nombres** (no los IDs), por ejemplo:

```env
ESTADOS=Averiado,Con daños internos,Con daños exteriores,Falta de uso,Sin daños
```

**Ubicaciones:** usar el nombre de provincia tal como sale en el listado (p. ej. `León`, `Ávila`, `Asturias`).

### Ejemplo de configuración completa

```env
BASE_URL=https://netsubasta.com
TIPOS=10,13
ESTADOS=Averiado,Con daños internos,Con daños exteriores,Falta de uso,Sin daños
UBICACIONES=León,Zamora,Salamanca,Valladolid,Palencia,Ávila,Burgos,Asturias,Madrid,Cantabria
SEEN_FILE=data/seen_ids.json
DETAIL_DELAY=0.5
FETCH_DETAILS=true
```

### Usar otro archivo de configuración

Puedes apuntar a un `.env` alternativo con la opción `--config`:

```bash
python3 alerta_subastas.py --config /ruta/a/mi-config.env
```

## Cómo lanzar el script

Entra en la carpeta del proyecto:

```bash
cd /home/usuario/PROYECTOS/Subastas
```

### Primera ejecución (recomendado)

La primera vez conviene **no recibir alertas de todo lo que ya está publicado**. Marca los vehículos actuales como vistos:

```bash
python3 alerta_subastas.py --init
```

A partir de ahí, solo se mostrarán **novedades**.

### Ejecución normal

Muestra únicamente vehículos nuevos que cumplen los filtros:

```bash
python3 alerta_subastas.py
```

Si no hay nada nuevo, verás:

```
No hay novedades.
```

### Otras opciones

```bash
# Ver todos los vehículos que cumplen filtros (sin marcarlos como vistos)
python3 alerta_subastas.py --all

# Borrar el historial y volver a empezar de cero
python3 alerta_subastas.py --reset

# Ayuda
python3 alerta_subastas.py --help
```

## Automatizar con crontab

Para comprobar cada 30 minutos y guardar el resultado en un log:

```bash
mkdir -p logs
crontab -e
```

Añade esta línea (ajusta la ruta si es distinta):

```cron
*/30 * * * * cd /home/usuario/PROYECTOS/Subastas && /usr/bin/python3 alerta_subastas.py >> logs/alertas.log 2>&1
```

Otras frecuencias de ejemplo:

```cron
# Cada hora
0 * * * * cd /home/usuario/PROYECTOS/Subastas && /usr/bin/python3 alerta_subastas.py >> logs/alertas.log 2>&1

# Cada día a las 8:00
0 8 * * * cd /home/usuario/PROYECTOS/Subastas && /usr/bin/python3 alerta_subastas.py >> logs/alertas.log 2>&1
```

**Importante:** ejecuta `--init` una vez antes de activar el cron, para no llenar el log con decenas de vehículos ya existentes.

## Qué información muestra

Por cada vehículo nuevo, el script imprime en terminal:

- Marca y modelo
- Kilometraje y año (1ª matriculación)
- Precio actual
- Ubicación y combustible
- Estado del vehículo
- Comentarios / descripción (si `FETCH_DETAILS=true`)
- Enlace directo a la ficha en netsubasta.com

Ejemplo de salida:

```
======================================================================
NUEVO: Bmw 120d Aut. 2008
  ID:         138507
  Tipo:       Turismos y vehículos comerciales
  URL:        https://netsubasta.com/vehiculos/bmw-120d-aut-2008-138507
  Ubicación:  León
  Estado:     Averiados
  Combustible:Diésel
  Marca:      Bmw
  Modelo:     120d Aut.
  Matricul.:  04/2008
  Kilómetros: 525.000 KM
  Precio:     349 €
  Comentarios:Avería en motor. Posible sistema de distribución afectado...
```

## Cómo funciona por dentro

1. Consulta el listado de netsubasta.com filtrando por cada **tipo** configurado en `TIPOS`.
2. Descarta vehículos que no estén en las **ubicaciones** o **estados** definidos en `config.env`.
3. Compara con `data/seen_ids.json` para detectar IDs no vistos antes.
4. Para cada vehículo nuevo, opcionalmente entra en la ficha y extrae la descripción.
5. Guarda los IDs mostrados en el historial para no repetir la alerta.

## Solución de problemas

| Problema | Qué hacer |
|----------|-----------|
| `python3: command not found` | Instala Python 3 o usa la ruta completa (`/usr/bin/python3`). |
| No aparece ningún vehículo | Revisa `UBICACIONES` y `ESTADOS` en `config.env`. Prueba con `--all`. |
| Salen demasiadas alertas la primera vez | Ejecuta `python3 alerta_subastas.py --init` antes de usar el cron. |
| Quiero volver a ver todo como nuevo | `python3 alerta_subastas.py --reset` y luego ejecuta sin `--init`. |
| El script tarda mucho | Pon `FETCH_DETAILS=false` en `config.env` (menos datos, más rápido). |

## Licencia / uso

Script de uso personal para monitorizar oportunidades en subastas. Respeta los términos de uso de netsubasta.com y no abuses de la frecuencia de consultas (`DETAIL_DELAY` ayuda a espaciar las peticiones).
