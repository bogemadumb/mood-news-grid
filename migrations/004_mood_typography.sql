-- Типографика тона хранится вместе с тоном, а не зашита в вёрстку.
-- Добавить пятый режим — это строка в таблице, а не новый макет.

ALTER TABLE moods ADD COLUMN btn_weight INTEGER NOT NULL DEFAULT 400;
ALTER TABLE moods ADD COLUMN btn_opsz   INTEGER NOT NULL DEFAULT 16;
ALTER TABLE moods ADD COLUMN btn_track  TEXT    NOT NULL DEFAULT '0em';
ALTER TABLE moods ADD COLUMN btn_style  TEXT    NOT NULL DEFAULT 'normal';

UPDATE moods SET btn_weight=400, btn_opsz=16, btn_track='0em',     btn_style='normal' WHERE code='neutral';
UPDATE moods SET btn_weight=600, btn_opsz=40, btn_track='-.015em', btn_style='normal' WHERE code='joyful';
UPDATE moods SET btn_weight=320, btn_opsz=12, btn_track='.02em',   btn_style='normal' WHERE code='sad';
UPDATE moods SET btn_weight=430, btn_opsz=22, btn_track='-.004em', btn_style='italic' WHERE code='ironic';

PRAGMA user_version = 4;
