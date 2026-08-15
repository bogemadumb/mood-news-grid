-- Порог стилистической дистанции — свой у каждого тона.
--
-- Единый порог 0.15 оказался неверен, и это видно на данных: нейтральный пересказ
-- новости, которая и так написана нейтрально, честно получает дистанцию 0.07 и статус COPY.
-- Требовать от него 0.15 значит требовать перефразирования ради перефразирования.
-- У эмоциональных тонов порог остаётся высоким: там похожесть на оригинал означает,
-- что модель не сделала работу.

ALTER TABLE moods ADD COLUMN style_distance_min REAL NOT NULL DEFAULT 0.15;

UPDATE moods SET style_distance_min = 0.06 WHERE code = 'neutral';
UPDATE moods SET style_distance_min = 0.15 WHERE code IN ('joyful', 'sad', 'ironic');

PRAGMA user_version = 3;
