#!/usr/bin/env bash
#
# Where the Shopify cycle is actually broken.
#
# The product's whole claim is one chain. A store connects; it tells us about an
# order; we sync the numbers; an engine proposes something; you approve it; the
# result gets measured. Until the last link closes, everything before it is
# scaffolding — impressive, and worth nothing to a seller. This script says
# which link is open, and only that.
#
# It changes nothing. Every query runs in a read-only transaction, the only
# request it makes is GET /health, and no secret is ever selected: credentials
# are counted, never read.
#
# A later link failing is usually not a finding but an echo — no orders means
# nothing to sync, nothing to propose, nothing to measure. So only the FIRST
# open link is reported as the break; the rest are marked as consequences.
#
# Every link past the connection is scoped to the connected store. Counting the
# whole database was this script's own first bug: proposals left over from
# testing belonged to two other tenants, and the cycle reported them as this
# store's progress. Other tenants' rows are now named in passing and never
# counted in — a total that flatters is worse than no total.
#
#   ops/check-store-cycle.sh
#
# Exit 0 when the whole chain is closed, 1 when a link is open, 2 when it could
# not look.
#
set -uo pipefail

PROJECT="${COMPOSE_PROJECT:-ai-commerce-operator}"
DB_ROLE="${DB_ROLE:-aco}"
DB_NAME="${DB_NAME:-aco}"

container_for() {
  docker ps --filter "label=com.docker.compose.project=$PROJECT" \
            --filter "label=com.docker.compose.service=$1" \
            --format '{{.ID}}' | head -1
}

DB="$(container_for db)"
API="$(container_for api)"

if [ -z "$DB" ]; then
  echo "Не найден контейнер db проекта \"$PROJECT\". Стек запущен?" >&2
  exit 2
fi

# The read-only transaction is a guard, not a promise: even a query with a typo
# that turned into a write would be refused by the server.
q() {
  docker exec -e PGOPTIONS='-c default_transaction_read_only=on' "$DB" \
    psql -U "$DB_ROLE" -d "$DB_NAME" -tAqc "$1" 2>/dev/null
}

field() { echo "$1" | cut -d'|' -f"$2"; }

BREAK=""
BREAK_N=0

link() {  # link <номер> <название> <0|1> <подробность>
  local n="$1" name="$2" ok="$3" detail="$4" mark
  if [ "$ok" = "1" ]; then
    mark="[ OK ]"
  elif [ -z "$BREAK" ]; then
    mark="[СБОЙ]"; BREAK="$name"; BREAK_N="$n"
  else
    mark="[ -- ]"
  fi
  printf '  %s  %s. %s\n' "$mark" "$n" "$name"
  printf '          %s\n\n' "$detail"
}

echo
echo "Цикл магазина Shopify — какое звено разорвано"
echo "=============================================="
echo

# ---------------------------------------------------------------- 0. адрес
# Shopify cannot reach localhost. If this link is open, every link after it is
# open for the same reason, and chasing them wastes the afternoon.
WEBHOOK_URI="$(docker exec "$API" printenv SHOPIFY_WEBHOOK_URI 2>/dev/null)"
if [ -z "$WEBHOOK_URI" ]; then
  link 0 "Публичный адрес" 0 "SHOPIFY_WEBHOOK_URI не задан в контейнере api — Shopify некуда стучаться."
  BASE=""
else
  BASE="${WEBHOOK_URI%/api/webhooks/shopify}"
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/health" 2>/dev/null)"
  if [ "$CODE" = "200" ]; then
    link 0 "Публичный адрес" 1 "$BASE — отвечает (/health = 200)"
  else
    link 0 "Публичный адрес" 0 "$BASE — не отвечает (HTTP ${CODE:-нет ответа}). Туннель упал или сменил имя."
  fi
fi

# ---------------------------------------------------------------- 1. OAuth
CH="$(q "select external_account_id || '|' || status || '|' || coalesce(to_char(connected_at,'YYYY-MM-DD HH24:MI'),'?') from channel_connections where provider='shopify' order by connected_at desc nulls last limit 1;")"
CREDS="$(q "select count(*) from integration_credentials where provider like 'shopify%';")"
STORE_ID="$(q "select store_id from channel_connections where provider='shopify' order by connected_at desc nulls last limit 1;")"

# Everything after this point is about ONE store. Counting the whole database
# instead was this script's own first bug: three proposals left over from
# testing belonged to two other tenants, and the cycle reported them as this
# store's progress — exactly the flattering arithmetic the script exists to
# refuse. `where false` is the honest answer when no store is connected.
if [ -n "$STORE_ID" ]; then
  MINE="store_id='$STORE_ID'"
  THEIRS="store_id<>'$STORE_ID'"
else
  MINE="false"
  THEIRS="true"
fi

if [ -n "$CH" ]; then
  SHOP="$(field "$CH" 1)"; ST="$(field "$CH" 2)"; WHEN="$(field "$CH" 3)"
  if [ "$ST" = "connected" ] && [ "${CREDS:-0}" -gt 0 ]; then
    link 1 "OAuth" 1 "$SHOP — подключён $WHEN, токен сохранён (ключей: $CREDS)"
  else
    link 1 "OAuth" 0 "$SHOP — статус \"$ST\", сохранённых ключей: ${CREDS:-0}. Подключение неполное."
  fi
else
  link 1 "OAuth" 0 "Ни одного магазина Shopify не подключено. Установи приложение на dev store."
fi

# ------------------------------------------------------------- 2. вебхуки
# Two different failures wear the same face. The subscriptions can be missing,
# or they can exist while pointing at a hostname that died with the last tunnel
# — Shopify keeps calling it and nobody hears anything.
SUB="$(q "select coalesce(settings->>'webhook_setup','-') || '|' || (case when jsonb_typeof(settings->'webhooks')='array' then jsonb_array_length(settings->'webhooks') else 0 end)::text || '|' || coalesce(settings->>'webhook_uri','') || '|' || coalesce(settings->>'webhook_error','') from channel_connections where provider='shopify' order by connected_at desc nulls last limit 1;")"
DEL="$(q "select count(*)::text || '|' || count(*) filter (where status='failed')::text || '|' || coalesce(to_char(max(received_at),'YYYY-MM-DD HH24:MI'),'') || '|' || count(*) filter (where status='completed')::text || '|' || count(*) filter (where status='processing')::text from webhook_deliveries where provider='shopify' and $MINE;")"

SETUP="$(field "$SUB" 1)"; TOPICS="$(field "$SUB" 2)"
REG_URI="$(field "$SUB" 3)"; WH_ERR="$(field "$SUB" 4)"
D_ALL="$(field "$DEL" 1)"; D_BAD="$(field "$DEL" 2)"; D_LAST="$(field "$DEL" 3)"
D_OK="$(field "$DEL" 4)"; D_STUCK="$(field "$DEL" 5)"

if [ -z "$CH" ]; then
  link 2 "Вебхуки" 0 "Проверять нечего: магазин не подключён."
elif [ "${TOPICS:-0}" = "0" ]; then
  link 2 "Вебхуки" 0 "Подписок нет (setup=$SETUP).${WH_ERR:+ Причина: $WH_ERR}"
elif [ -n "$REG_URI" ] && [ -n "$WEBHOOK_URI" ] && [ "$REG_URI" != "$WEBHOOK_URI" ]; then
  link 2 "Вебхуки" 0 "Подписки ($TOPICS шт.) нацелены на $REG_URI, а сервер слушает $WEBHOOK_URI. Shopify стучится в пустоту — нажми \"Point them here\" на экране Shopify."
elif [ "${D_ALL:-0}" = "0" ]; then
  link 2 "Вебхуки" 0 "Подписки живы ($TOPICS топиков, адрес совпадает), но не пришло ни одной доставки. Сделай тестовый заказ в магазине."
elif [ "${D_OK:-0}" = "0" ]; then
  # Arrival is not handling. A row left in `processing` is the dangerous state:
  # it looks like work in progress, and a Shopify retry inside the stale window
  # is answered "duplicate" with 200 — so the platform stops retrying and the
  # event is lost with nothing marked failed anywhere.
  link 2 "Вебхуки" 0 "Доставок: $D_ALL, но ни одна не обработана (зависших в processing: ${D_STUCK:-0}, с ошибкой: ${D_BAD:-0}). Последняя $D_LAST. Событие в метрики не попало — добери синком."
elif [ "${D_STUCK:-0}" != "0" ]; then
  link 2 "Вебхуки" 1 "$TOPICS топиков, обработано: $D_OK из $D_ALL, но ${D_STUCK} висит в processing (с ошибкой: ${D_BAD:-0}). Последняя $D_LAST"
else
  link 2 "Вебхуки" 1 "$TOPICS топиков, обработано: $D_OK из $D_ALL (с ошибкой: ${D_BAD:-0}), последняя $D_LAST"
fi

# --------------------------------------------------------- 3. синхронизация
# `costs_complete` is not decoration. Without real costs the profit is withheld
# on purpose, so a store can sync perfectly and still show no money.
SY="$(q "select count(*)::text || '|' || coalesce(min(metric_date)::text,'') || '|' || coalesce(max(metric_date)::text,'') || '|' || count(*) filter (where costs_complete)::text || '|' || coalesce(sum(orders),0)::text || '|' || coalesce(sum(revenue),0)::text || '|' || coalesce(max(currency),'') from commerce_daily_metrics where $MINE;")"
M_ALL="$(field "$SY" 1)"; M_FROM="$(field "$SY" 2)"; M_TO="$(field "$SY" 3)"; M_COST="$(field "$SY" 4)"
M_ORD="$(field "$SY" 5)"; M_REV="$(field "$SY" 6)"; M_CUR="$(field "$SY" 7)"

if [ -z "$CH" ]; then
  link 3 "Синхронизация" 0 "Проверять нечего: магазин не подключён."
elif [ "${M_ALL:-0}" = "0" ]; then
  link 3 "Синхронизация" 0 "Ни одного дня метрик. Запусти синк на экране Shopify (или POST /api/integrations/shopify/sync)."
elif [ "${M_COST:-0}" = "0" ]; then
  link 3 "Синхронизация" 1 "$M_ALL дн. ($M_FROM … $M_TO), заказов: $M_ORD, выручка: $M_REV $M_CUR. Себестоимость не заполнена ни за один день — прибыль будет удержана намеренно."
else
  link 3 "Синхронизация" 1 "$M_ALL дн. ($M_FROM … $M_TO), заказов: $M_ORD, выручка: $M_REV $M_CUR, дней с полной себестоимостью: $M_COST"
fi

# ------------------------------------------------------------ 4. предложение
RC="$(q "select count(*) from recommendations where $MINE;")"
PR="$(q "select count(*) from operator_actions where status='proposed' and $MINE;")"
PR_ALIEN="$(q "select count(*) from operator_actions where $THEIRS;")"
# Rows belonging to other tenants are named, never counted in: leftovers from
# testing must not read as this store's progress.
ALIEN_NOTE=""
[ "${PR_ALIEN:-0}" != "0" ] && ALIEN_NOTE=" (ещё $PR_ALIEN у других магазинов — к этому не относятся)"

if [ -z "$CH" ]; then
  link 4 "Предложение" 0 "Проверять нечего: магазин не подключён.$ALIEN_NOTE"
elif [ "${PR:-0}" = "0" ] && [ "${RC:-0}" = "0" ]; then
  # The engine exists now; an empty queue usually means nobody has asked it yet.
  # It also stays silent below a week of history, or once costs are complete and
  # refunds are low — both of those are answers, not faults.
  link 4 "Предложение" 0 "Ни одного предложения. Разбор продаж запускается кнопкой READ MY SALES NOW на экране PROPOSALS (POST /api/commerce/scan). Движок молчит и сам: при истории короче недели, при нулевой выручке и когда придраться не к чему.$ALIEN_NOTE"
elif [ "${PR:-0}" = "0" ]; then
  link 4 "Предложение" 0 "Находок: $RC, но ни одна не стала предложением к действию.$ALIEN_NOTE"
else
  link 4 "Предложение" 1 "Предложений ждёт решения: $PR (находок: ${RC:-0})$ALIEN_NOTE"
fi

# --------------------------------------------------------------- 5. одобрение
AP="$(q "select count(*) from operator_actions where status in ('approved','applied') and $MINE;")"
if [ -z "$CH" ]; then
  link 5 "Одобрение" 0 "Проверять нечего: магазин не подключён."
elif [ "${AP:-0}" = "0" ]; then
  link 5 "Одобрение" 0 "Ни одно предложение этого магазина не одобрено. Это твой шаг — без него оператор ничего не делает."
else
  link 5 "Одобрение" 1 "Одобрено и применено: $AP"
fi

# --------------------------------------------------------------- 6. измерение
# The only link that turns the project into a product.
MS="$(q "select count(*) filter (where measured_at is not null)::text || '|' || count(*) filter (where evidence_mode='real')::text from operator_actions where $MINE;")"
MEASURED="$(field "$MS" 1)"; REAL="$(field "$MS" 2)"
if [ -z "$CH" ]; then
  link 6 "Измерение" 0 "Проверять нечего: магазин не подключён."
elif [ "${MEASURED:-0}" = "0" ]; then
  link 6 "Измерение" 0 "Ни одного измеренного результата. Главное обещание продукта пока не подтверждено."
elif [ "${REAL:-0}" = "0" ]; then
  link 6 "Измерение" 0 "Измерено: $MEASURED, но ни одного с evidence_mode='real' — в payroll такое не попадёт."
else
  link 6 "Измерение" 1 "Измерено: $MEASURED, из них с реальными данными: $REAL"
fi

echo "=============================================="
if [ -z "$BREAK" ]; then
  echo "Цикл замкнут полностью. Есть измеренный результат на реальных данных."
  exit 0
fi

echo "Первое разорванное звено: $BREAK_N. $BREAK"
echo
case "$BREAK_N" in
  0) echo "Куда смотреть: жив ли контейнер туннеля (docker ps | grep tunnel) и совпадает"
     echo "ли его имя с SHOPIFY_WEBHOOK_URI. Быстрый туннель берёт новое имя при каждом"
     echo "запуске — тогда все три адреса надо заново вписать в Partner Dashboard." ;;
  1) echo "Куда смотреть: адреса в Shopify Partner Dashboard должны совпадать с теми,"
     echo "что видит контейнер api. Логи: docker compose logs api | grep -i shopify" ;;
  2) echo "Куда смотреть: экран Shopify показывает состояние подписок. Слово stale значит,"
     echo "что они нацелены на прошлый туннель; кнопка Point them here их переносит."
     echo "Если доставка висит в processing — событие уже потеряно: Shopify получил 200"
     echo "и повторять не будет. Данные добираются синком, он читает заказы напрямую." ;;
  3) echo "Куда смотреть: очередь и воркер. Синк идёт фоновой задачей —"
     echo "docker compose logs worker | tail -50" ;;
  4) echo "Куда смотреть: commerce_engine читает измеренные дни этого магазина."
     echo "Запусти разбор кнопкой на экране PROPOSALS. Если он вернул ноль находок,"
     echo "это ответ, а не поломка: нет недели истории, нет выручки, либо"
     echo "себестоимость заполнена и возвраты в норме." ;;
  5) echo "Куда смотреть: экран предложений. Одобрение — сознательно ручное:"
     echo "новый магазин ничего не запускает без разрешения." ;;
  6) echo "Куда смотреть: измерение наступает после окна в measurement_days."
     echo "Раньше срока результата не будет, и это правильно." ;;
esac
exit 1
