# Bot de Trading MEXC (Spot)

Bot de trading automatizado para el mercado **spot** de MEXC, con arquitectura
modular preparada para añadir futuros más adelante. Incluye modo simulación
(*paper trading*), gestión de riesgo y backtesting.

> ⚠️ **Aviso**: el trading automatizado conlleva riesgo de pérdida de capital.
> Este software se ofrece con fines educativos. Úsalo primero en modo `paper`
> y con cantidades pequeñas. No es asesoramiento financiero.

## Características

- ✅ Cliente REST de MEXC spot con firma HMAC-SHA256.
- ✅ Modo **paper** (simulación) y **live** (órdenes reales).
- ✅ Cuatro estrategias listas: cruce de medias, RSI, MACD y Bollinger
  (framework fácil de extender).
- ✅ **Multi-bot**: varias estrategias/pares en paralelo, cada uno con su propio
  riesgo y estado, desglosados en el dashboard.
- ✅ **Riesgo global compartido**: tope de exposición, posiciones y pérdida
  diaria combinados entre todos los bots.
- ✅ **Ajuste de precisión por símbolo** (`exchangeInfo`): redondea cantidades
  y precios y valida el importe mínimo para que MEXC no rechace las órdenes.
- ✅ Gestión de riesgo: stop-loss, take-profit, tamaño de posición, límite de
  pérdida diaria y máximo de posiciones abiertas.
- ✅ Backtesting con datos históricos reales.
- ✅ Notificaciones por **Telegram** en cada operación (opcional).
- ✅ **Persistencia en SQLite**: sobrevive a reinicios sin perder posiciones.
- ✅ **Dashboard web** (solo lectura) con curva de equity y métricas avanzadas
  (drawdown, profit factor, expectancy, Sharpe, rachas…).
- ✅ Logging a consola y archivo.

## Estructura

```
Proyecto01/
├── main.py                 # punto de entrada del bot
├── backtest.py             # backtesting de estrategias
├── dashboard.py            # servidor del dashboard web
├── requirements.txt
├── .env.example            # plantilla de credenciales (copiar a .env)
├── config/
│   └── config.example.yaml # plantilla de configuración (copiar a config.yaml)
├── src/
│   ├── config.py           # carga de .env + YAML
│   ├── logger.py
│   ├── mexc/               # cliente de la API de MEXC
│   ├── strategies/         # estrategias (ma_crossover, rsi, macd, bollinger)
│   ├── risk/               # gestión de riesgo
│   ├── trading/            # motor (engine) + runner multi-bot
│   ├── persistence/        # almacenamiento en SQLite
│   ├── notifications/      # notificaciones (Telegram)
│   ├── analytics/          # métricas de rendimiento y curva de equity
│   └── dashboard/          # dashboard web Flask (solo lectura)
└── tests/
```

## Puesta en marcha

1. **Instala dependencias** (Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

2. **Configura tus credenciales**:
   ```bash
   cp .env.example .env
   # edita .env y pon tu MEXC_API_KEY y MEXC_API_SECRET
   ```

3. **Configura los parámetros**:
   ```bash
   cp config/config.example.yaml config/config.yaml
   # ajusta símbolo, intervalo, estrategia y riesgo
   ```

4. **Comprueba la conexión**:
   ```bash
   python main.py --check
   ```

5. **Prueba las estrategias con datos históricos**:
   ```bash
   # Comparar TODAS las estrategias sobre los mismos datos:
   python backtest.py --compare --symbol BTCUSDT --interval 1h --limit 1000

   # Probar una estrategia concreta con sus parámetros:
   python backtest.py --strategy rsi --params '{"period": 14}'
   ```

6. **Ejecuta en modo simulación** (por defecto `TRADING_MODE=paper`):
   ```bash
   python main.py
   ```

7. Cuando estés seguro, cambia `TRADING_MODE=live` en `.env` para operar con
   dinero real. **Hazlo bajo tu propia responsabilidad y con poco capital.**

## Notificaciones por Telegram (opcional)

El bot envía un mensaje en cada operación (compra, venta por stop-loss /
take-profit / señal, y cuando se alcanza el límite de pérdida diaria).

1. Abre Telegram, habla con **@BotFather**, crea un bot y copia su **token**.
2. Escríbele cualquier mensaje a tu nuevo bot.
3. Visita `https://api.telegram.org/bot<TOKEN>/getUpdates` y busca
   `"chat":{"id": ...}` — ese número es tu **chat_id**.
4. Rellena en `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=123456789
   ```
5. Comprueba que funciona:
   ```bash
   python main.py --test-telegram
   ```

Si dejas ambos valores vacíos, las notificaciones se desactivan automáticamente
(el bot sigue funcionando igual). Un fallo de red al notificar nunca detiene el
trading.

## Seguridad de la API key

- Crea la key en https://www.mexc.com/user/openapi con permisos **solo de
  Spot Trading**. **No** habilites permisos de **retiro (withdraw)**.
- Si puedes, restringe la key por **IP**.
- El archivo `.env` está en `.gitignore`: **nunca** subas tus claves al repo.

## Sobre futuros

MEXC tiene **restringida la creación de órdenes de futuros vía API** para muchas
cuentas retail. Antes de desarrollar esa parte conviene verificar qué permite tu
cuenta. La arquitectura actual (cliente base + cliente spot) está pensada para
añadir un `MexcFuturesClient` cuando se confirme el acceso.

## Estrategias disponibles

Se selecciona con `strategy.name` en `config.yaml`. Cada una tiene sus propios
parámetros (ver `config/config.example.yaml`).

| Nombre | Tipo | Idea | Parámetros |
|---|---|---|---|
| `ma_crossover` | Tendencia | Cruce de medias móviles rápida/lenta | `fast_period`, `slow_period` |
| `rsi` | Reversión | Compra al salir de sobreventa, vende al salir de sobrecompra | `period`, `oversold`, `overbought` |
| `macd` | Momentum | Cruce de la línea MACD sobre su señal | `fast`, `slow`, `signal` |
| `bollinger` | Volatilidad | Compra/vende cuando el precio rompe las bandas | `period`, `num_std` |

Usa `python backtest.py --compare` para ver cuál rinde mejor en un par e
intervalo concretos antes de elegir. Puedes añadir la tuya creando una clase que
herede de `Strategy` en `src/strategies/` y registrándola en `STRATEGIES`.

## Precisión por símbolo

Antes de operar, el bot consulta `/api/v3/exchangeInfo` y aprende las reglas del
par: cuántos decimales admite la **cantidad** y el **precio**, y el **importe
mínimo** de orden (notional). Con eso:

- **Trunca** la cantidad a los decimales permitidos (hacia abajo, para no
  exceder tu balance) y **redondea** los precios de stop-loss/take-profit.
- **Omite la compra** si `quote_per_trade` no llega al mínimo del par (en MEXC
  suele ser ~5 USDT), avisando por log y Telegram en vez de mandar una orden
  que sería rechazada.
- Si el par no admite spot en ese momento, no opera.

Puedes ver la precisión de tu par con:
```bash
python main.py --check
```
Si MEXC no está accesible al arrancar, el bot lo avisa y sigue funcionando sin
ajuste de precisión (útil en modo paper).

## Multi-bot (varias estrategias/pares en paralelo)

Puedes correr varios bots a la vez definiendo una lista `bots:` en
`config.yaml` (ver ejemplo en `config/config.example.yaml`). Cada bot:

- Opera **su propio par y estrategia**, con su propia gestión de riesgo.
- Corre en **su propio hilo**; un `Ctrl+C` los detiene a todos de forma ordenada.
- Guarda sus operaciones **separadas** en la base de datos (etiqueta `bot`), así
  que el límite de pérdida diaria y las posiciones no se mezclan entre bots.
- Aparece **desglosado en el dashboard** (sección "Rendimiento por bot") además
  de en las métricas globales agregadas.

Lo que no especifiques por bot (interval, poll_seconds, risk…) se hereda de la
raíz del YAML. Si defines un solo bot (o usas el formato antiguo con `symbol` en
la raíz), todo funciona igual que antes.

```yaml
bots:
  - name: btc-tendencia
    symbol: BTCUSDT
    interval: 1h
    strategy: { name: ma_crossover, fast_period: 9, slow_period: 21 }
    risk: { quote_per_trade: 20.0 }
  - name: eth-reversion
    symbol: ETHUSDT
    interval: 15m
    strategy: { name: rsi, period: 14 }
    risk: { quote_per_trade: 15.0 }
```

## Riesgo global (compartido entre bots)

Cuando corres varios bots con la **misma cuenta y balance**, conviene un tope
combinado. Define una sección `global_risk` en `config.yaml`:

```yaml
global_risk:
  max_total_exposure: 100.0   # USDT máx. comprometidos a la vez (suma de bots)
  max_daily_loss: 80.0        # pérdida diaria combinada máxima (USDT)
  max_open_positions: 3       # posiciones abiertas simultáneas en total
```

- Ningún bot abrirá una posición que haga **superar** estos topes combinados.
- Si se alcanza la **pérdida diaria global**, **todos** los bots dejan de abrir
  posiciones hasta el día siguiente (las posiciones abiertas siguen gestionando
  su stop-loss/take-profit con normalidad).
- Un valor de `0` (o la ausencia de la sección) significa "sin límite".
- El gestor es **thread-safe** (los bots corren en hilos) y **se reconstruye
  desde la base de datos** al reiniciar, así que la exposición y el PnL diario
  global no se pierden.
- El dashboard muestra una sección **"Riesgo global"** con barras de uso frente
  a cada tope.

> El límite de pérdida diaria de cada bot (`risk.max_daily_loss`) sigue
> aplicándose por separado; el global actúa **por encima** como red de seguridad
> para el conjunto.

## Persistencia (SQLite)

El bot guarda su estado en una base de datos SQLite (`data/bot.db` por defecto,
configurable con `db_path` en `config.yaml`). Así, si el bot se reinicia o se
cae, al arrancar de nuevo:

- **Recupera las posiciones abiertas** y sigue vigilando su stop-loss/take-profit.
- **Restaura el PnL del día** y el bloqueo por pérdida máxima diaria.

Además, cada operación cerrada se guarda en la tabla `trades` como historial.
La carpeta `data/` está en `.gitignore`, así que la base de datos nunca se sube
al repositorio.

Tablas:
- `positions` — posiciones (abiertas y cerradas).
- `trades` — historial de operaciones cerradas con su PnL.
- `daily_state` — PnL y estado de bloqueo por día.

## Dashboard web

Un panel de **solo lectura** que lee la misma base de datos SQLite y muestra
posiciones abiertas, historial de operaciones y estadísticas. Se actualiza solo
cada 5 segundos. No ejecuta órdenes ni modifica nada. Incluye:

- **Curva de equity** (PnL acumulado) dibujada en canvas, sin librerías externas.
- **Métricas avanzadas**: profit factor, expectancy, drawdown máximo (abs. y %),
  mejor/peor operación, ganancia/pérdida media, ratio de Sharpe por operación y
  racha actual (ganadoras/perdedoras seguidas).

```bash
python dashboard.py                 # http://127.0.0.1:8000
python dashboard.py --port 8080
python dashboard.py --host 0.0.0.0  # accesible desde tu red local
```

Puedes tenerlo abierto mientras el bot corre en otra terminal: gracias al modo
WAL de SQLite, el dashboard lee sin bloquear las escrituras del bot.

> Si expones el dashboard con `--host 0.0.0.0`, tenlo en cuenta: no incluye
> autenticación. Úsalo solo en tu red local o detrás de un proxy con contraseña.

## Pruebas

```bash
pip install pytest
pytest
```

## Próximos pasos sugeridos

- [x] Añadir más estrategias (RSI, MACD, Bollinger).
- [ ] Estrategias de grid trading y DCA.
- [x] Persistir el estado de las posiciones (SQLite) para reinicios.
- [x] Notificaciones (Telegram) en cada operación.
- [x] Dashboard web para ver posiciones e historial.
- [x] Métricas avanzadas y curva de equity en el dashboard.
- [x] Multi-bot: varias estrategias/pares en paralelo.
- [x] Límite de riesgo global compartido entre bots.
- [x] Ajustar cantidades a la precisión (`exchangeInfo`) de cada símbolo.
- [ ] Soporte de futuros cuando la cuenta lo permita.
```
