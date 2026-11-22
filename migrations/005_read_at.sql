-- when a link was marked read (✅), for the obsidian export (#17). null for links read before this existed.
ALTER TABLE links ADD COLUMN read_at timestamptz;
