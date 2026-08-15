-- Сколько тонов ожидается для этой новости. У материалов о гибели людей
-- эмоциональные режимы отключены, поэтому «готова» она с одним нейтральным.
ALTER TABLE articles ADD COLUMN moods_expected INTEGER NOT NULL DEFAULT 4;

-- Нейтральный тон допустим всегда, остальные — только на нечувствительных материалах.
UPDATE moods SET allowed_max_sensitivity='any'  WHERE code='neutral';
UPDATE moods SET allowed_max_sensitivity='none' WHERE code IN ('joyful','sad','ironic');

PRAGMA user_version = 5;
