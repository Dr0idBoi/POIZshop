-- Очистка старых таблиц после миграции

-- Удаляем старую таблицу crm_orders_old (если существует)
DROP TABLE IF EXISTS crm_orders_old;

-- Проверяем, что основная таблица crm_orders существует
SELECT COUNT(*) as orders_count FROM crm_orders;

-- Выводим информацию о схеме таблицы
PRAGMA table_info(crm_orders);

