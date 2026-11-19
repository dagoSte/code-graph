CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT);
CREATE VIEW active AS
SELECT id, name FROM users WHERE id > 0;
WITH active AS (SELECT * FROM users)
SELECT * FROM active;
INSERT INTO users SELECT id + 1, name FROM active;
UPDATE users SET name = 'updated' WHERE id IN (SELECT id FROM active);
DELETE FROM users WHERE id < 0;
