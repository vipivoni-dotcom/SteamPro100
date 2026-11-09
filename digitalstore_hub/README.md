# DigitalStore Hub — MVP 0.1

Локальный прототип: каталог **собственных** цифровых продуктов с юридическим
фильтром НПД (422-ФЗ) и реестром поставщиков. К маркетплейсам не подключён.

> Фильтр — техническая защита от заведомо неподходящих операций, а не
> юридическая консультация. Спорные случаи проверяйте с юристом / по FAQ ФНС.

## Запуск

```bash
cd digitalstore_hub
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
uvicorn app:app --reload
```

Swagger: http://127.0.0.1:8000/docs

Переменные окружения:

| Переменная       | По умолчанию      | Значение                          |
| ---------------- | ----------------- | --------------------------------- |
| `DSH_TAX_REGIME` | `npd`             | `npd`, `ip` или `ooo`             |
| `DSH_DB_PATH`    | `digitalstore.db` | путь к файлу SQLite               |

## Товарные линии

- **Линия A** — `ownership: own_created` и типы `ebook`, `template`,
  `spreadsheet`, `course`, `software`. Допускается на НПД.
- **Линия B** — `ownership: resale` / `agency` либо типы `game_key`,
  `license_key`, `subscription`, `gift_card` (даже если помечены как
  собственные). На НПД — всегда HTTP 422. На ИП/ООО — только с поставщиком
  со статусом `candidate`.

## Реестр поставщиков

`POST /suppliers` считает баллы (происхождение 20, право перепродажи 25,
документы 15, API 15, работоспособность 10, возвраты 10, территория 5):

- 90–100 → `candidate`; 75–89 → `request_documents`; < 75 → `rejected`;
- нет права перепродажи или подтверждения происхождения → `rejected` независимо от баллов.

`PUT /suppliers/{id}/checks` — повторная проверка, `GET /suppliers/criteria` — критерии.

## Эндпоинты

`GET /` · `POST/GET /categories` · `POST /legal/check` ·
`POST/GET /products` · `GET/PATCH/DELETE /products/{id}` ·
`POST/GET /suppliers` · `GET /suppliers/{id}` · `PUT /suppliers/{id}/checks`

## Проверка

```bash
pytest -q
```

Сценарии: создание собственного товара (201, `status: draft`), повтор SKU (409),
блокировка `resale` / `agency` / подарочных карт на НПД (422), оценка поставщиков.
