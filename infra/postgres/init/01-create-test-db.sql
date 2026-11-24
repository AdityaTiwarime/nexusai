-- Runs once, the first time the database volume is created.
-- A separate database keeps automated tests away from your local dev data.
CREATE DATABASE nexusai_test OWNER nexus;
