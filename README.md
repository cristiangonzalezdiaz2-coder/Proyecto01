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
- ✅ Estrategia de ejemplo: cruce de medias móviles (fácil de reemplazar).
- ✅ Gestión de riesgo: stop-loss, take-profit, tamaño de posición, límite de
  pérdida diaria y máximo de posiciones abiertas.
- ✅ Backtesting con datos históricos reales.
- ✅ Logging a consola y archivo.

## Estructura

```
Proyecto01/
├── main.py                 # punto de entrada del bot
├── backtest.py             # backtesting de la estrategia
├── requirements.txt
├── .env.example            # plantilla de credenciales (copiar a .env)
├── config/
│   └── config.example.yaml # plantilla de configuración (copiar a config.yaml)
├── src/
│   ├── config.py           # carga de .env + YAML
│   ├── logger.py
│   ├── mexc/               # cliente de la API de MEXC
│   ├── strategies/         # estrategias (ma_crossover, ...)
│   ├── risk/               # gestión de riesgo
│   └── trading/            # motor de ejecución (paper/live)
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

5. **Prueba la estrategia con datos históricos**:
   ```bash
   python backtest.py --symbol BTCUSDT --interval 1h --limit 500
   ```

6. **Ejecuta en modo simulación** (por defecto `TRADING_MODE=paper`):
   ```bash
   python main.py
   ```

7. Cuando estés seguro, cambia `TRADING_MODE=live` en `.env` para operar con
   dinero real. **Hazlo bajo tu propia responsabilidad y con poco capital.**

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

## Pruebas

```bash
pip install pytest
pytest
```

## Próximos pasos sugeridos

- [ ] Añadir más estrategias (RSI, MACD, grid, DCA).
- [ ] Persistir el estado de las posiciones (SQLite) para reinicios.
- [ ] Notificaciones (Telegram/email) en cada operación.
- [ ] Ajustar cantidades a la precisión (`exchangeInfo`) de cada símbolo.
- [ ] Soporte de futuros cuando la cuenta lo permita.
```
