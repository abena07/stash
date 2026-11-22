-- a drop is now one discord message holding all its picks, with buttons per pick,
-- so several drop_items share one message id
ALTER TABLE drop_items DROP CONSTRAINT drop_items_discord_message_id_key;
CREATE INDEX drop_items_message_idx ON drop_items (discord_message_id);
